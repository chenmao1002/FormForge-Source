from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field

from . import __version__
from .adapters.importer import digest
from .schemas import StrictModel
from .store import DomainError, encode, now, uid
from .project_storage import prepare_project_storage


PACKAGE_SCHEMA = "formforge.project-package/1"
TERMINAL_JOB_STATES = {"completed", "failed", "cancelled", "interrupted"}
MAX_PACKAGE_FILES = 250_000
MAX_PACKAGE_BYTES = 50 * 1024 * 1024 * 1024
ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,100}$")
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ProjectExportRequest(StrictModel):
    expectedVersion: int = Field(ge=1)


class ProjectPackageImportRequest(StrictModel):
    packagePath: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, min_length=1, max_length=120)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _safe_name(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", value.strip()).strip("-.")
    return (name or "formforge-project")[:80]


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _publish_import_directory(source: Path, target: Path):
    """Retry only a transient Windows rename denial, before any DB commit."""
    delays=(.05,.1,.2,.4)
    for attempt in range(len(delays)+1):
        if target.exists():raise DomainError('version_conflict','导入目标工程目录已存在，未覆盖。',409)
        try:
            os.replace(source,target)
            return
        except PermissionError as error:
            if (getattr(error,'winerror',None) not in {5,32,33} or attempt==len(delays)
                    or target.exists() or not source.is_dir()):raise
            time.sleep(delays[attempt])


class ProjectTransfer:
    """Verified, portable snapshots of one FormForge project.

    Packages intentionally exclude service credentials, browser profiles,
    request replay keys and runtime caches. Domain records and the immutable
    project file tree are preserved; imports receive fresh local identities.
    """

    def __init__(self, config, store):
        self.config = config
        self.store = store
        self.exports = config.local / "project-exports"
        self.imports = config.local / "project-imports"
        self.exports.mkdir(parents=True, exist_ok=True)
        self.imports.mkdir(parents=True, exist_ok=True)

    def profile(self):
        return dict(
            schema=PACKAGE_SCHEMA,
            exportFormat="verified-zip",
            projectVersionBound=True,
            preserves=["project", "revisions", "jobs", "artifacts", "sources",
                       "sourceUploads", "orders", "supplierHistory", "events", "projectFiles"],
            excludes=["apiTokens", "supplierCredentials", "browserProfiles",
                      "requestReplayKeys", "runtimeCaches"],
            importCreatesNewProject=True,
            importedChecks="historical-version-bound-evidence",
            activeTasksAllowed=False,
        )

    def _metadata(self, project_id: str) -> dict[str, Any]:
        with self.store.read_tx() as db:
            project = self.store._get(db, "projects", project_id)
            records: dict[str, list[dict[str, Any]]] = {}
            for table in sorted(self.store.TABLES - {"projects"}):
                rows = db.execute(f"SELECT data FROM {table} WHERE project_id=? ORDER BY id", (project_id,))
                records[table] = [json.loads(row[0]) for row in rows]
            events = [dict(sourceSeq=row["seq"], projectId=row["project_id"], jobId=row["job_id"],
                           type=row["type"], payload=json.loads(row["payload"]), createdAt=row["created_at"])
                      for row in db.execute("SELECT * FROM events WHERE project_id=? ORDER BY seq", (project_id,))]
        return dict(schema=PACKAGE_SCHEMA, project=project, records=records, events=events)

    @staticmethod
    def _assert_stable(metadata: dict[str, Any], expected_version: int):
        project = metadata["project"]
        if project["version"] != expected_version:
            raise DomainError("version_conflict", "工程版本已变化，请刷新后重新导出。", 409)
        active = [row for row in metadata["records"].get("jobs", [])
                  if row.get("status") not in TERMINAL_JOB_STATES]
        if active:
            raise DomainError("busy", "工程仍有任务在执行；任务结束后再导出工程包。", 409)
        uploading = [row for row in metadata["records"].get("source_uploads", [])
                     if row.get("status") == "uploading"]
        if uploading:
            raise DomainError("busy", "工程仍有资料正在上传；提交或取消上传后再导出。", 409)

    def _project_files(self, project_id: str) -> list[dict[str, Any]]:
        root = self.config.data / "projects" / project_id
        if not root.exists():
            return []
        if _is_reparse(root) or not root.is_dir():
            raise DomainError("invalid_input", "工程数据目录不是可导出的普通目录。")
        rows = []
        total_size = 0
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if _is_reparse(path):
                raise DomainError("invalid_input", "工程数据包含链接或重解析入口，不能导出。")
            if path.is_dir():
                continue
            if not path.is_file():
                raise DomainError("invalid_input", "工程数据包含不支持的文件类型，不能导出。")
            relative = path.relative_to(root).as_posix()
            rows.append(dict(path=relative, size=path.stat().st_size, sha256=digest(path)))
            total_size += rows[-1]["size"]
            if len(rows) > MAX_PACKAGE_FILES or total_size > MAX_PACKAGE_BYTES:
                raise DomainError("invalid_input", "工程包超过当前支持的文件数量或总大小。")
        return rows

    def export(self, project_id: str, expected_version: int) -> dict[str, Any]:
        metadata = self._metadata(project_id)
        self._assert_stable(metadata, expected_version)
        metadata_bytes = _json_bytes(metadata)
        files = self._project_files(project_id)
        exported_at = now()
        manifest = dict(
            schema=PACKAGE_SCHEMA,
            exportedAt=exported_at,
            appVersion=__version__,
            sourceProject=dict(id=project_id, name=metadata["project"]["name"],
                               version=metadata["project"]["version"]),
            records=dict(path="records.json", size=len(metadata_bytes),
                         sha256=hashlib.sha256(metadata_bytes).hexdigest()),
            files=files,
            totals=dict(files=len(files), bytes=sum(row["size"] for row in files)),
            exclusions=["API令牌", "供应商凭据", "浏览器登录资料", "请求重放键", "运行缓存"],
        )
        export_id = uid("px")
        folder = self.exports / export_id
        folder.mkdir()
        filename = f"{_safe_name(metadata['project']['name'])}-{exported_at[:10]}.formforge.zip"
        package = folder / filename
        try:
            with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6, allowZip64=True) as archive:
                archive.writestr("manifest.json", _json_bytes(manifest))
                archive.writestr("records.json", metadata_bytes)
                root = self.config.data / "projects" / project_id
                for row in files:
                    source = root / PurePosixPath(row["path"])
                    sha = hashlib.sha256()
                    size = 0
                    with source.open("rb") as reader, archive.open("files/" + row["path"], "w", force_zip64=True) as writer:
                        while chunk := reader.read(1024 * 1024):
                            writer.write(chunk)
                            sha.update(chunk)
                            size += len(chunk)
                    if size != row["size"] or sha.hexdigest() != row["sha256"]:
                        raise DomainError("input_changed", "导出期间工程文件发生变化；未发布工程包。", 409)
            latest = self._metadata(project_id)
            self._assert_stable(latest, expected_version)
            if hashlib.sha256(_json_bytes(latest)).digest() != hashlib.sha256(metadata_bytes).digest():
                raise DomainError("input_changed", "导出期间工程记录发生变化；未发布工程包。", 409)
            package_sha = digest(package)
            receipt = dict(id=export_id, projectId=project_id, projectVersion=expected_version,
                           schema=PACKAGE_SCHEMA, filename=filename, packagePath=str(package),
                           packageSha256=package_sha, size=package.stat().st_size,
                           fileCount=len(files), sourceBytes=manifest["totals"]["bytes"],
                           exportedAt=exported_at,
                           downloadUrl=f"/api/project-exports/{export_id}/file")
            (folder / "receipt.json").write_bytes(_json_bytes(receipt))
            with self.store.tx() as db:
                current = self.store._get(db, "projects", project_id)
                if current["version"] != expected_version:
                    raise DomainError("version_conflict", "工程版本已变化；工程包已生成但未登记为当前导出。", 409)
                self.store._event(db, "project.exported", project_id,
                                  {k: receipt[k] for k in ("id", "projectVersion", "packageSha256", "size", "fileCount")})
            return receipt
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def export_file(self, export_id: str) -> tuple[dict[str, Any], Path]:
        if not ID_PATTERN.fullmatch(export_id):
            raise DomainError("not_found", "未找到工程导出包。", 404)
        folder = self.exports / export_id
        receipt_path = folder / "receipt.json"
        if not receipt_path.is_file() or _is_reparse(receipt_path):
            raise DomainError("not_found", "未找到工程导出包。", 404)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            filename = receipt["filename"]
            if (not isinstance(filename, str) or not isinstance(receipt.get("size"), int)
                    or receipt["size"] < 0
                    or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("packageSha256", "")))):
                raise ValueError("receipt fields")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise DomainError("input_changed", "工程导出回执无效，拒绝下载。", 409) from error
        package = folder / filename
        if not _inside(folder, package) or not package.is_file() or _is_reparse(package):
            raise DomainError("not_found", "工程导出包文件不存在。", 404)
        if package.stat().st_size != receipt["size"] or digest(package) != receipt["packageSha256"]:
            raise DomainError("input_changed", "工程导出包字节已变化，拒绝下载。", 409)
        return receipt, package

    @staticmethod
    def _zip_name(name: str) -> PurePosixPath:
        if not isinstance(name, str) or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
            raise DomainError("invalid_input", "工程包包含不安全的绝对路径。")
        path = PurePosixPath(name)
        if (not name or path.as_posix() != name
                or any(part in {"", ".", ".."} for part in path.parts)
                or any(any(ord(char) < 32 for char in part) or re.search(r'[<>:"|?*]', part)
                       or part.endswith((" ", "."))
                       or part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
                       for part in path.parts)):
            raise DomainError("invalid_input", "工程包包含不安全的相对路径。")
        return path

    @staticmethod
    def _read_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
        try:
            info = archive.getinfo(name)
        except KeyError as error:
            raise DomainError("invalid_input", f"工程包缺少 {name}。") from error
        if info.file_size > limit:
            raise DomainError("invalid_input", f"工程包内的 {name} 过大。")
        try:
            data = archive.read(info)
        except (zipfile.BadZipFile, RuntimeError) as error:
            raise DomainError("input_changed", f"工程包内的 {name} 校验失败。") from error
        if len(data) != info.file_size:
            raise DomainError("invalid_input", f"工程包内的 {name} 长度不符。")
        return data

    def _new_id_map(self, records: dict[str, list[dict[str, Any]]], old_project: str) -> dict[str, str]:
        old_ids = {old_project}
        for rows in records.values():
            if not isinstance(rows, list):
                raise DomainError("invalid_input", "工程包数据表格式无效。")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    raise DomainError("invalid_input", "工程包记录缺少有效ID。")
                if row["id"] in old_ids:
                    raise DomainError("invalid_input", "工程包记录包含重复ID。")
                old_ids.add(row["id"])
        mapping: dict[str, str] = {}
        used: set[str] = set()
        for old in sorted(old_ids):
            prefix = old.split("_", 1)[0] if "_" in old else "i"
            prefix = re.sub(r"[^A-Za-z0-9]", "", prefix)[:12] or "i"
            new = uid(prefix)
            while new in used:
                new = uid(prefix)
            mapping[old] = new
            used.add(new)
        return mapping

    @staticmethod
    def _remap(value: Any, mapping: dict[str, str]) -> Any:
        if isinstance(value, dict):
            return {key: ProjectTransfer._remap(item, mapping) for key, item in value.items()}
        if isinstance(value, list):
            return [ProjectTransfer._remap(item, mapping) for item in value]
        if not isinstance(value, str):
            return value
        if value in mapping:
            return mapping[value]
        if "/" in value:
            return "/".join(mapping.get(part, part) for part in value.split("/"))
        if "\\" in value:
            return "\\".join(mapping.get(part, part) for part in value.split("\\"))
        return value

    @staticmethod
    def _remap_path(path: str, mapping: dict[str, str]) -> str:
        source = ProjectTransfer._zip_name(path)
        target = PurePosixPath(*(mapping.get(part, part) for part in source.parts))
        return target.as_posix()

    def _verify_imported_files(self, project_id: str, records: dict[str, list[dict[str, Any]]]):
        project_root = (self.config.data / "projects" / project_id).resolve()

        def checked(relative: str) -> Path:
            path = (self.config.data / PurePosixPath(relative)).resolve()
            if not _inside(project_root, path):
                raise DomainError("invalid_input", "工程记录引用了工程目录之外的文件。")
            return path

        for table in ("artifacts", "sources"):
            for row in records.get(table, []):
                path = checked(row.get("relativePath", ""))
                if not path.is_file() or path.stat().st_size != row.get("size") or digest(path) != row.get("sha256"):
                    raise DomainError("input_changed", "导入后的产物或来源文件与工程记录不一致。")
        for revision in records.get("revisions", []):
            root = checked(revision.get("relativePath", ""))
            if not root.is_dir():
                raise DomainError("input_changed", "导入后的设计版本目录不存在。")
            for item in revision.get("manifest", []):
                path = root / self._zip_name(item.get("path", ""))
                if not _inside(root, path) or not path.is_file() or path.stat().st_size != item.get("size") or digest(path) != item.get("sha256"):
                    raise DomainError("input_changed", "导入后的设计版本清单校验失败。")

    def import_package(self, package_path: str, name: str | None = None) -> dict[str, Any]:
        package = Path(package_path).expanduser().resolve()
        if not package.is_file() or _is_reparse(package):
            raise DomainError("not_found", "未找到指定的FormForge工程包。", 404)
        if package.stat().st_size > MAX_PACKAGE_BYTES:
            raise DomainError("invalid_input", "工程包超过当前支持的大小。")
        package_sha = digest(package)
        temp = Path(tempfile.mkdtemp(prefix="import-", dir=self.imports))
        final: Path | None = None
        try:
            try:
                archive = zipfile.ZipFile(package, "r")
            except (zipfile.BadZipFile, OSError) as error:
                raise DomainError("invalid_input", "文件不是有效的FormForge工程包。") from error
            with archive:
                infos = archive.infolist()
                if len(infos) > MAX_PACKAGE_FILES + 2:
                    raise DomainError("invalid_input", "工程包文件数量超过当前限制。")
                names: set[str] = set()
                total = 0
                for info in infos:
                    path = self._zip_name(info.filename)
                    if info.filename in names or info.is_dir():
                        raise DomainError("invalid_input", "工程包包含重复条目或目录占位条目。")
                    if info.flag_bits & 0x1:
                        raise DomainError("invalid_input", "工程包不能包含加密条目。")
                    names.add(info.filename)
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK:
                        raise DomainError("invalid_input", "工程包不能包含符号链接。")
                    total += info.file_size
                    if total > MAX_PACKAGE_BYTES:
                        raise DomainError("invalid_input", "工程包解压后超过当前支持的大小。")
                    if path.parts[0] not in {"manifest.json", "records.json", "files"}:
                        raise DomainError("invalid_input", "工程包包含未登记的顶层内容。")
                try:
                    manifest = json.loads(self._read_member(archive, "manifest.json", 8 * 1024 * 1024))
                    records_bytes = self._read_member(archive, "records.json", 512 * 1024 * 1024)
                    payload = json.loads(records_bytes)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise DomainError("invalid_input", "工程包清单不是有效JSON。") from error
                if not isinstance(manifest, dict) or not isinstance(payload, dict):
                    raise DomainError("invalid_input", "工程包清单不是有效对象。")
                if manifest.get("schema") != PACKAGE_SCHEMA or payload.get("schema") != PACKAGE_SCHEMA:
                    raise DomainError("invalid_input", "工程包格式或版本不受支持。")
                expected_records = manifest.get("records") or {}
                if (expected_records.get("path") != "records.json"
                        or expected_records.get("size") != len(records_bytes)
                        or expected_records.get("sha256") != hashlib.sha256(records_bytes).hexdigest()):
                    raise DomainError("input_changed", "工程包记录清单SHA-256不匹配。")
                project = payload.get("project")
                records = payload.get("records")
                events = payload.get("events")
                if not isinstance(project, dict) or not isinstance(records, dict) or not isinstance(events, list):
                    raise DomainError("invalid_input", "工程包缺少工程记录或事件。")
                if set(records) - (self.store.TABLES - {"projects"}):
                    raise DomainError("invalid_input", "工程包包含当前版本不认识的数据表。")
                old_project = project.get("id")
                source_project = manifest.get("sourceProject") or {}
                if (not isinstance(old_project, str) or source_project.get("id") != old_project
                        or source_project.get("version") != project.get("version")):
                    raise DomainError("invalid_input", "工程包的工程身份不一致。")
                jobs = records.get("jobs", [])
                if any(job.get("status") not in TERMINAL_JOB_STATES for job in jobs):
                    raise DomainError("invalid_input", "工程包包含未结束任务，不能导入。")
                if any(upload.get("status") == "uploading" for upload in records.get("source_uploads", [])):
                    raise DomainError("invalid_input", "工程包包含未结束的资料上传，不能导入。")
                file_rows = manifest.get("files")
                if not isinstance(file_rows, list) or len(file_rows) > MAX_PACKAGE_FILES:
                    raise DomainError("invalid_input", "工程包文件清单无效。")
                declared: dict[str, dict[str, Any]] = {}
                for row in file_rows:
                    if not isinstance(row, dict):
                        raise DomainError("invalid_input", "工程包文件清单无效。")
                    relative = self._zip_name(row.get("path", "")).as_posix()
                    if (relative in declared or not isinstance(row.get("size"), int) or row["size"] < 0
                            or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", "")))):
                        raise DomainError("invalid_input", "工程包文件清单包含重复路径或无效摘要。")
                    declared[relative] = row
                totals = manifest.get("totals") or {}
                if totals.get("files") != len(declared) or totals.get("bytes") != sum(row["size"] for row in declared.values()):
                    raise DomainError("input_changed", "工程包文件汇总与清单不一致。")
                actual_files = {name[6:] for name in names if name.startswith("files/")}
                if actual_files != set(declared) or names != {"manifest.json", "records.json", *{"files/" + path for path in declared}}:
                    raise DomainError("invalid_input", "工程包内容和文件清单不一致。")
                mapping = self._new_id_map(records, old_project)
                new_project = mapping[old_project]
                extracted = temp / "project"
                extracted.mkdir()
                storage_policy = (prepare_project_storage(self.config.root, extracted)
                                  if self.config.project_storage['enabled'] else dict(self.config.project_storage))
                for relative, row in declared.items():
                    target_relative = self._remap_path(relative, mapping)
                    target = extracted / PurePosixPath(target_relative)
                    if not _inside(extracted, target):
                        raise DomainError("invalid_input", "工程包文件路径越界。")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    sha = hashlib.sha256()
                    size = 0
                    with archive.open("files/" + relative) as reader, target.open("xb") as writer:
                        while chunk := reader.read(1024 * 1024):
                            writer.write(chunk)
                            sha.update(chunk)
                            size += len(chunk)
                    if size != row.get("size") or sha.hexdigest() != row.get("sha256"):
                        raise DomainError("input_changed", "工程包文件SHA-256不匹配。")

            remapped_records: dict[str, list[dict[str, Any]]] = {}
            for table in sorted(self.store.TABLES - {"projects"}):
                rows = records.get(table, [])
                if not isinstance(rows, list):
                    raise DomainError("invalid_input", "工程包数据表格式无效。")
                remapped_records[table] = []
                for original in rows:
                    if original.get("projectId") != old_project:
                        raise DomainError("invalid_input", "工程包包含其他工程的数据。")
                    row = self._remap(original, mapping)
                    row["importedOriginalId"] = original["id"]
                    remapped_records[table].append(row)
            imported_at = now()
            imported_project = self._remap(project, mapping)
            imported_project["id"] = new_project
            imported_project["name"] = (name or str(project.get("name", "")).strip()).strip()
            if not imported_project["name"] or len(imported_project["name"]) > 120:
                raise DomainError("invalid_input", "导入后的工程名称需要1–120个字符。")
            imported_project.pop("deletedAt", None)
            imported_project["importedAt"] = imported_at
            imported_project["importedFrom"] = dict(
                schema=PACKAGE_SCHEMA, sourceProjectId=old_project,
                sourceProjectName=project.get("name"), exportedAt=manifest.get("exportedAt"),
                sourceAppVersion=manifest.get("appVersion"), packageSha256=package_sha,
                identitiesRemapped=True, credentialsImported=False, requestReplayKeysImported=False,
                qualification="历史检查与原文件按版本保留；本机环境及后续修改需重新检查。",
            )
            remapped_events = []
            for event in events:
                if not isinstance(event, dict) or event.get("projectId") != old_project:
                    raise DomainError("invalid_input", "工程包事件不属于声明的工程。")
                remapped_events.append(self._remap(event, mapping))

            final = self.config.data / "projects" / new_project
            if final.exists():
                raise DomainError("version_conflict", "导入目标工程目录已存在，请重试。", 409)
            final.parent.mkdir(parents=True, exist_ok=True)
            _publish_import_directory(extracted, final)
            try:
                self._verify_imported_files(new_project, remapped_records)
                with self.store.tx() as db:
                    self.store._put(db, "projects", imported_project)
                    for table, rows in remapped_records.items():
                        for row in rows:
                            self.store._put(db, table, row)
                    for event in remapped_events:
                        db.execute("INSERT INTO events(project_id,job_id,type,payload,created_at) VALUES(?,?,?,?,?)",
                                   (new_project, event.get("jobId"), event.get("type"),
                                    encode(event.get("payload")), event.get("createdAt") or imported_at))
                    self.store._event(db, "project.imported", new_project,
                                      dict(sourceProjectId=old_project, packageSha256=package_sha,
                                           records=sum(len(rows) for rows in remapped_records.values()),
                                           files=len(declared), importedAt=imported_at))
            except BaseException:
                shutil.rmtree(final, ignore_errors=True)
                final = None
                raise
            return dict(project=imported_project, schema=PACKAGE_SCHEMA, packageSha256=package_sha,
                        sourceProjectId=old_project, records=sum(len(rows) for rows in remapped_records.values()),
                        events=len(remapped_events), files=len(declared), sourceBytes=sum(row["size"] for row in declared.values()),
                        identitiesRemapped=True, credentialsImported=False, requestReplayKeysImported=False,
                        projectStorage={**storage_policy, 'directory': str(final)})
        finally:
            shutil.rmtree(temp, ignore_errors=True)
