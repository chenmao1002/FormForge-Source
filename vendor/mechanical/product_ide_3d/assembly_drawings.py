"""Geometry-linked CNC sheets for axial mechanical assemblies.

The section is a Boolean-clipped B-rep, not a second outline with hidden edges.
Its hatching is clipped against the actual planar cut faces. OCCT HLR supplies
all displayed model edges; 1:1 DXF geometry and STEP share the same coordinates.
Numeric acceptance limits come only from the typed engineering specification.
"""
from dataclasses import dataclass
from importlib import resources
import json
import math
from pathlib import Path

import cadquery as cq
import ezdxf
from OCP.BRepAdaptor import BRepAdaptor_Surface
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from .drawings import Sheet, project, wrap_drawing_text
from .annotation_layout import Rect, local_callout, fit_callout_view
from .pdf_fonts import embedded_pdf_fonts


class _DisplayText(str):
    """A localized, possibly wrapped fragment; never a second lookup key."""


def _language(value):
    normalized = value.replace("_", "-").casefold()
    if normalized == "en":
        return "en"
    if normalized in ("zh", "zh-cn"):
        return "zh-CN"
    raise ValueError("Drawing language must be en or zh-CN")


def _display(canvas, value):
    if isinstance(value, _DisplayText):
        return value
    catalog = getattr(canvas, "drawing_localization", None)
    language = getattr(canvas, "drawing_language", "en")
    return _DisplayText(catalog.text(str(value), language) if catalog else str(value))


def _display_lines(canvas, value, width):
    return [_DisplayText(line) for line in wrap_drawing_text(_display(canvas, value), width)]


def _technical_row(canvas, label, literal):
    """Translate the label once, preserving URLs, model paths and checksums."""
    return _DisplayText(f"{_display(canvas, label)}{literal}")


class _LocalizedSheet(Sheet):
    """One translation path for PDF/SVG labels and their DXF counterparts."""
    def localize(self, value):
        return _display(self.c, value)

    def text(self, x, y, value, size=3):
        super().text(x, y, self.localize(value), size)


def _axial_half_at(part, station_mm):
    bb = part.BoundingBox()
    margin = max(bb.xlen, bb.ylen, bb.zlen, 1.0) + 1.0
    clip = cq.Workplane("XY").box(
        bb.xmax - station_mm + margin, bb.ylen + 2 * margin,
        bb.zlen + 2 * margin, centered=(False, True, False)
    ).translate((station_mm, (bb.ymin + bb.ymax) / 2, bb.zmin - margin)).val()
    return part.intersect(clip)


def _axial_section_faces(half, station_mm):
    for face in half.Faces():
        bb = face.BoundingBox()
        if (face.geomType() == "PLANE" and bb.xlen < 1e-5 and
                abs(bb.xmin - station_mm) < 1e-5 and face.Area() > 1e-9):
            yield face


def axial_section(part):
    """Return the real clipped body and its local X cutting station in mm.

    Retain X=0 for material crossing the rotary axis. An offset/mirrored body
    uses its bounding-box centre; a compound with a gap there uses a solid's
    centre. Every selected station is interior and exposes actual planar
    section material, rather than relabelling an unchanged external view.
    """
    bb = part.BoundingBox()
    candidates = [0., (bb.xmin + bb.xmax) / 2]
    candidates.extend((solid.BoundingBox().xmin + solid.BoundingBox().xmax) / 2
                      for solid in part.Solids())
    tried = []
    for station in candidates:
        if (not bb.xmin + 1e-7 < station < bb.xmax - 1e-7 or
                any(abs(station - previous) < 1e-7 for previous in tried)):
            continue
        tried.append(station)
        half = _axial_half_at(part, station)
        if half.Solids() and half.isValid() and any(_axial_section_faces(half, station)):
            return half, float(station)
    raise ValueError(f"Axial section has no valid interior material cutting plane; "
                     f"local X bounds {bb.xmin:.6g} .. {bb.xmax:.6g} mm")


def axial_half_section(part):
    """Compatibility wrapper; use axial_section when the station is needed."""
    return axial_section(part)[0]


def section_hatching(half, spacing=4.0, *, station_mm=None):
    """Return exact material-only diagonal hatch segments in section (-Y,Z)."""
    if spacing <= 0:
        raise ValueError("Hatch spacing must be positive")
    station_mm = half.BoundingBox().xmin if station_mm is None else station_mm
    result = []
    for face in _axial_section_faces(half, station_mm):
        bb = face.BoundingBox()
        # Hatch z-y=constant; exact line/face common clips holes and recesses.
        low = bb.zmin - bb.ymax
        high = bb.zmax - bb.ymin
        offset = math.floor(low / spacing) * spacing
        while offset <= high + spacing:
            y0, y1 = bb.ymin - 1, bb.ymax + 1
            line = cq.Edge.makeLine((station_mm, y0, y0 + offset), (station_mm, y1, y1 + offset))
            common = face.intersect(line)
            for edge in common.Edges():
                a, b = edge.startPoint(), edge.endPoint()
                if (a - b).Length > 1e-6:
                    result.append(((-a.y, a.z), (-b.y, b.z)))
            offset += spacing
    return result


def numeric_limits(feature):
    """Explicit minimum/maximum, without inferring limits from an ISO label."""
    lower = getattr(feature, "lower_tolerance", None)
    upper = getattr(feature, "upper_tolerance", None)
    if lower is None and upper is None:
        return None
    if lower is None or upper is None or lower > upper:
        raise ValueError("Feature tolerances require ordered lower and upper values")
    return feature.nominal + lower, feature.nominal + upper


def feature_rows(feature):
    """All manufacturing requirements for one geometry-linked feature."""
    prefix = "D" if feature.kind == "diameter" else ""
    fit = (" " + feature.fit) if getattr(feature, "fit", "") else ""
    rows = [f"{feature.id}  {feature.label}"]
    if feature.kind in ("diameter", "length", "hole_pattern"):
        rows.append(f"{prefix}{feature.nominal:.3f}{fit} mm")
    limits = numeric_limits(feature)
    if limits:
        rows.append(f"LIMITS {limits[0]:.3f} .. {limits[1]:.3f} mm "
                    f"({feature.lower_tolerance:+.3f}/{feature.upper_tolerance:+.3f})")
    elif feature.kind != "face":
        rows.append("REFERENCE - acceptance limits not specified")
    datum = getattr(feature, "datum", "")
    gdt = getattr(feature, "gdt_mm", None)
    control_type = getattr(feature, "gdt_type", "")
    designation = bool(datum) and (feature.id == datum or
        (feature.kind == "face" and control_type.casefold() == "flatness") or
        (gdt is None and len(datum) == 1 and datum.isalpha()))
    if gdt is not None:
        rows.append(f"{control_type.upper()} {gdt:.3f} mm" +
                    (f" | references {datum}" if datum and not designation else ""))
    if designation:
        rows.append(f"DATUM DESIGNATION {datum}")
    elif datum and gdt is None:
        rows.append(f"DATUM REFERENCES {datum}")
    if getattr(feature, "roughness_ra_um", None) is not None:
        rows.append(f"SURFACE Ra <= {feature.roughness_ra_um:.2f} um")
    if getattr(feature, "surface", ""):
        rows.append(feature.surface)
    rows.extend(getattr(feature, "notes", []))
    return rows


