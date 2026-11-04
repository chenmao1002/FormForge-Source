"""Real OCCT arm solids, composed kinematics, collision and gravity screening."""
from dataclasses import dataclass, field
import itertools
import math
from pathlib import Path

import cadquery as cq
from OCP.gp import gp_Trsf

from .arm_spec import ArmDesign, ArmPart, BoxPrimitive, validate_angles
from .assembly_cad import AssemblyModel
from .geometry import collision


@dataclass
class ArmModel(AssemblyModel):
    poses: dict = field(default_factory=dict)


def _identity():
    return [[float(i == j) for j in range(4)] for i in range(4)]


def _multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _joint_matrix(translation, axis, angle_deg):
    x, y, z = axis
    angle = math.radians(angle_deg)
    c, s, v = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return [[x*x*v+c, x*y*v-z*s, x*z*v+y*s, translation[0]],
            [y*x*v+z*s, y*y*v+c, y*z*v-x*s, translation[1]],
            [z*x*v-y*s, z*y*v+x*s, z*z*v+c, translation[2]],
            [0.0, 0.0, 0.0, 1.0]]


def transform_point(matrix, point):
    """Transform a local millimetre point using a homogeneous rigid matrix."""
    return tuple(sum(matrix[i][j] * point[j] for j in range(3)) + matrix[i][3] for i in range(3))


def forward_kinematics(design: ArmDesign, angles_deg=None):
    """Parent * translation * local-axis rotation, independent of input order.

    Overrides compose simultaneously. Omitted joint angles retain defaults.
    A child joint's translation follows every upstream rotation.
    """
    angles_deg = {} if angles_deg is None else angles_deg
    validate_angles(design, angles_deg)
    links = {link.name: link for link in design.links}
    result = {}
    active = set()

    def resolve(name):
        if name in result:
            return result[name]
        if name in active:
            raise ValueError("Arm link parent cycle is forbidden")
        active.add(name)
        link = links[name]
        parent = _identity() if link.parent is None else resolve(link.parent)
        result[name] = _multiply(parent, _joint_matrix(link.translation, link.axis,
                                  angles_deg.get(name, link.angle_deg)))
        active.remove(name)
        return result[name]

    for link in design.links:
        resolve(link.name)
    return result


def _primitive(primitive):
    if isinstance(primitive, BoxPrimitive):
        return cq.Workplane("XY").box(*primitive.size).translate(primitive.center).val()
    start = tuple(primitive.center[i] - primitive.axis[i] * primitive.length / 2 for i in range(3))
    return cq.Solid.makeCylinder(primitive.diameter / 2, primitive.length,
                                 cq.Vector(*start), cq.Vector(*primitive.axis))


def _world_shape(shape, matrix):
    # A directly populated cq.Matrix is tagged as a general gp_GTrsf even
    # for identity; OCCT's rigid converter then refuses it. Preserve the
    # analytic cylinders and normals with a real rigid gp_Trsf Location.
    rigid = gp_Trsf()
    rigid.SetValues(*(value for row in matrix[:3] for value in row))
    return shape.moved(cq.Location(rigid))


def print_placement(part: ArmPart, shape):
    """Apply declared XYZ print rotations, center XY and place real stock on Z=0."""
    placed = shape
    for axis, angle in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), part.print_rotation_deg):
        if angle:
            placed = placed.rotate((0, 0, 0), axis, angle)
    bounds = placed.BoundingBox()
    return placed.translate((-(bounds.xmin + bounds.xmax) / 2,
                             -(bounds.ymin + bounds.ymax) / 2, -bounds.zmin))


def _pairs(parts):
    rows = []
    for first, second in itertools.combinations(parts, 2):
        a, b = parts[first], parts[second]
        bounds_a, bounds_b = a.BoundingBox(), b.BoundingBox()
        gaps = [max(0, amin-bmax, bmin-amax) for amin, amax, bmin, bmax in (
            (bounds_a.xmin, bounds_a.xmax, bounds_b.xmin, bounds_b.xmax),
            (bounds_a.ymin, bounds_a.ymax, bounds_b.ymin, bounds_b.ymax),
            (bounds_a.zmin, bounds_a.zmax, bounds_b.zmin, bounds_b.zmax))]
        lower_bound = math.sqrt(sum(gap*gap for gap in gaps))
        overlap = collision(a, b)
        rows.append({"parts": [first, second], "intersection_mm3": overlap,
                     "aabb_separation_lower_bound_mm": lower_bound,
                     "minimum_distance_mm": None if lower_bound > 1e-5 else a.distance(b),
                     "distance_evaluation": "bounds" if lower_bound > 1e-5 else "occt"})
    return rows


