"""Real OCCT axial parts, supplier STEP geometry, fits and sampled motion checks."""
from dataclasses import dataclass, field
import hashlib
import itertools
import math
from pathlib import Path

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface

from .assembly_spec import AssemblyDesign, AxialPart
from .assembly_kinematics import motion_axis_evidence, record_kinematics
from .assembly_mass import record_mass_properties
from .geometry import box_between, collision
from .assembly_primitives import place_shape, mirror_step_shape

# A long-lived Python caller must reload changed implementation code before
# writing cache entries under a new engine fingerprint. Workers additionally
# pass their immutable start-of-run snapshot.
_IMPORTED_SOURCE_HASHES = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in Path(__file__).parent.glob("*.py")}


@dataclass
class AssemblyModel:
    parts: dict = field(default_factory=dict)
    local_parts: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    features: list = field(default_factory=list)
    checks: dict = field(default_factory=dict)
    measures: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


def bounds_overlap(a, b):
    """Conservative broad phase: even thin material overlaps reach OCCT."""
    return all(hi >= lo for lo, hi in (
        (max(a.xmin, b.xmin), min(a.xmax, b.xmax)),
        (max(a.ymin, b.ymin), min(a.ymax, b.ymax)),
        (max(a.zmin, b.zmin), min(a.zmax, b.zmax))))


def _numeric_check(model, operation, name_a, name_b, compute, *, pose=None):
    cache = getattr(model, "_check_cache", None)
    return cache.number(operation, name_a, name_b, compute, pose=pose) if cache else compute()


def _cylinder_diameter(face):
    # A Boolean cut or STEP import may wrap a cylinder in a rectangular
    # trimmed surface. Use OCCT's surface adaptor to recover the basis cylinder.
    return 2 * BRepAdaptor_Surface(face.wrapped, True).Cylinder().Radius()


def _cylinder(diameter, z0, z1, x=0, y=0):
    return cq.Solid.makeCylinder(diameter / 2, z1 - z0, cq.Vector(x, y, z0))


def _axial_profile(start_diameter, end_diameter, z0, z1):
    if abs(start_diameter - end_diameter) < 1e-12:
        return _cylinder(start_diameter, z0, z1)
    return cq.Solid.makeCone(start_diameter / 2, end_diameter / 2, z1 - z0,
                             cq.Vector(0, 0, z0))


def _import_part(part, root):
    path = (root / part.step).resolve()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if part.step_sha256 and sha != part.step_sha256:
        raise ValueError("Supplier STEP hash mismatch: " + part.name)
    all_shapes = cq.importers.importStep(str(path)).solids().vals()
    if part.step_solid_indices is not None:
        if any(i >= len(all_shapes) for i in part.step_solid_indices):
            raise ValueError("STEP solid index outside source assembly: " + part.name)
        shapes = [all_shapes[i] for i in part.step_solid_indices]
    else:
        shapes = all_shapes
    if not shapes or any(not s.isValid() or s.Volume() <= 0 for s in shapes):
        raise ValueError("Supplier STEP has invalid/empty solids: " + part.name)
    shape = shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
    return mirror_step_shape(part, shape), sha, len(all_shapes)


