"""CAD-derived orthographic engineering sheets and process cut patterns.

Views use OCCT hidden-line removal. Display curves are discretized to 0.01 mm;
manufacturing cut DXFs retain true circles/arcs. STEP is the authoritative B-rep.
"""
import html
import json
import re
import textwrap

import cadquery as cq
import ezdxf
from OCP.BRepLib import BRepLib
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics

from .pdf_fonts import embedded_pdf_fonts


def project(part, direction, xdir):
    algo = HLRBRep_Algo()
    algo.Add(part.wrapped)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(), gp_Dir(*direction), gp_Dir(*xdir))))
    algo.Update()
    algo.Hide()
    shapes = HLRBRep_HLRToShape(algo)
    lines = []
    for hidden, wrapped in [(False, shapes.VCompound()), (False, shapes.OutLineVCompound()),
                            (True, shapes.HCompound()), (True, shapes.OutLineHCompound())]:
        if wrapped.IsNull():
            continue
        BRepLib.BuildCurves3d_s(wrapped, 1e-6)
        for edge in cq.Shape(wrapped).Edges():
            curve = edge._geomAdaptor()
            samples = GCPnts_QuasiUniformDeflection(curve, 0.01, curve.FirstParameter(), curve.LastParameter())
            if samples.IsDone() and samples.NbPoints() > 1:
                lines.append((hidden, [(samples.Value(i).X(), samples.Value(i).Y())
                                       for i in range(1, samples.NbPoints()+1)]))
    if not lines:
        raise ValueError("CAD projection produced no edges")
    return lines


class Sheet:
    def __init__(self, canvas, title):
        self.c = canvas
        self.items = []
        self.line(8, 8, 412, 8)
        self.line(8, 289, 412, 289)
        self.text(12, 281, title, 5)

    def line(self, x0, y0, x1, y1, dashed=False, color="#1d2733", weight=0.2):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(weight*mm)
        self.c.setDash([1.5*mm, 1*mm] if dashed else [])
        self.c.line(x0*mm, y0*mm, x1*mm, y1*mm)
        self.items.append(f'<path d="M{x0},{297-y0} L{x1},{297-y1}" stroke="{color}" stroke-width="{weight}" fill="none"'+
                          (' stroke-dasharray="1.5,1"' if dashed else '')+'/>' )

    def path(self, points, hidden):
        self.c.setStrokeColor("#9099a5" if hidden else "#1d2733")
        self.c.setLineWidth((0.12 if hidden else 0.2)*mm)
        self.c.setDash([1.5*mm, mm] if hidden else [])
        path = self.c.beginPath()
        path.moveTo(points[0][0]*mm, points[0][1]*mm)
        for x, y in points[1:]:
            path.lineTo(x*mm, y*mm)
        self.c.drawPath(path)
        content = "M"+" L".join(f"{x:.4f},{297-y:.4f}" for x, y in points)
        self.items.append(f'<path d="{content}" stroke="'+("#9099a5" if hidden else "#1d2733")+
                          '" stroke-width="0.2" fill="none"'+(' stroke-dasharray="1.5,1"' if hidden else '')+'/>' )

    def text(self, x, y, value, size=3):
        self.c.setFillColor("#1d2733")
        font = embedded_pdf_fonts(str(value)).regular
        self.c.setFont(font, size*mm)
        self.c.drawString(x*mm, y*mm, str(value))
        self.items.append(f'<text x="{x}" y="{297-y}" font-size="{size}" fill="#1d2733" font-family="Product IDE 3D Sans SC,Noto Sans SC,Arial,sans-serif">{html.escape(str(value))}</text>')

    def dimension(self, x0, x1, y, label):
        self.line(x0, y, x1, y, weight=0.15)
        for x in (x0, x1):
            self.line(x, y-2, x, y+2, weight=0.15)
            self.line(x-1, y-1, x+1, y+1, weight=0.15)
        self.text((x0+x1)/2-6, y+1.5, label, 2.8)

    def save_svg(self, path):
        font_style = ('<style>@font-face{font-family:"Product IDE 3D Sans SC";'
                      'src:url("ProductIDE3DSansSC-Regular.ttf") format("truetype");}</style>'
                      if (path.parent / "ProductIDE3DSansSC-Regular.ttf").is_file() else "")
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" viewBox="0 0 420 297">'+
                        font_style + '<rect width="420" height="297" fill="white"/>'+"".join(self.items)+'</svg>', encoding="utf-8")


