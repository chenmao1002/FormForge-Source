"""Separate process lets the CLI hard-bound expensive OCCT operations."""
from pathlib import Path
import argparse
import sys
import json

from .cad import build_model
from .delivery import read_design, export_delivery, digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    output = args.output
    before = {p.name: digest(p) for p in Path(__file__).parent.glob("*.py")}
    d = read_design(output/"design.json")
    if d.kind == "rotary_assembly":
        from .assembly_cad import build_assembly
        model = build_assembly(d, output, preflight=True, cache_dir=args.cache_dir, source_hashes=before)
    elif d.kind == "articulated_arm":
        from .arm_cad import build_arm
        model = build_arm(d, output)
    else:
        model = build_model(d, output)
    report = export_delivery(d, model, output)
    if before != report["source_hashes"]:
        raise ValueError("CAD source changed while generating; no delivery published")
    print(json.dumps({"cad_verified": report["cad_verified"], "checks": report["checks"]}))
    return 0 if report["cad_verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