def _reconstruct_part(part, model):
    pieces = []
    for segment in part.segments:
        outer_end = segment.outer_diameter if segment.outer_diameter_end is None else segment.outer_diameter_end
        inner_end = segment.inner_diameter if segment.inner_diameter_end is None else segment.inner_diameter_end
        shape = _axial_profile(segment.outer_diameter, outer_end, segment.z0, segment.z1)
        if segment.inner_diameter or inner_end:
            shape = shape.cut(_axial_profile(segment.inner_diameter, inner_end, segment.z0, segment.z1))
        pieces.append(shape)
    for box in part.boxes:
        x, y = box.center_xy
        piece = box_between(x - box.length / 2, x + box.length / 2,
                            y - box.width / 2, y + box.width / 2, box.z0, box.z1).val()
        if box.angle_deg:
            piece = piece.rotate((x, y, 0), (x, y, 1), box.angle_deg)
        pieces.append(piece)
    for profile in part.profiles:
        if profile.operation == "add":
            pieces.append(cq.Workplane("XY", origin=(0, 0, profile.z0))
                          .polyline(profile.points_xy).close().extrude(profile.z1 - profile.z0).val())
    shape = pieces[0]
    for piece in pieces[1:]:
        shape = shape.fuse(piece)
    shape = shape.clean()
    profile_operations = []
    for profile in part.profiles:
        if profile.operation != "cut":
            continue
        cutter = (cq.Workplane("XY", origin=(0, 0, profile.z0))
                  .polyline(profile.points_xy).close().extrude(profile.z1 - profile.z0).val())
        before = shape.Volume()
        shape = shape.cut(cutter).clean()
        removed = before - shape.Volume()
        model.checks[f"{part.name}_{profile.name}_profile_cut"] = removed > 1e-6 and collision(shape, cutter) < 1e-5
        profile_operations.append({**profile.model_dump(), "removed_volume_mm3": removed})
    holes = []
    for pattern in part.holes:
        removed_volumes = []
        all_clear = True
        for index, (x, y) in enumerate(pattern.centers(), 1):
            cutter = _cylinder(pattern.diameter, pattern.z0, pattern.z1, x, y)
            if pattern.counterbore_diameter is not None:
                bottom = (pattern.z1 - pattern.counterbore_depth if pattern.counterbore_side == "top"
                          else pattern.z0)
                cutter = cutter.fuse(_cylinder(pattern.counterbore_diameter, bottom,
                                               bottom + pattern.counterbore_depth, x, y))
            if pattern.countersink_diameter is not None:
                depth = pattern.countersink_depth()
                if pattern.countersink_side == "top":
                    cone = cq.Solid.makeCone(pattern.diameter / 2, pattern.countersink_diameter / 2,
                                             depth, cq.Vector(x, y, pattern.z1 - depth))
                else:
                    cone = cq.Solid.makeCone(pattern.countersink_diameter / 2, pattern.diameter / 2,
                                             depth, cq.Vector(x, y, pattern.z0))
                cutter = cutter.fuse(cone)
            before = shape.Volume()
            shape = shape.cut(cutter).clean()
            removed = before - shape.Volume()
            remaining = collision(shape, cutter)
            removed_volumes.append(removed)
            all_clear = all_clear and removed > 1e-6 and remaining < 1e-5
        key = part.name + "_" + pattern.name + "_holes_cut"
        model.checks[key] = all_clear
        holes.append({"name": pattern.name, "count": len(pattern.centers()),
                      "centers_local_mm": pattern.centers(), "removed_volume_mm3": removed_volumes,
                      "countersink_full_depth_mm": pattern.countersink_depth()})
        if pattern.countersink_diameter is not None and pattern.countersink_depth() > pattern.z1 - pattern.z0:
            model.warnings.append(part.name + "/" + pattern.name +
                                  ": full countersink is deeper than this part's hole interval; a flush head needs matching relief in the adjoining part or a qualified special head.")
        if pattern.kind == "tap":
            model.warnings.append(part.name + "/" + pattern.name + ": " + pattern.thread +
                                  " is a tapping requirement; nominal CAD contains pilot bores, not thread teeth.")
    radial_holes = []
    for hole in part.radial_holes:
        theta = math.radians(hole.angle_deg)
        dx, dy = math.cos(theta), math.sin(theta)
        cutter = cq.Solid.makeCylinder(hole.diameter / 2, hole.outer_radius - hole.inner_radius,
                                       cq.Vector(hole.inner_radius * dx, hole.inner_radius * dy, hole.z),
                                       cq.Vector(dx, dy, 0))
        before = shape.Volume()
        shape = shape.cut(cutter).clean()
        removed = before - shape.Volume()
        model.checks[f"{part.name}_{hole.name}_radial_hole_cut"] = removed > 1e-6 and collision(shape, cutter) < 1e-5
        radial_holes.append({**hole.model_dump(), "removed_volume_mm3": removed})
        if hole.kind == "tap":
            model.warnings.append(part.name + "/" + hole.name + ": " + hole.thread +
                                  " is a radial tapping requirement; CAD contains only the pilot bore.")
    keyways = []
    for keyway in part.keyways:
        bottom_x = keyway.shaft_diameter / 2 - keyway.depth if keyway.side == "external" else 0
        radial_length = keyway.depth + 1 if keyway.side == "external" else keyway.shaft_diameter / 2 + keyway.depth
        plane = cq.Plane(origin=(bottom_x, 0, 0), xDir=(0, 1, 0), normal=(1, 0, 0))
        profile = cq.Workplane(plane).center(0, (keyway.z0 + keyway.z1) / 2)
        profile = (profile.rect(keyway.width, keyway.z1 - keyway.z0) if keyway.end_style == "square"
                   else profile.slot2D(keyway.z1 - keyway.z0, keyway.width, angle=90))
        cutter = (profile.extrude(radial_length).val()
                  .rotate((0, 0, 0), (0, 0, 1), keyway.angle_deg))
        cylinder_sizes = [_cylinder_diameter(face)
                          for face in shape.Faces() if face.geomType() == "CYLINDER"]
        before = shape.Volume()
        shape = shape.cut(cutter).clean()
        removed = before - shape.Volume()
        remaining = collision(shape, cutter)
        model.checks[f"{part.name}_{keyway.name}_keyway_cut"] = (
            removed > 1e-6 and remaining < 1e-5
            and any(abs(d - keyway.shaft_diameter) < 1e-4 for d in cylinder_sizes))
        model.checks[f"{part.name}_{keyway.name}_shaft_not_severed"] = len(shape.Solids()) == 1
        if keyway.side == "internal":
            # Retain the original outward cylindrical boundary over the slot.
            retained_outer = [face for face in shape.Faces() if face.geomType() == "CYLINDER"
                              and _cylinder_diameter(face) > keyway.shaft_diameter + 2 * keyway.depth
                              and face.BoundingBox().zmin <= keyway.z0 + 1e-5
                              and face.BoundingBox().zmax >= keyway.z1 - 1e-5]
            model.checks[f"{part.name}_{keyway.name}_outer_wall_retained"] = bool(retained_outer)
        keyways.append({**keyway.model_dump(), "removed_volume_mm3": removed})
    edge_operations = []
    for treatment in part.edge_treatments:
        bounds = shape.BoundingBox()
        if treatment.selector == "all":
            edges = shape.Edges()
        elif treatment.selector == "vertical":
            edges = [edge for edge in shape.Edges() if edge.geomType() == "LINE"
                     and edge.BoundingBox().xlen < 1e-6 and edge.BoundingBox().ylen < 1e-6]
        else:
            target = bounds.zmax if treatment.selector == "top" else bounds.zmin
            edges = [edge for edge in shape.Edges() if edge.BoundingBox().zlen < 1e-6
                     and abs(edge.BoundingBox().zmin - target) < 1e-6]
        if not edges:
            raise ValueError(f"Part {part.name} edge treatment {treatment.name}: selector {treatment.selector} found no edges")
        before = shape.Volume()
        try:
            workplane = cq.Workplane(obj=shape).newObject(edges)
            shape = (workplane.fillet(treatment.size_mm) if treatment.kind == "fillet"
                     else workplane.chamfer(treatment.size_mm)).val().clean()
        except Exception as exc:
            raise ValueError(f"Part {part.name} edge treatment {treatment.name}: {treatment.kind} {treatment.size_mm:g} mm failed; reduce size or select fewer edges") from exc
        changed = abs(before - shape.Volume())
        model.checks[f"{part.name}_{treatment.name}_edge_treatment"] = changed > 1e-6 and shape.isValid()
        edge_operations.append({**treatment.model_dump(), "selected_edge_count": len(edges),
                                "volume_change_mm3": shape.Volume() - before})
    model.measures[part.name + "_primitive_operations"] = {
        "profile_cuts": profile_operations, "edge_treatments": edge_operations}
    return shape, holes, keyways, radial_holes


