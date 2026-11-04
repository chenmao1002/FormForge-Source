"""Workspace information and narrowly scoped local preferences/tool profiles."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time
import uuid

from . import __version__
from .settings_schemas import (ManufacturingPersonalDefaults, PcbPersonalDefaults,
                               ToolchainActivationRequest, ToolchainProfileRequest,
                               WorkspaceDefaultsUpdate)
from .store import DomainError
from .module_settings import ModuleDefaults
from .project_storage import allocated_file_bytes


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024))


def safe_directory(path: Path, root: Path):
    """Check every component without following directory links or junctions."""
    relative = path.absolute().relative_to(root.absolute())
    current = root
    if linked(current.lstat()):
        return False
    for component in relative.parts:
        current = current / component
        if linked(current.lstat()):
            return False
    return path.is_dir()


def directory_usage(path: Path, root: Path, budget=0.65, entry_limit=100_000,
                    progress=lambda value: None, stopped=lambda: False):
    result = dict(path=str(path), bytes=0, files=0, skippedLinks=0, unreadableEntries=0,
                  status='complete', reason=None, allocatedBytes=0, allocatedFiles=0, allocationUnreadableEntries=0, compressedFiles=0)
    deadline = time.monotonic() + budget
    try:
        if not safe_directory(path, root):
            result.update(status='unavailable', reason='目录为链接、重解析路径或不是普通目录。')
            return result
    except FileNotFoundError:
        result['status'] = 'missing'
        return result
    except (OSError, ValueError):
        result.update(status='unavailable', reason='无法读取此目录。')
        return result
    pending, entries = [path], 0
    last_progress = time.monotonic()
    try:
        while pending:
            if time.monotonic() >= deadline or stopped():
                result.update(status='partial', reason='达到本轮统计时间上限。')
                return result
            folder = pending.pop()
            if not safe_directory(folder, root):
                result['skippedLinks'] += 1
                continue
            try:
                with os.scandir(folder) as iterator:
                    for entry in iterator:
                        if time.monotonic() >= deadline or entries >= entry_limit or stopped():
                            result.update(status='partial', reason='达到本轮统计时间或文件数上限。')
                            return result
                        entries += 1
                        try:
                            info = entry.stat(follow_symlinks=False)
                            if linked(info):
                                result['skippedLinks'] += 1
                            elif stat.S_ISDIR(info.st_mode):
                                pending.append(Path(entry.path))
                            elif stat.S_ISREG(info.st_mode):
                                result['bytes'] += info.st_size
                                result['files'] += 1
                                if getattr(info, 'st_file_attributes', 0) & 0x800:
                                    result['compressedFiles'] += 1
                                try:
                                    stored = allocated_file_bytes(Path(entry.path), info)
                                    if stored is not None:
                                        result['allocatedBytes'] += stored
                                        result['allocatedFiles'] += 1
                                    else:
                                        result['allocationUnreadableEntries'] += 1
                                except OSError:
                                    result['allocationUnreadableEntries'] += 1
                        except OSError:
                            result['unreadableEntries'] += 1
                        if time.monotonic() - last_progress >= 0.25:
                            progress(dict(result))
                            last_progress = time.monotonic()
            except OSError:
                result['unreadableEntries'] += 1
    except OSError:
        result['unreadableEntries'] += 1
    if result['unreadableEntries']:
        result.update(status='partial', reason='部分文件不可读或在统计时发生变化。')
    return result


class WorkspaceSettings:
    def __init__(self, service):
        self.service = service
        self.config = service.config
        self.started_at = timestamp()
        self._storage = None
        self._storage_time = 0.0
        self._storage_lock = asyncio.Lock()
        self._state_lock = threading.Lock()
        self._storage_task = None
        self._scan_stop = threading.Event()
        self._configuration_lock = threading.RLock()
        self._configuration_path = self.config.local / 'workspace-settings.json'

    @staticmethod
    def _initial_configuration():
        return dict(schemaVersion='formforge.workspace-settings/1', version='ws_initial', updatedAt=None,
                    pcbDefaults=PcbPersonalDefaults().model_dump(mode='json'),
                    moduleDefaults=ModuleDefaults().model_dump(mode='json'),
                    manufacturingDefaults=ManufacturingPersonalDefaults().model_dump(mode='json'),
                    toolchains=dict(profiles=[], active={'kicad': None, 'arm-gcc': None}))

    def _read_configuration(self):
        with self._configuration_lock:
            if not self._configuration_path.is_file():
                return self._initial_configuration()
            try:
                if linked(self._configuration_path.lstat()):
                    raise ValueError('linked settings file')
                value = json.loads(self._configuration_path.read_text(encoding='utf8'))
                if value.get('schemaVersion') != 'formforge.workspace-settings/1' or not isinstance(value.get('version'), str):
                    raise ValueError('settings schema')
                value['pcbDefaults'] = PcbPersonalDefaults.model_validate(value.get('pcbDefaults', {})).model_dump(mode='json')
                value['manufacturingDefaults'] = ManufacturingPersonalDefaults.model_validate(value.get('manufacturingDefaults', {})).model_dump(mode='json')
                value['moduleDefaults'] = ModuleDefaults.model_validate(value.get('moduleDefaults', {})).model_dump(mode='json')
                from .rule_presets import SaveRulePreset
                for preset in value.setdefault('rulePresets',[]):
                    SaveRulePreset.model_validate(dict(expectedSettingsVersion=value['version'],name=preset['name'],description=preset.get('description',''),modules=preset['modules']))
                    if not isinstance(preset.get('id'),str):raise ValueError('preset id')
                toolchains = value.get('toolchains')
                if not isinstance(toolchains, dict) or not isinstance(toolchains.get('profiles'), list) or not isinstance(toolchains.get('active'), dict):
                    raise ValueError('toolchains schema')
                ids = set()
                for row in toolchains['profiles']:
                    if not isinstance(row, dict) or row.get('kind') not in {'kicad', 'arm-gcc'} or not isinstance(row.get('id'), str):
                        raise ValueError('toolchain profile')
                    if row['id'] in ids:
                        raise ValueError('duplicate toolchain profile')
                    ids.add(row['id'])
                if any(selected is not None and selected not in ids for selected in toolchains['active'].values()):
                    raise ValueError('active toolchain profile')
                toolchains['active'] = {kind: toolchains['active'].get(kind) for kind in ('kicad', 'arm-gcc')}
                return value
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                raise DomainError('settings_unavailable', '本机设置文件无效；未覆盖原文件，请修复或恢复后重试。', 409) from None

    def _write_configuration(self, value):
        with self._configuration_lock:
            try:
                if not safe_directory(self.config.local, self.config.root):
                    raise OSError('unsafe runtime directory')
                if self._configuration_path.exists() and linked(self._configuration_path.lstat()):
                    raise OSError('linked settings file')
                value = copy.deepcopy(value)
                value.update(version='ws_' + uuid.uuid4().hex, updatedAt=timestamp())
                raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf8')
                temporary = self.config.local / ('workspace-settings-' + uuid.uuid4().hex + '.tmp')
                temporary.write_bytes(raw)
                os.replace(temporary, self._configuration_path)
                return value
            except (OSError, TypeError, ValueError):
                try:
                    temporary.unlink(missing_ok=True)
                except (OSError, UnboundLocalError):
                    pass
                raise DomainError('settings_unavailable', '本机设置无法安全保存；原配置保持不变。', 503) from None

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with Path(path).open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _run_version(args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        output = (result.stdout or result.stderr).strip()
        if result.returncode or not output:
            raise ValueError('selected tool did not report a version')
        return output

    def _validate_toolchain(self, kind, executable, name, profile_id=None):
        try:
            selected = Path(executable).expanduser()
            if not selected.is_absolute():
                raise ValueError('tool path must be absolute')
            selected = selected.resolve(strict=True)
            if not selected.is_file() or linked(selected.lstat()):
                raise ValueError('tool path must be a regular file')
            suffix = '.exe' if os.name == 'nt' else ''
            companions = {}
            if kind == 'kicad':
                if selected.name.lower() != 'kicad-cli' + suffix:
                    raise ValueError('expected kicad-cli executable')
                version = self._run_version([str(selected), 'version']).splitlines()[0]
                if not version.startswith('10.'):
                    raise ValueError('KiCad 10 is required')
                python = selected.with_name('python' + suffix)
                share = selected.parent.parent / 'share' / 'kicad'
                if not python.is_file() or linked(python.lstat()) or not share.is_dir():
                    raise ValueError('matching KiCad Python or share directory is missing')
                python_version = self._run_version([str(python), '--version']).splitlines()[0]
                companions = {'python': str(python.resolve()), 'share': str(share.resolve()),
                              'pythonVersion': python_version, 'pythonSha256': self._sha256(python)}
                hashes = {'executable': self._sha256(selected), 'python': companions['pythonSha256']}
            else:
                if selected.name.lower() != 'arm-none-eabi-gcc' + suffix:
                    raise ValueError('expected arm-none-eabi-gcc executable')
                version = self._run_version([str(selected), '--version']).splitlines()[0]
                if 'arm-none-eabi' not in version.lower():
                    raise ValueError('ARM bare-metal GCC is required')
                root = selected.parent.parent.resolve()
                paths = {}
                for tool in ('objcopy', 'readelf', 'size'):
                    path = selected.with_name('arm-none-eabi-' + tool + suffix).resolve(strict=True)
                    if not path.is_file() or not path.is_relative_to(root):
                        raise ValueError('ARM companion tool is missing')
                    paths[tool] = str(path)
                for tool in ('cc1', 'as', 'ld'):
                    output = self._run_version([str(selected), '-print-prog-name=' + tool]).splitlines()[0]
                    path = Path(output).resolve(strict=True)
                    if not path.is_file() or not path.is_relative_to(root):
                        raise ValueError('ARM subtool is outside the selected toolchain')
                    paths[tool] = str(path)
                runtime = self._run_version([str(selected), '-mcpu=cortex-m0plus', '-mthumb', '-mfloat-abi=soft',
                                             '-print-libgcc-file-name']).splitlines()[0]
                libgcc = Path(runtime).resolve(strict=True)
                if not libgcc.is_file() or libgcc.name != 'libgcc.a' or not libgcc.is_relative_to(root):
                    raise ValueError('matching ARM runtime library is missing')
                paths['libgcc'] = str(libgcc)
                hashes = {'executable': self._sha256(selected), **{tool: self._sha256(path) for tool, path in paths.items()}}
                companions = {'root': str(root), 'paths': paths}
            fingerprint = hashlib.sha256(json.dumps(dict(kind=kind, version=version, hashes=hashes),
                                                        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            return dict(id=profile_id or 'tp_' + uuid.uuid4().hex, kind=kind, name=name.strip(),
                        executable=str(selected), version=version, companions=companions, hashes=hashes,
                        fingerprint=fingerprint, validatedAt=timestamp())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            message = {'kicad': '所选文件不是完整可用的 KiCad 10 工具链。',
                       'arm-gcc': '所选文件不是完整可用的 ARM GNU 工具链。'}[kind]
            raise DomainError('invalid_toolchain', message + ' ' + str(error), 400) from None

    def _ensure_version(self, value, expected):
        if value['version'] != expected:
            raise DomainError('version_conflict', '本机设置已变化，请刷新后再保存。', 409)

    def _ensure_idle(self):
        if any(job.get('status') in {'queued', 'running'} for job in self.service.store.list('jobs')):
            raise DomainError('busy', '仍有工程任务在执行；工具链只能在任务全部结束后切换。', 409)

    def configuration(self):
        value = self._read_configuration()
        runtime = {'kicad': str(self.service.eda.cli) if self.service.eda.cli else None,
                   'arm-gcc': str(self.service.firmware.compiler.gcc) if self.service.firmware.compiler.gcc else None}
        profiles = {row['id']: row for row in value['toolchains']['profiles']}
        active = value['toolchains']['active']
        pending = {}
        for kind in ('kicad', 'arm-gcc'):
            profile = profiles.get(active.get(kind))
            pending[kind] = (bool(profile and Path(profile['executable']) != Path(runtime[kind] or ''))
                             or (profile is None and self.config.tool_origins.get(kind) == 'saved-profile'))
        result = copy.deepcopy(value)
        result['toolchains']['runtime'] = runtime
        result['toolchains']['origins'] = {kind: self.config.tool_origins.get(kind, 'automatic') for kind in ('kicad', 'arm-gcc')}
        result['toolchains']['restartRequired'] = pending
        result['privacy'] = dict(manufacturingStoresFullAddress=False, manufacturingStoresPhone=False,
                                 manufacturingStoresTaxIdentity=False, scope='masked-provider-profile-selection-only')
        return result

    def update_defaults(self, request):
        parsed = request if isinstance(request, WorkspaceDefaultsUpdate) else WorkspaceDefaultsUpdate.model_validate(request)
        with self._configuration_lock:
            value = self._read_configuration()
            self._ensure_version(value, parsed.expectedVersion)
            if parsed.moduleDefaults is not None:
                value['moduleDefaults'] = parsed.moduleDefaults.model_dump(mode='json')
            value['pcbDefaults'] = parsed.pcbDefaults.model_dump(mode='json')
            value['manufacturingDefaults'] = parsed.manufacturingDefaults.model_dump(mode='json')
            return self._public_configuration(self._write_configuration(value))

    def add_toolchain(self, request):
        parsed = request if isinstance(request, ToolchainProfileRequest) else ToolchainProfileRequest.model_validate(request)
        with self._configuration_lock:
            value = self._read_configuration()
            self._ensure_version(value, parsed.expectedVersion)
            existing = next((row for row in value['toolchains']['profiles']
                             if row['kind'] == parsed.kind and Path(row['executable']) == Path(parsed.executable).expanduser()), None)
            profile = self._validate_toolchain(parsed.kind, parsed.executable, parsed.name, existing and existing['id'])
            value['toolchains']['profiles'] = [row for row in value['toolchains']['profiles'] if row['id'] != profile['id']]
            value['toolchains']['profiles'].append(profile)
            if parsed.activate:
                self._ensure_idle()
                value['toolchains']['active'][parsed.kind] = profile['id']
            return self._public_configuration(self._write_configuration(value))

    def activate_toolchain(self, request):
        parsed = request if isinstance(request, ToolchainActivationRequest) else ToolchainActivationRequest.model_validate(request)
        with self._configuration_lock:
            value = self._read_configuration()
            self._ensure_version(value, parsed.expectedVersion)
            if parsed.profileId is None:
                self._ensure_idle()
                value['toolchains']['active'][parsed.kind] = None
                return self._public_configuration(self._write_configuration(value))
            profile = next((row for row in value['toolchains']['profiles'] if row['id'] == parsed.profileId), None)
            if not profile or profile['kind'] != parsed.kind:
                raise DomainError('not_found', '没有找到对应类型的工具链档案。', 404)
            self._ensure_idle()
            refreshed = self._validate_toolchain(parsed.kind, profile['executable'], profile['name'], profile['id'])
            value['toolchains']['profiles'] = [refreshed if row['id'] == profile['id'] else row
                                                for row in value['toolchains']['profiles']]
            value['toolchains']['active'][parsed.kind] = profile['id']
            return self._public_configuration(self._write_configuration(value))

    def _public_configuration(self, stored):
        # Re-read through the public path so restart/source information is always current.
        return self.configuration()

    def new_project_preferences(self, explicit):
        configuration = self._read_configuration()
        defaults = configuration['pcbDefaults']
        labels = {
            'board-corner': '板框默认', 'routing-style': '布线风格', 'placement-density': '摆放密度',
            'silkscreen-style': '丝印风格', 'via-preference': '过孔偏好'}
        values = {
            'board-corner': {'mode': defaults['boardCorner'], 'radiusMm': defaults['cornerRadiusMm'],
                             'squareCornerReason': defaults.get('squareCornerReason')},
            'routing-style': defaults['routingStyle'], 'placement-density': defaults['placementDensity'],
            'silkscreen-style': defaults['silkscreenStyle'], 'via-preference': defaults['viaPreference']}
        personal = [dict(id='pcb.' + key, label=labels[key], scope='pcb', level='soft', source='personal-default',
                         sourceVersion=configuration['version'], value=value) for key, value in values.items()]
        result = copy.deepcopy(explicit or {})
        supplied = result.get('rules') if isinstance(result.get('rules'), list) else []
        supplied_ids = {row.get('id') for row in supplied if isinstance(row, dict)}
        result['rules'] = [row for row in personal if row['id'] not in supplied_ids] + supplied
        result.setdefault('text', '')
        result['personalDefaultsSnapshot'] = dict(settingsVersion=configuration['version'], pcbDefaults=copy.deepcopy(defaults),
            moduleDefaults=copy.deepcopy(configuration['moduleDefaults']))
        return result

    def project_modules(self, project_id):
        from .module_settings import MODELS, effective
        project = self.service.store.get('projects', project_id)
        preferences = project.get('preferences', {})
        inherited = {**preferences, 'moduleDefaults': {}}
        modules = {name: dict(values=effective(preferences, name), inherited=effective(inherited, name),
                    source='project-override' if name in preferences.get('moduleDefaults', {}) else
                        'project-snapshot' if name in preferences.get('personalDefaultsSnapshot', {}).get('moduleDefaults', {}) else 'system-default',
                    inheritedSource='project-snapshot' if name in preferences.get('personalDefaultsSnapshot', {}).get('moduleDefaults', {}) else 'system-default') for name in MODELS}
        return dict(projectId=project_id, projectVersion=project['version'], modules=modules,
                    appliesTo='future-tasks-only', explicitRequestWins=True)

    def manufacturing_snapshot(self):
        configuration = self._read_configuration()
        return dict(settingsVersion=configuration['version'], providerSelection=copy.deepcopy(configuration['manufacturingDefaults']),
                    containsFullAddress=False, containsPhone=False, containsTaxIdentity=False,
                    verification='supplier-checkout-response-remains-authoritative')

    def snapshot(self):
        config, service = self.config, self.service
        root = config.root
        command = root / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python')
        proxy = root / 'scripts/stdio-proxy.py'
        url = f'http://127.0.0.1:{config.port}/mcp'
        compiler = service.firmware.compiler
        mechanical_versions = (service.mechanical.runtime or {}).get('versions', {})
        cam_versions = (service.external_cam.runtime or {}).get('versions', {})

        def tool(id, name, path, version, issue, purpose, origin='automatic'):
            exists = bool(path and Path(path).is_file())
            available = exists and not issue
            return dict(id=id, name=name, path=str(path) if path else None, version=version,
                        available=available, reason=issue or (None if exists else '当前文件不存在。'),
                        purpose=purpose, origin=origin, checkBasis='service-startup-and-current-file-existence')

        tools = [
            tool('python', '本地服务 Python', sys.executable, sys.version.split()[0], None, '网页、HTTP 与 MCP 服务'),
            tool('kicad', 'KiCad 10 CLI', service.eda.cli, service.eda.detected_version,
                 service.eda.issue, '原生电路检查与导出', config.tool_origins.get('kicad', 'automatic')),
            tool('kicad-python', 'KiCad Python', config.kicad_python, None,
                 None if config.kicad_python else '未找到与 KiCad 配套的 Python。', '受约束 PCB 编辑；此项仅检查配置文件存在'),
            tool('mechanical', '独立机械环境', config.mechanical_python,
                 f"CadQuery {mechanical_versions['cadquery']}" if 'cadquery' in mechanical_versions else None,
                 service.mechanical.issue, '机械建模、STEP 与装配几何'),
            tool('cam', '独立 CAM 环境', config.cam_python,
                 ' · '.join(f'{k} {v}' for k, v in cam_versions.items() if k in ('gerbonara', 'shapely')) or None,
                 service.external_cam.runtime_issue, 'Gerber、钻孔与装配文件审核'),
            tool('arm-gcc', 'ARM GNU 工具链', compiler.gcc, compiler.version,
                 compiler.issue, '固件编译与映像回读', config.tool_origins.get('arm-gcc', 'automatic')),
        ]
        # This is an explicit allowlist. Neither config.token nor vault state enters this response.
        flash_profile = service.firmware_flash.profile()
        flash_env = flash_profile.get('environment')
        tools.append(tool('flash', '独立烧录环境', str(config.root/'.venv-flash/Scripts/python.exe'),
                          ('pyOCD '+flash_env['packageVersions']['pyocd']+' · esptool '+flash_env['packageVersions']['esptool']) if flash_env else None,
                          None if flash_env else '请运行 scripts/Setup-Flash.ps1。',
                          '外部固件导入与离线计划；不连接设备', 'managed-environment'))
        return dict(app='FormForge', version=__version__, observedAt=timestamp(),
                    serviceStartedAt=self.started_at, readOnly=False,
                    readOnlySections=['connection', 'storage'], writableSections=['defaults', 'toolchains'],
                    connection=dict(endpoint=url, transport='Streamable HTTP', authentication='Bearer',
                        tokenFile=str(config.local / 'api-token.txt'), localOnly=True,
                        httpExample=dict(url=url, headers={'Authorization': 'Bearer <本机令牌>',
                                                           'Accept-Language': 'zh-CN'}),
                        stdioAvailable=command.is_file() and proxy.is_file(),
                        stdioExample={'mcpServers': {'formforge': dict(command=str(command), args=[str(proxy)],
                                                     env={'FORMFORGE_PORT': str(config.port),
                                                          'FORMFORGE_MCP_LANGUAGE': 'zh-CN'})}}),
                    storage=dict(root=str(root), database=str(config.data / 'formforge.sqlite3'),
                                 projects=str(config.data / 'projects'), runtime=str(config.local),
                                 projectStorage=config.project_storage),
                    tools=tools, configuration=self.configuration())

    async def storage(self):
        async with self._storage_lock:
            running = self._storage_task is not None and not self._storage_task.done()
            if not running and (self._storage is None or time.monotonic() - self._storage_time >= 30):
                self._scan_stop.clear()
                with self._state_lock:
                    self._storage = dict(startedAt=timestamp(), observedAt=timestamp(), cacheSeconds=30,
                        scanning=True, volume=None, measure='logical-and-os-stored-file-bytes', atomicSnapshot=False,
                        scope='listed-directories-only; excludes-links-and-reparse-points',
                        locations=[dict(id=id, name=name, description=description, path=str(path), bytes=0,
                            files=0, skippedLinks=0, unreadableEntries=0, status='scanning', reason=None)
                            for id, name, path, description in self._groups()])
                self._storage_task = asyncio.create_task(self._run_storage())
            with self._state_lock:
                return copy.deepcopy(self._storage)

    async def _run_storage(self):
        try:
            result = await asyncio.to_thread(self._measure_storage)
            result['scanning'] = False
            with self._state_lock:
                self._storage = result
        except Exception:
            with self._state_lock:
                self._storage['scanning'] = False
                for row in self._storage['locations']:
                    if row['status'] == 'scanning':
                        row.update(status='partial', reason='后台统计未完成，请稍后刷新。')
        finally:
            self._storage_time = time.monotonic()

    async def close(self):
        self._scan_stop.set()
        if self._storage_task is not None:
            await self._storage_task

    def _groups(self):
        root = self.config.root
        return [
            ('data', '工程数据', self.config.data, '工程快照、任务产物、资料与数据库'),
            ('runtime', '本地运行资料', self.config.local, '日志、冻结引擎与本机配置，包含受保护的凭据文件'),
            ('cache', '开发缓存', root / '.cache', '本项目的构建与临时缓存'),
            ('validation', '验证记录', root / 'validation', '测试结果与检查证据'),
            ('deliverables', '交付归档', root / 'deliverables', '阶段交付文件与源码快照'),
        ]

    def _measure_storage(self):
        root = self.config.root
        with self._state_lock:
            started = self._storage['startedAt']
        rows = []
        for index, (id, name, path, description) in enumerate(self._groups()):
            def progress(value):
                with self._state_lock:
                    self._storage['locations'][index].update(value, status='scanning')
                    self._storage['observedAt'] = timestamp()
            # Give every listed location its own budget, including the last one.
            usage = directory_usage(path, root, budget=60,
                                    entry_limit=1_000_000, progress=progress, stopped=self._scan_stop.is_set)
            row = dict(id=id, name=name, description=description, **usage)
            rows.append(row)
            with self._state_lock:
                self._storage['locations'][index] = row
                self._storage['observedAt'] = timestamp()
        try:
            disk = shutil.disk_usage(root)
            volume = dict(totalBytes=disk.total, freeBytes=disk.free)
        except OSError:
            volume = None
        return dict(startedAt=started, observedAt=timestamp(), cacheSeconds=30, locations=rows,
                    volume=volume, measure='logical-and-os-stored-file-bytes', atomicSnapshot=False,
                    scope='listed-directories-only; excludes-links-and-reparse-points')
