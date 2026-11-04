"""Explicit workspace cleanup and reversible project removal.

Cleanup discovers only known disposable locations. It never accepts caller paths.
Project removal preserves terminal task clocks and commercial observation records.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import threading
import time

from .settings import linked, safe_directory, timestamp
from .store import DomainError, now, uid
from .job_workspace_cleanup import WorkspaceCopies, RULE as WORKSPACE_RULE


def fail(message, code='invalid_input', status=409):
    raise DomainError(code, message, status)


def inspect_tree(path, root, stopped=lambda: False, budget=30):
    """Metadata-only stamp; links, unreadable trees and partial scans are ineligible."""
    deadline = time.monotonic() + budget
    records, pending, total, files, newest = [], [path], 0, 0, 0
    while pending:
        if stopped() or time.monotonic() > deadline:
            fail('目录统计未完成，本轮不提供清理。')
        folder = pending.pop()
        if not safe_directory(folder, root):
            fail('目录含链接或重解析路径，本轮不提供清理。')
        info = folder.lstat()
        newest = max(newest, info.st_mtime)
        records.append((str(folder.relative_to(path)), 'd', info.st_mtime_ns, info.st_ino))
        with os.scandir(folder) as entries:
            for entry in entries:
                if stopped() or time.monotonic() > deadline:
                    fail('目录统计未完成，本轮不提供清理。')
                info = entry.stat(follow_symlinks=False)
                if linked(info):
                    fail('目录内含链接或重解析文件，本轮不提供清理。')
                newest = max(newest, info.st_mtime)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    files += 1
                    records.append((str(Path(entry.path).relative_to(path)), 'f', info.st_size,
                                    info.st_mtime_ns, info.st_ino))
                else:
                    fail('目录包含非普通文件，本轮不提供清理。')
    stamp = hashlib.sha256(json.dumps(sorted(records), ensure_ascii=False).encode()).hexdigest()
    return dict(bytes=total, files=files, newest=newest, stamp=stamp)


def remove_tree(path, root, removed):
    """Confined no-link removal, reporting only files actually unlinked."""
    if not safe_directory(path, root):
        fail('目标目录已变成链接或重解析路径，已停止清理。')
    with os.scandir(path) as entries:
        children = [(Path(e.path), e.stat(follow_symlinks=False)) for e in entries]
    for child, info in children:
        if linked(info):
            fail('清理时发现目录链接，已停止此项。')
        if stat.S_ISDIR(info.st_mode):
            remove_tree(child, root, removed)
        elif stat.S_ISREG(info.st_mode):
            if not safe_directory(child.parent, root) or linked(child.lstat()):
                fail('文件路径已变化，已停止此项。')
            size = child.lstat().st_size
            child.unlink()
            removed(size)
        else:
            fail('清理时发现非普通文件，已停止此项。')
    if not safe_directory(path, root):
        fail('目录路径已变化，已停止此项。')
    path.rmdir()


class WorkspaceMaintenance:
    MIN_AGE_SECONDS = 3600
    CACHE_PATHS = ('.cache/pip', '.cache/pip-cam', '.cache/pip-components',
                   '.cache/npm', '.cache/electron', '.local/pip-cache', '.pytest_cache')
    # A retained synthetic package replay explicitly consumes this engine cache.
    PROTECTED = {'.cache/pytest-procurement-final'}

    def __init__(self, service):
        self.service, self.store, self.root = service, service.store, service.config.root
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.state = None
        self.targets = {}
        self.task = None
        self._cleaning = False
        self.purges = set()

    @property
    def cleaning(self):
        with self.lock:
            return self._cleaning

    def _idle(self, project=None):
        with self.store.read_tx() as db:
            sql = "SELECT 1 FROM jobs WHERE json_extract(data,'$.status') IN ('queued','running')"
            args = ()
            if project:
                sql += ' AND project_id=?'
                args = (project,)
            if db.execute(sql + ' LIMIT 1', args).fetchone():
                fail('仍有任务执行，请等待任务结束后再操作。', 'busy')
        if self.service.supplier_executor.active:
            fail('仍有供应商操作执行，请等待其返回后再操作。', 'busy')
        for name in ('desktop_browser', 'playwright_browser'):
            browser = getattr(self.service, name, None)
            if browser and browser.lock.locked():
                fail('供应商浏览器仍在执行操作，请等待其返回。', 'busy')
        checkpoint = self.root / '.local/playwright/checkpoint.json'
        if checkpoint.exists():
            if not safe_directory(checkpoint.parent, self.root) or linked(checkpoint.lstat()) or checkpoint.stat().st_size > 2*1024*1024:
                fail('供应商执行状态无法安全读取，请先核对执行器。', 'busy')
            try:
                state = json.loads(checkpoint.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                fail('供应商执行状态暂不可读，请先核对执行器。', 'busy')
            if state.get('status') == 'running' and (not project or state.get('projectId') == project):
                fail('此时仍有供应商后台任务执行，请等待其结束。', 'busy')

    def status(self):
        with self.lock:
            return copy.deepcopy(self.state or dict(status='idle', items=[], skipped=[], result=None))

    async def scan(self):
        with self.lock:
            if self.task and not self.task.done():
                fail('已有扫描或清理在执行。', 'busy')
            self.stop.clear()
            self.targets = {}
            self.state = dict(scanId=uid('scan'), status='scanning', startedAt=timestamp(),
                              minimumAgeHours=1, items=[], skipped=[], result=None, inspected=0)
            self.task = asyncio.create_task(asyncio.to_thread(self._scan))
            return self.status()

    def _test_workspace(self, path):
        if not safe_directory(path, self.root):
            return False
        with os.scandir(path) as entries:
            children = list(entries)
        # pytest basetemp consists of fixture directories, not top-level evidence.
        return (bool(children) and all(e.is_dir(follow_symlinks=False) and
                    not linked(e.stat(follow_symlinks=False)) for e in children) and
                any(re.fullmatch(r'(?:test_.+\d+|(?:external-cam-service|firmware|insertion|manufacturing|mechanical-native|mounting|pcb-geometry|procurement|silkscreen|structural)\d+)', e.name)
                    for e in children))

    def _discover(self):
        for relative in self.CACHE_PATHS:
            yield 'downloads', '下载与开发缓存', self.root / relative
        for base in ('.cache', '.local'):
            folder = self.root / base
            if not folder.exists() or not safe_directory(folder, self.root):
                continue
            for path in folder.iterdir():
                relative = path.relative_to(self.root).as_posix()
                if relative in self.PROTECTED or not path.is_dir() or linked(path.lstat()):
                    continue
                named = path.name.startswith(('pytest-', 'test-'))
                extra = base == '.local' and path.name.startswith(('body-', 'integration-', 'plane-selection-', 'workflow-', 'maintenance-'))
                if (named or extra) and self._test_workspace(path):
                    yield 'tests', '测试临时工作区', path
        validation = self.root / 'validation'
        if validation.exists() and safe_directory(validation, self.root):
            for run in validation.iterdir():
                if not run.is_dir() or linked(run.lstat()) or not re.match(r'^\d{8}-', run.name):
                    continue
                for path in run.iterdir():
                    if path.name.startswith('pytest-') and path.is_dir() and self._test_workspace(path):
                        yield 'tests', '测试临时工作区', path
        for base in ('.local/pcb-read', '.local/pcb-geometry-read'):
            folder = self.root / base
            if folder.exists() and safe_directory(folder, self.root):
                for path in folder.iterdir():
                    if path.name.startswith('read_') and path.is_dir() and not linked(path.lstat()):
                        yield 'reads', '原生读取临时副本', path
        for base in ('src', 'tests', 'scripts'):
            folder = self.root / base
            if not folder.exists() or not safe_directory(folder, self.root):
                continue
            for current, dirs, files in os.walk(folder, followlinks=False):
                dirs[:] = [d for d in dirs if not linked((Path(current)/d).lstat())]
                if Path(current).name == '__pycache__' and files and not dirs and all(n.endswith('.pyc') for n in files):
                    yield 'downloads', '下载与开发缓存', Path(current)

    def _scan(self):
        cutoff = time.time() - self.MIN_AGE_SECONDS
        try:
            for category, label, path in self._discover():
                if self.stop.is_set():
                    break
                if not path.exists():
                    continue
                relative = path.relative_to(self.root).as_posix()
                try:
                    info = inspect_tree(path, self.root, self.stop.is_set)
                    if info['newest'] > cutoff:
                        continue
                    id = hashlib.sha256(relative.encode()).hexdigest()[:24]
                    row = dict(id=id, category=category, categoryName=label, path=relative,
                               bytes=info['bytes'], files=info['files'])
                    with self.lock:
                        self.targets[id] = dict(path=relative, **info)
                        self.state['items'].append(row)
                except (OSError, DomainError) as error:
                    with self.lock:
                        self.state['skipped'].append(dict(path=relative, reason=str(error)))
                with self.lock:
                    self.state['inspected'] += 1
            copies = WorkspaceCopies(self.root, self.store, inspect_tree, self.stop.is_set, self.MIN_AGE_SECONDS)
            with self.lock:
                self.state['workspaceReview'] = dict(rule=WORKSPACE_RULE, inspected=0, eligible=0, retained=0)
            for job, path in copies.discover():
                if self.stop.is_set():
                    break
                relative = path.relative_to(self.root).as_posix()
                try:
                    info = copies.check(job, path)
                    id = hashlib.sha256(relative.encode()).hexdigest()[:24]
                    row = dict(id=id, category='workspaces', categoryName='依赖已核对的工程工作副本',
                               path=relative, bytes=info['bytes'], files=info['files'],
                               revisionId=info['workspaceProof']['revisionId'], rule=WORKSPACE_RULE)
                    with self.lock:
                        self.targets[id] = dict(path=relative, **info)
                        self.state['items'].append(row)
                        self.state['workspaceReview']['eligible'] += 1
                except (OSError, DomainError, ValueError, KeyError) as error:
                    with self.lock:
                        self.state['skipped'].append(dict(path=relative, reason=str(error)))
                        self.state['workspaceReview']['retained'] += 1
                with self.lock:
                    self.state['inspected'] += 1
                    self.state['workspaceReview']['inspected'] += 1
            with self.lock:
                self.state.update(status='cancelled' if self.stop.is_set() else 'ready',
                                  finishedAt=timestamp())
        except Exception:
            with self.lock:
                self.state.update(status='failed', message='扫描未完成，请重新扫描。')

    async def clean(self, scan_id, item_ids):
        # Serialize admission with job creation; engineering files require a dependency proof.
        with self.store.lock, self.lock:
            if self.task and not self.task.done():
                fail('已有扫描或清理在执行。', 'busy')
            if not self.state or self.state.get('scanId') != scan_id or self.state['status'] != 'ready':
                fail('清理预览已失效，请重新扫描。', 'version_conflict')
            if not item_ids or len(item_ids) != len(set(item_ids)) or any(i not in self.targets for i in item_ids):
                fail('请选择本轮预览中的有效清理项目。')
            self._idle()
            self._cleaning = True
            self.state.update(status='cleaning', scanStartedAt=self.state['startedAt'],
                              startedAt=timestamp(), finishedAt=None,
                              result=dict(bytes=0, files=0, completed=0,
                                                            total=len(item_ids), errors=[]))
            selected = [copy.deepcopy(self.targets[i]) for i in item_ids]
            self.state['workspaceCopies'] = [dict(path=row['path'], **row['workspaceProof'])
                                              for row in selected if 'workspaceProof' in row]
            self.task = asyncio.create_task(asyncio.to_thread(self._clean, selected))
            return self.status()

    def _clean(self, selected):
        copies = None
        def removed(size):
            with self.lock:
                self.state['result']['bytes'] += size
                self.state['result']['files'] += 1
        try:
            try:
                self._save_history(self.status())
            except (OSError, DomainError):
                with self.lock:
                    self.state['result']['errors'].append(dict(path='.local/maintenance-history', reason='无法保存清理记录，本轮未开始删除。'))
                    self.state.update(status='failed', finishedAt=timestamp())
                return
            for row in selected:
                if self.stop.is_set():
                    break
                path = self.root / row['path']
                try:
                    current = inspect_tree(path, self.root, self.stop.is_set)
                    if current['stamp'] != row['stamp'] or current['newest'] > time.time() - self.MIN_AGE_SECONDS:
                        fail('目录在预览后发生变化，已跳过，请重新扫描。')
                    if 'workspaceProof' in row:
                        if copies is None:
                            copies = WorkspaceCopies(self.root, self.store, inspect_tree, self.stop.is_set, self.MIN_AGE_SECONDS)
                        copies.recheck(row)
                    remove_tree(path, self.root, removed)
                    with self.lock:
                        self.state['result']['completed'] += 1
                except (OSError, DomainError, ValueError, KeyError) as error:
                    with self.lock:
                        self.state['result']['errors'].append(dict(path=row['path'], reason=str(error)))
                try:
                    self._save_history(self.status())
                except (OSError, DomainError):
                    with self.lock:
                        self.state['result']['errors'].append(dict(path='.local/maintenance-history', reason='进度记录写入失败，已停止后续清理。'))
                    break
            with self.lock:
                result = self.state['result']
                self.state.update(status='completed' if not result['errors'] and not self.stop.is_set() else 'partial',
                                  finishedAt=timestamp())
            try:
                self._save_history(self.status())
            except (OSError, DomainError):
                with self.lock:
                    self.state['result']['errors'].append(dict(path='.local/maintenance-history', reason='清理结果仍可读取，但记录写入失败。'))
                    self.state['status'] = 'partial'
        finally:
            with self.lock:
                self._cleaning = False
            self.service.settings._storage_time = 0

    def _save_history(self, record):
        folder = self.root / '.local/maintenance-history'
        folder.mkdir(exist_ok=True)
        if not safe_directory(folder, self.root):
            fail('清理记录目录不是普通目录。')
        summary = {k:record.get(k) for k in ('scanId','operation','status','startedAt','finishedAt','result','workspaceCopies')}
        target = folder / (record['scanId'] + '.json')
        temporary = target.with_suffix('.tmp')
        if (target.exists() and linked(target.lstat())) or (temporary.exists() and linked(temporary.lstat())):
            fail('清理记录文件不是普通文件。')
        temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(target)

    def history(self):
        folder = self.root / '.local/maintenance-history'
        if not folder.exists() or not safe_directory(folder, self.root):
            return []
        result = []
        for path in sorted(folder.glob('scan_*.json'), key=lambda p:p.name, reverse=True):
            if linked(path.lstat()) or path.stat().st_size > 4 * 1024 * 1024:
                continue
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                row = {k:value.get(k) for k in ('scanId','operation','status','startedAt','finishedAt','result')}
                if row['status'] == 'cleaning' and (not self.cleaning or row['scanId'] != self.status().get('scanId')):
                    row['status'] = 'interrupted'
                result.append(row)
            except (OSError, ValueError):
                continue
        return sorted(result, key=lambda r:r.get('finishedAt') or '', reverse=True)[:10]

    def _project(self, db, project_id, version, name):
        if not re.fullmatch(r'p_[0-9a-f]{16}', project_id):
            fail('工程编号无效。')
        project = self.store._get(db, 'projects', project_id, include_deleted=True)
        if project['version'] != version or project['name'] != name:
            fail('工程名称或版本已变化，请刷新后重新确认。', 'version_conflict')
        if project.get('purgedAt'):
            fail('此工程文件已彻底删除，不能恢复。', 'project_deleted', 410)
        return project

    def trash(self):
        with self.store.read_tx() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT data FROM projects WHERE json_extract(data,'$.deletedAt') IS NOT NULL "
                "AND json_extract(data,'$.purgedAt') IS NULL ORDER BY rowid DESC")]

    async def empty_trash(self, projects):
        # Freeze exactly the displayed projects; later additions are not included.
        with self.store.tx() as db, self.lock:
            if self.task and not self.task.done() or self.purges:
                fail('已有扫描或清理在执行。', 'busy')
            if len({p['projectId'] for p in projects}) != len(projects):
                fail('清空列表包含重复工程。')
            self._idle()
            for row in projects:
                project = self._project(db, row['projectId'], row['expectedVersion'], row['confirmName'])
                if not project.get('deletedAt'):
                    fail('清空列表中的工程已不在回收站，请刷新后重试。')
            self.stop.clear()
            self._cleaning = True
            self.state = dict(scanId=uid('scan'), operation='trash', status='cleaning',
                              startedAt=timestamp(), finishedAt=None, items=[], skipped=[],
                              result=dict(bytes=0, files=0, completed=0, total=len(projects), errors=[]))
            self.task = asyncio.create_task(asyncio.to_thread(self._empty_trash, copy.deepcopy(projects)))
            return self.status()

    def _empty_trash(self, projects):
        def removed(size):
            with self.lock:
                self.state['result']['bytes'] += size
                self.state['result']['files'] += 1
        try:
            # Persist the start before removing anything, as for ordinary cleanup.
            try:
                self._save_history(self.status())
            except (OSError, DomainError):
                with self.lock:
                    self.state['result']['errors'].append(dict(path='.local/maintenance-history', reason='无法保存清理记录，本轮未开始删除。'))
                    self.state.update(status='failed', finishedAt=timestamp())
                return
            for row in projects:
                if self.stop.is_set():
                    break
                try:
                    self.project_purge(row['projectId'], row['expectedVersion'], row['confirmName'], removed)
                    with self.lock:
                        self.state['result']['completed'] += 1
                except (OSError, DomainError) as error:
                    with self.lock:
                        self.state['result']['errors'].append(dict(path=row['confirmName'], reason=str(error)))
                self._save_history(self.status())
            with self.lock:
                self.state.update(status='partial' if self.stop.is_set() or self.state['result']['errors'] else 'completed',
                                  finishedAt=timestamp())
            self._save_history(self.status())
        except (OSError, DomainError) as error:
            with self.lock:
                self.state['result']['errors'].append(dict(path='.local/maintenance-history', reason=str(error)))
                self.state.update(status='partial', finishedAt=timestamp())
        finally:
            with self.lock:
                self._cleaning = False
            self.service.settings._storage_time = 0

    def project_change(self, project_id, version, name, restore=False):
        with self.store.tx() as db:
            p = self._project(db, project_id, version, name)
            self._idle(project_id)
            if project_id in self.purges:
                fail('此工程正在清除文件，请等待完成。', 'busy')
            if restore:
                if not p.get('deletedAt'):
                    fail('此工程不在回收站中。')
                if p.get('purgeStartedAt'):
                    fail('此工程文件已开始彻底清除，不能恢复；可重试清除剩余文件。')
                p.pop('deletedAt', None)
            else:
                if p.get('deletedAt'):
                    fail('此工程已在回收站中。')
                p['deletedAt'] = now()
            p.update(version=p['version'] + 1, updatedAt=now())
            self.store._put(db, 'projects', p)
            self.store._event(db, 'project.restored' if restore else 'project.deleted', project_id,
                              dict(name=name, version=p['version'], recoverable=not restore))
            return p

    def project_purge(self, project_id, version, name, on_removed=None):
        with self.store.tx() as db:
            p = self._project(db, project_id, version, name)
            self._idle(project_id)
            if not p.get('deletedAt'):
                fail('请先将工程移入回收站。')
            if project_id in self.purges:
                fail('此工程正在清除文件。', 'busy')
            p.update(purgeStartedAt=now(), version=p['version']+1)
            self.store._put(db, 'projects', p)
            self.purges.add(project_id)
        result = dict(bytes=0, files=0)
        def removed(size):
            result['bytes'] += size
            result['files'] += 1
            if on_removed:
                on_removed(size)
        try:
            path = self.root / 'data/projects' / project_id
            try:
                path.lstat()
                exists = True
            except FileNotFoundError:
                exists = False
            if exists:
                remove_tree(path, self.root, removed)
            with self.store.tx() as db:
                p = self.store._get(db, 'projects', project_id, include_deleted=True)
                p.update(purgedAt=now(), purgeResult=result, updatedAt=now())
                self.store._put(db, 'projects', p)
                self.store._event(db, 'project.purged', project_id, dict(name=name, **result))
            # Version/task/ordering metadata stays as a tombstone; supplier orders are not cancelled.
            return dict(projectId=project_id, status='purged', **result)
        except (OSError, DomainError) as error:
            with self.store.tx() as db:
                p = self.store._get(db, 'projects', project_id, include_deleted=True)
                p.update(purgeResult=result, purgeError=str(error), updatedAt=now())
                self.store._put(db, 'projects', p)
                self.store._event(db, 'project.purge_failed', project_id, dict(name=name, reason=str(error), **result))
            fail('工程文件未清除完，已保留删除状态，可重试。原因：' + str(error), 'purge_incomplete')
        finally:
            self.purges.discard(project_id)
            self.service.settings._storage_time = 0

    async def close(self):
        self.stop.set()
        if self.task:
            await self.task
