"""Declared axes, speed relations and equal-radius closed-loop design intent.

These checks validate declared nominal placement. The loop is an idealized
arclength envelope, not articulated chain contact, tooth geometry, a continuous
swept-volume proof or a load simulation. Python and browser illustrations consume
the same generated pose samples instead of maintaining two geometry formulas.
"""
import math
from typing import Literal

from pydantic import Field, model_validator

from .spec import Record


class AxisReference(Record):
    part: str
    point_local_mm: tuple[float, float, float] = (0, 0, 0)
    direction_local: tuple[float, float, float] = (0, 0, 1)

    @model_validator(mode="after")
    def nonzero_axis(self):
        if _norm(self.direction_local) < 1e-10:
            raise ValueError("Referenced local axis direction cannot be zero")
        return self


class CoaxialConstraint(Record):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    axes: list[AxisReference] = Field(min_length=2, max_length=128)
    tolerance_mm: float = Field(default=0.01, gt=0, le=10)
    angular_tolerance_deg: float = Field(default=0.01, gt=0, le=5)


class TransmissionRelation(Record):
    """Driven/driver scalar angular speed ratio about each referenced axis.

    Each scalar is positive by the right-hand rule about that axis reference's
    transformed direction. Antiparallel axis directions therefore reverse the
    ratio sign. Chain teeth give the mean angular speed ratio.
    """
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: Literal["external_gear", "belt", "chain"]
    driver: AxisReference
    driven: AxisReference
    driver_pitch_radius_mm: float = Field(gt=0, le=2000)
    driven_pitch_radius_mm: float = Field(gt=0, le=2000)
    speed_ratio: float = Field(ge=-1000, le=1000,
                              description="Driven/driver scalar angular speed ratio; each speed follows the right-hand rule about its own referenced axis direction")
    driver_teeth: int | None = Field(default=None, ge=3, le=1000)
    driven_teeth: int | None = Field(default=None, ge=3, le=1000)
    pitch_mm: float | None = Field(default=None, gt=0, le=500)
    tolerance_mm: float = Field(default=0.01, gt=0, le=10)

    @model_validator(mode="after")
    def compatible_counts(self):
        if abs(self.speed_ratio) < 1e-12:
            raise ValueError("Transmission speed ratio cannot be zero")
        if (self.driver_teeth is None) != (self.driven_teeth is None):
            raise ValueError("Transmission requires both driver and driven tooth counts")
        if self.kind == "chain" and (self.driver_teeth is None or self.pitch_mm is None):
            raise ValueError("Chain transmission requires both tooth counts and pitch_mm")
        return self


class ClosedLoopConstraint(Record):
    """Ordered link origins/tangents on a two-wheel, equal-radius loop."""
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    kind: Literal["belt", "chain"] = "chain"
    wheel_a: AxisReference
    wheel_b: AxisReference
    pitch_radius_mm: float = Field(gt=0, le=2000)
    pitch_mm: float = Field(gt=0, le=500)
    links: list[str] = Field(min_length=3, max_length=128)
    phase_mm: float = 0
    teeth: int | None = Field(default=None, ge=3, le=1000)
    tolerance_mm: float = Field(default=0.01, gt=0, le=10)
    angular_tolerance_deg: float = Field(default=0.1, gt=0, le=5)

    @model_validator(mode="after")
    def unique_links(self):
        if len(self.links) != len(set(self.links)):
            raise ValueError("Closed-loop links must be unique and ordered")
        return self


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    length = _norm(a)
    if length < 1e-10:
        raise ValueError("Kinematic direction cannot be zero")
    return tuple(value / length for value in a)


def _axis_angle_deg(a, b):
    return math.degrees(math.acos(min(1, max(-1, abs(_dot(_unit(a), _unit(b)))))))


def resolve_axis(reference, parts):
    from .assembly_primitives import place_direction, place_point
    if reference.part not in parts:
        raise ValueError("Kinematic axis references unknown part: " + reference.part)
    part = parts[reference.part]
    return place_point(part, reference.point_local_mm), _unit(place_direction(part, reference.direction_local))


def motion_axis_evidence(group, parts):
    if group.axis_reference is None:
        return {"status": "unbound", "basis": "explicit axis only; no part-axis association declared"}
    if group.axis_reference.part not in group.parts:
        raise ValueError(f"Motion {group.name}: axis_reference part {group.axis_reference.part} is not in moving parts")
    point, direction = resolve_axis(group.axis_reference, parts)
    offset = _norm(_cross(_sub(group.axis_point, point), direction))
    angle = _axis_angle_deg(group.axis_direction, direction)
    if offset > group.axis_tolerance_mm or angle > 0.01:
        raise ValueError(f"Motion {group.name}: axis does not match {group.axis_reference.part} "
                         f"local axis (offset {offset:.6g} mm, direction error {angle:.6g} deg)")
    return {"status": "associated", "part": group.axis_reference.part,
            "point_mm": point, "direction": direction, "axis_offset_mm": offset,
            "direction_error_deg": angle, "basis": "declared local part axis transformed to assembly frame"}


