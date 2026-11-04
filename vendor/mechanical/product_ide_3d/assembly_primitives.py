"""Shared local geometry reflection and rigid pose operations.

All rotations are right-handed. Fixed world X, Y, Z rotations run in that
order, then the optional arbitrary-axis rotation, then translation. Mirrors
operate on local geometry before this pose, never on the assembly frame.
"""
import math


def _rotate(vector, axis, angle_deg):
    length = math.sqrt(sum(value * value for value in axis))
    if length < 1e-9:
        raise ValueError("Rotation axis cannot be zero")
    unit = tuple(value / length for value in axis)
    angle = math.radians(angle_deg)
    cosine, sine = math.cos(angle), math.sin(angle)
    dot = sum(a * b for a, b in zip(unit, vector))
    cross = (unit[1] * vector[2] - unit[2] * vector[1],
             unit[2] * vector[0] - unit[0] * vector[2],
             unit[0] * vector[1] - unit[1] * vector[0])
    return tuple(vector[i] * cosine + cross[i] * sine + unit[i] * dot * (1 - cosine)
                 for i in range(3))


def place_direction(part, direction):
    result = tuple(direction)
    for axis, angle in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), part.rotation_deg):
        if angle:
            result = _rotate(result, axis, angle)
    if part.rotation_axis_angle_deg:
        result = _rotate(result, part.rotation_axis, part.rotation_axis_angle_deg)
    return result


def place_point(part, point):
    rotated = place_direction(part, point)
    return tuple(rotated[i] + part.position[i] for i in range(3))


def place_shape(part, shape):
    for axis, angle in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), part.rotation_deg):
        if angle:
            shape = shape.rotate((0, 0, 0), axis, angle)
    if part.rotation_axis_angle_deg:
        shape = shape.rotate((0, 0, 0), part.rotation_axis, part.rotation_axis_angle_deg)
    return shape.translate(part.position)


def mirror_step_shape(part, shape):
    for mirror in part.step_mirrors:
        origin = [0.0, 0.0, 0.0]
        origin[{"YZ": 0, "XZ": 1, "XY": 2}[mirror.plane]] = mirror.offset_mm
        shape = shape.mirror(mirror.plane, basePointVector=tuple(origin))
    return shape