def hole_coordinates(hole):
    for index, (x, y) in enumerate(hole.centers(), 1):
        yield index, x, y


def _profile_diameter(segment, outside=True):
    start = segment.outer_diameter if outside else segment.inner_diameter
    end = getattr(segment, "outer_diameter_end" if outside else "inner_diameter_end", None)
    return f"{start:.3f}" if end is None else f"{start:.3f} to {end:.3f}"


def _radial_material_ranges(part, hole):
    ranges = []
    for segment in part.segments:
        if segment.z0 - 1e-9 <= hole.z <= segment.z1 + 1e-9:
            fraction = (hole.z - segment.z0) / (segment.z1 - segment.z0)
            outer_end = getattr(segment, "outer_diameter_end", None)
            inner_end = getattr(segment, "inner_diameter_end", None)
            outer = (segment.outer_diameter + fraction * ((outer_end or segment.outer_diameter) - segment.outer_diameter)) / 2
            inner = (segment.inner_diameter + fraction * ((segment.inner_diameter if inner_end is None else inner_end) - segment.inner_diameter)) / 2
            low, high = max(inner, hole.inner_radius), min(outer, hole.outer_radius)
            if high > low:
                ranges.append((low, high))
    return ranges


def machining_rows(part, general_tolerance):
    rows = [
        f"PART {part.name} | MATERIAL {part.material} | units mm",
        "LOCAL FRAME: Z is rotary axis; X/Y hole coordinates originate on that axis.",
        f"General linear tolerance +/-{general_tolerance:.3f} mm; explicit feature limits take precedence.",
        "Z0 and Z1 below are absolute local axial coordinates, not chain dimensions.",
        "AXIAL PROFILE: machine all steps; OD = outside diameter, ID = bore diameter.",
    ]
    for index, segment in enumerate(part.segments, 1):
        rows.append(f"S{index:02d}  Z {segment.z0:.3f} .. {segment.z1:.3f}  "
                    f"L {segment.z1 - segment.z0:.3f}  "
                    f"OD {_profile_diameter(segment)}  ID {_profile_diameter(segment, False)}")
    for index, box in enumerate(getattr(part, "boxes", []), 1):
        rows.append(f"B{index:02d}  Z {box.z0:.3f} .. {box.z1:.3f}  "
                    f"LxW {box.length:.3f} x {box.width:.3f}  "
                    f"XY centre {box.center_xy[0]:.3f},{box.center_xy[1]:.3f}" +
                    (f" | rotation {box.angle_deg:.3f} deg about its centre" if box.angle_deg else ""))
    for index, profile in enumerate(part.profiles, 1):
        rows.append(f"P{index:02d} {profile.name}: {profile.operation.upper()} polygon; "
                    f"Z {profile.z0:.3f} .. {profile.z1:.3f}; depth {profile.z1 - profile.z0:.3f}.")
        for vertex, (x, y) in enumerate(profile.points_xy, 1):
            rows.append(f"  P{index:02d}.{vertex:02d}: X {x:+.3f}  Y {y:+.3f}")
    for index, treatment in enumerate(part.edge_treatments, 1):
        rows.append(f"E{index:02d} {treatment.name}: {treatment.kind.upper()} {treatment.size_mm:.3f} mm; "
                    f"local {treatment.selector.upper()} edges; apply after all cuts.")
    for index, keyway in enumerate(getattr(part, "keyways", []), 1):
        internal = getattr(keyway, "side", "external") == "internal"
        square = getattr(keyway, "end_style", "rounded") == "square"
        surface = "bore" if internal else "shaft"
        depth_direction = "outward" if internal else "inward"
        rows.append(f"KEYWAY K{index:02d} {keyway.name}: width {keyway.width:.3f}; "
                    f"depth {keyway.depth:.3f} {depth_direction} from {surface} D{keyway.shaft_diameter:.3f}; "
                    f"Z {keyway.z0:.3f} .. {keyway.z1:.3f}; "
                    f"angle {keyway.angle_deg:.3f} deg from +X; " +
                    ("square ends." if square else f"end R{keyway.end_radius:.3f}."))
        rows.append("Square-ended nominal geometry; inspect both entry openings and select the key fit." if square else
                    "Capsule-ended nominal geometry; select key-stock length/fit to the specified usable slot.")
        if internal and square:
            rows.append("Internal square keyway: broaching or through-profile wire EDM requires open ends; a closed internal pocket requires a separately qualified process.")
        elif internal:
            rows.append("Internal closed keyseat: blind internal seat requires sinker EDM with a shaped electrode or qualified internal milling; ordinary through broach or wire EDM cannot form this closed pocket without changing topology.")
    for index, hole in enumerate(getattr(part, "radial_holes", []), 1):
        action = (f"CORE DRILL D{hole.diameter:.3f}; TAP {hole.thread}" if hole.kind == "tap"
                  else f"DRILL D{hole.diameter:.3f}")
        rows.append(f"RADIAL RH{index:02d} {hole.name}: angle {hole.angle_deg:.3f} deg CCW from +X; "
                    f"axis Z {hole.z:.3f}; {action}.")
        rows.append(f"Radial cutter R {hole.inner_radius:.3f} .. {hole.outer_radius:.3f}; "
                    f"span {hole.outer_radius - hole.inner_radius:.3f} (includes any entry/exit overrun).")
        for low, high in _radial_material_ranges(part, hole):
            rows.append(f"Nominal profile material cut R {low:.3f} .. {high:.3f}; "
                        f"drilled material depth {high - low:.3f} from R{high:.3f} OD surface.")
    for pattern in part.holes:
        method = "MILL" if getattr(pattern, "operation", "drill") == "mill" else "DRILL"
        action = ((f"MILL CORE D{pattern.diameter:.3f}" if method == "MILL" else
                   f"CORE DRILL D{pattern.diameter:.3f}") + f"; TAP {pattern.thread}"
                  if pattern.kind == "tap" else f"{method} D{pattern.diameter:.3f}")
        if pattern.centers_xy is not None or pattern.linear_step_xy is not None:
            rows.append(f"HOLE {pattern.name}: {pattern.layout_description()}.")
        else:
            rows.append(f"HOLE {pattern.name}: {pattern.layout_description()}; "
                        f"first angle {pattern.angle_deg:.3f} deg from +X, CCW viewed from +Z.")
            if pattern.origin_xy != (0, 0):
                rows.append(f"Pattern centre X {pattern.origin_xy[0]:+.3f}  Y {pattern.origin_xy[1]:+.3f}.")
        rows.append(f"{action}; Z {pattern.z0:.3f} .. {pattern.z1:.3f}; "
                    f"axial depth {pattern.z1 - pattern.z0:.3f}.")
        if getattr(pattern, "counterbore_diameter", None):
            rows.append(f"CBORE D{pattern.counterbore_diameter:.3f} x "
                        f"{pattern.counterbore_depth:.3f} deep from {pattern.counterbore_side.upper()} face.")
        if getattr(pattern, "countersink_diameter", None):
            depth = pattern.countersink_depth()
            rows.append(f"CSK D{pattern.countersink_diameter:.3f} x "
                        f"{pattern.countersink_angle_deg:.3f} deg included angle from "
                        f"{pattern.countersink_side.upper()} face; depth to drill bore {depth:.3f}.")
            if depth > pattern.z1 - pattern.z0 + 1e-9:
                rows.append("CSK cone exceeds this part's hole span: adjacent mating-part relief is required as shown by the assembly specification.")
        for index, x, y in hole_coordinates(pattern):
            rows.append(f"  {pattern.name}.{index:02d}: X {x:+.3f}  Y {y:+.3f}")
    rows.extend([
        "PROCESS 1: rough turn OD, bores and shoulders; leave finishing stock on controlled fits.",
        "PROCESS 2: establish datum mounting face and rotary pilot in the same finishing setup.",
        "PROCESS 3: finish remaining controlled faces; retain datum registration for milling/drilling.",
        "PROCESS 4: index hole patterns from +X orientation; use each pattern's declared drilling or milling method; mill interrupted holes and tap only specified patterns.",
        "PROCESS 5: deburr, clean, inspect feature limits / GD&T / Ra, then trial assemble purchased interfaces.",
        "Tapped-hole solids represent unthreaded core bores; thread size and depth above control the machining operation.",
        "Blind drill/tap drill-point clearance and corner relief require tool/process review before CAM release.",
    ])
    rows.extend(getattr(part, "notes", []))
    return rows


