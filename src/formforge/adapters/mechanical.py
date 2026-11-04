"""Independent versioned mechanical recipes and a frozen headless CAD engine."""
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

from .connectivity import fingerprint
from .importer import digest,verify_snapshot,within
from .mechanical_inputs import KINDS,validate_references
from .mechanical_explosion import AGENT_INSTRUCTIONS as EXPLOSION_AGENT_INSTRUCTIONS
from .mechanical_engineering import registry,AGENT_INSTRUCTIONS as ENGINEERING_AGENT_INSTRUCTIONS
from .mechanical_presentation import POLICY as EDITING_POLICY,part_presentations
from .mechanical_appearance import POLICY as APPEARANCE_POLICY
from .mechanical_integrity import POLICY as PART_INTEGRITY_POLICY
from ..store import DomainError,uid,now

CODE_ROOT=Path(__file__).resolve().parents[3]

def freeze_sources(source_records,source,check_cancel=lambda:None):
    files={};records=[];total=0;ids=set();allowed={'.step','.stp','.pdf','.md','.json','.csv','.txt','.png','.jpg','.jpeg'}
    for record in source_records:
        check_cancel();path=Path(record['path']).expanduser()
        if not path.is_absolute():raise DomainError('invalid_input','机械来源必须为明确本机绝对路径。')
        if any(p.is_symlink() or (p.exists() and bool(getattr(p.stat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_REPARSE_POINT)) for p in [path,*path.parents]):raise DomainError('invalid_input','机械来源不接受链接或重解析路径。')
        path=path.resolve()
        if path.suffix.lower() not in allowed or any(p.lower() in {'.git','.venv','.venv-mechanical','node_modules'} for p in path.parts) or any(n in path.name.lower() for n in ('token','credential','.env')):raise DomainError('invalid_input','机械来源类型或位置不允许；不能复制凭据、环境或代码。')
        if not path.is_file() or path.stat().st_size>64*1024*1024:raise DomainError('invalid_input','机械来源缺失或超过64MB。')
        total+=path.stat().st_size
        if total>256*1024*1024:raise DomainError('invalid_input','机械来源合计超过256MB。')
        relative='inputs/'+record['id']+path.suffix.lower()
        if record['id'] in ids:raise DomainError('invalid_input','机械来源ID重复。')
        if digest(path)!=record['sha256']:raise DomainError('input_changed','机械来源SHA与明确声明不一致。',409)
        target=within(source,relative);target.parent.mkdir(exist_ok=True);shutil.copyfile(path,target)
        if digest(target)!=record['sha256'] or digest(path)!=record['sha256']:raise DomainError('input_changed','机械来源在复制期间改变。',409)
        ids.add(record['id']);files[relative]=record['sha256'];records.append({**record,'frozenPath':relative,'originalPath':str(path)})
    return files,records

class MechanicalAdapter:
    def __init__(self,config):
        self.config=config;self.issue=None;self.runtime=None;self.engine_hash=None;self.engine_hashes={};self.planning=registry()
        if not config.mechanical_python:
            self.issue='尚未安装本项目独立机械环境；运行 scripts/Setup-Mechanical.ps1。';return
        try:
            migration=json.loads((CODE_ROOT/'docs/migrations/M5-mechanical-engine.json').read_text(encoding='utf8'));contents={}
            for row in migration['files']:
                path=within(CODE_ROOT,row['destination']);data=path.read_bytes()
                if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('已迁入机械源码或资源摘要不符：'+row['destination'])
                contents[Path(row['destination']).relative_to('vendor/mechanical').as_posix()]=data
            for name in ['board_outline.py','mechanical_worker.py','mechanical_inputs.py','mechanical_explosion.py','mechanical_engineering.py','mechanical_set_screws.py','mechanical_presentation.py','mechanical_appearance.py','mechanical_integrity.py','mechanical_assembly.py','mechanical_features.py','mechanical_collision.py','mechanical_pcb.py','mechanical_motion.py','insertion_spec.py']:contents[name]=Path(__file__).with_name(name).read_bytes()
            for path in sorted((CODE_ROOT/'standards/mechanical').glob('*.json')):contents['profiles/'+path.name]=path.read_bytes()
            self.engine_hashes={n:hashlib.sha256(data).hexdigest() for n,data in contents.items()};self.engine_hash=fingerprint(self.engine_hashes)
            self.engine_root=config.local/'engines/mechanical'/self.engine_hash;self.engine_root.mkdir(parents=True,exist_ok=True)
            for name,data in contents.items():
                target=within(self.engine_root,name)
                if target.exists() and digest(target)!=self.engine_hashes[name]:raise ValueError('冻结机械引擎缓存发生变化。')
                if not target.exists():target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
            self.planning=registry(self.engine_root/'profiles')
            self.runtime=self.worker(dict(action='probe'),config.local/'mechanical-probes'/uid('probe'),lambda:None,45)
            if self.runtime['versions']['cadquery']!='2.6.1' or self.runtime['versions']['cadquery-ocp']!='7.8.1.1.post1':raise ValueError('机械CAD依赖版本与已验收版本不符。')
        except (ValueError,OSError,DomainError) as error:self.issue=str(error)

    def describe(self):
        return dict(id='cadquery-mechanical',version='0.10.2',available=self.issue is None,issue=self.issue,kindSchemas=list(KINDS),units='mm',
            editingPolicy=EDITING_POLICY,
            appearance=APPEARANCE_POLICY,
            partIntegrity=PART_INTEGRITY_POLICY,
            manufacturingPlanning=dict(**self.planning,requiredFor='physical-design',agentInstructions=ENGINEERING_AGENT_INSTRUCTIONS,checkScope='Declared critical native wall sections, local mating gaps, screw paths and tool space; no full-surface DFM or production approval.',headlessNutJoints=dict(method='set_screw_nut',datum='drive-face, axis toward flat pressure tip',required=['receiver_host_part','receiver_entry_offset','pressure_part','pressure_point'],optional=['receiver_clock_degrees','capture_displacement_mm'],scope='Nominal source-backed contact and finite hardware capture; no clamp force, frictional pull retention or thread strength qualification.')),
            explosionView=dict(policy='assembly-groups-v1',default='measured-z-layers',configurableKinds=['feature_model','assembly'],agentInstructions=EXPLOSION_AGENT_INSTRUCTIONS),
            pcbaModel=dict(unit='one-assembly',source='pcb_geometry_export: assembly.step',preserveSourceColors=True,splitComponents=False,
                featureModelBinding=dict(supported=True,requestFields=['pcbRevisionId','expectedBoardHash','pcbGeometryJobId','pcbPart'],
                    outputPolicy='Complete source STEP through rigid transforms to one output, never split or fuse; native engineering checks remain separate.',automaticEnclosureMounts=False)),
            featureModel=dict(parametric=True,expressions='arithmetic and named parameters',interferenceCheckDefault=False,
                operations=['box','cylinder','cone','sphere','extrude','revolve','loft','sweep','union','cut','intersect','fillet','chamfer','shell','transform','mirror','linear_pattern','polar_pattern','step','standard_part'],
                booleanRefinement=dict(field='refine',default=False,scope='Explicit same-domain face/edge unification after a single-solid Boolean; preserve solid count. Complete PCBA assemblies remain unchanged step outputs.'),
                agentInstructions='PCB plus all its components is one PCBA assembly. Import the complete PCB assembly.step as one step feature/output part; preserve_colors defaults true. Do not split devices into mechanical output parts. General mechanical/product modelling uses feature_model. Physical design must first select process/material/finish and fixation in engineering_plan from manufacturingPlanning; critical engineering failures and physical collisions block adoption. Concept geometry may omit the plan and is reported unplanned. Read its schema; compose named features and editable parameters, then output selected features as parts. Material, supplier documents, torque budgets and manufacturing checks are not required for independent geometry. Use actual model proportions, roundovers, transitions and seams for appearance. Change parameters or features and submit a new mechanical version; preserve expressions. Only enable check_interference when overlapping output parts should block adoption. Full sketch constraint solving, automatic assembly solving and continuous motion checks are not provided by this entry.'),
            independentHead='moduleHeads.mechanical',arbitraryPythonAccepted=False,engineBundleHash=self.engine_hash,runtime=self.runtime,
            source='MIT headless Product_IDE_3D snapshot; bundled OFL fonts',manufacturingApproved=False,physicalVerification=False)

    def worker(self,params,folder,check_cancel,timeout=300):
        if self.issue:raise DomainError('dependency_missing',self.issue)
        for name,sha in self.engine_hashes.items():
            if digest(within(self.engine_root,name))!=sha:raise DomainError('input_changed','冻结机械引擎或资源摘要发生变化。',409)
        folder.mkdir(parents=True,exist_ok=True);request=folder/'request.json';response=folder/'response.json';log=folder/'native.log'
        request.write_text(json.dumps(params,ensure_ascii=False,allow_nan=False),encoding='utf8')
        temp=folder/'temp';temp.mkdir(exist_ok=True)
        env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TEMP':str(temp),'TMP':str(temp),'MPLCONFIGDIR':str(temp/'matplotlib')}
        for key in ['PYTHONHOME','PYTHONPATH']:env.pop(key,None)
        start=time.monotonic()
        with log.open('wb') as stream:
            process=subprocess.Popen([self.config.mechanical_python,'-I','-B',str(self.engine_root/'mechanical_worker.py'),str(request),str(response)],cwd=folder,env=env,stdout=stream,stderr=subprocess.STDOUT,
                creationflags=(subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name=='nt' else 0)
            try:
                while process.poll() is None:
                    check_cancel()
                    if time.monotonic()-start>timeout:raise DomainError('tool_failed','机械原生任务达到时间预算；候选未采纳。')
                    time.sleep(.1)
            finally:
                if process.poll() is None:process.kill()
                process.wait(timeout=10)
        check_cancel()
        if process.returncode or not response.is_file():raise DomainError('tool_failed','机械原生操作失败：'+log.read_text(encoding='utf8',errors='replace')[-2400:])
        result=json.loads(response.read_text(encoding='utf8'))
        result['engineProvenance']=dict(engineBundleHash=self.engine_hash,filesSha256=self.engine_hashes,pythonPath=self.config.mechanical_python,pythonSha256=digest(Path(self.config.mechanical_python)))
        return result

    def schema(self,kind):
        if kind not in KINDS:raise DomainError('invalid_input','不支持的机械设计类型。')
        result=self.worker(dict(action='schema',kind=kind),self.config.local/'mechanical-schema'/uid('schema'),lambda:None,45)
        result['explosionView']=self.describe()['explosionView']
        result['manufacturingPlanning']=self.describe()['manufacturingPlanning']
        result['editingPolicy']=EDITING_POLICY
        result['appearancePolicy']=APPEARANCE_POLICY
        result['partIntegrityPolicy']=PART_INTEGRITY_POLICY
        if kind=='feature_model':
            result['schema']['required']=list(dict.fromkeys(['kind',*result['schema'].get('required',[])]))
            result['usage']=self.describe()['featureModel']
        if kind=='enclosure':
            result['pcbPlanning']=dict(
                requiredBeforePcbLayout=True,
                scope='Plan enclosure interfaces before PCB layout; these are the supported enclosure recipe constraints, not universal PCB requirements.',
                decisions=['Choose board retention or explicitly leave retention unverified.',
                    'Reserve board outline, actual mounting holes and the complete support/fastener envelope before placing components.',
                    'Declare each connector face, mating datum, seated mating bodies and full withdrawal travel; a window fitted to the connector alone does not prove mating access.'],
                standoffRetention=dict(appliesWhen='pcbMounts is explicitly selected',minimumPureNpthHoles=3,
                    nonCollinearRequired=True,minimumDoubledTriangleAreaMm2=1,
                    postDiameterMm='max(hole diameter + 2.4, 5.5)',
                    requiredEvidence=['Actual PCB NPTH feature IDs and hole coordinates/diameters',
                        'Declared fastener shaft/head/engagement/clearance envelope and frozen JSON evidence'],
                    otherRetention='No automatic requirement for three holes when this retention method is not selected; other retention methods are not certified by this recipe.'),
                boardEnvelope=dict(cavityLengthMinimum='board.length + 2 * board_clearance',
                    cavityWidthMinimum='board.width + 2 * board_clearance',
                    standoffHeightMinimum='board.bottom_height + board_clearance',
                    cavityHeightMinimum='standoff_height + board.thickness + board.top_height + board_clearance + 2',
                    controlledTopAccess='With actual geometry binding and explicit top pcbPassages, only those component solids may protrude through their verified rounded windows; coarse height uses measured non-passage components. Original board.top_height, all-solid shell collisions and every other component clearance remain checked.',
                    coordinateFrame='Board underside centre; actual exported STEP thickness and explicit origin must be used for geometry binding.'),
                connectorAccess=dict(faces=['left','right','front','back','top'],
                    topOpening='part=lid, face=top; u/v are enclosure X/Y. Actual net aperture is width/height + 2 * clearance.',
                    topLipMarginMm=3,
                    requirement='Include female housing, carrier PCB, pull tab or cable strain relief where present; check the seated pose and continuous withdrawal against shell and every traced PCBA solid.',
                    verification='mechanical_insertion_check with frozen explicit mating evidence; connector projection and static fit alone are insufficient.'),
                roundedGeometry=dict(cornerRadiusDefaultMm=5,edgeFilletDefaultMm=.6,
                    requirement='Select explicit corner_radius, edge_fillet and each opening.radius; preserve process wall, lip and fastener clearances.'),
                physicalCompatibilityCertified=False)
        return result

    def candidate(self,project_id,job,pcb_dependency=None):
        params=job['params'];design=params['design']
        appearance=design.get('appearance') or {}
        if params.get('pcbPart') and any(row.get('part')==params['pcbPart'] for row in appearance.get('materials',[])+appearance.get('decals',[])):
            raise DomainError('invalid_input','绑定的完整PCBA保留原生源外观；请将材质与贴花应用于外壳零件。')
        if design.get('kind') not in KINDS:raise DomainError('invalid_input','请先读取 mechanical_schema 并提交支持的 kind。')
        try:encoded=json.dumps(design,ensure_ascii=False,allow_nan=False).encode('utf8')
        except (ValueError,TypeError) as error:raise DomainError('invalid_input','机械参数必须为有限数值的JSON。') from error
        if len(encoded)>512*1024:raise DomainError('invalid_input','机械参数超过512KB。')
        rid=uid('r');source=self.config.data/'projects'/project_id/'revisions'/rid/'source';source.mkdir(parents=True)
        files,records=freeze_sources(params.get('sources',[]),source)
        try:validate_references(design,files)
        except ValueError as error:raise DomainError('invalid_input',str(error)) from error
        (source/'design.json').write_bytes(encoded)
        provenance=dict(schemaVersion=1,sources=records,engineBundleHash=self.engine_hash,pcbDependency=pcb_dependency,contextSnapshot=job['contextSnapshot'])
        (source/'source-provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding='utf8')
        manifest=[dict(path=p.relative_to(source).as_posix(),sha256=digest(p),size=p.stat().st_size) for p in sorted(source.rglob('*')) if p.is_file()]
        return dict(id=rid,projectId=project_id,module='mechanical',backend='cadquery-mechanical',origin='mechanical_designed',parentRevisionId=params.get('expectedMechanicalRevisionId'),createdByJobId=job['id'],
            relativePath=source.relative_to(self.config.data).as_posix(),entrypoints=dict(mechanical='design.json'),manifest=manifest,totalBytes=sum(r['size'] for r in manifest),createdAt=now(),message=params['message'],changes=[dict(kind='mechanical_design',designKind=design['kind'])],validation=dict(status='pending'),
            mechanicalDesign=dict(kind=design['kind'],designSha256=digest(source/'design.json'),sources=records,pcbDependency=pcb_dependency,contextBinding=fingerprint({k:job['contextSnapshot'][k] for k in ['specification','preferences']}),engineBundleHash=self.engine_hash),downstreamRequiresReview=['manufacturing'])

    def build(self,revision,output,check_cancel,phase,publish,timeout):
        source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest']);deadline=time.monotonic()+timeout
        cad=output/'cad';shutil.copytree(source,cad)
        files={r['frozenPath']:r['sha256'] for r in revision['mechanicalDesign']['sources']}
        phase('使用冻结参数和来源构建真实机械实体并导出 STEP / GLB')
        built=self.worker(dict(action='build',root=str(cad),files=files),output/'native-build',check_cancel,max(.1,deadline-time.monotonic()))
        # The native outputs may be inspected during verification, always as candidates.
        for name,kind,mime in [('assembly.glb','mechanical_model','model/gltf-binary'),('assembly.step','mechanical_step','model/step'),('assembly-view.json','mechanical_view','application/json')]:publish(cad/name,kind,mime)
        before={p.relative_to(cad).as_posix():digest(p) for p in cad.rglob('*') if p.is_file()}
        phase('独立进程重新构建，并回读 STEP 和输出摘要')
        verified=self.worker(dict(action='verify',root=str(cad),files=files),output/'native-verify',check_cancel,max(.1,deadline-time.monotonic()))
        if any(digest(within(cad,n))!=sha for n,sha in before.items()):raise DomainError('input_changed','机械回读期间原导出被改变。',409)
        verify_snapshot(source,revision['manifest'])
        native=built['report'];checks=native['checks'];passed=bool(checks) and all(v is True for v in checks.values()) and native['cad_verified'] and verified['passed'] and bool(built['view']['parts'])
        engineering=native.get('measures_mm',{}).get('engineering',dict(status='unplanned',checksPassed=None,manufacturingApproved=False))
        result=dict(revisionId=revision['id'],module='mechanical',checksPassed=bool(passed),cadVerified=native['cad_verified'],verification=verified,nativeChecks=checks,warnings=native.get('warnings',[]),parts=built['view']['parts'],view=built['view'],
            normalizedDesign=built['normalizedDesign'],measures=native.get('measures_mm',native.get('measures',{})),designSha256=revision['mechanicalDesign']['designSha256'],sourceProvenance=revision['mechanicalDesign'],engineProvenance=built['engineProvenance'],runtime=self.runtime,
            appearance=built.get('appearance'),engineering=engineering,manufacturingApproved=False,hardwareVerified=False,scope='Deterministic nominal CAD, explicit dependencies and independent native STEP replay; not strength, physical fit, manufacture or continuous motion approval')
        (output/'mechanical-report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8');publish(output/'mechanical-report.json','mechanical_report','application/json')
        for path in sorted(cad.rglob('*')):
            if path.is_file() and path.name not in {'assembly.glb','assembly.step','assembly-view.json'}:
                publish(path,'mechanical_document',mimetypes.guess_type(path)[0] or 'application/octet-stream')
        return result,cad

    def read(self,revision):
        if revision.get('module')!='mechanical':raise DomainError('invalid_input','指定版本不是机械设计。')
        source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest'])
        design=json.loads((source/'design.json').read_text(encoding='utf8'))
        sources=[{**row,'path':str(within(source,row['frozenPath']))} for row in revision['mechanicalDesign']['sources']]
        return dict(revisionId=revision['id'],module='mechanical',design=design,partPresentation=part_presentations(design,self.planning['hardware']),editingPolicy=EDITING_POLICY,designSha256=digest(source/'design.json'),manifest=revision['manifest'],sources=sources,pcbDependency=revision['mechanicalDesign'].get('pcbDependency'),validation=revision['validation'],engineBundleHash=revision['mechanicalDesign']['engineBundleHash'])
