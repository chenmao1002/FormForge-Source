"""Assembly identification without remote, ambiguous component leader rays.

The overview is an actual assembled B-rep section. Component numbers refer to
separate whole-component HLR views, never to guessed bounding-box interfaces.
"""
import json

import cadquery as cq

from .drawings import project


def _fixed(pdf, english, chinese):
    from .assembly_drawings import _DisplayText
    return _DisplayText(chinese if getattr(pdf, "drawing_language", "en") == "zh-CN" else english)


def _identity_view(sheet, space, shape, bounds, dx, dy, identifier):
    """Draw placed geometry, without assuming an off-axis part lies on X=0."""
    lines = project(shape, (0, -1, 0), (1, 0, 0))
    points = [point for _, line in lines for point in line]
    xmin, xmax = min(x for x, y in points), max(x for x, y in points)
    ymin, ymax = min(y for x, y in points), max(y for x, y in points)
    left, bottom, right, top = bounds
    scale = min((right - left) / max(xmax - xmin, 1e-6),
                (top - bottom) / max(ymax - ymin, 1e-6), 3.0)
    ox = (left + right) / 2 - (xmin + xmax) * scale / 2
    oy = (bottom + top) / 2 - (ymin + ymax) * scale / 2
    sheet.items.append(f'<g class="component-geometry" data-component="{identifier}">')
    for hidden, line in lines:
        sheet.path([(ox + x * scale, oy + y * scale) for x, y in line], hidden)
        space.add_lwpolyline([(dx + x, dy + y) for x, y in line],
                            dxfattribs={"layer": "HIDDEN" if hidden else "VISIBLE"})
    sheet.items.append('</g>')
    space.add_text(identifier + " / FRONT -Y / 1:1", dxfattribs={"height": 3, "layer": "LABELS"}).set_placement(
        (dx + xmin, dy + ymax + 10))
    return {"view_normal": [0, -1, 0], "x_direction": [1, 0, 0],
            "frame": "placed assembly frame", "geometry_panel_bounds_mm": list(bounds),
            "projected_bounds_mm": [xmin, ymin, xmax, ymax],
            "paper_geometry_bounds_mm": [ox + xmin * scale, oy + ymin * scale,
                                         ox + xmax * scale, oy + ymax * scale],
            "scale": scale, "has_interface_leader": False}