def _text(sheet, x, y, value, size=3):
    sheet.text(x, y, value, size)


def _arrow(sheet, tip, tail):
    dx, dy = tip[0] - tail[0], tip[1] - tail[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return
    ux, uy = dx / length, dy / length
    for sign in (-1, 1):
        sheet.line(tip[0], tip[1], tip[0] - 2 * ux + sign * 0.7 * uy,
                   tip[1] - 2 * uy - sign * 0.7 * ux, weight=0.15)


@dataclass
class _View:
    sheet: Sheet
    space: object
    ox: float
    oy: float
    scale: float
    dx: float
    dy: float
    bbox: tuple

    def paper(self, point):
        return self.ox + point[0] * self.scale, self.oy + point[1] * self.scale

    def cad(self, point):
        return self.dx + point[0], self.dy + point[1]

    def paper_to_cad(self, point):
        return self.cad(((point[0] - self.ox) / self.scale, (point[1] - self.oy) / self.scale))

    def leader(self, anchor, target, label=None):
        start = self.paper(anchor)
        elbow = (target[0] - 5, target[1])
        self.sheet.line(*start, *elbow, weight=0.13)
        self.sheet.line(*elbow, *target, weight=0.13)
        _arrow(self.sheet, start, elbow)
        self.space.add_lwpolyline([self.cad(anchor), self.paper_to_cad(elbow), self.paper_to_cad(target)],
                                 dxfattribs={"layer": "LEADERS"})
        if label:
            label = self.sheet.localize(label)
            self.sheet.text(target[0] + 1, target[1] + 0.8, label, 2.5)
            self.space.add_text(label, dxfattribs={"height": 2.5 / self.scale, "layer": "FEATURES"}).set_placement(
                self.paper_to_cad((target[0] + 1, target[1] + 0.8)))

    def dimension(self, first, second, offset, label, vertical=False):
        label = self.sheet.localize(label)
        p1, p2 = self.paper(first), self.paper(second)
        if vertical:
            x = offset
            self.sheet.line(x, p1[1], x, p2[1], weight=0.13)
            for point in (p1, p2):
                self.sheet.line(point[0], point[1], x + 2, point[1], weight=0.12)
                self.sheet.line(x - 1, point[1] - 1, x + 1, point[1] + 1, weight=0.13)
            self.sheet.text(x + 2, (p1[1] + p2[1]) / 2, label, 2.5)
            base = self.paper_to_cad((x, p1[1]))
        else:
            y = offset
            self.sheet.line(p1[0], y, p2[0], y, weight=0.13)
            for point in (p1, p2):
                self.sheet.line(point[0], point[1], point[0], y + 2, weight=0.12)
                self.sheet.line(point[0] - 1, y - 1, point[0] + 1, y + 1, weight=0.13)
            self.sheet.text((p1[0] + p2[0]) / 2 - 8, y + 2, label, 2.5)
            base = self.paper_to_cad((p1[0], y))
        self.space.add_linear_dim(base=base, p1=self.cad(first), p2=self.cad(second),
                                  angle=90 if vertical else 0, text=label, dimstyle="EZDXF",
                                  dxfattribs={"layer": "DIMENSIONS"}).render()


def _view(sheet, space, lines, center, width, height, dx, dy, label, scale_limit=1.0, hatches=()):
    points = [point for _, line in lines for point in line]
    xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
    ymin, ymax = min(p[1] for p in points), max(p[1] for p in points)
    scale = min(width / max(xmax - xmin, 1e-6), height / max(ymax - ymin, 1e-6), scale_limit)
    view = _View(sheet, space, center[0] - (xmin + xmax) / 2 * scale,
                 center[1] - (ymin + ymax) / 2 * scale, scale, dx, dy,
                 (xmin, xmax, ymin, ymax))
    view.paper_segments = [(view.paper(first),view.paper(second))
                           for _,line in lines for first,second in zip(line,line[1:])]
    for hidden, line in lines:
        sheet.path([view.paper(point) for point in line], hidden)
        space.add_lwpolyline([view.cad(point) for point in line],
                            dxfattribs={"layer": "HIDDEN" if hidden else "VISIBLE"})
    for first, second in hatches:
        sheet.line(*view.paper(first), *view.paper(second), color="#79838c", weight=0.09)
        space.add_line(view.cad(first), view.cad(second), dxfattribs={"layer": "SECTION_HATCH"})
    sheet.text(center[0] - width / 2, center[1] + height / 2 + 9,
               f"{label}  scale {scale:.3f}:1", 2.8)
    space.add_text(sheet.localize(label + " / 1:1"), dxfattribs={"height": 3, "layer": "LABELS"}).set_placement(
        (dx + xmin, dy + ymax + 10))
    # The axis is a reference, separate from manufactured edges.
    extension=min(4,3/scale)
    sheet.line(*view.paper((0, ymin - extension)), *view.paper((0, ymax + extension)), dashed=True,
               color="#506d86", weight=0.12)
    space.add_line(view.cad((0, ymin - extension)), view.cad((0, ymax + extension)),
                   dxfattribs={"layer": "CENTRE"})
    return view


def _footer(sheet, page, spec_hash, text="CNC design review; mm; STEP governs 3D geometry"):
    sheet.line(12, 28, 408, 28, weight=0.15)
    sheet.text(12, 22, text, 2.7)
    sheet.text(12, 16, _technical_row(sheet.c, "Specification SHA256 ", spec_hash), 2.2)
    sheet.text(371, 16, f"Sheet {page}", 2.7)


def _wrapped_rows(sheet, rows, x, y, width, size=2.8, leading=5.3):
    # Existing width-aware wrapper uses 3.1 mm; narrower font conservatively fits.
    for row in rows:
        for line in _display_lines(sheet.c, row, width):
            sheet.text(x, y, line, size)
            y -= leading
    return y


def _draw_local_annotation(view, anchor, identifier, bounds, *, context=None):
    """One short, bounded leader in one explicitly identified geometry panel."""
    segments = getattr(view, "paper_segments", ())
    try:
        callout = local_callout(view.paper(anchor), bounds, geometry_segments=segments,
                                label_width=max(8, len(identifier)*1.9))
    except ValueError as error:
        context=context or {}
        raise ValueError(f"{context.get('part','unknown part')} / {identifier} / "
                         f"page {context.get('page','?')} / {context.get('category','callout')}: {error}") from error
    start, target = callout.anchor, callout.target
    view.sheet.line(*start, *target, weight=.16)
    _arrow(view.sheet, start, target)
    box = callout.label_box
    view.sheet.c.saveState()
    view.sheet.c.setFillColor("white")
    view.sheet.c.rect(box.xmin*mm, box.ymin*mm, (box.xmax-box.xmin)*mm,
                      (box.ymax-box.ymin)*mm, fill=1, stroke=0)
    view.sheet.c.restoreState()
    view.sheet.items.append(f'<rect x="{box.xmin}" y="{297-box.ymax}" width="{box.xmax-box.xmin}" '
                            f'height="{box.ymax-box.ymin}" fill="white"/>')
    view.sheet.text(*callout.text_origin, identifier, 2.8)
    view.space.add_lwpolyline([view.cad(anchor), view.paper_to_cad(target)],
                             dxfattribs={"layer":"LEADERS"})
    view.space.add_text(identifier,dxfattribs={"height":2.8/view.scale,"layer":"FEATURES"}).set_placement(
        view.paper_to_cad(callout.text_origin))
    record = dict(context or {})
    record.update(panel_bounds=[bounds.xmin,bounds.ymin,bounds.xmax,bounds.ymax],
                  id=identifier, projected_anchor=list(anchor),
                  paper_anchor=list(start), leader=[list(start),list(target)],
                  label_bounds=[box.xmin,box.ymin,box.xmax,box.ymax],
                  text_origin=list(callout.text_origin),scale=view.scale)
    view.sheet.c.drawing_annotation_layout.append(record)
    return record


def _callout_geometry_view(sheet,space,lines,center,width,height,dx,dy,label,
                           anchor,identifier,bounds,context,*,hatches=()):
    """Preflight a blank identifier region before committing PDF/SVG geometry."""
    try:
        fit=fit_callout_view(lines,center,width,height,anchor,bounds,
                             label_width=max(8,len(identifier)*1.9))
    except ValueError as error:
        raise ValueError(f"{context['part']} / {identifier} / page {context['page']} / "
                         f"{context['category']}: {error}") from error
    return _view(sheet,space,lines,center,width,height,dx,dy,label,
                 scale_limit=fit.scale,hatches=hatches)


def _direction_view(shape, projections, hatches, mode, angle=0):
    """Cache exact HLR views, including opposed hole entries and real key cuts."""
    key=(mode,round(angle%360,9))
    cached=projections.setdefault("details",{})
    if key in cached:
        return cached[key]
    if mode == "section":
        station = projections.get("section_station_mm", 0.)
        value=(projections["section"],hatches,(-1.,0.,0.),(0.,-1.,0.),
               f"AXIAL CUT X={station:.12g} | U=-Y, V=Z")
        projections.setdefault("section_planes", {})[key] = {
            "section_station_mm": station, "cut_plane_normal_local": [1., 0., 0.],
            "cut_plane_offset_mm": station}
    elif mode in ("top","bottom"):
        sign=1. if mode=="top" else -1.
        direction=(0.,0.,sign)
        lines=projections["top"] if mode=="top" else project(shape,direction,(1.,0.,0.))
        value=(lines,(),direction,(1.,0.,0.),
               "AXIAL VIEW +Z | U=X, V=Y" if sign>0 else "AXIAL VIEW -Z | U=X, V=-Y")
    else:
        theta=math.radians(angle)
        radial=(math.cos(theta),math.sin(theta),0.)
        tangent=(-math.sin(theta),math.cos(theta),0.)
        if mode in ("key_section","angled_section"):
            rotated=shape.rotate((0,0,0),(0,0,1),90-angle)
            half, station = axial_section(rotated)
            value=(project(half,(-1,0,0),(0,-1,0)),section_hatching(half, station_mm=station),
                   tangent,(-radial[0],-radial[1],0.),
                   f"{'KEY AXIAL CUT' if mode=='key_section' else 'AXIAL CUT'} angle {angle:.3f} deg | U=-R, V=Z"+
                   (f" | CUT NORMAL OFFSET {-station:.12g} mm" if abs(station) > 1e-7 else "")+
                   (" | keyway on U<0" if mode=='key_section' else ""))
            projections.setdefault("section_planes", {})[key] = {
                "section_station_mm": station, "section_station_frame": "Z-rotated local X",
                "cut_plane_normal_local": list(tangent), "cut_plane_offset_mm": -station}
        else:
            value=(project(shape,radial,tangent),(),radial,tangent,
                   f"RADIAL VIEW angle {angle:.3f} deg | U=tangent, V=Z")
    cached[key]=value
    return value


def _cut_plane_context(projections, mode, angle=0):
    return projections.get("section_planes", {}).get((mode, round(angle % 360, 9)), {})


def annotation_entries(part, shape=None):
    """Stable technical IDs and independently meaningful machining locators."""
    entries=[]
    if not (part.segments or part.boxes or part.profiles or part.holes or part.keyways or part.radial_holes):
        return entries
    spans=list(part.segments)+list(part.boxes)+list(part.profiles)
    if spans:
        low,high=min(item.z0 for item in spans),max(item.z1 for item in spans)
    elif shape is not None:
        box=shape.BoundingBox()
        low,high=box.zmin,box.zmax
    else:
        raise ValueError("Imported-only annotation entries require the actual CAD shape bounds")
    faces=shape.Faces() if shape is not None else ()
    def on_surface(point):
        vertex=cq.Vertex.makeVertex(*point)
        return any(face.distance(vertex)<1e-6 for face in faces)
    for index,segment in enumerate(part.segments,1):
        z=(segment.z0+segment.z1)/2
        diameter=(segment.outer_diameter+(segment.outer_diameter_end if segment.outer_diameter_end is not None else segment.outer_diameter))/2
        mode,angle,anchor_local="section",0.,(0.,diameter/2,z)
        if shape is not None and not on_surface(anchor_local):
            for theta_deg in (45.,135.,0.,180.,225.,315.,270.):
                theta=math.radians(theta_deg)
                point=(diameter/2*math.cos(theta),diameter/2*math.sin(theta),z)
                if on_surface(point):
                    mode,angle,anchor_local="angled_section",theta_deg,point
                    break
            else:
                raise ValueError(f"Profile S{index:02d} has no real outer-surface witness at its axial midpoint")
        entries.append(dict(id=f"S{index:02d}",mode=mode,angle=angle,anchor=(-diameter/2,z),
            anchor_local=anchor_local,rows=[
                f"S{index:02d} | PROFILE SEGMENT | Z {segment.z0:.3f} .. {segment.z1:.3f}",
                f"OD {_profile_diameter(segment)} | ID {_profile_diameter(segment,False)} | L {segment.z1-segment.z0:.3f}"]))
    for index, box in enumerate(part.boxes, 1):
        theta = math.radians(box.angle_deg)
        z = (box.z0 + box.z1) / 2
        candidates = []
        for dx, dy in ((box.length / 2, 0), (-box.length / 2, 0),
                       (0, box.width / 2), (0, -box.width / 2)):
            candidates.append((box.center_xy[0] + dx * math.cos(theta) - dy * math.sin(theta),
                               box.center_xy[1] + dx * math.sin(theta) + dy * math.cos(theta), z))
        witness = next((p for p in candidates if shape is None or on_surface(p)), None)
        if witness is None:
            # Fused overlap can bury a complete primitive. It remains listed
            # in the coordinate schedule, with no fabricated surface leader.
            continue
        entries.append(dict(id=f"B{index:02d}", mode="top", angle=0., anchor=witness[:2],
                            anchor_local=witness, rows=[
            f"B{index:02d} | BOX | Z {box.z0:.3f} .. {box.z1:.3f}",
            f"LxW {box.length:.3f} x {box.width:.3f} | XY {box.center_xy[0]:+.3f},{box.center_xy[1]:+.3f}",
            f"ROTATION {box.angle_deg:.3f} deg about box centre"]))
    for index, profile in enumerate(part.profiles, 1):
        z = (profile.z0 + profile.z1) / 2
        points = profile.points_xy
        candidates = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, z)
                      for a, b in zip(points, points[1:] + points[:1])]
        witness = next((p for p in candidates if shape is None or on_surface(p)), None)
        if witness is None:
            continue
        entries.append(dict(id=f"P{index:02d}", mode="top", angle=0., anchor=witness[:2],
                            anchor_local=witness, rows=[
            f"P{index:02d} | {profile.name} | {profile.operation.upper()} POLYGON",
            f"Z {profile.z0:.3f} .. {profile.z1:.3f} | DEPTH {profile.z1 - profile.z0:.3f}",
            f"{len(points)} XY vertices in coordinate schedule"]))
    for index,pattern in enumerate(part.holes,1):
        mode="bottom" if abs(pattern.z0-low)<abs(high-pattern.z1) else "top"
        if pattern.counterbore_diameter:
            mode="top" if pattern.counterbore_side=="top" else "bottom"
        if pattern.countersink_diameter:
            mode="top" if pattern.countersink_side=="top" else "bottom"
        _,x,y=next(hole_coordinates(pattern))
        z=pattern.z1 if mode=="top" else pattern.z0
        entries.append(dict(id=f"H{index:02d}",mode=mode,angle=0.,anchor=(x,y if mode=="top" else -y),
            anchor_local=(x,y,z),rows=[f"H{index:02d} | {pattern.name}",
                f"ENTRY Z {z:.3f} | first hole XY {x:+.3f}, {y:+.3f}",
                ((f"PCD {pattern.pcd:.3f}" if pattern.centers_xy is None and pattern.linear_step_xy is None
                  else pattern.layout_description()) +
                 f" | {pattern.count}x D{pattern.diameter:.3f} | Z {pattern.z0:.3f} .. {pattern.z1:.3f}")]))
    for index,key in enumerate(part.keyways,1):
        z=(key.z0+key.z1)/2
        theta=math.radians(key.angle_deg)
        internal=key.side=="internal"
        radius=key.shaft_diameter/2+(key.depth if internal else -key.depth)
        anchor=(-radius,z) if internal else (0.,z)
        entries.append(dict(id=f"K{index:02d}",mode="key_section" if internal else "radial",angle=key.angle_deg,
            anchor=anchor,anchor_local=(radius*math.cos(theta),radius*math.sin(theta),z),
            rows=[f"K{index:02d} | {key.name}",
                  f"WIDTH {key.width:.3f} | DEPTH {key.depth:.3f} | Z {key.z0:.3f} .. {key.z1:.3f}",
                  f"ANGLE {key.angle_deg:.3f} deg from +X | " + ("INTERNAL KEYWAY" if internal else "EXTERNAL KEYWAY")]))
    for index,hole in enumerate(part.radial_holes,1):
        theta=math.radians(hole.angle_deg)
        radius=max((end for _,end in _radial_material_ranges(part,hole)),default=hole.outer_radius)
        entries.append(dict(id=f"RH{index:02d}",mode="radial",angle=hole.angle_deg,anchor=(0.,hole.z),
            anchor_local=(radius*math.cos(theta),radius*math.sin(theta),hole.z),
            rows=[f"RH{index:02d} | {hole.name}",
                  f"ANGLE {hole.angle_deg:.3f} deg from +X | AXIS Z {hole.z:.3f}",
                  f"D{hole.diameter:.3f} | RADIAL R {hole.inner_radius:.3f} .. {hole.outer_radius:.3f}"]))
    return entries


