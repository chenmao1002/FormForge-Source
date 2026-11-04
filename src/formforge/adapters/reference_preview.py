"""Diagnostic SVG from measured native reference evidence, not a fabrication plot."""
import math
import xml.etree.ElementTree as ET


def render(report):
    def n(value):
        if not math.isfinite(value):raise ValueError('Non-finite diagnostic coordinate')
        return f'{value:.6f}'.rstrip('0').rstrip('.') if value else '0'
    origin=report['boardBounds']['origin'];size=report['boardBounds']['size'];x,y=origin;w,h=size
    svg=ET.Element('svg',xmlns='http://www.w3.org/2000/svg',viewBox=' '.join(n(v) for v in [x-2,y-7,w+4,h+9]),
        attrib={'data-revision':report['revisionId'],'data-board-sha256':report['boardSha256']})
    ET.SubElement(svg,'title').text='参考检查定位图 · 非制造图 · '+report['revisionId']
    ET.SubElement(svg,'rect',x=n(x-2),y=n(y-7),width=n(w+4),height=n(h+9),fill='#103137')
    ET.SubElement(svg,'rect',x=n(x),y=n(y),width=n(w),height=n(h),fill='#173e45',stroke='#658589',attrib={'stroke-width':'.1'})
    text=ET.SubElement(svg,'g',fill='#edf5ec',attrib={'font-family':'Microsoft YaHei, sans-serif'})
    ET.SubElement(text,'text',x=n(x),y=n(y-4.5),attrib={'font-size':'1.7'}).text='参考检查定位 · 非制造图'
    ET.SubElement(text,'text',x=n(x),y=n(y-2),attrib={'font-size':'1'}).text=f"原始未满足 {report['strictMissingTrackCount']} / 当前 {report['missingTrackCount']} · 绿：原始覆盖 · 黄：受控终端 · 红：仍需处理"
    def shape(polygons,color):
        for poly in polygons:
            paths=[]
            for points in [poly['outer']]+poly.get('holes',[]):
                if points:paths.append('M '+' L '.join(n(p[0])+' '+n(p[1]) for p in points)+' Z')
            ET.SubElement(svg,'path',d=' '.join(paths),fill=color,attrib={'fill-opacity':'.65','fill-rule':'evenodd'})
    for row in report['tracks']:
        color='#8ed5a2' if row.get('rawCovered',row['covered']) else '#f7c66a' if row['covered'] else '#ff6a6a'
        if 'start' in row:
            ET.SubElement(svg,'line',x1=n(row['start'][0]),y1=n(row['start'][1]),x2=n(row['end'][0]),y2=n(row['end'][1]),stroke=color,attrib={'stroke-width':n(row['width']),'stroke-linecap':'round'})
        elif 'at' in row:ET.SubElement(svg,'circle',cx=n(row['at'][0]),cy=n(row['at'][1]),r=n(row['diameter']/2),fill=color)
        evidence=row.get('terminalEvidence')
        if evidence:
            shape(evidence['rawMissingPolygons'],'#f7c66a');shape(evidence['remainingPolygons'],'#ff555f')
            for terminal in evidence['terminals']:
                contact=terminal.get('nearestReturn')
                if contact and terminal['eligible']:
                    ET.SubElement(svg,'line',x1=n(terminal['at'][0]),y1=n(terminal['at'][1]),x2=n(contact['at'][0]),y2=n(contact['at'][1]),stroke='#8ed5a2',attrib={'stroke-width':'.08','stroke-dasharray':'.3 .2'})
    return ET.tostring(svg,encoding='utf-8',xml_declaration=True)
