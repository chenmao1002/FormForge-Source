"""Declared CSV/XLSX mappings; retain original data and never execute formulas."""
import csv
from decimal import Decimal, InvalidOperation
import io
import math
from pathlib import Path
import re

from shapely.geometry import Point
from openpyxl import load_workbook
import openpyxl.xml

from external_cam_inputs import archive_index

MAX_ROWS=10000
MAX_COLUMNS=100


def file_bytes(location,sources,archive):
    if location.get('source'):
        data=Path(sources[location['source']['sourceId']]).read_bytes()
    else:data=archive[location['archivePath']]
    if len(data)>16*1024*1024:raise ValueError('Assembly table exceeds 16MiB.')
    return data


def workbook(data):
    if not openpyxl.xml.DEFUSEDXML:raise ValueError('Defused XML support is required.')
    z,infos=archive_index(data)
    with z:
        if any(i.filename.lower().endswith(('vbaproject.bin','.vba')) or '/externallinks/' in '/'+i.filename.lower() for i in infos):
            raise ValueError('Macros and external workbook links are not accepted.')
    return load_workbook(io.BytesIO(data),read_only=True,data_only=False,keep_links=False)


def probe_table(data,location):
    if location['format']=='xlsx':
        book=workbook(data)
        try:names=book.sheetnames
        finally:book.close()
        if location.get('sheet') is None:return dict(sheets=names,headers=[],worksheetSelectionRequired=True)
    else:names=[]
    return dict(sheets=names,**cells(data,location,preview=True))


def cells(data,location,preview=False):
    if location['format']=='csv':
        text=data.decode(location['encoding'])
        reader=csv.reader(io.StringIO(text,newline=''),delimiter=location['delimiter'],strict=True)
        iterator=((i,list(row)) for i,row in enumerate(reader,1))
        book=None
    else:
        book=workbook(data)
        if location['sheet'] not in book.sheetnames:
            book.close();raise ValueError('Declared XLSX worksheet is missing.')
        sheet=book[location['sheet']]
        if sheet.max_column and sheet.max_column>MAX_COLUMNS:
            book.close();raise ValueError('Worksheet exceeds 100 columns.')
        def sheet_rows():
            for i,row in enumerate(sheet.iter_rows(),1):
                values=[]
                for cell in row:
                    if cell.data_type=='f':raise ValueError('XLSX formulas must be converted to reviewed literal values.')
                    if cell.data_type=='e':raise ValueError('XLSX contains an error cell.')
                    values.append('' if cell.value is None else str(cell.value))
                yield i,values
        iterator=sheet_rows()
    headers=None;rows=[]
    try:
        for index,row in iterator:
            if index>MAX_ROWS+location['headerRow']:raise ValueError('Assembly table exceeds 10000 data rows.')
            if len(row)>MAX_COLUMNS or any(len(v)>4000 for v in row):raise ValueError('Table column/cell limit exceeded.')
            if index<location['headerRow']:continue
            if index==location['headerRow']:
                headers=[str(v).strip() for v in row]
                while headers and not headers[-1]:headers.pop()
                if not headers or any(not v for v in headers) or len(set(headers))!=len(headers):
                    raise ValueError('Table headers are empty or duplicated.')
                if preview:return dict(headers=headers,rows=[],headerRow=index)
                continue
            if not any(v.strip() for v in row):continue
            if len(row)>len(headers):raise ValueError('Data row is wider than the declared header.')
            row=row+['']*(len(headers)-len(row))
            rows.append(dict(row=index,values=dict(zip(headers,row))))
    finally:
        if book:book.close()
    if headers is None:raise ValueError('Declared header row is missing.')
    return dict(headers=headers,rows=rows,headerRow=location['headerRow'])


def reference(value):
    text=value.strip()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,79}',text):raise ValueError('Invalid component reference: '+text[:80])
    return text.upper()


