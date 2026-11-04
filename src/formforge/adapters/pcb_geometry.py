"""Read-only EDA→MCAD handoff with frozen models and native CLI placement."""
import hashlib,json,os,shutil,stat,subprocess,time,zipfile
from pathlib import Path
from .importer import within,digest,verify_snapshot
from .native_syntax import parse
from .board_outline import read_native as read_outline
from .board_stackup import read_native
from .connectivity import fingerprint
from . import pcb_mounting
from ..store import DomainError,uid

def fail(message):raise DomainError('invalid_input',message)

def component_filter(reference,references):
    if not isinstance(reference,str) or not reference or any(c in reference for c in '*?[],\\') or references.count(reference)!=1:
        fail('内部来源位号必须唯一且不含CLI通配符/列表符，防止选择范围被扩大。')
    return '--component-filter='+reference


def validate_model_scope(inventory,params):
    scope=params.get('scope','complete-pcba');models=params.get('models',[])
    if scope not in {'complete-pcba','selected-models','board-only'}:fail('未知几何覆盖范围。')
    if scope=='board-only' and models:fail('仅板体导出不能选择器件模型。')
    if scope=='selected-models' and not models:fail('局部模型导出必须有明确来源；仅板体须选择board-only。')
    if scope=='complete-pcba':
        selected={r['footprintId'] for r in models}
        missing=[f['reference'] for f in inventory['footprints'] if not f['mechanicalFeature'] and f['id'] not in selected]
        if missing:fail('完整PCBA缺少明确模型来源：'+', '.join(missing)+'。先读取pcb_geometry_read逐一绑定；局部/仅板体必须显式选择scope。')
    return scope

