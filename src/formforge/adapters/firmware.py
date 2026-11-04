"""Independent firmware candidates, registered C/H edits and native builds."""
import copy
import json
import shutil
import time
import zipfile
from pathlib import Path

from .connectivity import fingerprint
from .firmware_compiler import FirmwareCompiler
from .firmware_hardware import read_bound_hardware, validate_roles
from .firmware_policy import filename
from .importer import digest, within, verify_snapshot, reparse
from ..jobs import Cancelled
from ..source_registry import validate_registered_file
from ..store import DomainError, now, uid
from .firmware_targets import get_target


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')


def verified_source(config, revision):
    if revision.get('module') != 'firmware':
        raise DomainError('invalid_input', '指定版本不是固件设计。')
    root = within(config.data, revision['relativePath'])
    if any(reparse(p) for p in root.rglob('*')):
        raise DomainError('input_changed', '固件冻结输入不接受链接或重解析路径。', 409)
    if {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()} != {r['path'] for r in revision['manifest']}:
        raise DomainError('input_changed', '固件冻结输入文件集合发生变化。', 409)
    verify_snapshot(root, revision['manifest'])
    return root


class FirmwareAdapter:
    module = 'firmware'
    kind = 'firmware.design'
    version = 'arm-gcc-firmware/0.8.0'
    expected_head_field = 'expectedFirmwareRevisionId'

    def __init__(self, config, store):
        self.config, self.store = config, store
        self.compiler = FirmwareCompiler(config)

    def profile(self):
        return {**self.compiler.describe(), 'independentHead': 'moduleHeads.firmware',
                'defaultProfile':'STM32G030F6P6',
                'parameterScope':'parameters are legacy G030 defaults; read targets and hardware_read.roleOptions for the selected exact model',
                'targetSource':dict(uri='https://www.st.com/resource/en/datasheet/stm32g030f6.pdf',version='DS12991 Rev6 June2025',basis='Manufacturer datasheet reviewed; not physical device identification'),
                'parameters': dict(blink=dict(periodMs=[2, 60000], pinBindings={'led': '7'}),
                    uart_echo=dict(baud=[1200, 1000000], pinBindings={'tx': '9', 'rx': '10'}),
                    i2c_probe_uart=dict(baud=[1200, 1000000], pinBindings={'tx': '9', 'rx': '10', 'scl': '16', 'sda': '17'})),
                'sourceEditing': 'registered C/H add/replace/remove with expected old SHA; platform and SDK protected',
                'hardwareEvidenceRequired':True,
                'verification': 'native compilation, complete ELF/HEX/BIN loading bytes and startup/memory; behavior and code-to-hardware correctness not proven'}

    def hardware_read(self, project_id, revision_id, target, check_job_id=None):
        binding = read_bound_hardware(self.config, self.store, project_id, revision_id, target, check_job_id=check_job_id)
        return dict(revisionId=revision_id, **binding['hardware'], contract=binding['contract'],
                    inputManifestHash=binding['inputManifestHash'], netlistArtifactId=binding['netlistArtifact']['id'],
                    hardwareEvidence=binding['evidence'],nativeCheck=binding['check'],
                    hardwareVerified=False, behaviorVerified=False)

    def validate(self, project_id, params):
        self.compiler.verify_resources()
        if len(json.dumps(params, ensure_ascii=False, allow_nan=False).encode('utf8')) > 256 * 1024:
            raise DomainError('invalid_input', '固件设计请求超过256KiB。')
        if not params.get('hardwareEvidence'):raise DomainError('invalid_input','新固件写入须提交hardware_read返回的完整hardwareEvidence。')
        binding = read_bound_hardware(self.config, self.store, project_id, params['hardwareRevisionId'], params['targetReference'],params['hardwareEvidence'])
        if binding['contract']['profile']!=params['profile']:raise DomainError('invalid_input','固件型号与绑定的真实MCU型号不一致。')
        if binding['hardware']['hardwareHash'] != params['expectedHardwareHash']:
            raise DomainError('version_conflict', '硬件完整身份与声明摘要不符。', 409)
        validate_roles(binding['hardware'], params['targetReference'], params['pinBindings'], params.get('template'))
        parent = None
        if params['expectedFirmwareRevisionId']:
            parent = self.store.get('revisions', params['expectedFirmwareRevisionId'])
            if parent['projectId'] != project_id:
                raise DomainError('invalid_input', '固件父版本不属于此工程。')
            source = verified_source(self.config, parent)
            if not parent.get('validation', {}).get('adopted'):
                raise DomainError('invalid_input', '固件编辑父版本必须已经采纳。')
            if params['mode']=='edit' and parent['firmwareDesign']['profile']!=params['profile']:raise DomainError('invalid_input','更换MCU型号须重新生成平台，不能复用旧启动文件。')
        else:
            source = None
        seen = set()
        for operation in params['operations']:
            name = filename(operation['file'])
            if name.lower() in seen:
                raise DomainError('invalid_input', '同一次请求不能重复修改同一文件（忽略大小写）。')
            seen.add(name.lower())
            path = within(source / 'app', name) if source else None
            old_names = {r['path'][4:].lower() for r in parent['manifest'] if r['path'].startswith('app/')} if parent else set()
            if operation['kind'] == 'add_file':
                if name.lower() in old_names:
                    raise DomainError('version_conflict', '新增文件已经存在。', 409)
            elif name not in {r['path'][4:] for r in parent['manifest'] if r['path'].startswith('app/')} or not path or not path.is_file() or digest(path) != operation['expectedSha256']:
                raise DomainError('version_conflict', '待修改/删除的应用文件不存在或旧SHA不符。', 409)
            if operation['kind'] != 'remove_file':
                item = operation['source']
                if not validate_registered_file(self.config, project_id, item) or Path(item['path']).suffix.lower() != Path(name).suffix:
                    raise DomainError('invalid_input', '应用C/H须通过同工程资料登记，且来源文件类型匹配。')
        return binding

    def candidate(self, job, cancel):
        params = job['params']; binding = self.validate(job['projectId'], params)
        rid = uid('r'); root = self.config.data / 'projects' / job['projectId'] / 'revisions' / rid / 'source'
        root.mkdir(parents=True); records = {}; platform_provenance = None
        if params['mode'] == 'edit':
            parent = self.store.get('revisions', params['expectedFirmwareRevisionId']); source = verified_source(self.config, parent)
            for folder in ['app', 'platform', 'sdk']:
                cancel()
                if (source / folder).exists(): shutil.copytree(source / folder, root / folder)
            records = copy.deepcopy(parent['firmwareDesign']['applicationProvenance'])
            platform_provenance = copy.deepcopy(parent['firmwareDesign']['platformProvenance'])
            (root/'inputs').mkdir()
            for record in records.values():
                if record.get('frozenPath'):
                    old=within(source,record['frozenPath']);target=within(root,record['frozenPath'])
                    if digest(old)!=record['sha256']:raise DomainError('input_changed','应用源码来源与原件不同。',409)
                    shutil.copyfile(old,target)
        else:
            for folder in ['app', 'platform', 'inputs']: (root / folder).mkdir()
            seed = self.compiler.seed(params['template'], params.get('periodMs'), params.get('baud'),params['profile'])
            (root / 'app/main.c').write_text(seed['main.c'], encoding='utf8')
            for name in ['startup.c', 'linker.ld']: (root / 'platform' / name).write_text(seed[name], encoding='utf8')
            (root / 'platform/template-notes.md').write_text(seed['README.md'], encoding='utf8')
            shutil.copytree(self.compiler.engine_root / 'vendor/firmware/cmsis', root / 'sdk')
            if params['profile']!='STM32G030F6P6':
                profile=get_target(params['profile'])
                for folder in ['core',profile['family']]:
                    for file in (self.compiler.engine_root/'vendor/firmware/stm32'/folder).iterdir():
                        if file.is_file():shutil.copyfile(file,root/'sdk'/file.name)
            records['main.c'] = dict(origin='template', template=params['template'], engineHash=self.compiler.engineHash,
                generatorSha256=self.compiler.resources['vendor/firmware/kiforge_snapshot/g030_generate.py' if params['profile']=='STM32G030F6P6' else 'src/formforge/adapters/firmware_templates.py'], sha256=digest(root / 'app/main.c'))
            platform_provenance = dict(engineHash=self.compiler.engineHash, template=params['template'],
                sources=[json.loads((self.compiler.engine_root/'docs/migrations'/name).read_text(encoding='utf8')) for name in ['M6-cmsis.json','M6-stm32-cmsis.json']],
                files={p.relative_to(root).as_posix():digest(p) for folder in ['platform','sdk'] for p in (root/folder).rglob('*') if p.is_file()})
        changes = []
        for operation in params['operations']:
            cancel(); name = operation['file']; target = within(root / 'app', name)
            if operation['kind'] == 'remove_file':
                target.unlink(); records.pop(name); changes.append(dict(kind='remove_file', file=name, before=operation['expectedSha256'])); continue
            item = operation['source']; relative = 'inputs/' + item['sha256'] + Path(name).suffix
            raw = within(root, relative); raw.parent.mkdir(exist_ok=True)
            shutil.copyfile(item['path'], raw)
            if digest(raw) != item['sha256']: raise DomainError('input_changed', '登记源码复制期间发生变化。', 409)
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(raw, target)
            if not target.is_file() or digest(target)!=item['sha256']:raise DomainError('input_changed','应用文件保存后与明确源码不一致。',409)
            records[name] = dict(origin='registeredSource', sha256=item['sha256'], frozenPath=relative,
                source=item, previousSha256=operation.get('expectedSha256'))
            changes.append(dict(kind=operation['kind'], file=name, before=operation.get('expectedSha256'), after=item['sha256']))
        used={record['frozenPath'] for record in records.values() if record.get('frozenPath')}
        for old in (root/'inputs').iterdir():
            if old.relative_to(root).as_posix() not in used:old.unlink()
        cancel()
        original = within(self.config.data, binding['revision']['relativePath'])
        for row in binding['revision']['manifest']:
            cancel(); target = within(root / 'hardware/source', row['path']); target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(within(original, row['path']), target)
            if digest(target) != row['sha256']: raise DomainError('input_changed', '硬件原件复制期间改变。', 409)
        netlist = root / 'hardware/netlist.xml'; shutil.copyfile(binding['netlistPath'], netlist)
        if digest(netlist) != binding['netlistArtifact']['sha256']: raise DomainError('input_changed', '硬件网表复制期间改变。', 409)
        erc=root/'hardware/erc.json';shutil.copyfile(binding['ercPath'],erc)
        if digest(erc)!=binding['ercArtifact']['sha256']:raise DomainError('input_changed','硬件ERC复制期间改变。',409)
        save(root/'hardware/evidence.json',dict(identity=binding['evidence'],check=binding['check']))
        dependency = dict(revisionId=params['hardwareRevisionId'], hardwareHash=params['expectedHardwareHash'], graphHash=binding['hardware']['graphHash'],
            targetReference=params['targetReference'], inputManifestHash=binding['inputManifestHash'],
            sourceManifest=binding['revision']['manifest'], netlistArtifact=binding['netlistArtifact'], netlistSha256=digest(netlist),
            nativeEvidence=binding['evidence'],ercArtifact=binding['ercArtifact'],ercSha256=digest(erc))
        contract = validate_roles(binding['hardware'], params['targetReference'], params['pinBindings'], params.get('template'))
        design = dict(profile=params['profile'], mode=params['mode'], template=params.get('template'), periodMs=params.get('periodMs'), baud=params.get('baud'),
            requirements=params['requirements'], assumptions=params['assumptions'], clock=params['clock'], resetEntry=params['resetEntry'], ioVoltage=params['ioVoltage'],
            pinBindings=params['pinBindings'], hardwareDependency=dependency, roleContract=contract, applicationProvenance=records,
            platformProvenance=platform_provenance, targetSource=get_target(params['profile'])['source'],targetProfile=get_target(params['profile']),
            contextBinding=fingerprint({k:job['contextSnapshot'][k] for k in ['specification', 'preferences']}),
            engineHash=self.compiler.engineHash, toolchainHash=self.compiler.toolchainHash, toolchain=self.compiler.tools, runtimeLibraries=self.compiler.runtimeLibraries,
            sourcePolicy='bounded-C11-input-v1; not an OS sandbox', platformProtected=True)
        save(root / 'design.json', design); save(root / 'hardware/identity.json', binding['hardware'])
        # Retain the executable engine closure and license/source records in the candidate.
        for name in self.compiler.resources:
            target = within(root / 'engine', name); target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(self.compiler.engine_root / name, target)
        save(root / 'engine-provenance.json', dict(engineHash=self.compiler.engineHash, files=self.compiler.resources,
            toolchainHash=self.compiler.toolchainHash, tools=self.compiler.tools, runtimeLibraries=self.compiler.runtimeLibraries, version=self.compiler.version))
        manifest = [dict(path=p.relative_to(root).as_posix(), sha256=digest(p), size=p.stat().st_size) for p in sorted(root.rglob('*')) if p.is_file()]
        return dict(id=rid, projectId=job['projectId'], module='firmware', backend='arm-gcc-firmware', origin='firmware_designed',
            parentRevisionId=params['expectedFirmwareRevisionId'], createdByJobId=job['id'], relativePath=root.relative_to(self.config.data).as_posix(),
            entrypoints=dict(firmware='design.json'), manifest=manifest, totalBytes=sum(r['size'] for r in manifest), createdAt=now(),
            message=params['message'], changes=changes or [dict(kind='firmware_template', template=params['template'])], validation=dict(status='pending'),
            firmwareDesign=design, downstreamRequiresReview=['manufacturing'])

    def execute(self, task):
        job = task.job; deadline = time.monotonic() + job['params']['timeoutSeconds']
        task.phase('冻结完整硬件身份、固件需求、源码和来源')
        revision = self.candidate(job, task.cancel); task.register_candidate(revision)
        source = verified_source(self.config, revision)
        task.publish(source / 'design.json', 'firmware_design', 'application/json')
        output = self.config.data / 'projects' / job['projectId'] / 'jobs' / job['id']; output.mkdir(parents=True)
        try:
            result = self.compiler.build(source, output / 'build', task.cancel, task.phase, max(.1, deadline - time.monotonic()))
        except DomainError as error:
            result = dict(checksPassed=False, compilerPassed=False, error=error.message, errorCode=error.code,
                commands=[], hardwareVerified=False, behaviorVerified=False, manufacturingApproved=False)
        verified_source(self.config, revision); task.cancel()
        if time.monotonic() >= deadline: raise DomainError('tool_failed', '固件任务达到总时间预算；候选未采纳。')
        result.update(revisionId=revision['id'], module='firmware', hardwareDependency=revision['firmwareDesign']['hardwareDependency'],
            engineHash=self.compiler.engineHash, toolchainHash=self.compiler.toolchainHash, runtimeLibraries=self.compiler.runtimeLibraries,
            scope='编译、完整装载字节和启动/内存回读；不证明行为、时序、源码与硬件语义或实物。')
        save(output / 'firmware-report.json', result); task.publish(output / 'firmware-report.json', 'firmware_report', 'application/json')
        outputs = {}
        for path in sorted((output / 'build').glob('*')):
            if not path.is_file() or path.suffix not in {'.elf', '.hex', '.bin', '.map', '.log'}: continue
            kind = {'.elf':'firmware_elf', '.hex':'firmware_hex', '.bin':'firmware_bin', '.map':'firmware_map', '.log':'firmware_log'}[path.suffix]
            if path.name == 'vectors.bin': kind = 'firmware_vectors'
            # Failed builds remain diagnostic, including any partial linker output.
            task.publish(path, kind); outputs['build/' + path.name] = digest(path)
        package = output / 'firmware-project.zip'
        with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
            for row in revision['manifest']:
                task.cancel(); archive.write(within(source, row['path']), 'source/' + row['path'])
            for name in outputs: archive.write(output / name, 'outputs/' + name)
            archive.write(output / 'firmware-report.json', 'outputs/firmware-report.json')
            archive.writestr('formforge-manifest.json', json.dumps({k:v for k,v in revision.items() if k != 'validation'}, ensure_ascii=False, indent=2))
            archive.writestr('formforge-output-hashes.json', json.dumps({**outputs, 'firmware-report.json':digest(output / 'firmware-report.json')}, indent=2))
        task.publish(package, 'firmware_project', 'application/zip'); task.cancel()
        if time.monotonic() >= deadline: raise DomainError('tool_failed', '固件任务达到总时间预算；候选未采纳。')
        if self.store.finish_module(job['id'], revision['id'], result, self.module,
                '固件版本已采纳；行为和实物验证另列', '固件检查需处理，当前版本未变') is None: raise Cancelled()

    def read(self, project_id, revision_id=None, file=None, offset=0, limit=12000):
        project = self.store.get('projects', project_id); rid = revision_id or project.get('moduleHeads', {}).get('firmware')
        if not rid: raise DomainError('not_found', '工程尚无固件版本。', 404)
        revision = self.store.get('revisions', rid)
        if revision['projectId'] != project_id: raise DomainError('invalid_input', '固件版本不属于此工程。')
        source = verified_source(self.config, revision); design = revision['firmwareDesign']
        if not 0 <= offset <= 2 * 1024 * 1024 or not 1 <= limit <= 32000: raise DomainError('invalid_input', '源码读取分页超出范围。')
        files = [dict(file=r['path'][4:], sha256=r['sha256'], size=r['size']) for r in revision['manifest'] if r['path'].startswith('app/')]
        selected = None
        if file is not None:
            filename(file); record = next((r for r in files if r['file'] == file), None)
            if record is None: raise DomainError('not_found', '此固件没有该应用文件。', 404)
            text = within(source / 'app', file).read_text(encoding='utf-8-sig', errors='replace')
            selected = dict(**record, text=text[offset:offset+limit], offset=offset, nextOffset=offset+limit if offset+limit<len(text) else None)
        job = self.store.get('jobs', revision['createdByJobId'])
        dependency_changed = self.store.head_revision(project_id) != design['hardwareDependency']['revisionId']
        context_changed = fingerprint({k:project[k] for k in ['specification', 'preferences']}) != design['contextBinding']
        return dict(revisionId=rid, module='firmware', currentFirmwareRevisionId=project.get('moduleHeads', {}).get('firmware'),
            design=design, files=files, file=selected, validation=revision['validation'], result=job.get('result'),
            hardwareRevisionChanged=dependency_changed, requirementsOrPreferencesChanged=context_changed,
            requiresDependencyReview=dependency_changed or context_changed, sourceOffsetUnit='Unicode characters',
            hardwareVerified=False, behaviorVerified=False)