def _descendants(design, name):
    names = {name}
    while True:
        expanded = names | {link.name for link in design.links if link.parent in names}
        if expanded == names:
            return names
        names = expanded


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _gravity_torques(design, transforms, masses, local_centers, pose_name):
    rows = []
    gravity = tuple(direction * 9.80665 for direction in design.gravity_direction)
    for joint in design.links:
        if joint.servo_part is None:
            continue
        matrix = transforms[joint.name]
        origin = transform_point(matrix, (0, 0, 0))
        # Rotation about its own local axis leaves that axis unchanged.
        axis = tuple(sum(matrix[i][j]*joint.axis[j] for j in range(3)) for i in range(3))
        descendants = _descendants(design, joint.name)
        contributions = []

        def contribution(name, mass_g, point):
            offset_m = tuple((point[i] - origin[i]) / 1000 for i in range(3))
            force = tuple(mass_g / 1000 * value for value in gravity)
            signed = _dot(axis, _cross(offset_m, force))
            contributions.append({"part": name, "mass_g": mass_g,
                                  "center_world_mm": list(point), "signed_torque_nm": signed})

        for part in design.parts:
            if part.link in descendants:
                contribution(part.name, masses[part.name],
                             transform_point(transforms[part.link], local_centers[part.name]))
        if design.tool_link in descendants and design.payload_g:
            contribution("payload", design.payload_g,
                         transform_point(transforms[design.tool_link], design.tool_point_mm))
        signed_torque = sum(item["signed_torque_nm"] for item in contributions)
        required = abs(signed_torque)
        available = joint.stall_torque_nm * joint.planning_torque_fraction
        rows.append({"pose": pose_name, "joint": joint.name, "servo_part": joint.servo_part,
                     "joint_origin_world_mm": list(origin), "joint_axis_world": list(axis),
                     "required_static_torque_nm": required, "signed_static_torque_nm": signed_torque,
                     "stall_torque_nm": joint.stall_torque_nm,
                     "planning_torque_fraction": joint.planning_torque_fraction,
                     "planning_available_torque_nm": available, "margin_nm": available-required,
                     "planning_margin_ratio": None if required < 1e-12 else available/required,
                     "passed": required <= available + 1e-9, "mass_contributions": contributions,
                     "basis": "Static gravity at this sampled pose; stall fraction is a planning heuristic, not a continuous rating."})
    return rows


