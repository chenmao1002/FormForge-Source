"""Exact binary STL edge topology, without mesh repair or printability claims."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import struct


CHECKER_VERSION = 'binary-stl-edge-topology/1'
SCOPE = ('Exact binary STL triangle-edge topology; not self-intersection, wall thickness, '
         'strength, print orientation or supplier approval.')
_RESULTS = {}


def inspect_bytes(raw, name):
    """Inspect the same float32 bytes whose SHA is recorded in the result."""
    sha = hashlib.sha256(raw).hexdigest()
    key = (CHECKER_VERSION, sha)
    cached = _RESULTS.get(key)
    if cached is not None:
        return {**deepcopy(cached), 'file': str(name)}
    result = _inspect_topology(raw, name, sha)
    # Reuse the same bytes across quote/prepare/submit, retaining reports, not meshes.
    _RESULTS[key] = deepcopy(result)
    if len(_RESULTS) > 128:
        _RESULTS.pop(next(iter(_RESULTS)), None)
    return result


def _inspect_topology(raw, name, sha):
    if len(raw) < 84:
        raise ValueError(f'Not a binary STL: {name}')
    count = struct.unpack_from('<I', raw, 80)[0]
    if len(raw) != 84 + 50 * count or not count:
        raise ValueError(f'Expected nonempty binary STL: {name}')
    edges = defaultdict(list)
    parent = list(range(count))
    degenerate = nonfinite = 0
    volume6 = 0.0

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(count):
        values = struct.unpack_from('<12f', raw, 84 + 50 * i)
        a, b, c = [tuple(values[j:j + 3]) for j in (3, 6, 9)]
        if not all(math.isfinite(v) for v in values):
            nonfinite += 1
            continue
        u = tuple(b[j] - a[j] for j in range(3))
        v = tuple(c[j] - a[j] for j in range(3))
        cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
        if len({a, b, c}) != 3 or sum(x*x for x in cross) == 0:
            degenerate += 1
        volume6 += a[0]*(b[1]*c[2]-b[2]*c[1]) + a[1]*(b[2]*c[0]-b[0]*c[2]) + a[2]*(b[0]*c[1]-b[1]*c[0])
        for x, y in ((a, b), (b, c), (c, a)):
            key = (x, y) if x < y else (y, x)
            edges[key].append((i, 1 if x < y else -1))
    boundary = nonmanifold = inconsistent = 0
    examples = []
    for key, faces in edges.items():
        boundary += len(faces) == 1
        nonmanifold += len(faces) > 2
        inconsistent += len(faces) == 2 and sum(sign for _, sign in faces) != 0
        if len(faces) != 2 and len(examples) < 12:
            examples.append({'vertices': key, 'incidentFaces': len(faces)})
        for face, _ in faces[1:]:
            parent[root(face)] = root(faces[0][0])
    components = len({root(i) for i in range(count)})
    passed = not (boundary or nonmanifold or inconsistent or degenerate or nonfinite) and components == 1 and volume6 > 0
    return dict(file=str(name),sha256=sha,triangles=count,
                boundaryEdges=boundary,nonmanifoldEdges=nonmanifold,inconsistentWindingEdges=inconsistent,
                degenerateTriangles=degenerate,nonfiniteTriangles=nonfinite,surfaceComponents=components,
                signedVolumeMm3=volume6/6,problemEdgeExamples=examples,passed=passed)


def inspect(path):
    path = Path(path)
    return inspect_bytes(path.read_bytes(), str(path))


def report(files, **binding):
    """Attach caller-owned version/identity binding without upgrading the scope."""
    passed = bool(files) and all(row['passed'] for row in files)
    return dict(**binding, checkerVersion=CHECKER_VERSION, files=files, passed=passed,
                localTopologyPassed=passed, supplierReviewVerified=False,
                manufacturingApproved=False, scope=SCOPE)