def _feature(model, part_name, feature_id):
    return next(f for f in model.metadata[part_name].engineering_features if f.id == feature_id)


def _record_diameter_features(part, shape, model):
    # A drawing/fit number must correspond to actual cylindrical geometry.
    # This checks nominal size only; the tolerance and finish remain proposals.
    diameters = [_cylinder_diameter(face)
                 for face in shape.Faces() if face.geomType() == "CYLINDER"]
    for feature in part.engineering_features:
        if feature.kind == "diameter":
            model.checks[f"{part.name}_{feature.id}_nominal_diameter_in_cad"] = any(
                abs(d - feature.nominal) < 1e-4 for d in diameters)


def _record_fits(design, model):
    values = []
    for fit in design.fits:
        shaft = _feature(model, fit.shaft_part, fit.shaft_feature)
        hole = _feature(model, fit.hole_part, fit.hole_feature)
        shaft_low, shaft_high = shaft.limits()
        hole_low, hole_high = hole.limits()
        low, high = hole_low - shaft_high, hole_high - shaft_low
        passed = low >= fit.min_clearance_mm - 1e-9
        if fit.max_clearance_mm is not None:
            passed = passed and high <= fit.max_clearance_mm + 1e-9
        model.checks["fit_" + fit.name] = passed
        values.append({"name": fit.name, "shaft_part": fit.shaft_part, "hole_part": fit.hole_part,
                       "shaft_limits_mm": [shaft_low, shaft_high], "hole_limits_mm": [hole_low, hole_high],
                       "min_diametric_clearance_mm": low, "max_diametric_clearance_mm": high,
                       "required_min_diametric_clearance_mm": fit.min_clearance_mm,
                       "required_max_diametric_clearance_mm": fit.max_clearance_mm,
                       "basis": "proposed numeric size intervals; negative clearance denotes intended interference; manufactured GD&T/coating/temperature excluded"})
    model.measures["fits"] = values


