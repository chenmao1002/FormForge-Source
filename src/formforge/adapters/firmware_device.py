"""Version-bound device jobs with an isolated process and one cross-project I/O lease."""
import base64
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from ..firmware_flash_schemas import FlashDeviceRequest
from ..processes import run_process
from ..store import DomainError, now, uid
from ..jobs import Cancelled
from .flash_formats import sha
from .firmware_targets import flash_target


@contextmanager
def device_lease(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if path.stat().st_size == 0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise DomainError('device_busy', '另一设备任务仍在运行，请等待其结束。', 409) from error
        try: yield
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream, fcntl.LOCK_UN)


class FirmwareDevice:
    version = 'firmware-device/1'
    # Existing task framework terminology: does not adopt a project revision.
    read_only = True

    def __init__(self, config, store, flash):
        self.config, self.store, self.flash = config, store, flash
        self.runner = run_process

    def validate(self, project, params):
        request = FlashDeviceRequest.model_validate(params).model_dump()
        environment = self.flash.profile()['environment']
        if not environment: raise DomainError('tool_unavailable', '烧录环境不可用，请重新核对。', 409)
        if request['backend']=='pyocd' and request['target']!='STM32G030F6P6':self.flash.device_pack(request['target'])
        if request['action'] in ('program', 'verify'):
            plan, bundle, _ = self.flash.execution_inputs(project, request['planId'], request['expectedPlanSha256'])
            if bundle['request']['target'] != request['target'] or plan['targetProfile']['backend'] != request['backend']:
                raise DomainError('target_mismatch', '设备请求与计划芯片不匹配。', 409)
            identification = self.flash.read(project, 'jobs', request['identificationJobId'])
            result = identification.get('result') or {}
            if (identification['kind'] != 'firmware.device' or identification['status'] != 'completed'
                    or identification['params']['action'] != 'identify'
                    or result.get('identitySha256') != request['expectedIdentitySha256']
                    or result.get('identity', {}).get('selector') != request['selector']
                    or result.get('identity', {}).get('backend') != request['backend']):
                raise DomainError('identity_changed', '识别记录不匹配，请先识别所选设备。', 409)

    def verify_environment(self):
        env = self.flash.profile()['environment']
        if not env: raise DomainError('tool_changed', '烧录工具环境缺失或变化。', 409)
        for package in json.loads((self.config.root/env['filesManifest']).read_text(encoding='utf8')):
            for item in package['files']:
                path = (self.config.root/item['path']).resolve()
                if not path.is_relative_to(self.config.root) or not path.is_file() or sha(path.read_bytes()) != item['sha256']:
                    raise DomainError('tool_changed', '烧录环境文件摘要不符，请重新核对。', 409)
        return env

    def execute(self, task):
        job = task.job; project = job['projectId']; request = dict(job['params'])
        output = self.config.data/'projects'/project/'jobs'/job['id']
        output.mkdir(parents=True, exist_ok=True)
        report_path = output/'device-report.json'; log = output/'device.log'
        report = dict(schemaVersion='formforge.device-report/1', jobId=job['id'],
                      outcome='no-write-attempted', phase='preflight', verified=False,
                      planId=request.get('planId'), planSha256=request.get('expectedPlanSha256'),
                      hardwareBehaviorVerified=False)
        report_path.write_text(json.dumps(report), encoding='utf8')
        try:
            with device_lease(self.config.local/'flash-device.lock'):
                task.phase('核对固件、计划与烧录环境')
                self.validate(project, request)
                env = self.verify_environment()
                payload = dict(request, pack=str(self.config.root/env['pack']['path']))
                if request['backend']=='pyocd':
                    payload['pack']=str(self.flash.device_pack(request['target']))
                    payload['deviceProfile']=flash_target(request['target'])
                if request['action'] in ('program', 'verify'):
                    plan, bundle, segments = self.flash.execution_inputs(project, request['planId'], request['expectedPlanSha256'])
                    payload['targetProfile'] = plan['targetProfile']
                    payload['segments'] = [dict(address=a, data=base64.b64encode(b).decode()) for a,b in segments]
                    # Coalesce complete declared erase ranges. Avoid esptool's overlapping-sector writes
                    # and padding outside the plan. Only the explicitly approved gaps become 0xff.
                    expanded = []
                    for row in plan['eraseRanges']:
                        start = row['address']; data = bytearray(b'\xff'*row['size'])
                        for a,b in segments:
                            left, right = max(start,a), min(start+len(data),a+len(b))
                            if left < right: data[left-start:right-start] = b[left-a:right-a]
                        expanded.append(dict(address=start, data=base64.b64encode(data).decode()))
                    payload['writeSegments'] = expanded
                    report.update(bundleId=bundle['id'], bundleSha256=bundle['bundleSha256'],
                                  buildBinding=bundle.get('buildBinding'), toolEnvironment=env)
                payload_path = output/'device-input.json'
                payload_path.write_text(json.dumps(payload), encoding='utf8')
                worker = output/'device-worker.py'
                worker.write_bytes(Path(__file__).with_name('flash_device_worker.py').read_bytes())
                report.update(adapterVersion=self.version, workerSha256=sha(worker.read_bytes()))
                last_phase = None
                def poll():
                    nonlocal last_phase
                    task.cancel()
                    try: phase = json.loads(report_path.read_text(encoding='utf8')).get('phase')
                    except (OSError, ValueError): return
                    if phase != last_phase:
                        last_phase = phase
                        task.phase('设备流程：'+str(phase))
                task.cancel()
                # Only this runner crosses into hardware; test replacements never load an SDK driver.
                report['deviceProcessStarted'] = True
                self.store.job_update(job['id'], result=dict(report))
                process = self.runner([self.config.root/env['python'], '-I',
                    worker, payload_path, report_path],
                    cwd=output, log=log, env={**os.environ, 'PYTHONNOUSERSITE':'1', 'PYTHONUTF8':'1',
                        'TEMP':str(output), 'TMP':str(output)}, cancel=poll, deadline=time.monotonic()+300)
                report.update(json.loads(report_path.read_text(encoding='utf8')))
                if process['exitCode'] or report.get('error'):
                    raise DomainError('device_failed', report.get('error', '设备子进程失败。'))
                if request['action'] in ('program', 'verify') and not report.get('verified'):
                    raise DomainError('verification_failed', '缺少完整回读校验结果。')
                report['checksPassed'] = True
                report['finishedAt'] = now()
                task.cancel()
                if self.store.finish_readonly(job['id'], report, '设备流程完成；运行行为验证另列') is None: raise Cancelled()
        except BaseException as error:
            try: report.update(json.loads(report_path.read_text(encoding='utf8')))
            except (OSError, ValueError): pass
            report.update(checksPassed=False, error=str(error) or '任务已取消', interrupted=isinstance(error, Cancelled), finishedAt=now())
            self.store.job_update(job['id'], result=report)
            raise
        finally:
            report.setdefault('finishedAt', now())
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
            # Diagnostics survive cancellation; task.publish intentionally refuses cancelled output.
            for path, kind, mime in [(report_path,'firmware_device_report','application/json'), (log,'firmware_device_log','text/plain')]:
                if path.is_file(): self.store.add_artifact(dict(id=uid('a'), projectId=project, jobId=job['id'],
                    revisionId=job['inputRevision'], name=path.name, kind=kind, mimeType=mime,
                    size=path.stat().st_size, sha256=sha(path.read_bytes()),
                    relativePath=path.relative_to(self.config.data).as_posix(), createdAt=now(), status='diagnostic-or-preview'))