def build_arm(design: ArmDesign, root: Path | str):
    """Build and screen every real part at default plus every declared composed pose.

    No intersecting pair is whitelisted. Checks report unsafe designs instead
    of converting them into apparently valid deliverables. Sweeps, acceleration,
    fastener strength, wire travel and printed fatigue remain unverified.
    """
    # Revalidate mutable records so changes cannot bypass references/limits.
    design = ArmDesign.model_validate(design.model_dump())
    model = ArmModel()
    masses, centers = {}, {}
    for part in design.parts:
        stock = [_primitive(primitive) for primitive in part.solids]
        shape = stock[0]
        for other in stock[1:]:
            shape = shape.fuse(other).clean()
        cut_rows = []
        for index, primitive in enumerate(part.cuts):
            cutter = _primitive(primitive)
            original_volume = shape.Volume()
            shape = shape.cut(cutter).clean()
            if not shape.Solids() or shape.Volume() < 1e-9:
                raise ValueError("Cuts remove all material from part: " + part.name)
            removed = original_volume - shape.Volume()
            residual = collision(shape, cutter)
            passed = removed > 1e-6 and residual < 1e-5
            model.checks[f"{part.name}_cut_{index}_removes_material"] = passed
            cut_rows.append({"index": index, "removed_volume_mm3": removed,
                             "remaining_cutter_intersection_mm3": residual, "passed": passed})
        solids = shape.Solids()
        model.checks[part.name + "_valid_solids"] = bool(solids) and all(solid.isValid() for solid in solids)
        model.checks[part.name + "_positive_volume"] = shape.Volume() > 1e-9
        if part.role == "printed":
            model.checks[part.name + "_single_printed_solid"] = len(solids) == 1
        model.local_parts[part.name] = shape
        model.metadata[part.name] = part
        center = shape.Center()
        centers[part.name] = (center.x, center.y, center.z)
        masses[part.name] = part.mass_g if part.mass_g is not None else shape.Volume() / 1000 * part.density_g_cm3
        bounds = shape.BoundingBox()
        model.measures[part.name] = {
            "volume_mm3": shape.Volume(), "solid_count": len(solids), "mass_g": masses[part.name],
            "mass_basis": "supplier/declared mass override" if part.mass_g is not None else "solid CAD volume times declared material density",
            "center_of_mass_local_mm": list(centers[part.name]),
            "bounds_mm": [bounds.xlen, bounds.ylen, bounds.zlen], "cuts": cut_rows,
            "model_provenance": part.model_provenance, "sources": part.sources}
        if part.role == "purchased" and part.mass_g is None:
            model.warnings.append(part.name + ": purchased mass was estimated from envelope volume and density; replace with measured/supplier mass.")
        if part.role == "purchased":
            model.warnings.append(part.name + ": primitive purchased geometry is a reconstructed envelope; connectors, spline/thread detail and supplier tolerances are unverified.")
    pose_rows, torques = [], []
    all_angles = [("default", {})] + [(pose.name, pose.angles_deg) for pose in design.poses]
    for name, overrides in all_angles:
        transforms = forward_kinematics(design, overrides)
        world = {part.name: _world_shape(model.local_parts[part.name], transforms[part.link]) for part in design.parts}
        pairs = _pairs(world)
        passed = all(pair["intersection_mm3"] < 1e-5 for pair in pairs)
        model.poses[name] = world
        model.checks["pose_" + name + "_no_interference"] = passed
        torque_rows = _gravity_torques(design, transforms, masses, centers, name)
        torques.extend(torque_rows)
        for row in torque_rows:
            model.checks[f"pose_{name}_{row['joint']}_static_torque_budget"] = row["passed"]
        pose_rows.append({"name": name,
                          "angles_deg": {link.name: overrides.get(link.name, link.angle_deg) for link in design.links},
                          "link_transforms": transforms, "pairwise_interference": pairs,
                          "tool_point_world_mm": list(transform_point(transforms[design.tool_link], design.tool_point_mm)),
                          "part_centers_world_mm": {part.name: list(transform_point(transforms[part.link], centers[part.name])) for part in design.parts},
                          "continuous_sweep_verified": False, "passed": passed})
    model.parts = model.poses["default"]
    model.checks["assembly_no_interference"] = model.checks["pose_default_no_interference"]
    model.checks["all_declared_poses_no_interference"] = all(row["passed"] for row in pose_rows)
    model.checks["all_sampled_static_torque_budgets"] = all(row["passed"] for row in torques)
    model.measures["pairwise_interference"] = pose_rows[0]["pairwise_interference"]
    model.measures["poses"] = pose_rows
    model.measures["static_torque"] = torques
    model.measures["total_part_mass_g"] = sum(masses.values())
    model.measures["payload_g"] = design.payload_g
    model.measures["continuous_sweep_verified"] = False
    model.measures["gravity_m_s2"] = [value*9.80665 for value in design.gravity_direction]
    model.measures["static_torque_summary"] = [
        {"joint": joint.name,
         "worst_sampled_required_nm": max(row["required_static_torque_nm"] for row in torques if row["joint"] == joint.name),
         "planning_available_nm": joint.stall_torque_nm * joint.planning_torque_fraction,
         "minimum_sampled_margin_nm": min(row["margin_nm"] for row in torques if row["joint"] == joint.name)}
        for joint in design.links if joint.servo_part]
    bounds = cq.Compound.makeCompound(list(model.parts.values())).BoundingBox()
    model.measures["assembly_bounds_mm"] = [bounds.xlen, bounds.ylen, bounds.zlen]
    model.warnings.extend([
        "Collision checks include all pairs at default and declared simultaneous poses; sampled clear poses do not certify a continuous sweep or slider angles.",
        "Gravity torque uses full solid plastic mass and supplier/declared purchased mass at CAD geometric centers. It excludes acceleration, impacts, friction, cable forces and center-of-mass uncertainty.",
        "Servo stall torque times a planning fraction is only a sizing heuristic; it is not a continuous torque rating or thermal/duty-cycle qualification.",
        "Nominal CAD and print placement do not certify printability, printed strength, assembly tolerances, fastener retention or physical operation."])
    return model