def _face_material_witness(shape,feature,station_mm=None):
    """Select material on the same controlled axial plane, never inside a hole."""
    x,y,z=feature.anchor_local
    station_mm = axial_section(shape)[1] if station_mm is None else station_mm
    faces=[face for face in shape.Faces() if face.geomType()=="PLANE" and
           face.BoundingBox().zlen<1e-6 and abs(face.BoundingBox().zmin-z)<1e-6]
    if not faces:
        raise ValueError(f"Face {feature.id} has no actual axial planar surface at Z {z}")
    bb=shape.BoundingBox()
    line = cq.Edge.makeLine((station_mm, bb.ymin - 1, z), (station_mm, bb.ymax + 1, z))
    preferred = -math.hypot(x, y) if abs(station_mm) < 1e-7 else y
    candidates = []
    for face in faces:
        for edge in face.intersect(line).Edges():
            low, high = sorted((edge.startPoint().y, edge.endPoint().y))
            if high - low < 1e-7:
                continue
            margin = min(.25, (high - low) / 4)
            for ordinate in (min(high - margin, max(low + margin, preferred)), (low + high) / 2):
                candidates.append((abs(ordinate - preferred), face, ordinate,
                                   min(.18, (high - low) / 8)))
    for _, face, ordinate, clearance in sorted(candidates, key=lambda item: item[0]):
        point = (station_mm, ordinate, z)
        vertex = cq.Vertex.makeVertex(*point)
        if face.distance(vertex) < 1e-7 and min(edge.distance(vertex) for edge in face.Edges()) > clearance:
            return point
    # A plate face away from the rotary axis can still have a valid, explicit
    # geometry anchor. Display that point in a top view instead of inventing a
    # witness on a section that does not intersect that controlled face.
    vertex = cq.Vertex.makeVertex(x, y, z)
    if any(face.distance(vertex) < 1e-7 and min(edge.distance(vertex) for edge in face.Edges()) > .18
           for face in faces):
        return (x, y, z)
    raise ValueError(f"Face {feature.id} has no unambiguous material witness on its axial cut")