def _record_motion(design, model):
    groups = []
    bounds = {name: shape.BoundingBox() for name, shape in model.parts.items()}
    for group in design.moving_groups:
        axis_association = motion_axis_evidence(group, model.metadata)
        fixed = {name: shape for name, shape in model.parts.items() if name not in group.parts}
        start = group.axis_point
        end = tuple(start[i] + group.axis_direction[i] for i in range(3))
        samples = []
        for index, angle in enumerate(group.angles_deg):
            collisions = []
            for name in group.parts:
                moved = model.parts[name].rotate(start, end, angle)
                moved_bounds = moved.BoundingBox()
                for fixed_name, fixed_shape in fixed.items():
                    if not bounds_overlap(moved_bounds, bounds[fixed_name]):
                        continue
                    volume = _numeric_check(model, "motion_intersection", name, fixed_name,
                                            lambda: moved.intersect(fixed_shape).Volume(),
                                            pose={"angle_deg": angle, "axis_point": start,
                                                  "axis_direction": group.axis_direction})
                    if volume > 1e-5:
                        collisions.append({"moving_part": name, "fixed_part": fixed_name,
                                           "intersection_mm3": volume})
            model.checks[f"motion_{group.name}_sample_{index}"] = not collisions
            samples.append({"angle_deg": angle, "collisions": collisions})
        groups.append({"name": group.name, "parts": group.parts, "axis_point_mm": start,
                       "axis_direction": group.axis_direction, "coverage": "sampled angles only",
                       "axis_association": axis_association,
                       "continuous_sweep_verified": False, "samples": samples})
    model.measures["motion"] = groups
    if groups:
        model.warnings.append("Motion checks cover the listed sampled angles only; continuous swept-volume clearance is not certified.")


def _aabb_distance_lower_bound(a, b):
    gaps = [max(0, getattr(a, lo) - getattr(b, hi), getattr(b, lo) - getattr(a, hi))
            for lo, hi in (("xmin", "xmax"), ("ymin", "ymax"), ("zmin", "zmax"))]
    return math.sqrt(sum(gap * gap for gap in gaps))


