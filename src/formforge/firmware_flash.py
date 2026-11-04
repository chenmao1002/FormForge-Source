"""Project-owned immutable external firmware bundles and offline erase plans."""
import copy
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from pydantic import ValidationError
from .firmware_flash_schemas import FlashBundleCreate, FlashPlanCreate, FlashImage, FlashInternalCreate
from .adapters.flash_formats import (archive_files, member_name, manifest_images, sections,
                                     merge_sections, sha, fail)
from .store import DomainError, encode, now, uid
from .adapters.firmware_targets import TARGETS as STM32_TARGETS, flash_target, PACKS


TARGETS = {
    'STM32G030F6P6': dict(backend='pyocd', base=0x08000000, size=32768, eraseSize=2048,
                        targetName='stm32g030f6px', architecture='cortex-m0plus'),
    'esp32': dict(backend='esptool', base=0, eraseSize=4096, imageChipId=0, architecture='xtensa'),
    'esp32s3': dict(backend='esptool', base=0, eraseSize=4096, imageChipId=9, architecture='xtensa'),
    'esp32c3': dict(backend='esptool', base=0, eraseSize=4096, imageChipId=5, architecture='riscv'),
}
TARGETS.update({name:flash_target(name) for name in STM32_TARGETS if name!='STM32G030F6P6'})


def content_hash(record):
    return sha(encode(record).encode('utf8'))


