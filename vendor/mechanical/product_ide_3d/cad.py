"""Actual OCCT solids, manufacturing features and interference evidence."""
from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import math

import cadquery as cq

from .geometry import box_between, rounded_box, collision, bounds_overlap
from .spec import Design


@dataclass
class Model:
    parts: dict = field(default_factory=dict)
    board: object = None
    board_source: str = "none"
    features: list = field(default_factory=list)
    checks: dict = field(default_factory=dict)
    measures: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    flat_outline: list = field(default_factory=list)
    bend_lines: list = field(default_factory=list)


def bore(x, y, diameter, z0, z1):
    return cq.Workplane("XY").center(x, y).circle(diameter/2).extrude(z1-z0).translate((0, 0, z0))


def board_model(d, root):
    b = d.board
    z = d.floor+d.standoff_height
    if b.step:
        path = (root/b.step).resolve()
        if path.suffix.lower() not in (".step", ".stp"):
            raise ValueError("PCB input must be STEP/STP")
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if b.step_sha256 and sha != b.step_sha256:
            raise ValueError("PCB STEP hash differs from specification")
        board = cq.importers.importStep(str(path))
        solids = board.solids().vals()
        if not solids or not all(s.isValid() and s.Volume() > 0 for s in solids):
            raise ValueError("PCB STEP contains invalid or empty solids")
        compound = cq.Compound.makeCompound(solids).translate((-b.origin[0], -b.origin[1], z-b.origin[2]))
        if b.substrate_solid_index is not None:
            if b.substrate_solid_index >= len(solids):
                raise ValueError("PCB substrate solid index outside imported assembly")
            substrate = solids[b.substrate_solid_index].translate((-b.origin[0], -b.origin[1], z-b.origin[2]))
            sb = substrate.BoundingBox()
            if abs(sb.zlen-b.thickness) > 0.1 or abs(sb.xlen-b.length) > 0.15 or abs(sb.ylen-b.width) > 0.15:
                raise ValueError("Selected PCB substrate does not match declared board dimensions")
            if abs((sb.xmin+sb.xmax)/2) > 0.1 or abs((sb.ymin+sb.ymax)/2) > 0.1 or abs(sb.zmin-z) > 0.1:
                raise ValueError("PCB origin is not the selected substrate underside centre")
            # Analytic inward cylindrical boundary coverage proves through-hole
            # topology without booleaning the entire drilled substrate per hole.
            # A PCB can have thousands of trimmed faces; that boolean is costly.
            cylinder_faces = []
            for face in substrate.Faces():
                if face.geomType() != "CYLINDER":
                    continue
                c = face._geomAdaptor().Cylinder()
                axis = c.Axis().Direction()
                if abs(axis.Z()) < 0.999999:
                    continue
                fb = face.BoundingBox()
                if fb.zmin > z+0.02 or fb.zmax < z+b.thickness-0.02:
                    continue
                u0, u1, v0, v1 = face.uvBounds()
                normal, position = face.normalAt((u0+u1)/2, (v0+v1)/2)
                center = c.Location()
                radial = cq.Vector(position.x-center.X(), position.y-center.Y(), 0)
                if normal.dot(radial) > -0.99*c.Radius():
                    continue
                cylinder_faces.append((center.X(), center.Y(), c.Radius(), abs(u1-u0), face.Area()))
            for hole in b.mounting_holes:
                matching = [face for face in cylinder_faces
                            if math.hypot(face[0]-hole.x, face[1]-hole.y) < 0.025
                            and hole.diameter/2-0.025 <= face[2] <= hole.diameter/2+0.1]
                angular_coverage = sum(face[3] for face in matching)
                full_area = 2*math.pi*(hole.diameter/2)*b.thickness
                if angular_coverage < 2*math.pi-0.01 or sum(face[4] for face in matching) < full_area*0.98:
                    raise ValueError("Mounting hole is not a real through hole in the selected PCB substrate")
        return compound, "imported STEP", sha
    board = box_between(-b.length/2, b.length/2, -b.width/2, b.width/2,
                        z-b.bottom_height, z+b.thickness+b.top_height)
    for hole in b.mounting_holes:
        board = board.cut(bore(hole.x, hole.y, hole.diameter, z-b.bottom_height-1, z+b.thickness+b.top_height+1))
    return board.val(), "declared conservative component envelope", None