class PcbGeometryAdapter:
    def __init__(self,config,mechanical):
        self.config=config;self.mechanical=mechanical;self.source_sha256=digest(Path(__file__))
        self.hashes={n:digest(Path(__file__).with_name(n)) for n in ['pcb_geometry_worker.py','native_asserts.py','board_outline.py']}
        self.engine_hash=fingerprint(self.hashes);self.engine=config.local/'engines/pcb-geometry'/self.engine_hash;self.engine.mkdir(parents=True,exist_ok=True)
        for n,h in self.hashes.items():
            p=self.engine/n
            if p.exists() and digest(p)!=h:fail('冻结的板体几何引擎摘要发生变化。')
            if not p.exists():shutil.copyfile(Path(__file__).with_name(n),p)

    def run(self,args,folder,cancel,timeout):
        folder.mkdir(parents=True,exist_ok=True);env={**os.environ,'TEMP':str(folder),'TMP':str(folder),'KICAD_CONFIG_HOME':str(folder/'config'),'PYTHONUTF8':'1'}
        start=time.monotonic()
        with (folder/'process.log').open('wb') as log:
            p=subprocess.Popen([str(a) for a in args],cwd=folder,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=(subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name=='nt' else 0)
            try:
                while p.poll() is None:
                    cancel()
                    if time.monotonic()-start>timeout:raise DomainError('tool_failed','板体几何导出达到时间预算。')
                    time.sleep(.1)
            finally:
                if p.poll() is None:p.kill()
                p.wait(timeout=10)
        cancel()
        if p.returncode:raise DomainError('tool_failed','原生几何操作失败：'+(folder/'process.log').read_text(encoding='utf8',errors='replace')[-1800:])

    def read(self,revision,folder=None,cancel=lambda:None,timeout=60):
        if not self.config.kicad_python or not self.config.kicad_cli:fail('需要本项目KiCad10原生环境。')
        source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest']);relative=revision['entrypoints'].get('board')
        if not relative:fail('此版本没有原生PCB。')
        board=within(source,relative)
        if board.stat().st_size>24*1024*1024:fail('当前几何交接的原生板上限为24MB。')
        root=parse(board.read_text(encoding='utf-8-sig'));stack=read_native(board)
        if not stack['copper'] or not stack['dielectrics']:fail('几何交接需要明确保存的物理叠层；不能猜测基体厚度。')
        try:frame=read_outline(root)
        except ValueError as error:fail(str(error))
        features=pcb_mounting.verify(root,revision.get('pcbDesign') or {},source);feature_ids={r['id'] for r in features}
        footprints=[];share=Path(self.config.kicad_cli).parent.parent/'share/kicad/3dmodels'
        for fp in root.children('footprint'):
            if any(n.children('layer') and n.one('layer').values[1]=='Edge.Cuts' for n in fp.values if hasattr(n,'children')):fail('暂不支持足迹内Edge.Cuts切口。')
            reference=next((p.values[2] for p in fp.children('property') if p.values[1]=='Reference'),None);models=[]
            for index,m in enumerate(fp.children('model')):
                name=m.values[1];candidate=None
                if name.startswith('${KICAD10_3DMODEL_DIR}/'):candidate=within(share,name.split('}/',1)[1])
                elif name.startswith('${KIPRJMOD}/'):candidate=within(source,name.split('}/',1)[1])
                models.append(dict(index=index,path=name,resolvedPath=str(candidate) if candidate and candidate.is_file() else None,sha256=digest(candidate) if candidate and candidate.is_file() else None,
                    transform={k:[float(v) for v in m.one(k).one('xyz').values[1:]] for k in ['offset','scale','rotate']},hidden=any(len(h.values)==1 or h.values[1] in ('yes','true') for h in m.children('hide'))))
            footprints.append(dict(id=fp.one('uuid').values[1],reference=reference,sha256=hashlib.sha256(fp.raw.encode('utf8')).hexdigest(),models=models,mechanicalFeature=fp.one('uuid').values[1] in feature_ids))
        folder=folder or self.config.local/'pcb-geometry-read'/uid('read');folder.mkdir(parents=True,exist_ok=True)
        request=folder/'request.json';response=folder/'response.json';request.write_text(json.dumps(dict(board=str(board))),encoding='utf8')
        if any(digest(self.engine/n)!=h for n,h in self.hashes.items()):fail('冻结板体几何引擎被改变。')
        self.run([self.config.kicad_python,'-I','-B',self.engine/'pcb_geometry_worker.py',request,response],folder,cancel,timeout)
        native=json.loads(response.read_text(encoding='utf8'));verify_snapshot(source,revision['manifest'])
        bypad={f['padId']:f for f in features}
        for hole in native['holes']:
            if hole['id'] not in bypad:continue
            f=bypad[hole['id']]
            if hole['plated'] or hole['number'] or hole['footprintId']!=f['id'] or hole['at']!=f['at'] or any(abs(d-f['drill'])>1e-6 for d in hole['drill']):fail('原生钻孔与明确机械特征不一致。')
            hole.update(mountingPurpose='explicit-mounting-feature',featureId=f['id'])
        if set(bypad)!={h['id'] for h in native['holes'] if h.get('featureId')}:fail('声明机械孔未出现在原生钻孔表。')
        x,y=frame['origin']
        return dict(revisionId=revision['id'],boardSha256=digest(board),units='mm',outline=dict(xmin=x,ymin=y,xmax=x+frame['width'],ymax=y+frame['height'],cornerRadius=frame['cornerRadius']),footprints=footprints,holes=native['holes'],
            mountingFeatures=features,assemblyPolicy=dict(defaultUnit='pcba',mechanicalInput='assembly.step',splitComponents=False,defaultCoverageScope='complete-pcba',partialRequiresExplicitScope=True),
            nominalThickness=native['thickness'],modelThickness=stack['modelThickness'],expectedSubstrateThickness=round(stack['modelThickness']-stack['copper'][0]['thickness']-stack['copper'][-1]['thickness'],6),
            nativeVersion=native['engineVersion'],coordinateContract='KiCad 2D (x,y) → STEP CAD (x,-y,z), user origin 0,0; substrate z=0. Outer copper and mask excluded.',mountingHolePurposeInferred=False)

    def export(self,revision,params,output,cancel,phase,publish):
        deadline=time.monotonic()+params['timeoutSeconds'];remaining=lambda:max(.1,deadline-time.monotonic())
        cli_sha=digest(Path(self.config.kicad_cli));self.run([self.config.kicad_cli,'version'],output/'cli-version',cancel,remaining())
        cli_version=(output/'cli-version/process.log').read_text(encoding='utf8').strip()
        if not cli_version.startswith('10.'):fail('几何交接需要KiCad10 CLI。')
        phase('读取原生板框、钻孔和模型引用');inventory=self.read(revision,output/'inventory',cancel,min(60,remaining()))
        if inventory['boardSha256']!=params['expectedBoardHash']:raise DomainError('version_conflict','板体几何请求的PCB摘要已变化。',409)
        scope=validate_model_scope(inventory,params)
        source=within(self.config.data,revision['relativePath']);snapshot=output/'source';snapshot.mkdir()
        for row in revision['manifest']:
            cancel();target=within(snapshot,row['path']);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(within(source,row['path']),target)
        verify_snapshot(snapshot,revision['manifest'])
        private=output/'workspace';private.mkdir();models_dir=private/'models';models_dir.mkdir();cad=output/'cad';cad.mkdir()
        tree=parse(within(snapshot,revision['entrypoints']['board']).read_text(encoding='utf-8-sig'));fps={f['id']:f for f in inventory['footprints']};selected={};records=[];ids=set();total=0
        for row in params['models']:
            fp=fps.get(row['footprintId']);index=row['modelIndex'];s=row['source'];key=(row['footprintId'],index)
            if not fp or fp['sha256']!=row['expectedFootprintHash'] or index>=len(fp['models']):raise DomainError('version_conflict','器件或模型索引/摘要已变化。',409)
            component_filter(fp['reference'],[f['reference'] for f in fps.values()])
            m=fp['models'][index]
            if m['path']!=row['expectedModelPath'] or m['hidden']:fail('模型引用已变或被隐藏，不能静默作为导出实体。')
            if key in selected or s['id'] in ids:fail('模型或来源ID重复。')
            path=Path(s['path'])
            if not path.is_absolute() or any(p.is_symlink() or (p.exists() and getattr(p.stat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT) for p in [path,*path.parents]):fail('模型来源必须为明确无链接绝对路径。')
            if path.suffix.lower() not in ['.step','.stp'] or not path.is_file() or path.stat().st_size>64*1024*1024:fail('模型只接受不超过64MB的STEP/STP。')
            total+=path.stat().st_size
            if total>256*1024*1024:fail('模型来源总量超过256MB。')
            if digest(path)!=s['sha256']:raise DomainError('input_changed','模型来源SHA不符。',409)
            dest=models_dir/(s['id']+'.step');shutil.copyfile(path,dest)
            if digest(dest)!=s['sha256'] or digest(path)!=s['sha256']:raise DomainError('input_changed','模型在冻结时变化。',409)
            ids.add(s['id']);selected[key]=dest.name;records.append({**row,'frozenPath':'workspace/models/'+dest.name})
        for fid in {k[0] for k in selected}:
            if {k[1] for k in selected if k[0]==fid}!=set(range(len(fps[fid]['models']))):fail('一个所选器件必须明确覆盖其全部模型引用。')
        edits=[]
        for fp in tree.children('footprint'):
            fid=fp.one('uuid').values[1]
            for index,node in enumerate(fp.children('model')):
                name=selected.get((fid,index));replacement=''
                if name:
                    tail=' '.join(c.raw for c in node.values[2:] if hasattr(c,'raw'))
                    replacement='(model '+json.dumps('${KIPRJMOD}/models/'+name)+' '+tail+')'
                edits.append((node.start,node.end,replacement))
        text=tree.source
        for start,end,replacement in sorted(edits,reverse=True):text=text[:start]+replacement+text[end:]
        board=private/'geometry.kicad_pcb';board.write_text(text,encoding='utf8');parts=[]
        selections=[('pcb',None)]+[('component_'+fid.replace('-',''),fps[fid]['reference']) for fid in sorted({k[0] for k in selected})]
        for name,ref in selections:
            cancel();phase('KiCad CLI 导出实际板体' if ref is None else 'KiCad CLI 导出已冻结器件 '+ref)
            target=cad/(name+'.step');args=[self.config.kicad_cli,'pcb','export','step','--user-origin','0x0mm','--no-optimize-step','--output',target]
            args+=['--board-only','--cut-vias-in-body'] if ref is None else ['--no-board-body',component_filter(ref,[f['reference'] for f in fps.values()])]
            self.run([*args,board],output/('cli-'+name),cancel,remaining())
            if not target.is_file():fail('原生CLI没有输出声明的STEP。')
            parts.append(dict(id=name,reference=ref,footprintId=next((fid for fid in fps if 'component_'+fid.replace('-','')==name),None),file=target.name,sha256=digest(target)))
        geometry={**inventory,'parts':parts,'thickness':inventory['expectedSubstrateThickness']}
        phase('独立CAD回读实体、板坐标和钻孔空隙');proof=self.mechanical.worker(dict(action='pcb_geometry_verify',root=str(cad),geometry=geometry),output/'cad-proof',cancel,remaining())
        replay=output/'replay';replay.mkdir()
        for p in parts:shutil.copyfile(cad/p['file'],replay/p['file'])
        phase('第二原生进程复核板体、孔洞与零件坐标')
        replay_proof=self.mechanical.worker(dict(action='pcb_geometry_verify',root=str(replay),geometry=geometry),output/'cad-replay',cancel,remaining())
        if any(proof[k]!=replay_proof[k] for k in ['checks','substrate','drillVoidChecks','partSolidIndices','outlineProof']) or any(proof['view'][k]!=replay_proof['view'][k] for k in ['parts','componentParts']):fail('独立原生几何复核结果不一致。')
        verify_snapshot(source,revision['manifest']);verify_snapshot(snapshot,revision['manifest'])
        if digest(Path(self.config.kicad_cli))!=cli_sha:fail('KiCad CLI在导出期间被改变。')
        for row in records:
            if digest(output/row['frozenPath'])!=row['source']['sha256']:fail('已冻结模型来源被改变。')
        selected_ids={k[0] for k in selected};components=[f for f in inventory['footprints'] if not f['mechanicalFeature']];omitted=[dict(id=f['id'],reference=f['reference'],reason='not-selected' if f['models'] else 'no-model-reference') for f in components if f['id'] not in selected_ids]
        result=dict(revisionId=revision['id'],boardSha256=inventory['boardSha256'],geometryScope=scope,completePcbaVerified=not omitted and proof['checksPassed'] and replay_proof['checksPassed'],checksPassed=proof['checksPassed'] and replay_proof['checksPassed'],geometry=geometry,nativeProof=proof,independentReplayMatched=True,models=records,
            coverage=dict(selectedComponents=len(selected_ids),totalComponents=len(components),complete=not omitted,omitted=omitted,substrateFeatures=inventory['mountingFeatures']),
            engineProvenance=dict(nativeBundleHash=self.engine_hash,nativeFiles=self.hashes,adapterSha256=self.source_sha256,mountingPolicySha256=digest(Path(pcb_mounting.__file__)),cliVersion=cli_version,cliPath=self.config.kicad_cli,cliSha256=cli_sha,cadEngine=proof['engineProvenance']),manufacturingApproved=False,hardwareVerified=False,
            scope='Frozen selected KiCad library models, native CLI placement and drilled substrate; omitted components, external mating parts and physical tolerance/fit remain unverified.')
        (output/'pcb-geometry-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
        for path,kind,mime in [(cad/'assembly.step','pcb_geometry_step','model/step'),(cad/'assembly.glb','pcb_geometry_model','model/gltf-binary'),(cad/'assembly-view.json','pcb_geometry_view','application/json'),(output/'pcb-geometry-report.json','pcb_geometry_report','application/json')]:publish(path,kind,mime)
        archive=output/'pcb-geometry.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for base in ['source','workspace','cad']:
                for p in sorted((output/base).rglob('*')):
                    if p.is_file():cancel();z.write(p,p.relative_to(output).as_posix())
            z.writestr('geometry-manifest.json',json.dumps(dict(revision=revision,result=result,files=[dict(path=p.relative_to(output).as_posix(),sha256=digest(p)) for base in ['source','workspace','cad'] for p in sorted((output/base).rglob('*')) if p.is_file()]),ensure_ascii=False,indent=2))
        if time.monotonic()>=deadline:raise DomainError('tool_failed','几何交接达到总时间预算。')
        publish(archive,'pcb_geometry_project','application/zip');cancel();return result