def assembly_pages(pdf, space, design, model, page, output, spec_hash):
    from .assembly_drawings import (_LocalizedSheet, _view, _footer, axial_section,
                                    section_hatching, _display_lines, _wrapped_rows, _technical_row)
    pdf.bookmarkPage("assembly-overview")
    pdf.addOutlineEntry(str(_fixed(pdf, "Assembly overview", "装配总览")), "assembly-overview")
    compound = cq.Compound.makeCompound(list(model.parts.values()))
    half, station = axial_section(compound)
    lines = project(half, (-1, 0, 0), (0, -1, 0))
    hatches = section_hatching(half, station_mm=station)
    if not hasattr(pdf, "drawing_section_planes"):
        pdf.drawing_section_planes = {"parts": {}}
    pdf.drawing_section_planes["assembly"] = {"frame": "placed CAD",
        "normal": [1., 0., 0.], "offset_mm": station, "kept_halfspace": "X >= offset_mm"}
    for start in range(0, len(design.parts), 12):
        sheet = _LocalizedSheet(pdf, f"{design.name} / assembled axial layout and parts identification")
        _view(sheet, space, lines, (129, 174), 195, 167, -600, start * 300,
              f"ASSEMBLY A-A X={station:.12g}", scale_limit=1.0, hatches=hatches)
        sheet.text(241, 266, "PART / axial position / provenance", 3)
        y = 253
        for index, part in enumerate(design.parts[start:start + 12], start + 1):
            sheet.text(241, y, f"P{index:02d} {part.name} [{part.role}]", 2.8)
            sheet.text(241, y - 4.5, _technical_row(pdf, "ID ", part.name), 2.1)
            sheet.text(241, y - 9, f"XYZ {part.position} | {part.model_provenance}", 2.1)
            y -= 16
        sheet.text(12, 72, _fixed(pdf,
            "P numbers identify whole components in the following isolated views; no interface is inferred from this overview.",
            "P 编号对应后续独立零件视图中的整个零件；本总览不以引线推断配合接口。"), 2.7)
        sheet.text(12, 66, "Purchased source/reconstruction bodies are assembly references. Their supplier drawings control those parts.", 2.8)
        sheet.text(12, 59, "Custom CNC sheets use each part's local frame; this page uses the placed assembly frame.", 2.8)
        _footer(sheet, page, spec_hash, "Assembly layout; verify fastening, cable routing and supplier interfaces before production")
        sheet.save_svg(output / ("assembly-layout.svg" if start == 0 else f"assembly-layout-{start // 12 + 1}.svg"))
        pdf.showPage()
        page += 1

    pdf.bookmarkPage("assembly-component-index")
    pdf.addOutlineEntry(str(_fixed(pdf, "Component identification", "装配零件识别")), "assembly-component-index")
    index_records = []
    for start in range(0, len(design.parts), 8):
        sheet = _LocalizedSheet(pdf, _fixed(pdf,
            f"{design.name} / isolated component identification",
            f"{design.name} / 独立零件识别"))
        for slot, part in enumerate(design.parts[start:start + 8]):
            identifier = f"P{start + slot + 1:02d}"
            left = 12 + (slot % 4) * 99.5
            bottom = 155 if slot < 4 else 38
            for first, second in (((left, bottom), (left + 97, bottom)),
                                  ((left + 97, bottom), (left + 97, bottom + 115)),
                                  ((left + 97, bottom + 115), (left, bottom + 115)),
                                  ((left, bottom + 115), (left, bottom))):
                sheet.line(*first, *second, color="#b7c8d0", weight=.13)
            sheet.text(left + 3, bottom + 109, f"{identifier} {part.name}", 2.6)
            sheet.text(left + 3, bottom + 104, _technical_row(pdf, "ID ", part.name), 1.8)
            sheet.text(left + 3, bottom + 99, f"[{part.role}]", 2.1)
            bounds = (left + 6, bottom + 34, left + 91, bottom + 94)
            record = _identity_view(sheet, space, model.parts[part.name], bounds,
                                    -2500 + (slot % 4) * 350, -1500 - (start // 8 * 2 + slot // 4) * 450,
                                    identifier)
            sheet.text(left + 3, bottom + 29, _fixed(pdf,
                f"FRONT -Y / scale {record['scale']:.3f}:1",
                f"前视 -Y / 比例 {record['scale']:.3f}:1"), 2.1)
            for number, line in enumerate(_display_lines(pdf, f"XYZ {part.position}", 90)):
                sheet.text(left + 3, bottom + 24 - number * 4, line, 2.1)
            sheet.text(left + 3, bottom + 12, part.model_provenance, 2.1)
            record.update({"id": identifier, "part_id": part.name, "page": page,
                           "position_xyz_mm": list(part.position), "rotation_deg": list(part.rotation_deg),
                           "svg": f"assembly-component-index-{start // 8 + 1}.svg"})
            index_records.append(record)
        _footer(sheet, page, spec_hash, str(_fixed(pdf,
            "Whole-component identity views; placed coordinates; supplier drawings govern purchased details",
            "整件识别视图；使用装配坐标；采购件细节以供应商图纸为准")))
        sheet.save_svg(output / f"assembly-component-index-{start // 8 + 1}.svg")
        pdf.showPage()
        page += 1
    (output / "assembly-component-index.json").write_text(
        json.dumps({"purpose": "whole-component identification, not interface or machining dimensions",
                    "components": index_records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    pdf.bookmarkPage("assembly-component-sources")
    pdf.addOutlineEntry(str(_fixed(pdf, "Component sources and assembly notes", "采购件来源与装配说明")),
                        "assembly-component-sources")
    source_rows = [f"ASSEMBLY {design.name}: purchased component dimensional sources and model provenance."]
    for part in design.parts:
        source_rows.append(f"{part.name}: {part.role}; {part.model_provenance}; position XYZ {part.position}")
        source_rows.extend(_technical_row(pdf, "SOURCE ", source) for source in part.sources)
        if getattr(part, "step", None):
            source_rows.append(_technical_row(pdf, "MODEL ",
                f"{part.step} | SHA256 {getattr(part, 'step_sha256', '')}"))
    source_rows.extend(getattr(design, "notes", []))
    source_rows.extend(getattr(design, "assumptions", []))
    source_rows.extend(model.warnings)
    wrapped = [line for row in source_rows for line in _display_lines(pdf, row, 396)]
    for start in range(0, len(wrapped), 35):
        sheet = _LocalizedSheet(pdf, f"{design.name} / component sources and assembly notes")
        _wrapped_rows(sheet, wrapped[start:start + 35], 12, 266, 396, leading=6.1)
        _footer(sheet, page, spec_hash)
        pdf.showPage()
        page += 1
    return page