def feature_lines(d, model):
    rows = []
    for i, f in enumerate(model.features, 1):
        kind = f["type"]
        if kind == "lid_fastener":
            rows.append(f'F{i}: lid screw XY=({f["x"]:.3f},{f["y"]:.3f}); base pilot D{f["pilot_diameter"]:.3f} depth {f["pilot_depth"]:.3f}; lid THRU D{f["clearance_diameter"]:.3f}; CBORE D{f["head_diameter"]:.3f} depth {f["head_depth"]:.3f}')
        elif kind == "pcb_standoff":
            rows.append(f'F{i}: PCB post XY=({f["x"]:.3f},{f["y"]:.3f}) H{f["height"]:.3f} OD{f["post_diameter"]:.3f} pilot D{f["pilot_diameter"]:.3f}')
        elif kind == "opening":
            rows.append(f'F{i}: {f["label"]}: {f["part"]}/{f["face"]} UV=({f["u"]:.3f},{f["v"]:.3f}) W{f["cut_width"]:.3f} H{f["cut_height"]:.3f} R{f["radius"]:.3f} THRU')
        elif kind == "through_hole":
            rows.append(f'F{i}: THRU D{f["diameter"]:.3f}; XY=({f["x"]:.3f},{f["y"]:.3f})')
        elif kind == "bend":
            rows.append(f'F{i}: {f["count"]} x {f["angle_deg"]} deg UP bends; inside R{f["inside_radius"]:.3f}; K={f["k_factor"]:.3f}; BA={f["allowance"]:.3f} each')
    return rows


def wrap_drawing_text(row, max_width=396):
    """Wrap by rendered width without splitting engineering numeric values."""
    if max_width <= 0:
        raise ValueError("Drawing text width must be positive")
    font = embedded_pdf_fonts(row).regular
    lines = []
    for paragraph in row.splitlines() or [""]:
        current = ""
        # Chinese can wrap between characters, but a signed decimal or exponent
        # must stay on one line: 3.125 must never look like separate 3.12 and 5.
        tokens = re.findall(r"[-+\u2212\u00b1]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|[\s\S]", paragraph)
        for token in tokens:
            if pdfmetrics.stringWidth(token, font, 3.1*mm) > max_width*mm:
                raise ValueError("Numeric value does not fit the drawing text width; enlarge the panel")
            candidate = current+token
            if current and pdfmetrics.stringWidth(candidate, font, 3.1*mm) > max_width*mm:
                lines.append(current)
                current = token
            else:
                current = candidate
        lines.append(current)
    return lines