def _diameter_material_witness(shape,feature):
    """Find a surviving coaxial cylindrical arc at the declared radius and Z."""
    radius=feature.nominal/2
    z=feature.anchor_local[2]
    faces=[]
    for face in shape.Faces():
        if face.geomType()!="CYLINDER":
            continue
        cylinder=BRepAdaptor_Surface(face.wrapped,True).Cylinder()
        axis=cylinder.Axis()
        location,direction=axis.Location(),axis.Direction()
        if abs(cylinder.Radius()-radius)<1e-6 and math.hypot(location.X(),location.Y())<1e-6 and abs(direction.Z())>1-1e-6:
            faces.append(face)
        elif abs(cylinder.Radius()-radius)<1e-6 and abs(direction.Z())>1-1e-6:
            # Off-axis bores are addressed by an explicit point on their
            # controlled wall, not by a fictitious radius about the origin.
            anchor = cq.Vertex.makeVertex(*feature.anchor_local)
            if face.distance(anchor) < 1e-7:
                return tuple(feature.anchor_local), "top", 0.
    if not faces:
        point=(0.,-radius,z)
        vertex=cq.Vertex.makeVertex(*point)
        if min(face.distance(vertex) for face in shape.Faces())<1e-7:
            return point,"section",0.
        raise ValueError(f"Diameter {feature.id} has no actual surface at its declared radius and Z")
    for angle in (-90.,45.,135.,225.,315.,0.,90.,180.):
        theta=math.radians(angle)
        point=(radius*math.cos(theta),radius*math.sin(theta),z)
        vertex=cq.Vertex.makeVertex(*point)
        if any(face.distance(vertex)<1e-7 for face in faces):
            return point,"section" if angle==-90. else "angled_section",angle
    raise ValueError(f"Diameter {feature.id} has no surviving controlled cylindrical surface at its anchor Z")


