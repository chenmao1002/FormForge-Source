"""Check the actual binary STL bytes handed to a print supplier; no mesh repair.

Exact float32 vertices are joined. Reports triangle-edge topology and connected
surface components, not self-intersection, wall thickness or printability.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from formforge.stl_topology import inspect, report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    results = [inspect(p) for p in args.files]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report(results),indent=2),encoding='utf-8')
    for r in results:
        print(Path(r['file']).name, 'PASS' if r['passed'] else 'FAIL',
              'boundary',r['boundaryEdges'],'nonmanifold',r['nonmanifoldEdges'],'winding',r['inconsistentWindingEdges'])
    raise SystemExit(0 if all(r['passed'] for r in results) else 1)
