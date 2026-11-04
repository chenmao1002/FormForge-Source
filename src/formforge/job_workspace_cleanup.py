"""Dependency-checked removal of exact KiCad inspection input copies.

This policy never selects job outputs, revisions, CAD, reports or failed jobs.
Native command arguments in a terminal job's result are historical execution
evidence; every other recorded project path is treated as a dependency.
"""
import hashlib
import json
import posixpath
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
import time

from .settings import linked, safe_directory
from .store import DomainError

RULE = 'kicad-inspection-input-copies-v1'
INSPECTIONS = {
    'eda.inspect': ('',),
    'schematic.edit': ('before', 'after'),
    'schematic.initialize': ('after',),
    'schematic.review': ('native',),
    'pcb.handoff': ('native',),
    **{kind: ('inspection',) for kind in ('pcb.initialize', 'pcb.edit', 'pcb.route', 'pcb.pair_route', 'pcb.sync')},
}
PATH = re.compile(r'projects/p_[0-9a-f]{16}/(?:jobs/j_[0-9a-f]{16}|revisions/r_[0-9a-f]{16})(?:/[^\s"<>|;,]*)?')


def reject(reason):
    raise DomainError('workspace_retained', reason, 409)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def strings(value, *, historical_result=False):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            # Skip only the known, completed native command-log structure.
            if (historical_result and key == 'commands' and isinstance(child, list) and child and
                    all(isinstance(c, dict) and {'name', 'args', 'exitCode'} <= c.keys() for c in child)):
                continue
            yield from strings(child, historical_result=historical_result)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child, historical_result=historical_result)


