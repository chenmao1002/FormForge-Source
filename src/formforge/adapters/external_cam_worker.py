"""Independent CAM subprocess. Only data supplied by the source-bound adapter."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import traceback
import warnings

from gerbonara import GerberFile, ExcellonFile
from gerbonara.cam import FileSettings
from gerbonara.utils import MM, Inch

from external_cam_inputs import archive_index,read_entry,function_hint
from external_cam_geometry import profile_geometry,drill_features,render_svg,ARC_ERROR_MM
from external_cam_tables import assembly_review,probe_table,file_bytes


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')


def write_progress(output,text):
    """A Windows reader can briefly deny replacement; progress is advisory."""
    temp=output/'progress.tmp';save(temp,dict(phase=text))
    for delay in (0,.01,.02,.04,.08,.16):
        if delay:time.sleep(delay)
        try:
            temp.replace(output/'progress.json')
            return True
        except PermissionError:
            pass
    print('Progress update skipped: progress.json remained locked; final report is checked separately.',file=sys.stderr)
    return False


def expected_function(layer,count):
    role=layer['role']
    if role=='copper':
        n=layer['copperLayer'];return ['Copper','L'+str(n),'Top' if n==1 else 'Bot' if n==count else 'Inr']
    if role in {'mask','paste','legend'}:
        return [{'mask':'Soldermask','paste':'Paste','legend':'Legend'}[role],'Top' if layer['side']=='top' else 'Bot']
    if role=='profile':return ['Profile']
    if role=='drill':return ['Plated' if layer['plating']=='plated' else 'NonPlated']
    return None


def parse_layer(text,layer,count):
    source_function=function_hint(text)
    expected=expected_function(layer,count)
    if source_function and source_function[:len(expected)]!=expected:
        raise ValueError('Declared layer identity conflicts with FileFunction: '+','.join(source_function))
    before=[]
    if layer['role']=='drill':
        source_units=set(re.findall(r'(?mi)^\s*(METRIC|INCH)(?:,|\s|$)',text))
        source_units|={'METRIC' if c=='71' else 'INCH' for c in re.findall(r'(?mi)^\s*M(71|72)\s*$',text)}
        if len(source_units)>1:raise ValueError('Conflicting drill unit declarations.')
        override=layer.get('drillFormat');settings=None
        if override:
            if source_units and source_units!={'METRIC' if override['unit']=='mm' else 'INCH'}:
                raise ValueError('Drill unit override conflicts with the actual header.')
            settings=FileSettings(unit=MM if override['unit']=='mm' else Inch,
                number_format=(override['integerDigits'],override['decimalDigits']),zeros=override['zerosSuppressed'])
        if not source_units and not override:raise ValueError('Drill units are absent; supply an explicit format declaration.')
        body='\n'.join(line.split(';',1)[0] for line in text.splitlines())
        coords=re.findall(r'[XYIJ]([+-]?(?:\d+(?:\.\d*)?|\.\d+))',body)
        fixed=any('.' not in v and float(v)!=0 for v in coords)
        header_format=re.search(r'(?i)(?:FILE_FORMAT\s*=\s*\d+\s*:\s*\d+|(?:METRIC|INCH),[LT]Z,0+\.0+)',text)
        if fixed and not header_format and not (override and override.get('integerDigits') is not None):
            raise ValueError('Fixed drill coordinates require an explicit numeric format; guessing is disabled.')
        if not re.search(r'M30[\s%]*$',body):raise ValueError('Drill end marker M30 is missing.')
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            doc=ExcellonFile.from_string(text,filename=layer['path'],settings=settings)
        for warning in caught:
            message=str(warning.message)
            # Gerbonara processes G90 and sets absolute mode, but warns about its
            # position. Verify that exact source statement, no generic waiver.
            safe=bool(re.search(r'"G90": G90 header statement found after end of header$',message)) and bool(re.search(r'(?m)^G90\s*$',body)) and doc.import_settings.notation=='absolute'
            before.append(dict(message=message,losslessHeaderPlacement=safe))
            if not safe:raise ValueError('Drill parser warning requires correction: '+message[:600])
        if override and override.get('integerDigits') is not None and tuple(doc.import_settings.number_format)!=(override['integerDigits'],override['decimalDigits']):
            raise ValueError('Drill numeric format override conflicts with the source metadata.')
        if override and override.get('zerosSuppressed') and doc.import_settings.zeros!=override['zerosSuppressed']:
            raise ValueError('Drill zero suppression differs from the declared override.')
    else:
        if re.search(r'%IF',text,re.I):raise ValueError('Gerber file includes are disabled.')
        if '%IPNEG*%' in text.upper():raise ValueError('Legacy global image negation is not supported by this review.')
        if not (re.search(r'%MO(?:MM|IN)\*%',text) or re.search(r'\bG7[01]\*',text)):
            raise ValueError('Gerber units are missing; no default unit is applied.')
        if not re.search(r'M02\*\s*$',text):raise ValueError('Gerber end marker M02 is missing.')
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            doc=GerberFile.from_string(text,filename=layer['path'],enable_include_dir=None)
        if caught:raise ValueError('Gerber parser warning requires correction: '+str(caught[0].message)[:600])
        raw_function=doc.file_attrs.get('.FileFunction')
        parsed_function=list(raw_function) if raw_function is not None else None
        if parsed_function!=source_function:raise ValueError('FileFunction text and parsed Gerber attribute differ.')
    if doc.import_settings.unit is None:raise ValueError('Parser has no explicit unit.')
    if len(doc.objects)>200000:raise ValueError('Parsed CAM object budget exceeded.')
    return doc,dict(fileFunction=source_function,identityEvidence='file-metadata' if source_function else 'caller-declared-only',
        unit=str(doc.import_settings.unit),numberFormat=list(doc.import_settings.number_format),zerosSuppressed=doc.import_settings.zeros,
        notation=doc.import_settings.notation,objectCount=len(doc.objects),objectTypes=dict(Counter(type(o).__name__ for o in doc.objects)),
        filePolarity=getattr(doc,'file_attrs',{}).get('.FilePolarity'),parserWarnings=before,
        previewInterpretation='source exposure pattern; mask/paste are openings; all layers use a common top-view coordinate frame')


def review(config,output):
    request=config['request'];problems=[];layers=[];files=[];board=None;outline=None;drills=[];statement_budget=0
    def progress(text):
        write_progress(output,text)
    def issue(code,message,**details):problems.append(dict(code=code,message=message,**details))
    progress('核对完整ZIP目录与原始文件摘要')
    archive,infos=archive_index(Path(config['archivePath']).read_bytes())
    payload={}
    with archive:
        for info in infos:
            data=read_entry(archive,info);payload[info.filename]=data
            files.append(dict(path=info.filename,bytes=len(data),sha256=hashlib.sha256(data).hexdigest()))
    consumed={v['path'] for v in request['layers']}|{v['path'] for v in request['ignoredFiles']}
    table_paths=set()
    for key in ['bom','placements']:
        if request.get(key) and request[key]['file'].get('archivePath'):
            path=request[key]['file']['archivePath']
            if path in consumed:raise ValueError('Table archive entry is also a layer or ignored file.')
            table_paths.add(path)
    if set(payload)!=consumed|table_paths:raise ValueError('Every archive entry must be explicitly assigned or ignored.')
    projects=set();file_rows=[]
    for index,layer in enumerate(request['layers']):
        progress('解析制造文件 '+str(index+1)+'/'+str(len(request['layers']))+' · '+layer['path'])
        row={**layer,'parsed':False};file_rows.append(row)
        try:
            data=payload[layer['path']];text=data.decode('utf-8-sig')
            if layer['role']=='job':
                job=json.loads(text)
                attrs=job.get('FilesAttributes')
                if not isinstance(attrs,list) or len(attrs)>128:raise ValueError('Gerber job has no bounded file list.')
                for entry in attrs:
                    name=entry.get('Path')
                    if name not in payload:raise ValueError('Gerber job references a file absent from the ZIP.')
                    source_function=function_hint(payload[name].decode('utf-8-sig'))
                    declared=entry.get('FileFunction','').replace('SolderMask','Soldermask').replace('SolderPaste','Paste').split(',')
                    if source_function is None or source_function[:len(declared)]!=declared:
                        raise ValueError('Gerber job file identity differs from actual X2 metadata.')
                row.update(parsed=True,jobFileCount=len(attrs));continue
            multiplier=1
            for sr in re.findall(r'%SR([^*]*)\*%',text):
                x=re.search(r'X(\d+)',sr);y=re.search(r'Y(\d+)',sr)
                multiplier=max(multiplier,int(x.group(1)) if x else 1)
                if x and y:multiplier=max(multiplier,int(x.group(1))*int(y.group(1)))
            estimate=(text.count('*') if layer['role']!='drill' else len(text.splitlines())+sum(int(v) for v in re.findall(r'(?m)^R(\d+)',text)))*multiplier
            statement_budget+=estimate
            if statement_budget>250000:raise ValueError('CAM statement/repeat budget exceeded before parsing.')
            doc,metadata=parse_layer(text,layer,request['copperLayerCount'])
            if sum(len(d.objects) for _,d in layers)+len(doc.objects)>200000:raise ValueError('Total CAM object budget exceeded.')
            bbox=doc.bounding_box(MM)
            row.update(parsed=True,**metadata,boundsMm=None if bbox is None else [*bbox[0],*bbox[1]])
            project=getattr(doc,'file_attrs',{}).get('.ProjectId')
            if project:projects.add(tuple(project))
            layers.append((layer,doc))
            if layer['role']=='profile':
                board,outline=profile_geometry(doc)
        except (ValueError,KeyError,UnicodeError,ArithmeticError,TypeError,SyntaxError,NotImplementedError) as error:
            issue('cam-file',str(error)[:1600],path=layer['path']);row['problem']=str(error)[:1600]
    if len(projects)>1:issue('project-metadata-mismatch','CAM files contain different ProjectId metadata.')
    if board is None:issue('profile-unavailable','No valid closed board profile was obtained.')
    for layer,doc in layers:
        if layer['role']=='drill':
            try:
                rows,issues=drill_features(doc,layer,board)
                drills.extend(dict(path=layer['path'],**r) for r in rows)
                problems.extend(dict(path=layer['path'],**r) for r in issues)
            except (ValueError,ArithmeticError,TypeError) as error:issue('drill-geometry',str(error),path=layer['path'])
    coincident={}
    for row in drills:
        points=tuple((round(x,6),round(y,6)) for x,y in row['pointsMm'])
        key=(row['kind'],round(row['diameterMm'],6),min(points,tuple(reversed(points))))
        prior=coincident.get(key)
        if prior is not None and prior['path']!=row['path']:
            issue('conflicting-drill-files' if prior['plating']!=row['plating'] else 'duplicate-drill-files',
                'Coincident drill geometry is repeated in different files; plating/staging must be resolved.',path=row['path'],featureIndex=row['index'],otherFile=prior['path'])
        coincident.setdefault(key,row)
    progress('核对BOM、DNP、装配位号和明确坐标变换')
    try:assembly=assembly_review(request,config['sourcePaths'],payload,board)
    except (ValueError,KeyError,UnicodeError,ArithmeticError,TypeError) as error:
        assembly=dict(parts=[],placements=[],selectedReferences=[],problems=[dict(code='assembly-table',message=str(error)[:1600])])
    problems.extend(assembly['problems'])
    previews=[]
    if layers:
        progress('从实际解析几何生成逐层图、钻孔与装配锚点')
        for index,(layer,doc) in enumerate(layers):
            try:
                name=f'layer-{index+1:03}.svg'
                image=render_svg(layers,board,[],layer['path'],selected=layer['path'])
                (output/name).write_text(image,encoding='utf8')
                previews.append(dict(path=name,sourceFile=layer['path'],role=layer['role'],side=layer.get('side'),copperLayer=layer.get('copperLayer')))
            except (ValueError,ArithmeticError,TypeError) as error:issue('cam-render',str(error),path=layer['path'])
        try:
            (output/'assembly-overlay.svg').write_text(render_svg(layers,board,assembly['placements'],'CAM · component anchors, common top view'),encoding='utf8')
            previews.insert(0,dict(path='assembly-overlay.svg',sourceFile=None,role='assembly-overlay'))
        except (ValueError,ArithmeticError,TypeError) as error:issue('cam-render',str(error))
    result=dict(schemaVersion=1,checksPassed=not problems,fileConsistencyPassed=not problems,problems=problems,
        files=files,layers=file_rows,ignoredFiles=request['ignoredFiles'],outline=outline,drills=drills,assembly=assembly,previews=previews,
        coordinateFrame='Gerber common XY in mm, Y up, CCW positive; no inferred bottom mirroring',
        geometryApproximationMaxErrorMm=ARC_ERROR_MM,commonProjectMetadata=bool(projects) and len(projects)==1,
        electricalEquivalenceVerified=False,engineeringChecksPassed=False,procurementReviewed=False,
        supplierOrientationReviewed=False,platformPreviewVerified=False,readyForQuote=False,readyToSubmit=False,
        hardwareVerified=False,manufacturingApproved=False,orderingEnabled=False)
    progress('制造文件检查结果已生成；工程、采购和平台资格另列')
    return result


if __name__=='__main__':
    try:
        config=json.loads(Path(sys.argv[1]).read_text('utf8'));output=Path(config['output']);output.mkdir(parents=True,exist_ok=True)
        if config.get('action')=='probe-table':
            table=config['table'];payload={}
            if table.get('archivePath'):
                archive,infos=archive_index(Path(config['archivePath']).read_bytes())
                with archive:
                    info=next(i for i in infos if i.filename==table['archivePath'])
                    payload[info.filename]=read_entry(archive,info)
            save(output/'table-probe.json',probe_table(file_bytes(table,config['sourcePaths'],payload),table))
        else:save(output/'cam-report.json',review(config,output))
    except Exception:
        traceback.print_exc();sys.exit(2)
