"""Optional resumable numeric OCCT checks; verification always rebuilds fresh.

Each entry is bound to the complete CAD implementation, runtime versions,
canonical part specifications and imported STEP digests. Corrupt entries are
ignored. Entries are committed individually so a later export failure does not
lose completed collision work.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class AssemblyCheckCache:
    def __init__(self, directory, design, model, *, source_hashes):
        import cadquery as cq
        from importlib.metadata import version
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        source = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in Path(__file__).parent.glob("*.py")}
        if source != source_hashes:
            raise ValueError("CAD source changed during local modeling; no collision cache used or written")
        self.engine = hashlib.sha256(_encoded({"source": source_hashes, "python": sys.version,
                                               "cadquery": cq.__version__, "ocp": version("cadquery-ocp")})).hexdigest()
        self.parts = {p.name: {"spec": p.model_dump(),
                              "step_sha256": model.measures[p.name]["source_step_sha256"]}
                      for p in design.parts}
        self.hits = 0
        self.misses = 0

    def number(self, operation, part_a, part_b, compute, *, pose=None):
        key = hashlib.sha256(_encoded({"engine": self.engine, "operation": operation,
                                       "a": self.parts[part_a], "b": self.parts[part_b],
                                       "pose": pose})).hexdigest()
        path = self.directory / (key + ".json")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            payload = {"key": record["key"], "value": record["value"]}
            value = payload["value"]
            if (payload["key"] == key and isinstance(value, (int, float)) and not isinstance(value, bool)
                    and math.isfinite(value) and value >= 0
                    and record["sha256"] == hashlib.sha256(_encoded(payload)).hexdigest()):
                self.hits += 1
                return value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.misses += 1
        value = float(compute())
        if not math.isfinite(value) or value < 0:
            raise ValueError("Invalid OCCT check result: " + operation)
        payload = {"key": key, "value": value}
        record = {**payload, "sha256": hashlib.sha256(_encoded(payload)).hexdigest()}
        fd, temporary = tempfile.mkstemp(prefix=".check-", suffix=".json", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        return value

    def evidence(self):
        return {"enabled": True, "hits": self.hits, "misses": self.misses,
                "engine_sha256": self.engine, "verification_uses_cache": False}