class FirmwareFlash:
    def __init__(self, config, store, sources):
        self.config, self.store, self.sources = config, store, sources

    def profile(self):
        path = self.config.root / '.local/flash-environment.json'
        environment = None
        if path.is_file():
            try: environment = json.loads(path.read_text(encoding='utf8'))
            except (ValueError, OSError): pass
        if environment:
            try:
                for relative, expected in [(environment['python'], environment['pythonSha256']),
                                           (environment['pack']['path'], environment['pack']['sha256']),
                                           (environment['filesManifest'], environment['filesManifestSha256'])]:
                    file = (self.config.root/relative).resolve()
                    if not file.is_relative_to(self.config.root.resolve()) or sha(file.read_bytes()) != expected:
                        raise ValueError('environment changed')
            except (KeyError, ValueError, OSError): environment = None
        return dict(schemaVersion='formforge.flash-profile/2', stage='device-workflow', deviceAccessEnabled=False,
                    deviceAccessPolicy='explicit-request-only', actions=['discover', 'identify', 'program', 'verify'],
                    targets=copy.deepcopy(TARGETS), environment=environment,
                    environmentStatus='python-pack-manifest-verified' if environment else 'not-installed-or-changed',
                    backends=[dict(id=x, status='software-integrated', hardwareVerified=False) for x in ('pyocd', 'esptool')] +
                             [dict(id='openocd', status='unavailable', reason='Extension only; not installed or connected.')],
                    formats=['elf', 'hex', 'bin', 'zip'], maxBytes=64*1024*1024)

    def list(self, project):
        self.store.get('projects', project)
        return dict(bundles=self.store.list('flash_bundles', project), plans=self.store.list('flash_plans', project),
                    deviceJobs=[j for j in self.store.list('jobs', project) if j['kind']=='firmware.device'])

    def read(self, project, table, id):
        self.store.get('projects', project)
        row = self.store.get(table, id)
        if row['projectId'] != project: raise DomainError('not_found', '烧录记录不属于此工程。', 404)
        return row

    def _source(self, project, ref):
        if 'artifactId' in ref:
            return self._internal(project, ref)[1]
        row, path = self.sources.get_record(project, ref['sourceId'])
        if row['sha256'] != ref['expectedSha256'] or row['provenanceSha256'] != ref['expectedProvenanceSha256']:
            raise DomainError('input_changed', '固件资料或来源摘要不匹配。', 409)
        return path.read_bytes()

    def _native_esp(self, data, item, request, options):
        python = self.config.root / '.venv-flash/Scripts/python.exe'
        worker = Path(__file__).parent / 'adapters/flash_offline_worker.py'
        if not python.is_file():
            raise DomainError('tool_unavailable', '请先运行 Setup-Flash.ps1 安装离线工具环境。', 409)
        environment = self.profile()['environment']
        if not environment: raise DomainError('tool_unavailable', '烧录环境记录缺失或发生变化，请重新核对环境。', 409)
        env = {**os.environ, 'PYTHONNOUSERSITE':'1', 'PYTHONUTF8':'1',
               'TEMP':str(self.config.local/'flash-temp'), 'TMP':str(self.config.local/'flash-temp')}
        (self.config.local/'flash-temp').mkdir(exist_ok=True)
        payload = dict(data=base64.b64encode(data).decode('ascii'), target=request['target'],
                       address=item['address'], role=item['role'], options=options)
        try:
            completed = subprocess.run([str(python), '-I', str(worker)], input=json.dumps(payload),
                                       text=True, encoding='utf8', capture_output=True, timeout=30,
                                       cwd=self.config.root, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            result = json.loads(completed.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise DomainError('tool_failed', 'ESP 离线解析失败：'+str(error), 422) from error
        if completed.returncode or not result.get('ok'): fail('ESP 原生解析拒绝：'+result.get('error', completed.stderr)[-1500:])
        result = result['result']; effective = base64.b64decode(result.pop('data'), validate=True)
        if result['toolVersion'] != environment['packageVersions']['esptool']:
            raise DomainError('tool_changed', 'esptool 版本与冻结记录不符。', 409)
        return effective, result

    def _resolve(self, project, request, include_data=False):
        images = request['images']
        cache, files, options = {}, {}, {}
        def load(ref):
            key = encode(ref)
            if key not in cache: cache[key] = self._source(project, ref)
            return cache[key]
        if request.get('archive'):
            ref = request['archive']; files[encode(ref)] = archive_files(load(ref))
            rows, options = manifest_images(files[encode(ref)], request['manifest'], request['target'])
            try: images = [FlashImage.model_validate(dict(source=ref, **row)).model_dump() for row in rows]
            except ValidationError as error: fail('固件清单字段无效：'+str(error))
        if not 1 <= len(images) <= 32: fail('烧录包需要 1 至 32 个镜像。')
        output, all_sections, partition_entries = [], [], []
        target = copy.deepcopy(TARGETS[request['target']])
        target['size'] = target.get('size', request.get('flashSize'))
        if target['backend'] == 'esptool':
            self._esp_options(options, target['size'])
            environment = self.profile()['environment']
            if not environment: raise DomainError('tool_unavailable', '请先核对独立烧录环境。', 409)
            packages = json.loads((self.config.root/environment['filesManifest']).read_text(encoding='utf8'))
            for package in packages:
                if package['name'].lower() not in ('esptool', 'esp-pylib'): continue
                for row in package['files']:
                    path = (self.config.root/row['path']).resolve()
                    if not path.is_relative_to(self.config.root.resolve()) or not path.is_file() or sha(path.read_bytes()) != row['sha256']:
                        raise DomainError('tool_changed', 'ESP 离线解析工具文件与环境记录不符。', 409)
        total = 0
        for item in images:
            data = load(item['source'])
            if item.get('member') is not None:
                key = encode(item['source'])
                if key not in files: files[key] = archive_files(data)
                name = member_name(item['member'])
                if name not in files[key]: fail('固件 ZIP 缺少镜像：' + name)
                data = files[key][name]
            total += len(data)
            if total > 64*1024*1024: fail('烧录包镜像总量超过 64 MiB。')
            if target['backend'] == 'esptool' and item['format'] != 'bin': fail('ESP 离线烧录接受 BIN，不把 ELF 当成可直接写入镜像。')
            original = data
            native = None
            if target['backend'] == 'esptool':
                if item.get('address') is None: fail('BIN 必须明确烧录地址。')
                entries = self._esp_image(data, item, target)
                partition_entries.extend(entries)
                data, native = self._native_esp(data, item, request, options)
            ranges = sections(data, item['format'], item.get('address'))
            for address, body in ranges:
                if address < target['base'] or address + len(body) > target['base'] + target['size']:
                    fail('固件装载范围超出声明的目标 Flash。')
            all_sections.extend(ranges)
            output.append(dict(**item, size=len(data), fileSha256=sha(original), effectiveSha256=sha(data),
                               transformation='esptool-public-merge-bin' if data != original else 'none-keep-image-header', nativeValidation=native,
                               ranges=[dict(address=a, size=len(b), sha256=sha(b)) for a, b in ranges]))
        merged = merge_sections(all_sections)
        if partition_entries:
            for item in output:
                if item['role'] in ('application', 'data'):
                    kind = 0 if item['role'] == 'application' else 1
                    if not any(a <= item['address'] and item['address'] + item['size'] <= b and t == kind
                               for a,b,t in partition_entries): fail('ESP 镜像与声明分区不匹配。')
        summary = [dict(address=a, size=len(b), sha256=sha(b)) for a, b in merged]
        # Hash only portable contents, not local source IDs/provenance paths.
        identity = dict(target=request['target'], targetProfile=target, options=options,
                        images=[{k: r[k] for k in ('format', 'role', 'address', 'size', 'fileSha256', 'effectiveSha256', 'ranges')} for r in output])
        result = dict(images=output, ranges=summary, targetProfile=target, flashOptions=options,
                      bundleSha256=content_hash(identity), partitionEntries=partition_entries)
        if include_data: result['_segments'] = merged
        return result

    def _internal(self, project, request):
        from .adapters.firmware import verified_source
        from .adapters.connectivity import fingerprint
        from .adapters.importer import within
        revision = self.read(project, 'revisions', request['revisionId'])
        job = self.read(project, 'jobs', request['buildJobId'])
        artifact = self.read(project, 'artifacts', request['artifactId'])
        if (revision.get('createdByJobId') != job['id'] or job['kind'] != 'firmware.design'
                or job['status'] != 'completed' or not (job.get('result') or {}).get('checksPassed')
                or revision.get('validation', {}).get('status') != 'passed'
                or artifact['jobId'] != job['id'] or artifact['revisionId'] != revision['id']
                or artifact['kind'] not in ('firmware_elf', 'firmware_hex', 'firmware_bin')):
            raise DomainError('invalid_input', '必须选择通过构建检查的同版固件产物。', 409)
        verified_source(self.config, revision)
        design = revision['firmwareDesign']; current = self.store.get('projects', project)
        changed = (self.store.head_revision(project) != design['hardwareDependency']['revisionId']
                   or fingerprint({k:current[k] for k in ('specification', 'preferences')}) != design['contextBinding'])
        context = content_hash(dict(hardwareRevisionId=self.store.head_revision(project),
                                    specification=current['specification'], preferences=current['preferences']))
        if request.get('reviewedContextSha256') and request['reviewedContextSha256'] != context:
            raise DomainError('dependency_changed', '绑定后的工程条件再次变化，请重新复核并建立新包。', 409)
        if changed and not (request.get('dependencyReviewReason') or '').strip():
            raise DomainError('dependency_changed', '硬件或工程条件已变化，请记录继续使用此固件的复核理由。', 409)
        path = within(self.config.data, artifact['relativePath'])
        data = path.read_bytes() if path.is_file() else b''
        if sha(data) != artifact['sha256'] or artifact['sha256'] != request['expectedSha256']:
            raise DomainError('input_changed', '构建产物摘要发生变化。', 409)
        return artifact, data

    def internal_candidates(self, project):
        self.store.get('projects', project)
        return dict(artifacts=[a for a in self.store.list('artifacts', project)
                              if a['kind'] in ('firmware_elf', 'firmware_hex', 'firmware_bin')])

    def create_internal(self, project, raw):
        request = FlashInternalCreate.model_validate(raw).model_dump()
        artifact, _ = self._internal(project, request)
        ref = {k:v for k,v in request.items() if k not in ('name', 'requestId')}
        current = self.store.get('projects', project)
        ref['reviewedContextSha256'] = content_hash(dict(hardwareRevisionId=self.store.head_revision(project),
                                    specification=current['specification'], preferences=current['preferences']))
        revision=self.store.get('revisions',request['revisionId'])
        normalized = dict(name=request['name'], target=revision['firmwareDesign']['profile'], flashSize=None, archive=None,
                          images=[dict(source=ref, format=artifact['kind'].split('_')[1], role='firmware',
                                       address=0x08000000 if artifact['kind'] == 'firmware_bin' else None)])
        resolved = self._resolve(project, normalized)
        return self._save(project, 'flash_bundles', 'fb', request,
                          dict(name=request['name'], sourceKind='internal-build', request=normalized,
                               buildBinding=ref, **resolved, qualification='build-and-range-checked',
                               buildVerified=True, hardwareVerified=False))

    def execution_inputs(self, project, plan_id, expected):
        plan = self.read(project, 'flash_plans', plan_id)
        if plan['planSha256'] != expected or not plan['checksPassed']:
            raise DomainError('invalid_plan', '烧录计划摘要不符或检查未通过。', 409)
        bundle = self.read(project, 'flash_bundles', plan['bundleId'])
        resolved = self._resolve(project, bundle['request'], include_data=True)
        if resolved['bundleSha256'] != plan['bundleSha256']:
            raise DomainError('input_changed', '计划绑定的固件内容已变化。', 409)
        if plan['toolEnvironment'] != self.profile()['environment'] or not plan['toolEnvironment']:
            raise DomainError('tool_changed', '工具环境变化或不可用，请重新生成计划。', 409)
        return plan, bundle, resolved.pop('_segments')

    @staticmethod
    def _esp_options(options, size):
        allowed = {'flash_mode': {'keep', 'qio', 'qout', 'dio', 'dout'},
                   'flash_freq': {'keep', '20m', '26m', '40m', '80m', '120m'},
                   'flash_size': {'keep', *[str(x)+'MB' for x in (1, 2, 4, 8, 16, 32)]}}
        for key, value in options.items():
            if key not in allowed or value not in allowed[key]: fail('ESP Flash 参数值不支持离线确定。')
        if options.get('flash_size', 'keep') != 'keep' and int(options['flash_size'][:-2])*1024*1024 != size:
            fail('清单 Flash 容量与声明容量不一致。')

    @staticmethod
    def _esp_image(data, item, target):
        entries = []
        role = item['role']
        if role in ('bootloader', 'application'):
            if len(data) < 24 or data[0] != 0xe9 or int.from_bytes(data[12:14], 'little') != target['imageChipId']:
                fail('ESP 镜像头与所选芯片不匹配。')
        if role == 'partition-table':
            if len(data) < 32 or data[:2] != b'\xaa\x50': fail('ESP 分区表头无效。')
            ranges = []
            for offset in range(0, len(data)-31, 32):
                row = data[offset:offset+32]
                if row[:2] == b'\xff\xff': break
                if row[:2] == b'\xeb\xeb':
                    if row[2:16] != b'\xff'*14 or row[16:] != hashlib.md5(data[:offset]).digest():
                        fail('ESP 分区表 MD5 校验失败。')
                    break
                if row[:2] != b'\xaa\x50': fail('ESP 分区表条目无效。')
                start, size = int.from_bytes(row[4:8], 'little'), int.from_bytes(row[8:12], 'little')
                if not size or start + size > target['size']: fail('ESP 分区范围超出 Flash。')
                if any(start < b and a < start + size for a, b in ranges): fail('ESP 分区重叠。')
                ranges.append((start, start + size))
                entries.append((start, start + size, row[2]))
        return entries

    def _save(self, project, table, prefix, request, payload):
        fingerprint = content_hash(request)
        with self.store.tx() as db:
            self.store._get(db, 'projects', project)
            rows = [json.loads(r[0]) for r in db.execute(f'SELECT data FROM {table} WHERE project_id=?', (project,))]
            for old in rows:
                if old.get('requestId') == request['requestId']:
                    if old.get('requestFingerprint') != fingerprint:
                        raise DomainError('request_conflict', '同一请求标识不能用于不同输入。', 409)
                    return old
            result = dict(id=uid(prefix), projectId=project, schemaVersion='formforge.'+prefix+'/1',
                          version=len(rows)+1, createdAt=now(), requestId=request['requestId'],
                          requestFingerprint=fingerprint, **payload)
            self.store._put(db, table, result)
            self.store._event(db, table+'.created', project, dict(id=result['id']))
        return result

    def create_bundle(self, project, raw):
        request = FlashBundleCreate.model_validate(raw).model_dump()
        resolved = self._resolve(project, request)
        return self._save(project, 'flash_bundles', 'fb', request,
                          dict(name=request['name'], sourceKind='external', request=request, **resolved,
                               qualification='format-and-range-only', hardwareVerified=False, buildVerified=False))

    def create_plan(self, project, raw):
        request = FlashPlanCreate.model_validate(raw).model_dump()
        bundle = self.read(project, 'flash_bundles', request['bundleId'])
        resolved = self._resolve(project, bundle['request'])
        if resolved['bundleSha256'] != bundle['bundleSha256'] or request['expectedBundleSha256'] != bundle['bundleSha256']:
            raise DomainError('input_changed', '烧录包或文件摘要已变化。', 409)
        profile = resolved['targetProfile']; unit = profile['eraseSize']
        sectors = {}
        ranges = resolved['ranges']
        geometry=profile.get('eraseSectors',[(0,unit)])
        offset=0
        while offset<profile['size']:
            sector_size=next(size for start,size in reversed(geometry) if offset>=start)
            start=profile['base']+offset
            if any(row['address']<start+sector_size and start<row['address']+row['size'] for row in ranges):sectors[start]=sector_size
            offset+=sector_size
        erase, uncovered = [], []
        for start in sorted(sectors):
            unit=sectors[start]
            if erase and erase[-1]['address']+erase[-1]['size'] == start: erase[-1]['size'] += unit
            else: erase.append(dict(address=start, size=unit))
            cursor = start
            for row in ranges:
                left, right = max(start, row['address']), min(start+unit, row['address']+row['size'])
                if right <= left: continue
                if cursor < left: uncovered.append(dict(address=cursor, size=left-cursor))
                cursor = right
            if cursor < start+unit: uncovered.append(dict(address=cursor, size=start+unit-cursor))
        issues = []
        if uncovered and request['uncoveredErasePolicy'] == 'reject':
            issues.append(dict(code='uncovered_erase_bytes', message='擦除扇区包含镜像以外字节，默认拒绝；请明确扩大范围及原因。'))
        warnings = []
        if profile['backend'] == 'esptool' and not resolved['partitionEntries']:
            warnings.append(dict(code='partition_layout_not_bound', message='未提供独立分区表，既有分区和完整启动链未验证。'))
        body = dict(bundleId=bundle['id'], bundleSha256=bundle['bundleSha256'], targetProfile=profile,
                    writeRanges=ranges, eraseRanges=erase, uncoveredEraseRanges=uncovered,
                    uncoveredErasePolicy=request['uncoveredErasePolicy'], eraseReason=request['eraseReason'],
                    checksPassed=not issues, issues=issues, warnings=warnings, deviceAccessEnabled=False, executionReady=False,
                    hardwareVerified=False, toolEnvironment=self.profile()['environment'],
                    verification=dict(required=True, scope='all-declared-write-ranges', performed=False),
                    qualification='offline-plan-only')
        body['planSha256'] = content_hash({k:v for k,v in body.items() if k != 'bundleId'})
        return self._save(project, 'flash_plans', 'fp', request, body)

    def device_pack(self,target):
        if target=='STM32G030F6P6':
            env=self.profile()['environment']
            if not env:raise DomainError('tool_unavailable','烧录环境不可用。',409)
            return self.config.root/env['pack']['path']
        p=PACKS[STM32_TARGETS[target]['family']]
        path=self.config.root/'.local/cmsis-packs'/p['file']
        if not path.is_file() or sha(path.read_bytes())!=p['sha256']:raise DomainError('tool_unavailable','所选型号的固定芯片包缺失或已变化，请运行 Setup-Flash.ps1。',409)
        return path