class WorkspaceCopies:
    def __init__(self, root, store, inspect, stopped=lambda: False, minimum_age=3600):
        self.root, self.data, self.store = root, root/'data', store
        self.inspect, self.stopped, self.minimum_age = inspect, stopped, minimum_age
        self.jobs, self.revisions, self.references = {}, {}, {}
        self.sources = {}
        with store.read_tx() as db:
            for table in sorted(store.TABLES):
                for record in db.execute(f'SELECT data FROM {table}'):
                    row = json.loads(record[0])
                    if table == 'jobs':
                        self.jobs[row['id']] = row
                    elif table == 'revisions':
                        self.revisions[row['id']] = row
                    values = strings({k: v for k, v in row.items() if table != 'jobs' or k != 'result'})
                    self._index(values, f'{table}:{row["id"]}')
                    if table == 'jobs':
                        self._index(strings(row.get('result'), historical_result=row.get('status') == 'completed'),
                                    f'{table}:{row["id"]}')

    def _index(self, values, owner):
        for text in values:
            for match in PATH.finditer(re.sub(r'/+', '/', text.replace('\\', '/').lower())):
                relative = posixpath.normpath(match.group().rstrip(').]'))
                key = '/'.join(relative.split('/')[:4])
                self.references.setdefault(key, set()).add((relative, owner))

    def protected_by(self, path):
        relative = path.relative_to(self.data).as_posix().lower()
        key = '/'.join(relative.split('/')[:4])
        for ref, owner in self.references.get(key, ()):
            if ref == relative or ref.startswith(relative+'/') or relative.startswith(ref+'/'):
                return owner
        return None

    def discover(self):
        for job in self.jobs.values():
            if not re.fullmatch(r'j_[0-9a-f]{16}', job['id']) or not re.fullmatch(r'p_[0-9a-f]{16}', job['projectId']):
                continue
            folder = self.data/'projects'/job['projectId']/'jobs'/job['id']
            for part in INSPECTIONS.get(job['kind'], ()):
                inspection = folder/part if part else folder
                for name in ('workspace', 'drawing-workspace'):
                    path = inspection/name
                    if path.exists():
                        yield job, path

    def _report(self, path):
        report = path.parent/'inspection.json'
        if not safe_directory(report.parent, self.root) or linked(report.lstat()) or report.stat().st_size > 8*1024*1024:
            reject('检查报告缺失、过大或包含链接，保留工作副本。')
        info = report.stat()
        return json.loads(report.read_text(encoding='utf-8-sig')), [info.st_size, info.st_mtime_ns, info.st_ino]

    def _source(self, revision, verify):
        key = revision['id']
        if key in self.sources:
            return self.sources[key]
        relative = revision['relativePath']
        prefix = f'projects/{revision["projectId"]}/revisions/{key}/'
        if not relative.startswith(prefix) or '..' in PurePosixPath(relative).parts or '\\' in relative:
            reject('冻结源不在独立版本目录中，保留工作副本。')
        source = self.data/relative
        info = self.inspect(source, self.root, self.stopped)
        expected = {}
        for row in revision['manifest']:
            name = row['path']
            parts = PurePosixPath(name).parts
            if not parts or '..' in parts or ':' in name or '\\' in name or name.startswith('/') or name in expected:
                reject('冻结源清单路径无法核对，保留工作副本。')
            expected[name] = row
        if not expected or info['files'] < len(expected):
            reject('冻结源文件与版本清单不完整，保留工作副本。')
        if verify:
            self._same_files(source, expected)
        self.sources[key] = source, info, expected
        return self.sources[key]

    def _same_files(self, path, expected):
        for name, row in expected.items():
            if self.stopped():
                reject('扫描已停止。')
            file = path/name
            if file.stat().st_size != row['size'] or digest(file) != row['sha256']:
                reject('副本或冻结源内容与清单不同，保留工作副本。')

    def check(self, job, path, previous=None):
        if job.get('status') != 'completed' or not job.get('finishedAt'):
            reject('任务未成功结束；失败、中断及运行中的任务工作副本保留。')
        finished = datetime.fromisoformat(job['finishedAt'].replace('Z', '+00:00')).astimezone(timezone.utc).timestamp()
        if time.time()-finished < self.minimum_age:
            reject('任务结束未超过 1 小时，保留工作副本。')
        owner = self.protected_by(path)
        if owner:
            reject('工作副本仍被工程记录引用：'+owner)
        report, report_stamp = self._report(path)
        if report.get('backend') != 'kicad10':
            reject('不是已识别的 KiCad 检查输入副本。')
        revision = self.revisions.get(report.get('inputRevision'))
        if not revision or revision['projectId'] != job['projectId']:
            reject('找不到同工程的冻结输入版本，保留工作副本。')
        source, source_info, expected = self._source(revision, verify=previous is None)
        proof = dict(rule=RULE, jobId=job['id'], revisionId=revision['id'],
                     source=source.relative_to(self.root).as_posix(), sourceStamp=source_info['stamp'], reportStamp=report_stamp,
                     manifestSha256=hashlib.sha256(json.dumps(revision['manifest'], sort_keys=True).encode()).hexdigest())
        if previous is not None:
            if proof != previous:
                reject('冻结源、任务或检查报告在预览后变化，请重新扫描。')
            return proof
        info = self.inspect(path, self.root, self.stopped)
        if info['newest'] > time.time()-self.minimum_age:
            reject('副本近期变化或含额外文件，保留工作副本。')
        # KiCad generates project-local UI state even during CLI inspection.
        # It is not an engineering source; only exact entrypoint sibling names
        # are recognized. Unknown extra files always retain the whole copy.
        local_state = {str(PurePosixPath(name).with_suffix('.kicad_prl'))
                       for name in revision.get('entrypoints', {}).values()
                       if isinstance(name, str) and name.endswith(('.kicad_sch', '.kicad_pcb'))}
        actual = {file.relative_to(path).as_posix() for file in path.rglob('*') if file.is_file()}
        if actual - set(expected) - local_state:
            reject('副本含未知额外文件，保留工作副本。')
        if path.name == 'drawing-workspace':
            rendering = report.get('schematicRendering') or {}
            project = rendering.get('project')
            if project not in expected or rendering.get('sourceProjectSha256') != expected[project]['sha256']:
                reject('绘图副本缺少可追溯的项目设置转换记录。')
            expected = {**expected, project: {**expected[project], 'size': (path/project).stat().st_size,
                                             'sha256': rendering.get('drawingProjectSha256')}}
        self._same_files(path, expected)
        return {**info, 'workspaceProof': proof}

    def recheck(self, row):
        job = self.jobs.get(row['workspaceProof']['jobId'])
        if job is None:
            reject('任务记录已变化，请重新扫描。')
        self.check(job, self.root/row['path'], previous=row['workspaceProof'])