def references(text,separator,expand):
    pattern={'comma':r',','semicolon':r';','whitespace':r'\s+','combined':r'[,;\s]+'}[separator]
    result=[]
    for token in re.split(pattern,text.strip()):
        if not token:continue
        match=re.fullmatch(r'([A-Za-z]+)(\d+)-([A-Za-z]+)(\d+)',token)
        if match:
            if not expand:raise ValueError('Reference range expansion was not declared.')
            prefix,start,end_prefix,end=match.groups();low,high=int(start),int(end)
            if prefix.casefold()!=end_prefix.casefold() or high<low or high-low>=MAX_ROWS:
                raise ValueError('Invalid or excessive reference range.')
            width=len(start) if len(start)==len(end) and (start.startswith('0') or end.startswith('0')) else 0
            result.extend(reference(prefix+str(n).zfill(width)) for n in range(low,high+1))
        else:result.append(reference(token))
        if len(result)>MAX_ROWS:raise ValueError('Reference count limit exceeded.')
    if not result:raise ValueError('Empty component reference field.')
    return result


def decimal_number(value,unit=None,angle=False):
    match=re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?)\s*([^\d\s]*)\s*',value)
    if not match:raise ValueError('Invalid numeric field: '+value[:80])
    number,suffix=match.groups()
    if suffix:
        allowed={'mm':{'mm'},'inch':{'in','inch','"'},'mil':{'mil'}}.get(unit,set())
        if angle:allowed={'deg','°'}
        if suffix.casefold() not in allowed:raise ValueError('Numeric suffix differs from the declared unit.')
    try:parsed=Decimal(number)
    except InvalidOperation as error:raise ValueError('Invalid numeric field.') from error
    if not parsed.is_finite() or abs(parsed)>Decimal('100000'):
        raise ValueError('Numeric field is non-finite or excessive.')
    return parsed


def transform_position(x,y,rotation,settings,unit):
    scale={'mm':1.0,'inch':25.4,'mil':.0254}[unit]
    angle=math.radians(settings['frameRotationDegrees'])
    px=float(x)*scale*settings['xSign'];py=float(y)*scale*settings['ySign']
    tx=px*math.cos(angle)-py*math.sin(angle)+settings['offsetXmm']
    ty=px*math.sin(angle)+py*math.cos(angle)+settings['offsetYmm']
    source_angle=math.radians(settings['sourceAngleZeroDegrees']+(float(rotation) if settings['sourceAngleDirection']=='ccw' else -float(rotation)))
    # Transform an orientation vector with the same reflection/rotation as XY.
    vx=math.cos(source_angle)*settings['xSign'];vy=math.sin(source_angle)*settings['ySign']
    rx=vx*math.cos(angle)-vy*math.sin(angle);ry=vx*math.sin(angle)+vy*math.cos(angle)
    return tx,ty,math.degrees(math.atan2(ry,rx))%360