def validate_polygon(points):
    """Reject degenerate, touching or self-crossing simple polygon boundaries."""
    if any(math.dist(a, b) < 1e-9 for i, a in enumerate(points) for b in points[i + 1:]):
        raise ValueError("Profile polygon vertices must be distinct; the closing point is implicit")
    edges = list(zip(points, points[1:] + points[:1]))
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges) / 2
    if abs(area) < 1e-8:
        raise ValueError("Profile polygon must enclose a non-zero area")

    def orientation(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def on_segment(a, b, p):
        return (abs(orientation(a, b, p)) < 1e-9 and
                min(a[0], b[0]) - 1e-9 <= p[0] <= max(a[0], b[0]) + 1e-9 and
                min(a[1], b[1]) - 1e-9 <= p[1] <= max(a[1], b[1]) + 1e-9)

    for i, (a, b) in enumerate(edges):
        for j, (c, d) in enumerate(edges[i + 1:], i + 1):
            if j == i + 1 or (i == 0 and j == len(edges) - 1):
                continue
            orientations = (orientation(a, b, c), orientation(a, b, d),
                            orientation(c, d, a), orientation(c, d, b))
            proper_cross = orientations[0] * orientations[1] < 0 and orientations[2] * orientations[3] < 0
            if proper_cross or any((on_segment(a, b, c), on_segment(a, b, d),
                                    on_segment(c, d, a), on_segment(c, d, b))):
                raise ValueError("Profile polygon cannot self-intersect or touch a non-adjacent edge")


def _mirrored_data(source, plane, offset):
    data = source.model_dump()
    axis = {"YZ": 0, "XZ": 1, "XY": 2}[plane]

    def point(values):
        values = list(values)
        values[axis] = 2 * offset - values[axis]
        return tuple(values)

    def xy(values):
        return point((*values, 0))[:2] if axis < 2 else tuple(values)

    def angle(value):
        return (180 - value) % 360 if axis == 0 else (-value) % 360 if axis == 1 else value

    def interval(item):
        if axis == 2:
            item["z0"], item["z1"] = 2 * offset - item["z1"], 2 * offset - item["z0"]

    for segment in data["segments"]:
        interval(segment)
        if axis == 2:
            for stem in ("outer_diameter", "inner_diameter"):
                if segment[stem + "_end"] is not None:
                    segment[stem], segment[stem + "_end"] = segment[stem + "_end"], segment[stem]
    for box in data["boxes"]:
        interval(box)
        box["center_xy"] = xy(box["center_xy"])
        if axis < 2:
            box["angle_deg"] = -box["angle_deg"]
    for profile in data["profiles"]:
        interval(profile)
        profile["points_xy"] = [xy(p) for p in profile["points_xy"]]
    for hole in data["holes"]:
        interval(hole)
        if hole["centers_xy"] is not None:
            hole["centers_xy"] = [xy(p) for p in hole["centers_xy"]]
        else:
            hole["origin_xy"] = xy(hole["origin_xy"])
            if hole["linear_step_xy"] is not None:
                step = list(hole["linear_step_xy"])
                if axis < 2:
                    step[axis] *= -1
                hole["linear_step_xy"] = tuple(step)
            else:
                hole["angle_deg"] = angle(hole["angle_deg"])
        if axis == 2:
            for stem in ("counterbore", "countersink"):
                hole[stem + "_side"] = "bottom" if hole[stem + "_side"] == "top" else "top"
    for keyway in data["keyways"]:
        interval(keyway)
        keyway["angle_deg"] = angle(keyway["angle_deg"])
    for hole in data["radial_holes"]:
        hole["angle_deg"] = angle(hole["angle_deg"])
        if axis == 2:
            hole["z"] = 2 * offset - hole["z"]
    for treatment in data["edge_treatments"]:
        if axis == 2 and treatment["selector"] in ("top", "bottom"):
            treatment["selector"] = "bottom" if treatment["selector"] == "top" else "top"
    for feature in data["engineering_features"]:
        feature["anchor_local"] = point(feature["anchor_local"])
    mass = data.get("mass_properties")
    if mass and mass.get("center_of_mass_local_mm") is not None:
        mass["center_of_mass_local_mm"] = point(mass["center_of_mass_local_mm"])
    if source.step:
        data["step_mirrors"].append({"plane": plane, "offset_mm": offset})
    return data


def resolve_mirrors(parts):
    """Expand local mirror aliases in any order and validate canonical replay.

    Aliases inherit body geometry, features and provenance. Their pose is
    independent of the source's assembly pose. Expanded canonical geometry is
    accepted only when it still matches the referenced source, so editing the
    source cannot leave a stale duplicate hidden in a saved design.
    """
    from .assembly_spec import AxialPart
    by_name = {part.name: part for part in parts}
    resolved, visiting = {}, set()
    geometry = ("segments", "boxes", "profiles", "holes", "radial_holes", "keyways",
                "edge_treatments", "engineering_features", "step_mirrors")

    def resolve(name):
        if name in resolved:
            return resolved[name]
        if name in visiting:
            raise ValueError("Mirror references form a cycle at part " + name)
        if name not in by_name:
            raise ValueError("Mirror references an unknown part: " + name)
        visiting.add(name)
        part = by_name[name]
        if part.mirror_of:
            source = resolve(part.mirror_of)
            if part.mirror_plane != "XY" and part.mirror_offset_mm and (
                    source.segments or source.keyways or source.radial_holes):
                raise ValueError(f"Mirror part {name}: offset XZ/YZ planes cannot shift a rotary local axis; use zero offset and part.position")
            data = _mirrored_data(source, part.mirror_plane, part.mirror_offset_mm)
            existing = part.model_dump()
            for key in geometry:
                # Empty arrays on a minimal alias carry no override. A full
                # saved alias must be equal to the independently derived body.
                if existing[key] and existing[key] != data[key]:
                    raise ValueError(f"Mirror part {name} cannot override or retain stale {key}; edit {source.name}")
            if part.step and part.step != source.step and not (
                    part.step_sha256 and part.step_sha256 == source.step_sha256):
                raise ValueError(f"Mirror part {name} cannot override its source STEP geometry")
            for key in ("name", "position", "rotation_deg", "rotation_axis", "rotation_axis_angle_deg",
                        "mirror_of", "mirror_plane", "mirror_offset_mm"):
                data[key] = existing[key]
            for key in ("material", "role", "sources", "model_provenance", "notes", "color"):
                if key in part.model_fields_set and existing[key] is not None:
                    # Canonical saves retain inherited metadata. Material,
                    # notes and colour may be intentionally varied by alias.
                    data[key] = existing[key]
            if "mass_properties" in part.model_fields_set:
                data["mass_properties"] = existing["mass_properties"]
            elif data["material"] != source.material:
                # Geometry reuse does not imply identical material density or
                # purchased mass. Keep engineering totals honestly partial.
                data["mass_properties"] = None
            part = AxialPart.model_validate(data)
        resolved[name] = part
        visiting.remove(name)
        return part

    return [resolve(part.name) for part in parts]
