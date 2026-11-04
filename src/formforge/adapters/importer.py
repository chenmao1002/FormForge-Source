from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path, PureWindowsPath

from ..store import DomainError, now, uid

EXTENSIONS = {".kicad_sch", ".kicad_pcb", ".kicad_pro", ".kicad_dru", ".kicad_sym",
              ".kicad_mod", ".kicad_wks", ".lib", ".dcm", ".step", ".stp", ".wrl"}
SKIP = {".git", ".venv", "node_modules", ".local", ".cache", ".ide", ".history", "__pycache__",
        "reports", "review", "artifacts", "build", "dist"}
NOTICES = {"SOURCE-LICENSE.txt", "SOURCE-NOTICE.md", "LICENSE", "LICENSE.txt", "README.md", 'formforge-specification.json', 'formforge-pcb.json', 'formforge-copper-intent.json', 'formforge-routing.json'}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def within(root: Path, relative: str):
    if Path(relative).is_absolute() or PureWindowsPath(relative).is_absolute():
        raise DomainError("invalid_input", "入口必须是工程目录内的相对路径。")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise DomainError("invalid_input", "路径超出工程目录。")
    return path


def reparse(path):
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def verify_snapshot(root, manifest):
    for entry in manifest:
        path = within(root, entry["path"])
        if not path.is_file() or digest(path) != entry["sha256"]:
            raise DomainError("input_changed", "输入快照发生变化，拒绝使用旧版本执行检查。", 409)


def stackup_sources(root):
    """Preserve explicitly referenced source bytes, never restore edit authority."""
    metadata=root/'formforge-pcb.json'
    if not metadata.is_file():return {}
    if reparse(metadata):raise DomainError('invalid_input','叠层版本资料不支持符号链接或目录联接。')
    if metadata.stat().st_size>8*1024*1024:raise DomainError('invalid_input','PCB版本资料超过8MB。')
    try:
        data=json.loads(metadata.read_text(encoding='utf-8-sig'));stack=data.get('stackup')
        if stack is None:return {}
        record=stack['source'];relative=record['path'];expected=record['sha256'];path=within(root,relative)
        if not re.fullmatch(r'[0-9a-f]{64}',expected) or path.parent!=root/'formforge-stackup-source' or path.stem!=expected:
            raise ValueError('Source path must be a hash-named file in formforge-stackup-source')
        if not path.is_file() or path.stat().st_size>8*1024*1024 or reparse(path) or digest(path)!=expected:raise ValueError('Source missing, changed or exceeds8MB')
        return {path.relative_to(root).as_posix():expected}
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        raise DomainError('invalid_input',f'无法恢复叠层来源文件：{exc}') from exc


def validate_references(root):
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".kicad_sch", ".kicad_pcb"} and path.name not in {"sym-lib-table", "fp-lib-table"}:
            continue
        content = path.read_text(encoding="utf-8-sig", errors="strict")
        refs = re.findall(r'\(property\s+"Sheetfile"\s+"([^"]+)"', content)
        for ref in refs:
            child = within(root, str(path.parent.relative_to(root) / ref))
            if not child.is_file():
                raise DomainError("invalid_input", f"缺少层级图纸：{ref}。请导入包含依赖的完整工程目录。")
        refs = re.findall(r'\((?:uri|model)\s+"([^"]+)"', content)
        for ref in refs:
            if ref.startswith("${KICAD10_"):
                continue
            value = ref.replace("${KIPRJMOD}", ".")
            if "${" in value or "://" in value:
                raise DomainError("invalid_input", f"尚不支持外部依赖引用：{ref}。请放入工程目录或改用 KiCad 10 系统库。")
            if path.name in {'sym-lib-table', 'fp-lib-table'}:
                value = str(path.parent.relative_to(root) / value)
            dependency = within(root, value)
            if not dependency.exists():
                raise DomainError("invalid_input", f"缺少库或模型：{ref}。请一并导入。")