def make_enclosure(d, root):
    m = Model()
    l, w, t, f = d.length, d.width, d.wall, d.floor
    ztop = f+d.height
    inner_r = max(0.5, d.corner_radius-t)
    boss_r = d.screw_diameter/2+2
    # CNC uses continuous outside rails for tapped fasteners, keeping the
    # internal pocket free of unmillable re-entrant boss/wall intersections.
    rail = t+2*boss_r if d.process.type == "cnc" else t
    outer = rounded_box(-l/2-rail, l/2+rail, -w/2-t, w/2+t, 0, ztop, d.corner_radius)
    outer = outer.faces("<Z").edges().fillet(d.edge_fillet)
    outer = outer.faces(">Z").edges().chamfer(d.chamfer)
    cavity = rounded_box(-l/2, l/2, -w/2, w/2, f, ztop+1, inner_r)
    # Pocket floor fillet gives real tool/print-friendly corners.
    cavity = cavity.faces("<Z").edges().fillet(min(d.edge_fillet, inner_r/2))
    base = outer.cut(cavity)
    # Conservative union of occupied-region AABBs. These include all possible
    # wall corner/floor fillets and posts. Cuts only remove material. Skipping a
    # component outside every region is therefore exact, not a sampling check.
    obstacles = [box_between(-l/2-rail, l/2+rail, -w/2-t, w/2+t, 0, f+d.edge_fillet).val().BoundingBox(),
                 box_between(-l/2-rail, -l/2+inner_r, -w/2-t, w/2+t, 0, ztop).val().BoundingBox(),
                 box_between(l/2-inner_r, l/2+rail, -w/2-t, w/2+t, 0, ztop).val().BoundingBox(),
                 box_between(-l/2-rail, l/2+rail, -w/2-t, -w/2+inner_r, 0, ztop).val().BoundingBox(),
                 box_between(-l/2-rail, l/2+rail, w/2-inner_r, w/2+t, 0, ztop).val().BoundingBox()]
    lid_z = ztop+d.seam_gap
    lid = rounded_box(-l/2-rail, l/2+rail, -w/2-t, w/2+t, lid_z, lid_z+d.lid_thickness, d.corner_radius)
    lid = lid.faces(">Z").edges().fillet(d.edge_fillet)
    lid = lid.faces("<Z").edges().chamfer(d.chamfer)
    lip_t = max(1.2, d.process.min_wall)
    lip_outer = rounded_box(-l/2+d.fit_gap, l/2-d.fit_gap, -w/2+d.fit_gap, w/2-d.fit_gap,
                            ztop-1.6, lid_z+0.1, max(0.2, inner_r-d.fit_gap))
    lip_inner = rounded_box(-l/2+d.fit_gap+lip_t, l/2-d.fit_gap-lip_t,
                            -w/2+d.fit_gap+lip_t, w/2-d.fit_gap-lip_t,
                            ztop-1.7, lid_z+0.2, max(d.process.tool_diameter/2 if d.process.type == "cnc" else 0.1,
                                                     inner_r-d.fit_gap-lip_t))
    lip = lip_outer.cut(lip_inner).faces("<Z").edges().chamfer(min(d.chamfer, lip_t/3))
    # Bosses connect to the side walls, avoiding detached solids.
    offset = boss_r-0.4
    screws = [(x, y) for x in (-l/2+offset, l/2-offset)
              for y in (-w/2+offset, w/2-offset)]
    if d.process.type == "cnc":
        screws = [(x, y) for x in (-l/2-t-boss_r, l/2+t+boss_r)
                  for y in (-w/2+2*boss_r, w/2-2*boss_r)]
    screw_clearance = d.screw_diameter+2*d.fit_gap
    head_d = d.screw_diameter*2+2*d.fit_gap
    for x, y in screws:
        boss = bore(x, y, 2*boss_r, f-0.1, ztop)
        if d.process.type != "cnc":
            base = base.union(boss)
            obstacles.append(boss.val().BoundingBox())
        pilot_depth = min(10, d.height-1)
        base = base.cut(bore(x, y, d.screw_pilot, ztop-pilot_depth, ztop+0.1))
        base = base.cut(cq.Solid.makeCone(d.screw_pilot/2, d.screw_pilot/2+d.chamfer,
                                        d.chamfer, cq.Vector(x, y, ztop-d.chamfer)))
        lip = lip.cut(bore(x, y, 2*(boss_r+d.fit_gap), ztop-2, lid_z+1))
        lid = lid.cut(bore(x, y, screw_clearance, lid_z-0.1, lid_z+d.lid_thickness+0.1))
        # A shallow counterbore leaves >= half the lid under the head.
        head_depth = min(1, d.lid_thickness/3)
        lid = lid.cut(bore(x, y, head_d, lid_z+d.lid_thickness-head_depth, lid_z+d.lid_thickness+0.1))
        m.features.append({"type": "lid_fastener", "x": x, "y": y, "pilot_diameter": d.screw_pilot,
                           "pilot_depth": pilot_depth, "clearance_diameter": screw_clearance,
                           "head_diameter": head_d, "head_depth": head_depth})
    lid = lid.union(lip)
    if d.board:
        b = d.board
        for hole in b.mounting_holes:
            pilot = min(d.screw_pilot, hole.diameter-2*d.fit_gap)
            if pilot < 0.8:
                raise ValueError("PCB mounting hole too small for pilot and clearance")
            post_d = max(hole.diameter+2.4, 5.5)
            post = bore(hole.x, hole.y, post_d, f-0.1, f+d.standoff_height)
            base = base.union(post)
            obstacles.append(post.val().BoundingBox())
            base = base.cut(bore(hole.x, hole.y, pilot, max(0.8, f-1), f+d.standoff_height+0.1))
            lead = min(d.chamfer, (hole.diameter-pilot)/2)
            base = base.cut(cq.Solid.makeCone(pilot/2, pilot/2+lead, lead,
                                            cq.Vector(hole.x, hole.y, f+d.standoff_height-lead)))
            m.features.append({"type": "pcb_standoff", "x": hole.x, "y": hole.y,
                               "height": d.standoff_height, "post_diameter": post_d, "pilot_diameter": pilot})
        m.board, m.board_source, board_hash = board_model(d, root)
        m.measures["board_step_sha256"] = board_hash
        if not b.mounting_holes:
            m.warnings.append("PCB mounting locations were not supplied; board retention requires design review.")
        if not b.step:
            m.warnings.append("PCB fit uses a declared envelope; import the real assembly STEP for component-level verification.")
    for o in d.openings:
        ow, oh = o.width+2*o.clearance, o.height+2*o.clearance
        if o.face == "top":
            cutter = rounded_box(o.u-ow/2, o.u+ow/2, o.v-oh/2, o.v+oh/2,
                                  ztop-2.1, lid_z+d.lid_thickness+1, o.radius)
            old = lid.val().Volume()
            lid = lid.cut(cutter)
            after = lid.val().Volume()
        elif o.face in ("front", "back"):
            y = (-1 if o.face == "front" else 1)*(w/2+t/2)
            cutter = box_between(o.u-ow/2, o.u+ow/2, y-t-1, y+t+1, o.v-oh/2, o.v+oh/2).edges("|Y").fillet(o.radius)
            old = base.val().Volume()
            base = base.cut(cutter)
            after = base.val().Volume()
        else:
            x = (-1 if o.face == "left" else 1)*(l/2+rail/2)
            cutter = box_between(x-rail-1, x+rail+1, o.u-ow/2, o.u+ow/2, o.v-oh/2, o.v+oh/2).edges("|X").fillet(o.radius)
            old = base.val().Volume()
            base = base.cut(cutter)
            after = base.val().Volume()
        target = lid if o.part == "lid" else base
        m.checks[f"opening_{len(m.features)}_through"] = collision(target.val(), cutter.val()) < 1e-5 and old-after > 1e-4
        m.features.append({"type": "opening", **o.model_dump(), "cut_width": ow, "cut_height": oh})
    m.parts = {"base": base.val(), "lid": lid.val()}
    m.measures.update({"nominal_fit_gap": d.fit_gap, "minimum_fit_gap": d.fit_gap-2*d.process.tolerance,
                       "nominal_seam_gap": d.seam_gap, "minimum_seam_gap": d.seam_gap-2*d.process.tolerance,
                       "internal_corner_radius": inner_r, "lip_thickness": lip_t,
                       "side_rail_width": rail,
                       "screw_centers": screws, "lid_z": lid_z})
    m.checks["base_lid_no_interference"] = collision(base.val(), lid.val()) < 1e-5
    if m.board:
        candidates = []
        board_solids = m.board.Solids()
        lid_bounds = lid.val().BoundingBox()
        for solid in board_solids:
            bb = solid.BoundingBox()
            if any(bounds_overlap(bb, occupied) for occupied in obstacles):
                candidates.append((base.val(), solid))
            if bounds_overlap(bb, lid_bounds):
                candidates.append((lid.val(), solid))
        volume = sum(collision(p, s) for p, s in candidates)
        m.measures["pcb_solid_count"] = len(board_solids)
        m.measures["exact_collision_candidates"] = len(candidates)
        m.measures["pcb_collision_mm3"] = volume
        m.checks["pcb_no_interference"] = volume < 1e-5
        bb = m.board.BoundingBox()
        # Use the true imported assembly extent, including overhanging components.
        side_gap = min(bb.xmin+l/2, l/2-bb.xmax, bb.ymin+w/2, w/2-bb.ymax)
        m.measures["pcb_min_side_gap"] = side_gap
        m.checks["pcb_side_gap"] = side_gap >= d.board_clearance-1e-5
        m.checks["pcb_vertical_gap"] = bb.zmin-f >= d.board_clearance-1e-5 and ztop-1.6-bb.zmax >= d.board_clearance-1e-5
    for k in d.keepouts:
        x, y, z = k.center
        a, b, c = k.size
        keepout = box_between(x-a/2, x+a/2, y-b/2, y+b/2, z-c/2, z+c/2).val()
        m.checks[f"keepout_{k.name}"] = sum(collision(keepout, p) for p in m.parts.values()) < 1e-5
    m.warnings.extend(["Screw choice, fit, component temperatures and load capacity require physical qualification.",
                       "No IP/EMC/thermal certification or CAM toolpaths are generated."])
    if d.process.type == "cnc":
        m.warnings.append("Pilot holes are untapped CAD geometry; specify thread and tapping operation before fabrication.")
    return m


