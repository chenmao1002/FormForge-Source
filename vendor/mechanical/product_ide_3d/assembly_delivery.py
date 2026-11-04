"""Immutable vendor evidence and replayable delivery for mechanical assemblies."""
import csv
from pathlib import Path
import shutil

import cadquery as cq

from .delivery import digest, write_json
from .glb import compact_glb


def snapshot_inputs(design, source_root, staging):
    inputs = staging / "inputs"
    inputs.mkdir()
    for part in design.parts:
        if not part.step:
            continue
        source = (source_root / part.step).resolve()
        expected = digest(source)
        if part.step_sha256 and part.step_sha256 != expected:
            raise ValueError("Supplier STEP hash mismatch: " + part.name)
        target = inputs / (part.name + ".step")
        shutil.copy2(source, target)
        if digest(target) != expected:
            raise ValueError("Supplier model changed during snapshot")
        part.step = target.relative_to(staging).as_posix()
        part.step_sha256 = expected
    evidence = []
    for index, filename in enumerate(design.evidence_files):
        source = (source_root / filename).resolve()
        expected = digest(source)
        target = inputs / (f"evidence-{index:02d}-" + source.name)
        shutil.copy2(source, target)
        if digest(target) != expected:
            raise ValueError("Supplier evidence changed during snapshot")
        evidence.append(target.relative_to(staging).as_posix())
    design.evidence_files = evidence


def _color(part, index):
    if part.color is not None:
        return cq.Color(*part.color)
    if part.role == "fastener":
        return cq.Color(0.16, 0.18, 0.22)
    if part.role == "machined":
        return cq.Color(0.19, 0.48, 0.65) if "Al" in part.material else cq.Color(0.64, 0.67, 0.72)
    return cq.Color(0.20, 0.22, 0.27) if "motor" in part.name else cq.Color(0.63, 0.55, 0.31)


def export_assembly_delivery(design, model, output):
    from .assembly_drawings import export_assembly_drawings
    from .viewer import export_viewer
    assembly = cq.Assembly(name=design.name)
    exploded = cq.Assembly(name=design.name + "_exploded")
    for index, (name, shape) in enumerate(model.parts.items()):
        part = model.metadata[name]
        cq.exporters.export(shape, str(output / (name + ".step")))
        if part.role == "machined":
            (output / "cnc-local").mkdir(exist_ok=True)
            cq.exporters.export(model.local_parts[name], str(output / "cnc-local" / (name + ".step")))
        cq.exporters.export(shape, str(output / (name + ".stl")), tolerance=0.05, angularTolerance=0.1)
        color = _color(part, index)
        assembly.add(shape, name=name, color=color)
        shift = ((shape.BoundingBox().zmin + shape.BoundingBox().zmax) / 2) * 0.5
        exploded.add(shape.translate((0, 0, shift)), name=name, color=color)
    assembly.save(str(output / "assembly.step"))
    assembly.save(str(output / "assembly.glb"))
    exploded.save(str(output / "exploded.step"))
    exploded.save(str(output / "exploded.glb"))
    model.measures["preview_compaction"] = compact_glb(output / "assembly.glb")
    compact_glb(output / "exploded.glb")
    pages = export_assembly_drawings(design, model, output, digest(output / "design.json"))
    export_viewer(output / "assembly.glb", output / "viewer.html", design, model)
    write_json(output / "features.json", model.features)
    write_json(output / "component-provenance.json", [
        {"part": p.name, "role": p.role, "model_provenance": p.model_provenance,
         "urls": p.sources, "step_sha256": p.step_sha256,
         "step_solid_indices": p.step_solid_indices, "rotation_deg": p.rotation_deg,
         "rotation_axis": p.rotation_axis, "rotation_axis_angle_deg": p.rotation_axis_angle_deg,
         "position_mm": p.position, "color_srgb": p.color, "notes": p.notes}
        for p in design.parts])
    with (output / "bom.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["part", "quantity", "material", "process", "model_origin", "sources", "note"])
        for part in design.parts:
            writer.writerow([part.name, 1, part.material, "CNC" if part.role == "machined" else "purchased",
                             part.model_provenance, " | ".join(part.sources), " | ".join(part.notes)])
    report = {"schema_version": 1, "kind": design.kind, "name": design.name,
              "cad_verified": all(model.checks.values()), "manufacturing_ready": False,
              "checks": model.checks, "measures_mm": model.measures, "drawing_pages": pages,
              "warnings": model.warnings, "assumptions": design.assumptions, "notes": design.notes,
              "source_hashes": {p.name: digest(p) for p in Path(__file__).parent.glob("*.py")},
              "artifact_hashes": {p.relative_to(output).as_posix(): digest(p)
                                  for p in sorted(output.rglob("*")) if p.is_file()}}
    write_json(output / "report.json", report)
    return report


def verify_assembly(design, output, report):
    from .assembly_cad import build_assembly
    model = build_assembly(design, output)
    checks = dict(model.checks)
    for name, original in model.parts.items():
        imported = cq.importers.importStep(str(output / (name + ".step"))).solids().vals()
        total = sum(s.Volume() for s in imported)
        checks[name + "_step_roundtrip"] = (len(imported) == len(original.Solids())
                                              and all(s.isValid() for s in imported)
                                              and abs(total - original.Volume()) < max(1e-3, original.Volume() * 1e-6))
        bbox = cq.Compound.makeCompound(imported).BoundingBox()
        reference = original.BoundingBox()
        checks[name + "_step_placement"] = all(abs(getattr(bbox, key) - getattr(reference, key)) < 1e-5
                                               for key in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax"))
    imported_assembly = cq.importers.importStep(str(output / "assembly.step")).solids().vals()
    checks["assembly_step_solid_count"] = len(imported_assembly) == sum(len(s.Solids()) for s in model.parts.values())
    checks["recorded_checks_match"] = model.checks == report["checks"]
    return {"passed": all(checks.values()), "checks": checks, "artifact_count": len(report["artifact_hashes"])}
