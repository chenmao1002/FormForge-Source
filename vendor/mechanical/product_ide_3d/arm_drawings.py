"""CAD hidden-line views and explicit local cutter schedules for printed arms."""
import cadquery as cq
from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen.canvas import Canvas

from .drawings import Sheet, project, wrap_drawing_text
from .pdf_fonts import embedded_pdf_fonts


def _views(sheet, shape):
    views = [("顶视 (+Z)", (0, 0, 1), (1, 0, 0), (105, 213)),
             ("前视 (-Y)", (0, -1, 0), (1, 0, 0), (105, 118)),
             ("右视 (+X)", (1, 0, 0), (0, 1, 0), (300, 118))]
    for label, direction, xdir, (cx, cy) in views:
        lines = project(shape, direction, xdir)
        points = [p for _, line in lines for p in line]
        xmin, xmax = min(x for x, y in points), max(x for x, y in points)
        ymin, ymax = min(y for x, y in points), max(y for x, y in points)
        scale = min(160 / max(xmax - xmin, 1), 63 / max(ymax - ymin, 1), 2)
        ox, oy = cx - scale * (xmin + xmax) / 2, cy - scale * (ymin + ymax) / 2
        sheet.text(cx - 70, cy + 41, f"{label}  比例 {scale:.3f}:1", 3.2)
        for hidden, line in lines:
            sheet.path([(ox + x * scale, oy + y * scale) for x, y in line], hidden)
        sheet.dimension(ox + xmin * scale, ox + xmax * scale, oy + ymin * scale - 7,
                        f"{xmax - xmin:.3f} mm")
        sheet.text(cx - 70, cy - 45, f"投影高度 {ymax - ymin:.3f} mm", 3)


def _rows(sheet, rows, y=265, max_lines=31):
    lines = [s for row in rows for s in wrap_drawing_text(row, 388)]
    if len(lines) > max_lines:
        raise ValueError("Arm drawing schedule exceeds one sheet")
    for index, line in enumerate(lines):
        sheet.text(14, y - index * 7.1, line, 3.1)


def export_arm_drawings(design, model, output):
    fonts = embedded_pdf_fonts()
    pdf = Canvas(str(output / "engineering.pdf"), pagesize=landscape(A3), initialFontName=fonts.regular)
    pdf.setTitle(design.name + " / 微型手臂 CAD 评审图")
    pages = 0

    def finish(sheet, name):
        nonlocal pages
        pages += 1
        sheet.text(14, 16, f"单位 mm | 第 {pages} 页 | CAD 设计评审 / 实物装配与负载尚待验证", 3)
        sheet.save_svg(output / (name + ".svg"))
        pdf.showPage()

    sheet = Sheet(pdf, design.name + " / 串联微型手臂总装")
    _views(sheet, cq.Compound.makeCompound(list(model.parts.values())))
    sheet.text(14, 54, f"演示负载 {design.payload_g:g} g；关节姿态与完整几何检查见 report.json。", 3.2)
    sheet.text(14, 45, "打印结构与采购件的身份、尺寸来源见 BOM 与来源清单。尺寸复核后再试装。", 3.2)
    sheet.text(14, 36, "堵转扭矩的规划比例不等于连续额定扭矩；有限姿态采样不等于连续扫掠或动力学仿真。", 3.2)
    finish(sheet, "assembly-drawing")
    for part in design.parts:
        if part.role != "printed":
            continue
        sheet = Sheet(pdf, (part.label or part.name) + " / " + part.name + " / 本地坐标")
        _views(sheet, model.local_parts[part.name])
        sheet.text(14, 55, f"材料 {part.material}；打印摆放旋转 XYZ={part.print_rotation_deg} deg；STL 已放到 Z=0。", 3)
        sheet.text(14, 45, "建议 0.2 mm 层高、至少 3 壁；孔位/舵盘配合须试片复核。边角去毛刺。", 3)
        sheet.text(14, 35, "本页总尺寸来自实际 CAD 投影；局部孔/口袋坐标见后页，精确实体以 STEP 为准。", 3)
        finish(sheet, part.name + "-drawing")
        rows = ["孔与让位的中心/方向均为本零件局部坐标，单位 mm。",
                "C 编号对应设计 JSON 的 cuts 顺序；长方体是布尔切除口袋，圆柱是孔。"]
        for i, cut in enumerate(part.cuts, 1):
            if cut.kind == "box":
                rows.append(f"C{i:02d}: 长方体切除，中心 XYZ={cut.center}，尺寸 XYZ={cut.size}。")
            else:
                rows.append(f"C{i:02d}: 圆柱切除，中心 XYZ={cut.center}，直径 {cut.diameter:g}，长度 {cut.length:g}，轴方向 {cut.axis}。")
        rows += part.notes
        wrapped = [s for row in rows for s in wrap_drawing_text(row, 388)]
        for offset in range(0, len(wrapped), 29):
            sheet = Sheet(pdf, (part.label or part.name) + " / 孔与装配说明")
            _rows(sheet, wrapped[offset:offset + 29])
            finish(sheet, part.name + f"-schedule-{offset // 29 + 1}")
    rows = ["设计假设与装配注意事项"] + design.assumptions + design.notes
    wrapped = [s for row in rows for s in wrap_drawing_text(row, 388)]
    for offset in range(0, len(wrapped), 29):
        sheet = Sheet(pdf, design.name + " / 设计与验证边界")
        _rows(sheet, wrapped[offset:offset + 29])
        finish(sheet, f"design-notes-{offset // 29 + 1}")
    pdf.save()
    return pages