def _record_insertions(design, model):
    paths = []
    bounds = {name: shape.BoundingBox() for name, shape in model.parts.items()}
    for path in design.insertion_checks:
        samples = []
        fractions = sorted({round(1 - i / (path.sample_count - 1), 12) for i in range(path.sample_count)} |
                           {round(value, 12) for value in path.sample_fractions}, reverse=True)
        for index, fraction in enumerate(fractions):
            offset = tuple(c * fraction for c in path.approach_vector_mm)
            collisions = []
            for moving_name in path.moving_parts:
                moving = model.parts[moving_name].translate(offset)
                moving_bounds = moving.BoundingBox()
                for stationary_name in path.stationary_parts:
                    stationary = model.parts[stationary_name]
                    if not bounds_overlap(moving_bounds, bounds[stationary_name]):
                        continue
                    volume = _numeric_check(model, "insertion_intersection", moving_name, stationary_name,
                                            lambda: moving.intersect(stationary).Volume(), pose={"offset_mm": offset})
                    if volume > 1e-5:
                        collisions.append({"moving_part": moving_name, "stationary_part": stationary_name,
                                           "intersection_mm3": volume})
            model.checks[f"insertion_{path.name}_sample_{index}"] = not collisions
            samples.append({"offset_mm": offset, "collisions": collisions})
        paths.append({"name": path.name, "moving_parts": path.moving_parts,
                      "stationary_parts": path.stationary_parts,
                      "approach_vector_mm": path.approach_vector_mm,
                      "uniform_sample_count": path.sample_count, "sample_fractions": path.sample_fractions,
                      "continuous_sweep_verified": False, "samples": samples})
    model.measures["insertion_paths"] = paths
    if paths:
        model.warnings.append("Assembly insertion checks cover the listed sampled translations of nominal rigid parts only; continuous swept volume, tool access and press-fit forces are not certified.")


def build_assembly_parts(design: AssemblyDesign, root: Path) -> AssemblyModel:
    """Build local/placed parts and their manufacturing checks without pair tests."""
    root = Path(root)
    model = AssemblyModel()
    for part in design.parts:
        if part.step:
            shape, source_hash, source_solid_count = _import_part(part, root)
            holes, keyways, radial_holes = [], [], []
        else:
            shape, holes, keyways, radial_holes = _reconstruct_part(part, model)
            source_hash = None
            source_solid_count = None
        solids = shape.Solids()
        valid = bool(solids) and shape.isValid() and all(s.isValid() and s.Volume() > 0 for s in solids)
        model.checks[part.name + "_valid_solids"] = valid
        if part.role == "machined":
            model.checks[part.name + "_single_machined_solid"] = len(solids) == 1
        model.local_parts[part.name] = shape
        model.parts[part.name] = place_shape(part, shape)
        model.metadata[part.name] = part
        _record_diameter_features(part, shape, model)
        bounds = model.parts[part.name].BoundingBox()
        model.measures[part.name] = {"volume_mm3": shape.Volume(), "solid_count": len(solids),
                                     "bounds_mm": [bounds.xlen, bounds.ylen, bounds.zlen],
                                     "min_mm": [bounds.xmin, bounds.ymin, bounds.zmin],
                                     "max_mm": [bounds.xmax, bounds.ymax, bounds.zmax],
                                     "position_mm": part.position, "rotation_deg": part.rotation_deg,
                                     "rotation_axis": part.rotation_axis,
                                     "rotation_axis_angle_deg": part.rotation_axis_angle_deg,
                                     "mirror_of": part.mirror_of,
                                     "source_step_sha256": source_hash,
                                     "source_step_solid_indices": part.step_solid_indices,
                                     "source_step_total_solid_count": source_solid_count,
                                     "model_provenance": part.model_provenance,
                                     "holes": holes, "keyways": keyways, "radial_holes": radial_holes}
        model.features.extend({"part": part.name, **f.model_dump()} for f in part.engineering_features)
        if part.model_provenance == "drawing_reconstruction":
            model.warnings.append(part.name + ": dimensional drawing reconstruction; this is not a manufacturer-supplied detailed model. Internal teeth, bearings and hidden details may be omitted.")
    return model