def assembly_review(request,sources,archive,board):
    if request['assemblyMode']=='pcb-only':
        return dict(parts=[],placements=[],selectedReferences=[],problems=[],scope='explicit-bare-board-only')
    problems=[];parts=[];positions=[]
    def issue(code,message,**fields):problems.append(dict(code=code,message=message,**fields))
    def table(spec):
        result=cells(file_bytes(spec['file'],sources,archive),spec['file'])
        missing={v for v in spec['columns'].values() if v is not None}-set(result['headers'])
        if missing:raise ValueError('Mapped columns missing: '+', '.join(sorted(missing)))
        return result
    bom=request['bom'];place=request['placements'];bom_data=table(bom);place_data=table(place)
    seen={}
    for row in bom_data['rows']:
        values=row['values'];col=bom['columns']
        try:
            refs=references(values[col['reference']],bom['referenceSeparator'],bom['expandRanges'])
            quantity=decimal_number(values[col['quantity']]) if bom['quantityMode']=='column' else Decimal(len(refs))
            if quantity!=len(refs):issue('bom-quantity','Per-board quantity differs from expanded references.',row=row['row'])
            if quantity<=0 or quantity!=quantity.to_integral_value():raise ValueError('BOM quantity must be a positive integer.')
            dnp=False
            if bom['dnpMode']=='column':
                value=values[col['dnp']].strip().casefold()
                if value in {v.strip().casefold() for v in bom['dnpTrueValues']}:dnp=True
                elif value not in {v.strip().casefold() for v in bom['dnpFalseValues']}:raise ValueError('Unknown DNP value; no default was applied.')
            for ref in refs:
                if ref in seen:issue('duplicate-bom-reference','Reference occurs more than once in BOM.',reference=ref,row=row['row'])
                part=dict(reference=ref,bomRow=row['row'],sourceReferenceField=values[col['reference']],dnp=dnp,perBoardQuantity=1,
                    **{field:values[column].strip() if column else None for field,column in col.items() if field in {'value','footprint','mpn','supplierPart'}})
                seen.setdefault(ref,part);parts.append(part)
        except ValueError as error:issue('bom-row',str(error),row=row['row'])
        if len(parts)>MAX_ROWS:raise ValueError('Expanded BOM exceeds 10000 references.')
    exclusions={reference(v['reference']):v['reason'] for v in request['excludedReferences']}
    exceptions={reference(v['reference']):v['reason'] for v in request['outsidePlacementExceptions']}
    for ref in exclusions:
        if ref not in seen:issue('unknown-exclusion','Excluded reference is absent from BOM.',reference=ref)
    wanted={p['reference'] for p in parts if not p['dnp'] and p['reference'] not in exclusions}
    for ref in exceptions:
        if ref not in wanted:issue('unused-outside-exception','Outside-profile exception does not identify a selected reference.',reference=ref)
    for part in parts:
        selected=part['reference'] in wanted
        part.update(selected=selected,exclusionReason=exclusions.get(part['reference']),
                    requiredQuantity=(None if request.get('boardQuantity') is None else request['boardQuantity']) if selected else 0)
    if not parts:issue('empty-bom','Assembly review requires at least one BOM reference.')
    placed={}
    for row in place_data['rows']:
        values=row['values'];col=place['columns']
        try:
            ref=reference(values[col['reference']])
            if ref in placed:issue('duplicate-placement','Reference occurs more than once in placements.',reference=ref,row=row['row'])
            if ref not in seen:issue('placement-without-bom','Placement reference is missing from BOM.',reference=ref,row=row['row'])
            side_value=values[col['side']].strip().casefold()
            if side_value in {v.strip().casefold() for v in place['topValues']}:side='top'
            elif side_value in {v.strip().casefold() for v in place['bottomValues']}:side='bottom'
            else:raise ValueError('Unknown placement side; no default was applied.')
            x=decimal_number(values[col['x']],place['unit']);y=decimal_number(values[col['y']],place['unit'])
            rotation=decimal_number(values[col['rotation']],angle=True)
            px,py,angle=transform_position(x,y,rotation,place[side+'Transform'],place['unit'])
            inside=bool(board.covers(Point(px,py))) if board is not None else None
            selected=ref in wanted
            if selected and inside is False and ref not in exceptions:
                issue('placement-outside-profile','Selected anchor is outside board material; review connector overhang explicitly.',reference=ref,row=row['row'])
            if selected and board is None:issue('placement-profile-unknown','Board boundary could not be checked.',reference=ref,row=row['row'])
            position=dict(reference=ref,placementRow=row['row'],side=side,sourceX=values[col['x']],sourceY=values[col['y']],
                sourceRotation=values[col['rotation']],sourceSide=values[col['side']],xMm=px,yMm=py,rotationDegrees=angle,
                selected=selected,anchorInsideProfile=inside,outsideException=exceptions.get(ref))
            positions.append(position);placed.setdefault(ref,position)
            for field in ['value','footprint']:
                column=col.get(field);expected=seen.get(ref,{}).get(field)
                if column and expected is not None:
                    actual=values[column].strip()
                    normalize=lambda v:v.rsplit(':',1)[-1] if field=='footprint' and request['footprintComparison']=='name-after-colon' else v
                    if normalize(actual)!=normalize(expected):issue('placement-'+field,'BOM and placement text differ under the declared comparison.',reference=ref,row=row['row'])
        except ValueError as error:issue('placement-row',str(error),row=row['row'])
    for ref in sorted(wanted-set(placed)):issue('missing-placement','Selected reference has no placement.',reference=ref)
    for part in parts:part['placement']=placed.get(part['reference'])
    return dict(parts=parts,placements=positions,selectedReferences=sorted(wanted),problems=problems,
        bomHeaders=bom_data['headers'],placementHeaders=place_data['headers'],scope='declared-assembly-selection',
        rotationAndCentroidSupplierReviewed=False,referenceMatching='case-insensitive; originals retained')
