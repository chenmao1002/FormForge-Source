"""Drawing acceptance checks before assembly collision sampling and export.

This deliberately uses the renderer's material witnesses on the real local
solids. A JSON envelope alone cannot establish whether a datum is on material.
"""
from pathlib import Path


class AssemblyPreflightError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("Assembly drawing preflight failed:\n" + "\n".join(
            f"- {issue['part']}/{issue['feature']}: {issue['message']} "
            f"{issue['suggestion']}" for issue in issues))


def validate_drawing_requirements(design):
    issues = []
    for part in design.parts:
        if part.role == "machined" and not part.engineering_features:
            issues.append({"part": part.name, "feature": "engineering_features",
                           "message": "CNC part requires geometry-linked precision features.",
                           "suggestion": "Declare a controlled size or datum on the real solid; use role=fastener only for an actual fastener."})
    if issues:
        raise AssemblyPreflightError(issues)


def validate_drawing_model(design, model):
    import cadquery as cq
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from .assembly_drawings import (annotation_entries, _face_material_witness,
                                    _diameter_material_witness, axial_section)
    validate_drawing_requirements(design)
    issues = []
    for check, passed in model.checks.items():
        if passed:
            continue
        part_name = next((p.name for p in sorted(design.parts, key=lambda p: len(p.name), reverse=True)
                          if check.startswith(p.name + "_")), "assembly")
        feature = check[len(part_name) + 1:] if part_name != "assembly" else check
        issues.append({"part": part_name, "feature": feature,
                       "message": "Local CAD geometry check failed: " + check + ".",
                       "suggestion": "Check solid continuity, hole/cut coordinates and controlled nominal sizes before assembly sampling."})
    count = 0
    for part in design.parts:
        if part.role != "machined":
            continue
        shape = model.local_parts[part.name]
        section_station = None
        try:
            _, section_station = axial_section(shape)
        except ValueError as error:
            issues.append({"part": part.name, "feature": "axial_section",
                           "message": str(error),
                           "suggestion": "Check the local solid and select a section plane that crosses actual material."})
        try:
            annotation_entries(part, shape)
        except ValueError as error:
            issues.append({"part": part.name, "feature": "machining_annotations",
                           "message": str(error),
                           "suggestion": "Check overlapping stock segments and the surviving controlled surface."})
        for feature in part.engineering_features:
            count += 1
            hint = feature.view_hint
            if hint == "auto":
                hint = "top" if feature.kind == "hole_pattern" else "section"
            try:
                # These are precisely the witnesses used by the section renderer.
                if hint == "section" and feature.kind == "face":
                    _face_material_witness(shape, feature, section_station)
                elif hint == "section" and feature.kind == "diameter":
                    _diameter_material_witness(shape, feature)
                elif feature.kind in ("face", "diameter"):
                    # Top/front views draw the declared point directly. They
                    # must not bypass the trimmed material surface check.
                    vertex = cq.Vertex.makeVertex(*feature.anchor_local)
                    candidates = [face for face in shape.Faces()
                                  if face.distance(vertex) < 1e-7]
                    if feature.kind == "face":
                        valid = any(face.geomType() == "PLANE" for face in candidates)
                    else:
                        valid = any(face.geomType() == "CYLINDER" and
                                    abs(2 * BRepAdaptor_Surface(face.wrapped, True).Cylinder().Radius()
                                        - feature.nominal) < 1e-6 for face in candidates)
                    if not valid:
                        raise ValueError(f"{feature.kind.title()} {feature.id} anchor is not on its actual controlled material surface")
            except ValueError as error:
                issues.append({"part": part.name, "feature": feature.id,
                               "message": str(error),
                               "suggestion": f"Check anchor_local={feature.anchor_local}, its Z plane, and remaining material after cuts; select a view that exposes this surface."})
    if issues:
        raise AssemblyPreflightError(issues)
    return {"passed": True, "machined_parts": sum(p.role == "machined" for p in design.parts),
            "precision_features": count, "scope": "real local geometry and drawing witnesses; assembly collision sampling excluded"}


def preflight_assembly(design, root):
    from .assembly_cad import build_assembly_parts
    from .assembly_kinematics import validate_kinematics
    validate_drawing_requirements(design)
    validate_kinematics(design)
    model = build_assembly_parts(design, Path(root))
    result = validate_drawing_model(design, model)
    return result, model