def _annotation_pages(pdf,space,part,shape,projections,hatches,index,page,output,spec_hash):
    entries=annotation_entries(part,shape)
    for start in range(0,len(entries),4):
        sheet=_LocalizedSheet(pdf,f"{part.name} / geometry-linked machining annotation details")
        sheet.text(12,270,"One identifier per geometry panel; complete machining values are in this part's schedule.",2.8)
        for offset,entry in enumerate(entries[start:start+4]):
            x0=12+(offset%2)*200
            y0=155 if offset<2 else 44
            x1,y1=x0+196,y0+108
            sheet.line(x0,y0,x1,y0,color="#aab3bd",weight=.1)
            sheet.line(x0,y1,x1,y1,color="#aab3bd",weight=.1)
            lines,hatch,direction,xdir,label=_direction_view(shape,projections,hatches,entry["mode"],entry["angle"])
            bounds=Rect(x0+3,y0+34,x1-3,y1-16)
            context={
                "part":part.name,"page":page,"category":"annotations","panel":offset+1,
                "anchor_local":entry["anchor_local"],"view_direction":direction,"view_xdir":xdir,
                "view_label":label,"locator_rows":entry["rows"]}
            context.update(_cut_plane_context(projections, entry["mode"], entry["angle"]))
            view=_callout_geometry_view(sheet,space,lines,(x0+98,y0+63),158,51,
                       index*700+(offset%2)*230,6000+start*220+(offset//2)*110,label,
                       entry["anchor"],entry["id"],bounds,context,hatches=hatch)
            _draw_local_annotation(view,entry["anchor"],entry["id"],bounds,context=context)
            rows=entry["rows"]
            _wrapped_rows(sheet,rows,x0+4,y0+29,188,size=2.5,leading=4.2)
        _footer(sheet,page,spec_hash)
        sheet.save_svg(output/f"{part.name}-annotations-{start//4+1}.svg")
        pdf.showPage()
        page+=1
    return page


def _part_geometry_page(pdf, space, part, shape, projections, hatches, index, page, output, spec_hash):
    station = projections["section_station_mm"]
    sheet = _LocalizedSheet(pdf, f"{part.name} / CNC orthographic and true axial section")
    sheet.text(12, 270, f"Material {part.material} | local part coordinates | third-angle views | section A-A: X={station:.12g}", 3)
    top = _view(sheet, space, projections["top"], (74, 205), 100, 93, index * 700, 0, "TOP +Z")
    section = _view(sheet, space, projections["section"], (214, 205), 100, 93, index * 700 + 200, 0,
                    f"A-A AXIAL CUT X={station:.12g}", hatches=hatches)
    front = _view(sheet, space, projections["front"], (347, 205), 94, 93, index * 700 + 400, 0, "FRONT -Y")
    xmin, xmax, ymin, ymax = top.bbox
    top.dimension((xmin, ymin), (xmax, ymin), 145, f"OD envelope {xmax - xmin:.3f}")
    # The end-view trace and every section label share the actual cut station.
    sheet.line(*top.paper((station, ymin - 5)), *top.paper((station, ymax + 5)), dashed=True, weight=0.2)
    sheet.text(*top.paper((station + 2, ymax + 5)), "A", 3)
    sheet.text(*top.paper((station + 2, ymin - 9)), "A", 3)
    sxmin, sxmax, szmin, szmax = section.bbox
    section.dimension((sxmax, szmin), (sxmax, szmax), 273, f"L {szmax - szmin:.3f}", vertical=True)
    sheet.text(12,123,"S / H / K / RH identifiers: see separate geometry-linked machining annotation details.",3)
    sheet.text(12, 56, "Sxx profile lengths / Z positions and hole coordinates: see this part's machining schedule.", 2.8)
    sheet.text(12, 49, "Critical fit limits, geometric controls, datum faces and Ra: see this part's geometry-linked precision sheet.", 2.8)
    sheet.text(12, 42, "Dashed = hidden edges; diagonal hatch = actual section material. Purchased components excluded from CNC sheets.", 2.7)
    _footer(sheet, page, spec_hash)
    sheet.save_svg(output / f"{part.name}-drawing.svg")
    pdf.showPage()


def _feature_pages(pdf, space, part, shape, projections, hatches, index, page, output, spec_hash):
    features = list(part.engineering_features)
    def projection_hint(feature):
        hint = getattr(feature, "view_hint", "auto")
        return ("top" if feature.kind == "hole_pattern" else "section") if hint == "auto" else hint
    # Each requirement has its own bounded view; no table-directed leader can
    # cross another view or another feature's text. Long notes still continue.
    groups, group, rows_used = [], [], 0
    for feature in features:
        rows = [line for row in feature_rows(feature) for line in _display_lines(pdf, row, 211)]
        for start in range(0, len(rows), 42):
            chunk = rows[start:start + 42]
            if start:
                chunk = [_display(pdf, f"{feature.id} / continued")] + chunk
            cost = max(len(chunk) * 4.5 + 8, 62)
            if group and rows_used + cost > 212:
                groups.append(group)
                group, rows_used = [], 0
            group.append((feature, chunk, cost))
            rows_used += cost
    if group:
        groups.append(group)
    if not groups:
        raise ValueError(f"CNC part {part.name} requires geometry-linked precision features")
    for group_index, group in enumerate(groups):
        sheet = _LocalizedSheet(pdf, f"{part.name} / geometry-linked CNC precision {group_index + 1}")
        sheet.text(12, 270, "Every F callout below is attached to its controlled surface or hole. Limits and Ra are acceptance requirements.", 2.8)
        sheet.line(180, 39, 180, 264, color="#8793a0", weight=0.12)
        y = 257
        for panel_index,(feature, rows, cost) in enumerate(group,1):
            hint = projection_hint(feature)
            x, local_y, z = feature.anchor_local
            actual_anchor=(x,local_y,z)
            angle=0.
            if hint=="front":
                angle=math.degrees(math.atan2(local_y,x))%360
                internal=next((key for key in part.keyways if key.side=="internal" and
                    key.z0-1e-7<=z<=key.z1+1e-7 and
                    abs((key.angle_deg-angle+180)%360-180)<1e-5),None)
                if internal and internal.end_style=="square":
                    mode="bottom"
                    actual_anchor=(x,local_y,internal.z0)
                    anchor=(x,-local_y)
                elif internal:
                    mode="key_section"
                    anchor=(-math.hypot(x,local_y),z)
                else:
                    mode="radial"
                    theta=math.radians(angle)
                    anchor=(-x*math.sin(theta)+local_y*math.cos(theta),z)
            elif hint=="top":
                mode="top"
                anchor=(x,local_y)
            else:
                mode="section"
                anchor=(math.hypot(x,local_y),z)
                actual_anchor=(0.,-math.hypot(x,local_y),z)
                station = projections["section_station_mm"]
                if abs(station) > 1e-7:
                    actual_anchor = (station, local_y, z)
                    anchor = (-local_y, z)
                if feature.kind=="face":
                    actual_anchor=_face_material_witness(shape,feature,station)
                    if abs(actual_anchor[0] - station) > 1e-7:
                        mode="top"
                        anchor=actual_anchor[:2]
                    else:
                        anchor=(-actual_anchor[1],actual_anchor[2])
                elif feature.kind=="diameter":
                    actual_anchor,mode,angle=_diameter_material_witness(shape,feature)
                    radius=math.hypot(*actual_anchor[:2])
                    anchor=actual_anchor[:2] if mode=="top" else (-radius,z) if mode=="angled_section" else (radius,z)
            lines,hatch,direction,xdir,label=_direction_view(shape,projections,hatches,mode,angle)
            plane = _cut_plane_context(projections, mode, angle)
            if feature.kind in ("face", "diameter") and plane and abs(
                    sum(a * b for a, b in zip(actual_anchor, plane["cut_plane_normal_local"]))
                    - plane["cut_plane_offset_mm"]) > 1e-6:
                # A surviving controlled surface may be outside this cutting
                # plane. Keep its real point and show it in an uncut top view.
                mode, angle, anchor = "top", 0., actual_anchor[:2]
                lines,hatch,direction,xdir,label=_direction_view(shape,projections,hatches,mode)
            height=cost-30
            bounds=Rect(12,y-cost+5,178,y-14)
            context={
                "part":part.name,"page":page,"category":"precision","panel":panel_index,
                "anchor_local":actual_anchor,"spec_anchor_local":tuple(feature.anchor_local),
                "view_direction":direction,"view_xdir":xdir,"view_label":label,
                "projection_kind":mode}
            context.update(_cut_plane_context(projections, mode, angle))
            view=_callout_geometry_view(sheet,space,lines,(91,y-18-height/2),126,height,
                       index*700,1000+group_index*700-panel_index*180,label,
                       anchor,feature.id,bounds,context,hatches=hatch)
            _draw_local_annotation(view,anchor,feature.id,bounds,context=context)
            _wrapped_rows(sheet, rows, 196, y, 211, size=2.6, leading=4.5)
            for row_index, row in enumerate(rows):
                view.space.add_text(row, dxfattribs={"height": 2.6 / view.scale, "layer": "FEATURES"}).set_placement(
                    view.paper_to_cad((196, y - row_index * 4.5)))
            y -= cost
            sheet.line(12, y + 3, 406, y + 3, color="#bbc2c9", weight=0.1)
        _footer(sheet, page, spec_hash, "Proposed custom-part acceptance criteria; validate fit against purchased-part drawing revision")
        sheet.save_svg(output / f"{part.name}-precision-{group_index + 1}.svg")
        pdf.showPage()
        page += 1
    return page


def _schedule_pages(pdf, space, part, general_tolerance, index, page, output, spec_hash):
    lines = [line for row in machining_rows(part, general_tolerance)
             for line in _display_lines(pdf, row, 396)]
    for start in range(0, len(lines), 35):
        sheet = _LocalizedSheet(pdf, f"{part.name} / axial profile, hole coordinates and machining process")
        for row_index, line in enumerate(lines[start:start + 35]):
            sheet.text(12, 266 - row_index * 6.1, line, 2.8)
            space.add_text(line, dxfattribs={"height": 3, "layer": "SCHEDULE"}).set_placement(
                (index * 700 + 400, 500 + start * 7 - row_index * 7))
        _footer(sheet, page, spec_hash)
        sheet.save_svg(output / f"{part.name}-machining-{start // 35 + 1}.svg")
        pdf.showPage()
        page += 1
    return page


def _assembly_pages(pdf, space, design, model, page, output, spec_hash):
    from .assembly_layout_pages import assembly_pages
    return assembly_pages(pdf,space,design,model,page,output,spec_hash)


def export_assembly_drawings(design, model, output, spec_hash, language="en", localization=None):
    """Export A3 PDF, per-part SVGs and geometry-linked 1:1 engineering DXF."""
    language = _language(language)
    if language != "en" and localization is None:
        raise ValueError("Chinese drawings require a localization catalog")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    fonts=embedded_pdf_fonts()
    pdf = Canvas(str(output / "engineering.pdf"), pagesize=landscape(A3),initialFontName=fonts.regular)
    pdf.drawing_language = language
    pdf.drawing_localization = localization
    pdf.drawing_annotation_layout=[]
    pdf.drawing_section_planes={"parts": {}}
    pdf.setTitle(_display(pdf, design.name + " rotary joint engineering drawings"))
    doc = ezdxf.new("R2010", setup=True)
    doc.units = 4
    font_file="ProductIDE3DSansSC-Regular.ttf"
    doc.styles.get("Standard").dxf.font = font_file
    bundled=resources.files("product_ide_3d").joinpath("assets","fonts")
    (output/font_file).write_bytes(bundled.joinpath(font_file).read_bytes())
    license_file=bundled.joinpath("OFL.txt")
    if license_file.is_file():
        (output/"font-license-OFL.txt").write_bytes(license_file.read_bytes())
    for layer, color in [("VISIBLE", 7), ("HIDDEN", 8), ("DIMENSIONS", 4),
                         ("LABELS", 3), ("FEATURES", 2), ("LEADERS", 4),
                         ("SECTION_HATCH", 8), ("CENTRE", 6), ("SCHEDULE", 3)]:
        doc.layers.new(layer, dxfattribs={"color": color, "linetype": "DASHED" if layer in ("HIDDEN", "CENTRE") else "CONTINUOUS"})
    space = doc.modelspace()
    page = _assembly_pages(pdf, space, design, model, 1, output, spec_hash)
    for index, part in enumerate(design.parts):
        if part.role != "machined":
            continue
        shape = model.local_parts[part.name]
        pdf.bookmarkPage(f"cnc-part-{index}")
        display=localization.part(part.name,language) if localization else part.name
        pdf.addOutlineEntry(f"{display} [{part.name}]" if display!=part.name else part.name,
                            f"cnc-part-{index}",level=0,closed=True)
        half, station = axial_section(shape)
        pdf.drawing_section_planes["parts"][part.name] = {"frame": "local CAD",
            "normal": [1., 0., 0.], "offset_mm": station, "kept_halfspace": "X >= offset_mm"}
        projections = {
            "section_station_mm": station,
            "top": project(shape, (0, 0, 1), (1, 0, 0)),
            "front": project(shape, (0, -1, 0), (1, 0, 0)),
            "section": project(half, (-1, 0, 0), (0, -1, 0)),
        }
        hatches = section_hatching(half, station_mm=station)
        _part_geometry_page(pdf, space, part, shape, projections, hatches, index, page, output, spec_hash)
        page += 1
        page = _annotation_pages(pdf,space,part,shape,projections,hatches,index,page,output,spec_hash)
        page = _feature_pages(pdf, space, part, shape, projections, hatches, index, page, output, spec_hash)
        page = _schedule_pages(pdf, space, part, design.general_tolerance_mm, index, page, output, spec_hash)
    pdf.save()
    doc.saveas(output / "engineering.dxf")
    (output/"annotation-layout.json").write_text(json.dumps({"schema_version":1,
        "units":"paper mm; local/projected model mm","page_count":page-1,
        "scope":"One CNC callout per bounded geometry panel; source CAD and specification unchanged",
        "section_planes":pdf.drawing_section_planes,
        "callouts":pdf.drawing_annotation_layout},indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return page - 1