def import_project(config, project_id, params, phase, check_cancel):
    requested = Path(params["sourceDir"]).expanduser()
    if not requested.is_dir() or reparse(requested):
        raise DomainError("invalid_input", "请选择真实存在的工程目录，不支持符号链接或目录联接。")
    source = requested.resolve()
    if source == config.root or source.is_relative_to(config.data) or config.data.is_relative_to(source):
        raise DomainError("invalid_input", "不能将 FormForge 自身或运行数据目录作为导入来源。")
    declared_sources=stackup_sources(source)
    entries = {k: params.get(k) or None for k in ("schematic", "board")}
    if not any(entries.values()):
        raise DomainError("invalid_input", "至少指定原理图或 PCB 入口。")
    for kind, relative in entries.items():
        if relative:
            path = within(source, relative)
            suffix = ".kicad_sch" if kind == "schematic" else ".kicad_pcb"
            if not path.is_file() or path.suffix.lower() != suffix:
                raise DomainError("invalid_input", f"{kind} 入口不存在或格式不正确。")
    revision_id = uid("r")
    dest = config.data / "projects" / project_id / "revisions" / revision_id / "source"
    dest.mkdir(parents=True)
    manifest, total = [], 0
    phase("正在冻结工程输入")
    for current, dirs, files in os.walk(source):
        check_cancel()
        current = Path(current)
        dirs[:] = [d for d in dirs if d.lower() not in SKIP and not d.startswith(".")]
        for name in dirs + files:
            if reparse(current / name):
                raise DomainError("invalid_input", "工程中包含符号链接或目录联接，不能保证输入隔离。")
        for name in files:
            path = current / name
            relative = path.relative_to(source).as_posix()
            provenance = name == 'provenance.json' and re.fullmatch(r'FF_[0-9a-f]{16}', path.parent.name) and path.parent.parent.name == 'formforge-libraries'
            if path.suffix.lower() not in EXTENSIONS and name not in {"sym-lib-table", "fp-lib-table"} | NOTICES and not provenance and relative not in declared_sources:
                continue
            check_cancel()
            total += path.stat().st_size
            if len(manifest) >= 2000 or total > 200 * 1024 * 1024:
                raise DomainError("invalid_input", "首版导入上限为 2000 个工程文件、200 MB；请缩小到所需工程目录。")
            before = digest(path)
            if relative in declared_sources and before!=declared_sources[relative]:raise DomainError('input_changed','叠层来源在复制前变化。',409)
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if before != digest(target) or before != digest(path):
                raise DomainError("input_changed", "来源在复制期间发生变化，请保存工程后重新导入。", 409)
            manifest.append({"path": relative, "sha256": before, "size": target.stat().st_size})
    for entry in manifest:
        if digest(source / entry["path"]) != entry["sha256"]:
            raise DomainError("input_changed", "来源在导入期间发生变化，请重新导入。", 409)
    if not declared_sources.keys()<={entry['path'] for entry in manifest}:raise DomainError('invalid_input','叠层来源文件未完整冻结。')
    validate_references(dest)
    from .symbol_library import recover_libraries
    libraries = recover_libraries(dest, entries['schematic']) if entries['schematic'] else []
    specification = None
    if entries['schematic']:
        spec_path = dest / Path(entries['schematic']).parent / 'formforge-specification.json'
        if spec_path.exists():
            if spec_path.stat().st_size > 1024 * 1024:
                raise DomainError('invalid_input','工程设计依据文件超过 1 MB。')
            try:
                specification = json.loads(spec_path.read_text(encoding='utf-8-sig'))
                if (specification.get('schemaVersion') != 1 or not isinstance(specification.get('requirements'),str)
                        or not isinstance(specification.get('title'),str) or not isinstance(specification.get('decisions'),dict)):
                    raise ValueError('invalid specification structure')
            except (ValueError,AttributeError) as exc:
                raise DomainError('invalid_input',f'无法恢复工程设计依据：{exc}') from exc
    check_cancel()
    return dict(id=revision_id, projectId=project_id, backend="kicad10", sourceDirectory=str(source),
                relativePath=dest.relative_to(config.data).as_posix(), entrypoints=entries,
                manifest=manifest, createdAt=now(), origin="imported", totalBytes=total, symbolLibraries=libraries,
                designSpecification=specification)
