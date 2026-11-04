"""Domestic JLC SMT selection drafts; every electrical reference remains explicit."""
import csv
from decimal import Decimal,InvalidOperation
import hashlib
import json
import re


BOM_NAME='jlc-smt-bom-matching-only.csv'
CPL_NAME='jlc-smt-cpl-matching-only.csv'
NOTES_NAME='jlc-smt-matching-notes.json'


def _group_bom(rows):
    """Combine identical selected JLC items; unknown and mail-in parts stay explicit."""
    groups={}
    for row in rows:
        fields=tuple((k,v) for k,v in row.items() if k not in {'Designator','Quantity'})
        eligible=row['FormForge Status']=='selected' and bool(row['LCSC Part #'])
        key=(fields,None if eligible else row['Designator'])
        groups.setdefault(key,[]).append(row)
    result=[]
    for members in groups.values():
        refs=sorted((r['Designator'] for r in members),key=lambda ref:[int(p) if p.isdigit() else p for p in re.split(r'(\d+)',ref)])
        result.append({**members[0],'Designator':','.join(refs),'Quantity':len(refs)})
    return result


def _verify_pair(output,expected_bom,expected_cpl):
    """Expand grouped CSV and compare every original field and every CPL row."""
    with (output/BOM_NAME).open(encoding='utf-8-sig',newline='') as f:bom=list(csv.DictReader(f))
    with (output/CPL_NAME).open(encoding='utf-8-sig',newline='') as f:cpl=list(csv.DictReader(f))
    expected={r['Designator']:{k:str(v) for k,v in r.items()} for r in expected_bom}
    expanded={}
    for row in bom:
        refs=row['Designator'].split(',')
        if row['Quantity']!=str(len(refs)):raise ValueError('Matching BOM quantity roundtrip failed')
        for ref in refs:
            if ref in expanded or ref not in expected:raise ValueError('Matching BOM reference roundtrip failed')
            original={**row,'Designator':ref,'Quantity':'1'}
            if original!=expected[ref]:raise ValueError('Matching BOM identity roundtrip failed')
            expanded[ref]=original
    if expanded!=expected:raise ValueError('Matching BOM coverage roundtrip failed')
    if cpl!=[{k:str(v) for k,v in r.items()} for r in expected_cpl]:raise ValueError('Matching CPL roundtrip failed')


def write_matching_files(output,result):
    """Write only after validation; missing choices/centers remain visible, never DNP."""
    bom=[];cpl=[];details=[];errors=[];seen=set()
    def issue(ref,message):errors.append(dict(code='invalid_matching_file',reference=ref,message=message))
    for row in result['rows']:
        ref=row['reference']
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',ref) or ref.upper() in seen:
            issue(ref,'嘉立创匹配位号无效或不区分大小写时重复。')
        seen.add(ref.upper())
        selected=row['disposition']!='unresolved'
        sku=row.get('supplierPartNumber') if row.get('supplier','').casefold() in {'lcsc','jlcpcb'} else None
        if sku and not re.fullmatch(r'C\d+',sku):issue(ref,'供应商C编号格式无效，不能导出为嘉立创料号。')
        bom_row={'Comment':row.get('mpn') if selected else row.get('nativeValue'),
            'Designator':ref,'Footprint':row.get('packageName') if selected else row.get('footprint'),
            'LCSC Part #':sku or '', 'Quantity':1,'Manufacturer':row.get('manufacturer') or '',
            'MPN':row.get('mpn') or '', 'FormForge Status':row['disposition'],'Mounting':row.get('mounting') or 'unknown'}
        if not bom_row['Comment'] or not bom_row['Footprint']:issue(ref,'匹配BOM的原生值/完整型号及封装不能为空。')
        if any(str(v).lstrip().startswith(('=','+','-','@')) for v in bom_row.values()):issue(ref,'匹配BOM文本不能包含表格公式。')
        bom.append(bom_row)
        pos=row.get('supplierPosition');native=row.get('nativePosition') or {}
        try:
            x=Decimal(str(pos['xMm'] if pos else native['PosX']))
            y=Decimal(str(pos['yMm'] if pos else native['PosY']))
            rot=Decimal(str(pos['rotationDegrees'] if pos else native['Rot']))
            layer=pos['layer'] if pos else {'top':'Top','bottom':'Bottom'}[native['Side']]
            if not all(v.is_finite() for v in [x,y,rot]) or layer not in {'Top','Bottom'}:raise ValueError()
            if abs(x)>1000000 or abs(y)>1000000:raise ValueError()
            rot=((rot%360)+360)%360
            if rot!=rot.quantize(Decimal('.001')):raise ValueError()
        except (InvalidOperation,ValueError,KeyError,TypeError):
            issue(ref,'匹配坐标缺失或无效；需有限毫米坐标、明确贴装面及最多三位小数角度。');continue
        basis='declared-engineering-center' if pos else 'unreviewed-native-anchor'
        cpl.append({'Designator':ref,'Mid X':format(x,'f'),'Mid Y':format(y,'f'),'Layer':layer,'Rotation':format(rot,'f'),
            'FormForge Position Basis':basis,'FormForge Status':row['disposition']})
        details.append(dict(reference=ref,disposition=row['disposition'],mounting=row.get('mounting'),
            positionBasis=basis,partSourceId=row.get('partSourceId'),nativePosition=native,
            declaredPosition=pos,platformZeroVerified=False,pickupPointVerified=False,productionPlacementApproved=False))
    if len(bom)!=result['selectedReferenceCount'] or len(cpl)!=len(bom):issue(None,'匹配资料未完整覆盖制造场景全部器件。')
    if errors:return errors
    grouped_bom=_group_bom(bom)
    files=[]
    for name,rows in [(BOM_NAME,grouped_bom),(CPL_NAME,cpl)]:
        path=output/name
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        files.append(dict(name=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    _verify_pair(output,bom,cpl)
    grouping=dict(strategy='identical-selected-jlc-code-and-all-declared-bom-fields',
        bomLineCount=len(grouped_bom),cplReferenceCount=len(cpl),quantitiesPerBoard=True,referenceCoverageVerified=True,
        groups=[dict(line=index+1,references=row['Designator'].split(','),quantityPerBoard=row['Quantity'],
                     supplierPartNumber=row['LCSC Part #']) for index,row in enumerate(grouped_bom)])
    result['matchingExport']={k:v for k,v in grouping.items() if k!='groups'}
    notes=dict(schemaVersion='formforge.smt-matching/2',purpose='part-selection-only',bomGrouping=grouping,
        projectId=result.get('projectId'),revisionId=result.get('revisionId'),jobId=result.get('jobId'),
        manufacturingDependency=result.get('manufacturingDependency'),boardQuantity=result.get('boardQuantity'),
        selectedReferenceCount=result['selectedReferenceCount'],unresolvedReferences=result['unresolvedReferences'],
        omittedSelectedReferences=[],allSelectedReferencesRetained=True,scope='all references selected by bound manufacturing scene; original scene exclusions remain in bound report',files=files,parts=details,
        warning='Selection drafts only. Unresolved items are not DNP. Native anchors are unreviewed; declared engineering centers and CAD rotations are not platform zero/pickup approval. Do not release production.',
        supplierFilesReady=False,platformPartMatchingVerified=False,platformPreviewVerified=False,readyToSubmit=False,manufacturingApproved=False)
    (output/NOTES_NAME).write_text(json.dumps(notes,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    return []