def export_drawings(d, model, output, spec_hash):
    fonts = embedded_pdf_fonts()
    pdf = Canvas(str(output/"engineering.pdf"), pagesize=landscape(A3), initialFontName=fonts.regular)
    pdf.setTitle(d.name+" engineering drawings")
    doc = ezdxf.new("R2010", setup=True)
    doc.units = 4
    for layer, color in [("VISIBLE", 7), ("HIDDEN", 8), ("DIMENSIONS", 4), ("LABELS", 3)]:
        doc.layers.new(layer, dxfattribs={"color": color, "linetype": "DASHED" if layer == "HIDDEN" else "CONTINUOUS"})
    space = doc.modelspace()
    count = 0
    for part_index, (name, part) in enumerate(model.parts.items()):
        sheet = Sheet(pdf, f"{d.name} / {name} - CAD engineering views")
        views = [("TOP (+Z)", (0, 0, 1), (1, 0, 0)), ("FRONT (-Y)", (0, -1, 0), (1, 0, 0)),
                 ("RIGHT (+X)", (1, 0, 0), (0, 1, 0))]
        bb = part.BoundingBox()
        scale = min(125/bb.xlen, 75/bb.ylen, 50/bb.zlen, 1.3)
        for i, (label, direction, xdir) in enumerate(views):
            lines = project(part, direction, xdir)
            coords = [point for hidden, line in lines for point in line]
            xmin, xmax = min(x for x, y in coords), max(x for x, y in coords)
            ymin, ymax = min(y for x, y in coords), max(y for x, y in coords)
            cx, cy = [(105, 220), (105, 124), (285, 124)][i]
            sheet.text(cx-60, 263 if i == 0 else 165, f"{label} - scale {scale:.3f}:1", 3)
            ox, oy = cx-scale*(xmin+xmax)/2, cy-scale*(ymin+ymax)/2
            # Export 1:1 DXF model coordinates, with separated views.
            dx = i*(max(d.length, d.width)+80)
            dy = part_index*(max(d.width, d.height)+100)
            for hidden, line in lines:
                sheet.path([(ox+x*scale, oy+y*scale) for x, y in line], hidden)
                space.add_lwpolyline([(x+dx, y+dy) for x, y in line],
                                     dxfattribs={"layer": "HIDDEN" if hidden else "VISIBLE"})
            sheet.dimension(ox+xmin*scale, ox+xmax*scale, oy+ymin*scale-7, f"{xmax-xmin:.3f}")
            sheet.text(cx-60, 172 if i == 0 else 90, f"View height: {ymax-ymin:.3f} mm", 3)
            space.add_text(name+" "+label, dxfattribs={"height": 3, "layer": "LABELS"}).set_placement((dx+xmin, dy+ymax+10))
            space.add_linear_dim(base=(dx, dy+ymin-10), p1=(dx+xmin, dy+ymin), p2=(dx+xmax, dy+ymin),
                                 angle=0, dimstyle="EZDXF", dxfattribs={"layer": "DIMENSIONS"}).render()
        sheet.line(12, 87, 408, 87)
        sheet.text(12, 80, f"Process: {d.process.type} | Material: {d.process.material} | General linear tolerance: +/- {d.process.tolerance:.3f} mm", 3.2)
        if d.process.type == "sheet_metal":
            detail = f"Sheet thickness {d.process.sheet_thickness:.3f}; inside bend R{d.process.bend_radius:.3f} (bracket); deburr all cut edges."
        else:
            detail = f"Outer corner R{d.corner_radius:.3f}; edge fillet R{d.edge_fillet:.3f}; chamfer {d.chamfer:.3f} x 45 deg."
        sheet.text(12, 72, detail, 3)
        sheet.text(12, 64, "Units: mm. Third-angle views. Datum A: base underside Z=0; B/C: central X/Y planes. See feature schedule and STEP.", 3)
        sheet.text(12, 56, f"Specification SHA256: {spec_hash}", 2.7)
        sheet.text(12, 48, "CAD GEOMETRY REVIEW - supplier and physical approval pending. Hidden lines dashed. Not a CAM/toolpath file.", 3)
        if d.kind == "enclosure":
            sheet.text(12, 39, f"Per-side locating gap {d.fit_gap:.3f}, worst case {d.fit_gap-2*d.process.tolerance:.3f}; seam {d.seam_gap:.3f}, worst case {d.seam_gap-2*d.process.tolerance:.3f}", 3)
        sheet.text(355, 16, f"Sheet {count+1} / part {name}", 3)
        sheet.save_svg(output/f"{name}-drawing.svg")
        pdf.showPage()
        count += 1
    schedule = ["All coordinates and feature sizes below are in mm.",
                "Opening UV: front/back = X,Z; left/right = Y,Z; top = X,Y. Z is measured from datum A.",
                "PCB STEP origin is the PCB underside centre in the source frame; import uses translation only.",
                f"Wall {d.wall:.3f}; floor {d.floor:.3f}; lid {d.lid_thickness:.3f}; cavity {d.length:.3f} x {d.width:.3f} x {d.height:.3f} (enclosure).",
                "Screws: thread-forming candidate for printed base; specify tapping/thread grade for CNC; no thread geometry is modelled."]
    schedule += feature_lines(d, model)
    if d.kind == "enclosure":
        schedule += [f'Left/right wall/fastener rail width: {model.measures["side_rail_width"]:.3f} mm; front/back nominal wall: {d.wall:.3f} mm.']
    if model.flat_outline:
        schedule += [f'Flat blank {model.measures["flat_length"]:.3f} x {d.width:.3f}; thickness {d.process.sheet_thickness:.3f}.',
                     f'90 deg bend allowance = pi/2*(R+K*t) = {model.measures["bend_allowance_90"]:.6f} mm; angular tolerance +/-{d.process.bend_tolerance_deg:.3f} deg.',
                     "BEND_UP lines mark neutral-axis bend-zone midpoints. CUT layer only is used for cutting."]
    schedule += ["Notes: "+s for s in d.notes+d.assumptions+model.warnings]
    wrapped = [line for row in schedule for line in wrap_drawing_text(row)]
    for page in range(0, len(wrapped), 32):
        sheet = Sheet(pdf, d.name+" / manufacturing feature schedule")
        for i, line in enumerate(wrapped[page:page+32]):
            sheet.text(12, 267-i*7.4, line, 3.1)
        sheet.text(12, 16, f"Sheet {count+1} | specification {spec_hash[:16]} | review required", 3)
        pdf.showPage()
        count += 1
    pdf.save()
    doc.saveas(output/"engineering.dxf")
    return count


def export_cut_pattern(d, model, output):
    if d.process.type != "sheet_metal":
        return
    doc = ezdxf.new("R2010", setup=True)
    doc.units = 4
    doc.layers.new("CUT", dxfattribs={"color": 7})
    doc.layers.new("BEND_UP", dxfattribs={"color": 4, "linetype": "DASHED"})
    space = doc.modelspace()
    if d.kind == "u_bracket":
        space.add_lwpolyline(model.flat_outline, close=True, dxfattribs={"layer": "CUT"})
        for a, b in model.bend_lines:
            space.add_line(a, b, dxfattribs={"layer": "BEND_UP"})
        for hole in d.holes:
            space.add_circle((hole.x, hole.y), hole.diameter/2, dxfattribs={"layer": "CUT"})
        doc.saveas(output/"flat-pattern.dxf")
    else:
        # Exact, closed section of sheet with rounded corners and circular holes.
        section = cq.Workplane(obj=model.parts["panel"]).section(d.process.sheet_thickness/2)
        cq.exporters.export(section, str(output/"cut-pattern.dxf"))