def stadium_pose_2d(distance_mm, center_distance_mm, radius_mm):
    """Return position and unit tangent; centres are at +/- full distance/2."""
    if not all(math.isfinite(value) for value in (distance_mm, center_distance_mm, radius_mm)):
        raise ValueError("Loop dimensions and distance must be finite")
    if center_distance_mm <= 0 or radius_mm <= 0:
        raise ValueError("Loop center distance and pitch radius must be positive")
    half = center_distance_mm / 2
    d = distance_mm % (2 * center_distance_mm + 2 * math.pi * radius_mm)
    if d < center_distance_mm:
        return (-half + d, -radius_mm), (1.0, 0.0)
    d -= center_distance_mm
    if d < math.pi * radius_mm:
        phi = d / radius_mm - math.pi / 2
        return ((half + radius_mm * math.cos(phi), radius_mm * math.sin(phi)),
                (-math.sin(phi), math.cos(phi)))
    d -= math.pi * radius_mm
    if d < center_distance_mm:
        return (half - d, radius_mm), (-1.0, 0.0)
    d -= center_distance_mm
    phi = d / radius_mm + math.pi / 2
    return ((-half + radius_mm * math.cos(phi), radius_mm * math.sin(phi)),
            (-math.sin(phi), math.cos(phi)))


def stadium_pose_samples(center_distance_mm, radius_mm, sample_count=4096):
    """Periodic browser keyframes; interpolation is visual, not collision evidence."""
    if sample_count < 16 or sample_count > 65536:
        raise ValueError("Loop illustration requires 16..65536 pose samples")
    length = 2 * center_distance_mm + 2 * math.pi * radius_mm
    rows = []
    for index in range(sample_count):
        point, tangent = stadium_pose_2d(index * length / sample_count, center_distance_mm, radius_mm)
        rows.append([*point, *tangent])
    step = length / sample_count
    return {"loop_mm": length, "sample_count": sample_count, "poses": rows,
            "interpolation_position_error_bound_mm": min(step / 2, step * step / (8 * radius_mm)),
            "basis": "kernel-generated idealized arclength loop samples; visual interpolation only"}


def _loop_frame(loop, parts):
    a, normal = resolve_axis(loop.wheel_a, parts)
    b, b_normal = resolve_axis(loop.wheel_b, parts)
    separation = _sub(b, a)
    distance = _norm(separation)
    if _axis_angle_deg(normal, b_normal) > loop.angular_tolerance_deg:
        raise ValueError(f"Closed loop {loop.name}: wheel axes are not parallel")
    if abs(_dot(separation, normal)) > loop.tolerance_mm:
        raise ValueError(f"Closed loop {loop.name}: referenced pitch-circle centres are not coplanar")
    if distance <= loop.tolerance_mm:
        raise ValueError(f"Closed loop {loop.name}: wheel centres must be distinct")
    u = _unit(separation)
    v = _unit(_cross(normal, u))
    midpoint = tuple((x + y) / 2 for x, y in zip(a, b))
    return midpoint, u, v, distance


def closed_loop_pose(loop, parts, distance_mm):
    midpoint, u, v, center_distance = _loop_frame(loop, parts)
    (x, y), (tx, ty) = stadium_pose_2d(distance_mm, center_distance, loop.pitch_radius_mm)
    point = tuple(midpoint[i] + x * u[i] + y * v[i] for i in range(3))
    tangent = tuple(tx * u[i] + ty * v[i] for i in range(3))
    return point, tangent


