"""Reuse immutable parent PCB reads only within one explicitly owned job.

Candidate reads, native mutation, ERC/DRC and every adoption proof stay outside
this cache. Native misses use the adapter's source checks; hits check the source
once before decoding their isolated copy. Engine identity uses the bundle frozen
and checked at adapter startup; changing installed engines requires a restart.
"""
import hashlib
import json
import time
from collections import OrderedDict
from pathlib import Path

from .connectivity import fingerprint
from .importer import verify_snapshot, within
from ..store import DomainError


class PcbReadSession:
    max_bytes = 32 * 1024 * 1024
    max_entries = 2

    def __init__(self, adapter, metrics_path):
        self.adapter = adapter
        self.metrics_path = Path(metrics_path)
        self._snapshots = OrderedDict()
        self.calls = []

    def _identity(self, revision):
        adapter = self.adapter
        if not adapter.config.kicad_python:
            raise DomainError('dependency_missing', '缺少 KiCad 10 Python 板编辑引擎。')
        runtime = Path(adapter.config.kicad_python)
        stat = runtime.stat()
        return fingerprint(dict(source=str(adapter.config.data.resolve()), revision=revision,
            engine=adapter.engine_hash, modules=adapter.engine_hashes,
            adapter=adapter.source_sha256, mounting=adapter.mounting_policy_sha256,
            netlist=adapter.netlist_policy_sha256,
            runtime=[str(runtime.resolve()), stat.st_size, stat.st_mtime_ns]))

    def report(self):
        return dict(schemaVersion=1, policy='job-local-parent-native-read-v1',
            nativeReadAttempts=sum(c['nativeReadAttempted'] for c in self.calls),
            reuseCount=sum(c['reused'] for c in self.calls), calls=self.calls)

    def read(self, revision, folder, check_cancel=lambda: None):
        started = time.perf_counter()
        record = dict(revisionId=revision['id'], requestedFolder=str(folder),
                      nativeReadAttempted=False, reused=False, status='failed')
        try:
            check_cancel()
            source = within(self.adapter.config.data, revision['relativePath'])
            key = self._identity(revision)
            cached = self._snapshots.get(key)
            if cached is None:
                record['nativeReadAttempted'] = True
                snapshot = self.adapter.read(revision, folder, check_cancel)
                encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf8')
                origin = str(folder)
            else:
                verify_snapshot(source, revision['manifest'])
                encoded, origin = cached
                snapshot = json.loads(encoded)
                record['reused'] = True
            check_cancel()
            if cached is None:
                if self._identity(revision) != key:
                    raise DomainError('input_changed', 'PCB 读取期间输入或引擎身份变化。', 409)
            # Store bytes, not caller-owned nested dicts. A planner/proof may
            # mutate its returned copy without changing the later baseline.
            if len(encoded) <= self.max_bytes:
                self._snapshots[key] = (encoded, origin)
                self._snapshots.move_to_end(key)
                while (len(self._snapshots) > self.max_entries or
                       sum(len(item[0]) for item in self._snapshots.values()) > self.max_bytes):
                    self._snapshots.popitem(last=False)
            record.update(status='completed', sourceReadFolder=origin,
                          snapshotSha256=hashlib.sha256(encoded).hexdigest())
            return snapshot
        except Exception as exc:
            record['errorCode'] = getattr(exc, 'code', type(exc).__name__)
            raise
        finally:
            record['elapsedSeconds'] = round(time.perf_counter() - started, 6)
            self.calls.append(record)
            self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
            self.metrics_path.write_text(json.dumps(self.report(), ensure_ascii=False, indent=2), encoding='utf8')
