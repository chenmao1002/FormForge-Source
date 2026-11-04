"""Immutable-input, fresh-output CAD delivery with replayable checks."""
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .spec import Design


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def read_design(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if value.get("kind") == "rotary_assembly":
        from .assembly_spec import AssemblyDesign
        return AssemblyDesign.model_validate(value)
    if value.get("kind") == "articulated_arm":
        from .arm_spec import ArmDesign
        return ArmDesign.model_validate(value)
    return Design.model_validate(value)


def _fresh_sibling(path, suffix):
    first_revision = 1
    if suffix == "r":
        # Continue the delivery series; failed diagnostics keep the full name.
        match = re.fullmatch(r"(.+)-r([0-9]{3,})", path.name)
        if match:
            path = path.with_name(match[1])
            first_revision = int(match[2]) + 1
    for revision in range(first_revision, 100000):
        candidate = path.with_name(f"{path.name}-{suffix}{revision:03d}")
        if not candidate.exists():
            return candidate
    raise ValueError("No fresh output revision available")


def generate(spec_path, output, timeout=300, *, next_revision=False, keep_failed=False, localization_path=None, cache_dir=None):
    spec_path, output = Path(spec_path).resolve(), Path(output).resolve()
    if output.exists():
        if next_revision:
            output = _fresh_sibling(output, "r")
        else:
            raise ValueError("Output already exists; select a fresh directory or use --next-revision")
    d = read_design(spec_path)
    if d.kind == "rotary_assembly":
        from .assembly_preflight import validate_drawing_requirements
        from .assembly_kinematics import validate_kinematics
        validate_drawing_requirements(d)
        validate_kinematics(d)
        if localization_path:
            from .localization import Localization
            Localization.read(localization_path).validate_design(d)
    elif localization_path:
        raise ValueError("Localization preflight supports rotary_assembly only")
    if cache_dir is not None:
        cache_dir = Path(cache_dir).resolve()
        if d.kind != "rotary_assembly":
            raise ValueError("Collision check caching supports rotary_assembly only")
        if cache_dir == output or cache_dir.is_relative_to(output):
            raise ValueError("Cache directory must be outside the fresh delivery output")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".product3d-", dir=output.parent) as temporary:
        staging = Path(temporary)
        if d.kind == "rotary_assembly":
            from .assembly_delivery import snapshot_inputs
            snapshot_inputs(d, spec_path.parent, staging)
        elif d.kind == "articulated_arm":
            from .arm_delivery import snapshot_arm_inputs
            snapshot_arm_inputs(d, spec_path.parent, staging)
        elif d.board and d.board.step:
            source = (spec_path.parent/d.board.step).resolve()
            source_hash = digest(source)
            if d.board.step_sha256 and source_hash != d.board.step_sha256:
                raise ValueError("PCB STEP hash mismatch")
            (staging/"inputs").mkdir()
            shutil.copy2(source, staging/"inputs"/"pcb.step")
            if digest(staging/"inputs"/"pcb.step") != source_hash:
                raise ValueError("PCB changed while copying")
            d.board.step = "inputs/pcb.step"
            d.board.step_sha256 = source_hash
        write_json(staging/"design.json", d.model_dump())
        try:
            try:
                worker_args = [sys.executable, "-m", "product_ide_3d._worker", str(staging)]
                if cache_dir is not None:
                    worker_args.extend(["--cache-dir", str(cache_dir)])
                run = subprocess.run(worker_args,
                                     capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
            except subprocess.TimeoutExpired as error:
                if keep_failed:
                    def decoded(value):
                        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
                    (staging/"worker-stdout.txt").write_text(decoded(error.stdout), encoding="utf-8")
                    (staging/"worker-stderr.txt").write_text(decoded(error.stderr), encoding="utf-8")
                raise ValueError(f"CAD worker exceeded {timeout}s") from None
            if run.returncode not in (0, 2) or not (staging/"report.json").is_file():
                if keep_failed:
                    (staging/"worker-stdout.txt").write_text(run.stdout, encoding="utf-8")
                    (staging/"worker-stderr.txt").write_text(run.stderr, encoding="utf-8")
                message = (run.stderr or run.stdout)[-3000:]
                raise ValueError("CAD worker failed: "+message)
            report = json.loads((staging/"report.json").read_text(encoding="utf-8"))
        except (ValueError, KeyboardInterrupt) as error:
            if keep_failed:
                failed_output = _fresh_sibling(output, "failed")
                write_json(staging/"failure.json", {"complete": False, "cad_verified": False,
                           "error": str(error) or "Cancelled", "intended_output": str(output)})
                os.rename(staging, failed_output)
                if isinstance(error, ValueError):
                    raise ValueError(f"{error}\nIncomplete diagnostic output retained: {failed_output}") from error
            if isinstance(error, ValueError):
                raise ValueError(f"{error}\nPartial output discarded; use --keep-failed to retain diagnostics.") from error
            raise
        if output.exists():
            raise ValueError("Output appeared during generation; no files overwritten")
        # Rename into the final directory on the same filesystem. Never merge.
        for attempt in range(10):
            try:
                os.rename(staging, output)
                break
            except PermissionError:
                # Windows indexers/antivirus can retain a just-written GLB/PDF
                # briefly even after the worker exits. No outputs are replaced.
                if attempt == 9 or output.exists():
                    raise
                time.sleep(0.3)
    return {**report, "output": str(output)}


def verify(output):
    output = Path(output).resolve()
    report = json.loads((output/"report.json").read_text(encoding="utf-8"))
    for name, expected in report["artifact_hashes"].items():
        path = (output/name).resolve()
        if not path.is_relative_to(output) or digest(path) != expected:
            raise ValueError("Delivery artifact changed: "+name)
    d = read_design(output/"design.json")
    if d.kind == "rotary_assembly":
        from .assembly_delivery import verify_assembly
        return verify_assembly(d, output, report)
    if d.kind == "articulated_arm":
        from .arm_delivery import verify_arm
        return verify_arm(d, output, report)
    from .cad import build_model
    import cadquery as cq
    rebuilt = build_model(d, output)
    valid = dict(rebuilt.checks)
    for name, part in rebuilt.parts.items():
        imported = cq.importers.importStep(str(output/(name+".step")))
        solids = imported.solids().vals()
        valid[name+"_step_roundtrip"] = len(solids) == 1 and solids[0].isValid() and abs(solids[0].Volume()-part.Volume()) < max(1e-3, part.Volume()*1e-6)
    # The report cannot claim checks that deterministic reconstruction disagrees with.
    valid["recorded_checks_match"] = rebuilt.checks == report["checks"]
    return {"passed": all(valid.values()), "checks": valid, "artifact_count": len(report["artifact_hashes"])}


def export_delivery(d, model, output):
    if d.kind == "rotary_assembly":
        from .assembly_delivery import export_assembly_delivery
        return export_assembly_delivery(d, model, output)
    if d.kind == "articulated_arm":
        from .arm_delivery import export_arm_delivery
        return export_arm_delivery(d, model, output)
    import cadquery as cq
    from .drawings import export_drawings, export_cut_pattern
    assembly = cq.Assembly(name=d.name)
    for index, (name, part) in enumerate(model.parts.items()):
        cq.exporters.export(part, str(output/(name+".step")))
        print_part = part
        if name == "lid":
            # Outside face down, lip points upward; print bed Z=0.
            print_part = print_part.rotate((0, 0, 0), (1, 0, 0), 180)
        print_part = print_part.translate((0, 0, -print_part.BoundingBox().zmin))
        cq.exporters.export(print_part, str(output/(name+".stl")), tolerance=0.05, angularTolerance=0.1)
        assembly.add(part, name=name, color=cq.Color(0.18+index*0.4, 0.3+index*0.2, 0.42+index*0.2))
    if model.board:
        assembly.add(model.board, name="pcb", color=cq.Color(0.12, 0.5, 0.28))
    assembly.save(str(output/"assembly.step"))
    assembly.save(str(output/"assembly.glb"))
    from .glb import compact_glb
    model.measures["preview_compaction"] = compact_glb(output/"assembly.glb")
    cq.exporters.export(cq.Compound.makeCompound(list(model.parts.values())), str(output/"isometric.svg"),
                        opt={"width": 1000, "height": 750, "marginLeft": 30, "marginTop": 30, "showAxes": False})
    pages = export_drawings(d, model, output, digest(output/"design.json"))
    export_cut_pattern(d, model, output)
    write_json(output/"features.json", model.features)
    with (output/"bom.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.writer(stream)
        writer.writerow(["part", "quantity", "material", "process", "note"])
        for name in model.parts:
            writer.writerow([name, 1, d.process.material, d.process.type, "Supplier/physical review pending"])
        if d.kind == "enclosure":
            writer.writerow(["lid_fastener", 4, "steel", "purchased", f"M{d.screw_diameter:g} candidate; qualify length/thread/head"])
            if d.board:
                writer.writerow(["PCB", 1, "customer supplied", "assembly", model.board_source])
                writer.writerow(["PCB_fastener", len(d.board.mounting_holes), "steel", "purchased", "Select to suit recorded post pilot and PCB holes"])
    report = {"schema_version": 1, "name": d.name, "cad_verified": all(model.checks.values()),
              "manufacturing_ready": False, "checks": model.checks, "measures_mm": model.measures,
              "board_evidence": model.board_source, "drawing_pages": pages,
              "warnings": model.warnings, "assumptions": d.assumptions,
              "source_hashes": {p.name: digest(p) for p in Path(__file__).parent.glob("*.py")},
              "artifact_hashes": {p.relative_to(output).as_posix(): digest(p)
                                  for p in sorted(output.rglob("*")) if p.is_file()}}
    write_json(output/"report.json", report)
    return report