def validate_kinematics(design):
    """Fail fast on declared design intent before expensive solid operations."""
    from .assembly_primitives import place_direction, place_point
    parts = {part.name: part for part in design.parts}
    evidence = {"coaxial_constraints": [], "transmissions": [], "closed_loops": [], "motion_axes": []}
    for field in ("coaxial_constraints", "transmissions", "closed_loops"):
        values = getattr(design, field, [])
        if len({value.name for value in values}) != len(values):
            raise ValueError("Duplicate assembly " + field)
    for group in design.moving_groups:
        evidence["motion_axes"].append({"name": group.name, **motion_axis_evidence(group, parts)})
    for constraint in design.coaxial_constraints:
        point, direction = resolve_axis(constraint.axes[0], parts)
        axes = []
        for reference in constraint.axes[1:]:
            other_point, other_direction = resolve_axis(reference, parts)
            offset = _norm(_cross(_sub(other_point, point), direction))
            angle = _axis_angle_deg(direction, other_direction)
            if offset > constraint.tolerance_mm or angle > constraint.angular_tolerance_deg:
                raise ValueError(f"Coaxial {constraint.name}: {reference.part} axis offset {offset:.6g} mm "
                                 f"and direction error {angle:.6g} deg exceed declared tolerances")
            axes.append({"part": reference.part, "axis_offset_mm": offset, "direction_error_deg": angle})
        evidence["coaxial_constraints"].append({"name": constraint.name, "reference_part": constraint.axes[0].part,
                                                  "axes": axes, "passed": True})
    for relation in design.transmissions:
        a, da = resolve_axis(relation.driver, parts)
        b, db = resolve_axis(relation.driven, parts)
        if _axis_angle_deg(da, db) > 0.01 or abs(_dot(_sub(b, a), da)) > relation.tolerance_mm:
            raise ValueError(f"Transmission {relation.name}: pitch-circle axes must be parallel and coplanar")
        radii = (relation.driver_pitch_radius_mm, relation.driven_pitch_radius_mm)
        if relation.kind == "external_gear":
            error = abs(_norm(_sub(a, b)) - sum(radii))
            if error > relation.tolerance_mm:
                raise ValueError(f"Transmission {relation.name}: gear centre distance misses pitch tangency by {error:.6g} mm")
        elif _norm(_sub(a, b)) <= abs(radii[0] - radii[1]) + relation.tolerance_mm:
            raise ValueError(f"Transmission {relation.name}: belt/chain centres cannot define external tangents")
        expected = radii[0] / radii[1]
        if relation.driver_teeth is not None:
            tooth_ratio = relation.driver_teeth / relation.driven_teeth
            if relation.kind == "chain":
                for radius, teeth in zip(radii, (relation.driver_teeth, relation.driven_teeth)):
                    required_radius = relation.pitch_mm / (2 * math.sin(math.pi / teeth))
                    if abs(radius - required_radius) > relation.tolerance_mm:
                        raise ValueError(f"Transmission {relation.name}: chain pitch radius disagrees with pitch and tooth count")
                expected = tooth_ratio
            elif abs(expected - tooth_ratio) > 1e-7:
                raise ValueError(f"Transmission {relation.name}: pitch radii and tooth counts imply different speed ratios")
        expected *= -1 if relation.kind == "external_gear" else 1
        # Parallelism treats axis lines as unoriented; signed speeds do not.
        # omega_driven * driven_axis = transmission_sign * ratio * omega_driver * driver_axis.
        expected *= 1 if _dot(da, db) >= 0 else -1
        if abs(expected - relation.speed_ratio) > max(1e-8, abs(expected) * 1e-7):
            raise ValueError(f"Transmission {relation.name}: declared speed ratio {relation.speed_ratio:g} "
                             f"does not match pitch/tooth ratio {expected:g}")
        evidence["transmissions"].append({"name": relation.name, "kind": relation.kind,
                                           "driven_over_driver_speed_ratio": expected, "passed": True,
                                           "axis_direction_alignment": "parallel" if _dot(da, db) >= 0 else "antiparallel",
                                           "basis": "declared pitch geometry/tooth counts; scalar speeds follow the right-hand rule about each referenced axis direction; actual tooth contact excluded"})
    for loop in design.closed_loops:
        _, _, _, distance = _loop_frame(loop, parts)
        required_length = 2 * distance + 2 * math.pi * loop.pitch_radius_mm
        declared_length = len(loop.links) * loop.pitch_mm
        if abs(required_length - declared_length) > loop.tolerance_mm:
            raise ValueError(f"Closed loop {loop.name}: {len(loop.links)} x pitch = {declared_length:.6g} mm "
                             f"does not close around wheel centres ({required_length:.6g} mm)")
        if loop.teeth is not None and abs(loop.pitch_radius_mm - loop.pitch_mm / (2 * math.sin(math.pi / loop.teeth))) > loop.tolerance_mm:
            raise ValueError(f"Closed loop {loop.name}: pitch radius disagrees with chain pitch/tooth count")
        links = []
        for index, name in enumerate(loop.links):
            if name not in parts:
                raise ValueError(f"Closed loop {loop.name}: unknown link part {name}")
            expected_point, tangent = closed_loop_pose(loop, parts, loop.phase_mm + index * loop.pitch_mm)
            actual_point = place_point(parts[name], (0, 0, 0))
            actual_tangent = _unit(place_direction(parts[name], (1, 0, 0)))
            offset = _norm(_sub(actual_point, expected_point))
            angle = math.degrees(math.acos(min(1, max(-1, _dot(actual_tangent, tangent)))))
            if offset > loop.tolerance_mm or angle > loop.angular_tolerance_deg:
                raise ValueError(f"Closed loop {loop.name}: link {name} misses its declared path "
                                 f"(position error {offset:.6g} mm, tangent error {angle:.6g} deg)")
            links.append({"part": name, "arclength_mm": loop.phase_mm + index * loop.pitch_mm,
                          "position_error_mm": offset, "tangent_error_deg": angle})
        evidence["closed_loops"].append({"name": loop.name, "kind": loop.kind, "passed": True,
                                          "center_distance_mm": distance, "pitch_radius_mm": loop.pitch_radius_mm,
                                          "required_loop_length_mm": required_length, "link_pitch_length_mm": declared_length,
                                          "links": links,
                                          "basis": "idealized equal-radius arclength loop; articulated pin contact and tooth engagement excluded"})
    return evidence


def record_kinematics(design, model):
    evidence = validate_kinematics(design)
    model.measures["kinematics"] = evidence
    for category in ("coaxial_constraints", "transmissions", "closed_loops"):
        for value in evidence[category]:
            model.checks[f"kinematic_{category}_{value['name']}"] = value["passed"]