def build_assembly(design: AssemblyDesign, root: Path, *, preflight=False, cache_dir=None, source_hashes=None) -> AssemblyModel:
    """Build actual solids, then evaluate deterministic fit/interference checks.

    Interference and torque failures remain inspectable in the returned model.
    Optional drawing preflight runs before costly pair or motion operations.
    """
    if cache_dir is not None and source_hashes is None:
        source_hashes = _IMPORTED_SOURCE_HASHES
    model = build_assembly_parts(design, root)
    if preflight:
        from .assembly_preflight import validate_drawing_model
        validate_drawing_model(design, model)
    record_kinematics(design, model)
    record_mass_properties(design, model)
    if cache_dir is not None:
        from .assembly_cache import AssemblyCheckCache
        model._check_cache = AssemblyCheckCache(cache_dir, design, model, source_hashes=source_hashes)
    pair_values = []
    bounds = {name: shape.BoundingBox() for name, shape in model.parts.items()}
    for (name_a, shape_a), (name_b, shape_b) in itertools.combinations(model.parts.items(), 2):
        candidate = bounds_overlap(bounds[name_a], bounds[name_b])
        volume = _numeric_check(model, "static_intersection", name_a, name_b,
                                lambda: shape_a.intersect(shape_b).Volume()) if candidate else 0.0
        lower_bound = _aabb_distance_lower_bound(bounds[name_a], bounds[name_b])
        evaluate_exact = lower_bound <= 2
        pair_values.append({"part_a": name_a, "part_b": name_b,
                            "intersection_mm3": volume,
                            "minimum_distance_mm": _numeric_check(model, "static_distance", name_a, name_b,
                                                                  lambda: shape_a.distance(shape_b)) if evaluate_exact else None,
                            "intersection_evaluation": "exact" if candidate else "bounds",
                            "aabb_separation_lower_bound_mm": lower_bound,
                            "distance_evaluation": "exact" if evaluate_exact else "bounds"})
    model.measures["pairwise_interference"] = pair_values
    model.measures["collision_screening"] = {
        "pair_count": len(pair_values),
        "intersection_candidates": sum(p["intersection_evaluation"] == "exact" for p in pair_values),
        "intersection_bounds_skips": sum(p["intersection_evaluation"] == "bounds" for p in pair_values),
        "exact_distance_candidates": sum(p["distance_evaluation"] == "exact" for p in pair_values),
        "basis": "conservative world AABBs; no overlap erosion; exact OCCT for candidates"}
    model.checks["assembly_no_interference"] = all(p["intersection_mm3"] < 1e-5 for p in pair_values)
    _record_fits(design, model)
    _record_motion(design, model)
    _record_insertions(design, model)
    if cache_dir is not None:
        model.measures["collision_check_cache"] = model._check_cache.evidence()
    motor_output = design.motor_continuous_torque_nm * design.ratio * design.efficiency_assumption
    required = design.target_output_torque_nm * design.torque_service_factor
    model.checks["motor_continuous_torque_budget"] = motor_output >= required - 1e-9
    model.checks["reducer_continuous_torque_budget"] = design.reducer_continuous_torque_nm >= required - 1e-9
    model.measures["torque_budget_nm"] = {"target": design.target_output_torque_nm,
                                          "service_factor": design.torque_service_factor,
                                          "required": required,
                                          "motor_continuous": design.motor_continuous_torque_nm,
                                          "ratio": design.ratio,
                                          "efficiency_assumption": design.efficiency_assumption,
                                          "motor_limited_output": motor_output,
                                          "reducer_continuous_limit": design.reducer_continuous_torque_nm,
                                          "available_continuous_output": min(motor_output, design.reducer_continuous_torque_nm)}
    input_rpm = design.nominal_output_rpm * design.ratio
    model.measures["speed_budget_rpm"] = {"nominal_output": design.nominal_output_rpm,
                                         "required_reducer_input": input_rpm,
                                         "reducer_rated_input_limit": design.reducer_rated_input_rpm}
    if design.reducer_rated_input_rpm is not None:
        model.checks["reducer_rated_input_speed_budget"] = input_rpm <= design.reducer_rated_input_rpm + 1e-9
    else:
        model.warnings.append("Reducer rated input speed was not provided; nominal torque has not been checked against the selected operating speed.")
    compound = cq.Compound.makeCompound(list(model.parts.values()))
    bounds = compound.BoundingBox()
    model.measures["assembly_bounds_mm"] = [bounds.xlen, bounds.ylen, bounds.zlen]
    model.warnings.extend(["Size limits, fits, geometric controls and roughness are proposed manufacturing requirements; nominal CAD cannot prove that fabricated parts meet them.",
                           "Torque screening uses stated continuous ratings and assumed efficiency; speed, duty cycle, temperature, motor drive, bearing life and external loads need separate qualification."])
    return model
