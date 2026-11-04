"""Instance-owned, byte-bounded schematic display snapshots, never check inputs."""
from collections import OrderedDict
import hashlib
import json
import threading


def cache_key(source, revision, parser_policy):
    identity = dict(source=str(source), revisionId=revision['id'], projectId=revision.get('projectId'),
                    manifest=revision['manifest'], entrypoints=revision['entrypoints'],
                    symbolLibraries=revision.get('symbolLibraries', []), functionalGroups=revision.get('functionalGroups'),parserPolicy=parser_policy)
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


class SchematicReadCache:
    """Retain at most two display snapshots and 16 MiB of serialized payload.

    JSON bytes keep parsed native trees out of the cache and give each reader an
    independent nested value. Source SHA verification belongs to read_schematic,
    including hits; candidates and native checks never consume this cache.
    """
    def __init__(self, max_entries=2, max_bytes=16 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._entries = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            payload = self._entries.get(key)
            if payload is None:
                return None
            self._entries.move_to_end(key)
        return json.loads(payload)

    def put(self, key, snapshot):
        if self.max_entries <= 0 or self.max_bytes <= 0:
            return
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf8')
        if len(payload) > self.max_bytes:
            return
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= len(old)
            self._entries[key] = payload
            self._bytes += len(payload)
            while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                _, removed = self._entries.popitem(last=False)
                self._bytes -= len(removed)
