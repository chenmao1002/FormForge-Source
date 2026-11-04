"""Fresh, replayable articulated arm and print-bed deliveries."""
import csv
import math
from pathlib import Path
import re
import shutil
import struct

import cadquery as cq
import numpy as np

from .delivery import digest, write_json
from .glb import compact_glb


def snapshot_arm_inputs(design, source_root, staging):
    """Archive dimensional evidence alongside the normalized specification."""
    evidence = []
    (staging / "inputs").mkdir()
    for index, filename in enumerate(design.evidence_files):
        source = (source_root / filename).resolve()
        target = staging / "inputs" / (f"evidence-{index:02d}-" + source.name)
        expected = digest(source)
        shutil.copy2(source, target)
        if digest(target) != expected:
            raise ValueError("Arm evidence changed during snapshot")
        evidence.append(target.relative_to(staging).as_posix())
    design.evidence_files = evidence


def _assembly(design, shapes, path):
    assembly = cq.Assembly(name=design.name)
    for part in design.parts:
        assembly.add(shapes[part.name], name=part.name, color=cq.Color(*part.color))
    assembly.save(str(path.with_suffix(".step")))
    assembly.save(str(path.with_suffix(".glb")))
    compact_glb(path.with_suffix(".glb"))


def export_arm_delivery(design, model, output):
    from .arm_cad import print_placement
    from .arm_drawings import export_arm_drawings
    from .arm_viewer import export_arm_viewer

    (output / "parts").mkdir()
    (output / "print").mkdir()
    (output / "poses").mkdir()
    print_manifest = []
    for part in design.parts:
        name = part.name
        cq.exporters.export(model.parts[name], str(output / "parts" / (name + ".step")))
        if part.role == "printed":
            printable = print_placement(part, model.local_parts[name])
            cq.exporters.export(printable, str(output / "print" / (name + ".step")))
            cq.exporters.export(printable, str(output / "print" / (name + ".stl")),
                                tolerance=0.035, angularTolerance=0.1)
            bb = printable.BoundingBox()
            print_manifest.append({"part": name, "label": part.label,
                                   "rotation_deg": part.print_rotation_deg,
                                   "bed_min_z_mm": bb.zmin,
                                   "size_mm": [bb.xlen, bb.ylen, bb.zlen],
                                   "solid_volume_mm3": printable.Volume(),
                                   "material": part.material,
                                   "notes": part.notes})
    _assembly(design, model.parts, output / "assembly")
    for name, shapes in model.poses.items():
        if name != "default":
            _assembly(design, shapes, output / "poses" / name)
    write_json(output / "print-manifest.json", print_manifest)
    write_json(output / "component-provenance.json", [
        {"part": p.name, "label": p.label, "role": p.role, "link": p.link,
         "model_provenance": p.model_provenance, "sources": p.sources,
         "mass_override_g": p.mass_g, "notes": p.notes} for p in design.parts])
    with (output / "bom.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["part", "label", "quantity", "material", "process", "sources", "note"])
        for p in design.parts:
            writer.writerow([p.name, p.label, 1, p.material,
                             "FDM" if p.role == "printed" else "purchased",
                             " | ".join(p.sources), " | ".join(p.notes)])
    pages = export_arm_drawings(design, model, output)
    export_arm_viewer(output / "assembly.glb", output / "viewer.html", design, model)
    report = {"schema_version": 1, "kind": design.kind, "name": design.name,
              "cad_verified": all(model.checks.values()), "manufacturing_ready": False,
              "continuous_sweep_verified": False, "physical_load_verified": False,
              "checks": model.checks, "measures_mm": model.measures,
              "drawing_pages": pages, "warnings": model.warnings,
              "assumptions": design.assumptions, "notes": design.notes,
              "source_hashes": {p.name: digest(p) for p in Path(__file__).parent.glob("*.py")},
              "artifact_hashes": {p.relative_to(output).as_posix(): digest(p)
                                  for p in sorted(output.rglob("*")) if p.is_file()}}
    write_json(output / "report.json", report)
    return report


def _geometric_match(reference, candidate):
    tolerance = max(1e-3, reference.Volume() * 1e-6)
    if not candidate.isValid() or abs(candidate.Volume() - reference.Volume()) >= tolerance:
        return False
    bbox, expected = candidate.BoundingBox(), reference.BoundingBox()
    if any(abs(getattr(bbox, k) - getattr(expected, k)) >= 1e-5
           for k in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")):
        return False
    # Volume and envelope alone cannot detect a relocated hole of equal size.
    return reference.cut(candidate).Volume() + candidate.cut(reference).Volume() < tolerance


def _roundtrip(shape, path):
    imported = cq.importers.importStep(str(path)).solids().vals()
    if not imported:
        return False
    rebuilt = cq.Compound.makeCompound(imported)
    return (len(imported) == len(shape.Solids()) and all(s.isValid() for s in imported)
            and _geometric_match(shape, rebuilt))


def _print_stl_matches(shape, path):
    """Check tessellated print coordinates and mesh volume against print STEP."""
    raw = path.read_bytes()
    count = struct.unpack_from("<I", raw, 80)[0] if len(raw) >= 84 else 0
    if count and len(raw) == 84 + count * 50:
        record = np.dtype({"names": ["vertices"], "formats": [("<f4", (3, 3))],
                           "offsets": [12], "itemsize": 50})
        triangles = np.asarray(np.frombuffer(raw, dtype=record, count=count, offset=84)["vertices"], dtype=float)
    else:
        tokens = re.findall(rb"\bvertex\s+(\S+)\s+(\S+)\s+(\S+)", raw)
        if not tokens or len(tokens) % 3:
            return False
        try:
            triangles = np.asarray(tokens, dtype=float).reshape(-1, 3, 3)
        except ValueError:
            return False
    if not np.isfinite(triangles).all():
        return False
    points = triangles.reshape(-1, 3)
    bbox = shape.BoundingBox()
    expected = np.asarray([[bbox.xmin, bbox.ymin, bbox.zmin], [bbox.xmax, bbox.ymax, bbox.zmax]])
    actual = np.stack([points.min(axis=0), points.max(axis=0)])
    volume = abs(np.einsum("ij,ij->i", triangles[:, 0],
                          np.cross(triangles[:, 1], triangles[:, 2])).sum() / 6)
    return bool(np.max(np.abs(actual - expected)) <= 0.075
                and abs(actual[0, 2]) < 1e-5
                and abs(volume - shape.Volume()) <= max(0.05, shape.Volume() * 0.02))


def _record_matches(expected, recorded):
    """Replay numeric engineering evidence with explicit floating tolerances."""
    if isinstance(expected, dict):
        return (isinstance(recorded, dict) and expected.keys() == recorded.keys()
                and all(_record_matches(value, recorded[key]) for key, value in expected.items()))
    if isinstance(expected, (list, tuple)):
        return (isinstance(recorded, (list, tuple)) and len(expected) == len(recorded)
                and all(_record_matches(a, b) for a, b in zip(expected, recorded)))
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return (isinstance(recorded, (int, float)) and not isinstance(recorded, bool)
                and math.isfinite(recorded) and math.isclose(expected, recorded, rel_tol=1e-7, abs_tol=1e-6))
    return type(expected) is type(recorded) and expected == recorded


def verify_arm(design, output, report):
    from .arm_cad import build_arm, print_placement
    model = build_arm(design, output)
    checks = dict(model.checks)
    for part in design.parts:
        name = part.name
        checks[name + "_step_roundtrip_and_placement"] = _roundtrip(
            model.parts[name], output / "parts" / (name + ".step"))
        if part.role == "printed":
            printable = print_placement(part, model.local_parts[name])
            checks[name + "_print_step_roundtrip_and_placement"] = _roundtrip(
                printable, output / "print" / (name + ".step"))
            checks[name + "_print_bed_zero"] = abs(printable.BoundingBox().zmin) < 1e-6
            checks[name + "_print_stl_bounds_and_volume"] = _print_stl_matches(
                printable, output / "print" / (name + ".stl"))
    for name, shapes in model.poses.items():
        path = output / "assembly.step" if name == "default" else output / "poses" / (name + ".step")
        # Compare every exported posed solid against its rebuilt volume and box,
        # so swapping a pose file for the default file cannot pass verification.
        solids = cq.importers.importStep(str(path)).solids().vals()
        remaining = list(solids)
        matched = True
        for shape in shapes.values():
            for reference in shape.Solids():
                candidate = next((s for s in remaining if _geometric_match(reference, s)), None)
                if candidate is None or not candidate.isValid():
                    matched = False
                    break
                remaining.remove(candidate)
        checks[name + "_assembly_step_roundtrip_and_pose"] = matched and not remaining
    checks["recorded_checks_match"] = model.checks == report["checks"]
    checks["recorded_measures_match"] = _record_matches(model.measures, report["measures_mm"])
    checks["recorded_scope_and_status_match"] = (
        report.get("cad_verified") is all(model.checks.values())
        and report.get("manufacturing_ready") is False
        and report.get("continuous_sweep_verified") is False
        and report.get("physical_load_verified") is False
        and report.get("kind") == design.kind and report.get("name") == design.name
        and report.get("assumptions") == design.assumptions and report.get("notes") == design.notes)
    return {"passed": all(checks.values()), "checks": checks,
            "artifact_count": len(report["artifact_hashes"])}