def make_panel(d):
    m = Model()
    t = d.process.sheet_thickness if d.process.type == "sheet_metal" else d.floor
    panel = rounded_box(-d.length/2, d.length/2, -d.width/2, d.width/2, 0, t, d.corner_radius)
    if d.process.type != "sheet_metal":
        panel = panel.faces(">Z").edges().chamfer(d.chamfer)
        panel = panel.faces("<Z").edges().fillet(d.edge_fillet)
    for hole in d.holes:
        panel = panel.cut(bore(hole.x, hole.y, hole.diameter, -1, t+1))
        m.features.append({"type": "through_hole", **hole.model_dump()})
    m.parts = {"panel": panel.val()}
    m.measures["thickness"] = t
    m.warnings.append("Deburr cut edges; drawing tolerance must be accepted by the selected fabricator.")
    return m


def make_bracket(d):
    m = Model()
    t, r = d.process.sheet_thickness, d.process.bend_radius
    l, w, h = d.length, d.width, d.height
    base = box_between(-l/2, l/2, -w/2, w/2, 0, t)
    # Quarter-annulus bends are real cylindrical solids, not intersecting boxes.
    for side in (-1, 1):
        cx, cz = side*l/2, t+r
        outer = cq.Solid.makeCylinder(r+t, w, cq.Vector(cx, -w/2, cz), cq.Vector(0, 1, 0))
        inner = cq.Solid.makeCylinder(r, w+2, cq.Vector(cx, -w/2-1, cz), cq.Vector(0, 1, 0))
        ring = outer.cut(inner)
        x0, x1 = (cx-r-t, cx+0.01) if side < 0 else (cx-0.01, cx+r+t)
        quadrant = box_between(x0, x1, -w/2-0.01, w/2+0.01, -0.01, cz).val()
        bend = ring.intersect(quadrant)
        wall0, wall1 = (cx-r-t, cx-r) if side < 0 else (cx+r, cx+r+t)
        flange = box_between(wall0, wall1, -w/2, w/2, cz, t+h)
        base = base.union(cq.Workplane(obj=bend)).union(flange)
    for hole in d.holes:
        base = base.cut(bore(hole.x, hole.y, hole.diameter, -1, t+1))
        m.features.append({"type": "through_hole", **hole.model_dump()})
    ba = math.pi/2*(r+d.process.k_factor*t)
    straight = h-r
    flat_length = l+2*ba+2*straight
    half = flat_length/2
    m.parts = {"u_bracket": base.val()}
    m.flat_outline = [(-half, -w/2), (half, -w/2), (half, w/2), (-half, w/2)]
    bend_x = l/2+ba/2
    m.bend_lines = [((-bend_x, -w/2), (-bend_x, w/2)), ((bend_x, -w/2), (bend_x, w/2))]
    m.measures.update({"thickness": t, "bend_allowance_90": ba, "flat_length": flat_length,
                       "flat_width": w, "bend_radius": r, "k_factor": d.process.k_factor,
                       "bend_angles_deg": [90, 90], "bend_directions": ["UP", "UP"],
                       "flange_straight": straight})
    m.features.append({"type": "bend", "count": 2, "angle_deg": 90, "inside_radius": r,
                       "k_factor": d.process.k_factor, "allowance": ba})
    m.warnings.append("K factor and bend allowance must be calibrated for supplier/material/tooling; no arbitrary sheet unfold is claimed.")
    return m


def build_model(d: Design, root: Path):
    m = make_enclosure(d, root) if d.kind == "enclosure" else make_bracket(d) if d.kind == "u_bracket" else make_panel(d)
    for name, part in m.parts.items():
        m.checks[f"{name}_valid_single_solid"] = part.isValid() and len(part.Solids()) == 1 and part.Volume() > 0
        m.measures[name] = {"volume_mm3": part.Volume(), "bounds_mm": [part.BoundingBox().xlen, part.BoundingBox().ylen, part.BoundingBox().zlen]}
    return m
