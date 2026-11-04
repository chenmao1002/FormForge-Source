"""Compare native CSV data with board/graph identity; no procurement inference."""
import csv
import math
import xml.etree.ElementTree as ET
from collections import Counter
from .connectivity import assemble, group, read_graph


def csv_rows(path, fields):
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames!=fields:raise ValueError('无法识别原生CSV列：'+str(reader.fieldnames))
        rows=list(reader)
    if len(rows)>10000 or any(None in row for row in rows):raise ValueError('CSV行数或字段数异常。')
    return rows


def preflight(native, netlist, bom_path, position_path, params):
    problems=[]
    def issue(code,message,reference=None):problems.append(dict(code=code,message=message,reference=reference))
    parts=native['footprints'];counts=Counter(f['reference'] for f in parts)
    for ref,count in counts.items():
        if count!=1:issue('duplicate_board_reference','PCB位号重复。',ref)
    board={f['reference']:f for f in parts}
    comps=ET.parse(netlist).getroot().findall('components/comp')
    component_counts=Counter(c.attrib['ref'] for c in comps)
    if any(n!=1 for n in component_counts.values()):issue('duplicate_schematic_reference','原生网表位号重复。')
    electrical={c.attrib['ref']:c for c in comps}
    expected={r for r,f in board.items() if not f['boardOnly']}
    for fp in parts:
        if fp['boardOnly'] and (fp['pads'] or not fp['excludeFromBom'] or not fp['excludeFromPosition']):
            issue('board_only_identity','board-only对象包含有号焊盘或未排除装配，不能自动当安装孔。',fp['reference'])
    if expected!=set(electrical):issue('component_set','PCB电气器件与原生网表集合不一致。')
    pin_components=[];pin_groups={}
    for ref in sorted(expected):
        pins={}
        for pad in board[ref]['pads']:
            net=pad['net'] if pad['netCode'] else None
            if pad['number'] in pins and pins[pad['number']]!=net:issue('duplicate_pad_net','同号焊盘网络不同。',ref)
            pins[pad['number']]=net
        pin_components.append(dict(reference=ref,pins=sorted(pins)))
        for pin,net in pins.items():pin_groups.setdefault(('net',net) if net else ('isolated',ref,pin),[]).append((ref,pin))
    actual_graph=assemble(pin_components,[group(pins) for pins in pin_groups.values()])
    schematic_graph=read_graph(netlist)
    if actual_graph['hash']!=schematic_graph['hash']:issue('complete_pin_graph','全部器件引脚/网络分组与原生原图不一致。')
    bom=csv_rows(bom_path,['Reference','Value','Footprint','Quantity','DNP','MPN','LCSC'])
    pos=csv_rows(position_path,['Ref','Val','Package','PosX','PosY','Rot','Side'])
    for rows,field,label in [(bom,'Reference','BOM'),(pos,'Ref','坐标')]:
        for ref,n in Counter(row[field] for row in rows).items():
            if n!=1:issue('duplicate_'+field,label+'位号重复或被分组。',ref)
    by_bom={row['Reference']:row for row in bom};by_pos={row['Ref']:row for row in pos}
    expected_bom={ref for ref in expected if not board[ref]['excludeFromBom']}
    expected_pos={ref for ref,f in board.items() if not f['excludeFromPosition'] and not f['boardOnly']}
    if set(by_bom)!=expected_bom:issue('bom_set','BOM缺少或多出位号：'+str(sorted(set(by_bom)^expected_bom)))
    if set(by_pos)!=expected_pos:issue('position_set','原始坐标缺少或多出位号：'+str(sorted(set(by_pos)^expected_pos)))
    for ref in sorted(expected & set(electrical)):
        fp=board[ref];comp=electrical[ref]
        if comp.findtext('value')!=fp['value'] or comp.findtext('footprint')!=fp['footprint']:
            issue('native_component_identity','原图与PCB的数值或封装不同。',ref)
        pins={p['number']:p['net'] for p in fp['pads']}
        if len({p['number'] for p in fp['pads']})!=len(pins):
            # Repeated pad numbers are allowed only when their actual nets agree.
            for p in fp['pads']:
                if pins[p['number']]!=p['net']:issue('duplicate_pad_net','同号焊盘网络不同。',ref)
        if ref in by_bom:
            row=by_bom[ref]
            if row['Value']!=fp['value'] or row['Footprint']!=fp['footprint'] or row['Quantity']!='1':
                issue('bom_identity','BOM数值/封装/数量不匹配。',ref)
            if bool(row['DNP'].strip())!=fp['dnp']:issue('dnp_mismatch','原图BOM和PCB的DNP不一致。',ref)
        if fp['layer'] not in {'F.Cu','B.Cu'}:issue('assembly_layer','器件不在可识别装配面。',ref)
    for ref,row in by_pos.items():
        if ref not in board:continue
        fp=board[ref]
        try:
            x,y,rotation=[float(row[k]) for k in ['PosX','PosY','Rot']]
            difference=abs((rotation-fp['rotation']+180)%360-180)
            valid=all(math.isfinite(v) for v in [x,y,rotation]) and abs(x-fp['at'][0])<=.000002 and abs(y+fp['at'][1])<=.000002 and difference<=.000002
        except (ValueError,TypeError):valid=False
        if not valid:issue('position_geometry','坐标/旋转未匹配原生板；单位或原点可能有误。',ref)
        if row['Side']!={'F.Cu':'top','B.Cu':'bottom'}.get(fp['layer']):issue('position_side','坐标板面不匹配。',ref)
        if row['Val']!=fp['value'] or row['Package']!=fp['footprint'].split(':')[-1]:issue('position_identity','坐标数值或封装不匹配。',ref)
    excluded={e['reference']:e['reason'] for e in params['exclusions']}
    if not set(excluded)<=expected:issue('unknown_exclusion','不装件必须是明确存在的电气位号。')
    selected=[];omissions=[];missing=[]
    for ref,fp in sorted(board.items()):
        reason=None
        if fp['boardOnly']:reason='原生board-only机械对象，不推测为待装器件'
        elif fp['dnp']:reason='原生DNP'
        elif ref in excluded:reason=excluded[ref]
        elif params['assemblyScope']=='pcb-only':reason='本场景仅PCB'
        elif fp['layer'] not in params['assembledSides']:reason='本场景未选择该面'
        elif params['assemblyScope']=='smd-only' and not fp['smd']:reason='本场景明确只含SMD'
        if reason:
            omissions.append(dict(reference=ref,reason=reason,boardOnly=fp['boardOnly']));continue
        if fp['excludeFromBom'] or fp['excludeFromPosition']:
            issue('selected_part_excluded','选择装配的器件原生排除了BOM或坐标，需先明确修订设计。',ref)
        row=by_bom.get(ref,{})
        if not row.get('MPN') or not row.get('LCSC'):missing.append(ref)
        mounting='smd-with-through-hole-pads' if fp['smd'] and fp['hasThroughHolePads'] else 'smd' if fp['smd'] else 'through-hole' if fp['throughHole'] else 'unspecified'
        selected.append(dict(reference=ref,value=fp['value'],footprint=fp['footprint'],mounting=mounting,
            layer=fp['layer'],dnp=fp['dnp'],position=by_pos.get(ref),mpn=row.get('MPN',''),supplierPart=row.get('LCSC',''),
            requiredQuantity= params['boardQuantity'] if params['boardQuantity'] is not None else None,
            directionReviewed=False,procurementReviewed=False))
    return dict(fileConsistencyPassed=not problems,problems=problems,selectedParts=selected,omissions=omissions,
        missingProcurement=missing,counts=dict(board=len(parts),electrical=len(expected),boardOnly=len(parts)-len(expected),
            bom=len(bom),positions=len(pos),selected=len(selected),omitted=len(omissions)),
        coordinateConvention=native['positionTransform'],schematicGraphHash=schematic_graph['hash'],boardGraphHash=actual_graph['hash'],supplierFieldsAreDeclaredNotVerified=True,
        placementRotationIsCadNotSupplierCorrection=True)
