"""Read-only, source-bound native manufacturing exports and assembly preflight."""
import csv
import hashlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

from .connectivity import fingerprint
from .importer import digest, within, verify_snapshot, reparse
from .manufacturing_preflight import preflight
from ..jobs import Cancelled
from ..processes import run_process
from ..store import DomainError


def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')


def native_check_summary(erc,drc,erc_code,drc_code,parity):
    """Keep strict acceptance and expose errors, advisories and exclusions separately."""
    erc_all=[v for sheet in erc['sheets'] for v in sheet['violations']]
    drc_all=[v for key in ['violations','unconnected_items','schematic_parity'] for v in drc[key]]
    active=lambda rows:[v for v in rows if not v.get('excluded')]
    erc_items,drc_items=active(erc_all),active(drc_all)
    unconnected,parity_items=active(drc['unconnected_items']),active(drc['schematic_parity'])
    return dict(nativeChecksPassed=erc_code==drc_code==0 and not erc_items and not drc_items,
        nativeErrorChecksPassed=(erc_code in {0,5} and drc_code in {0,5} and parity
            and not unconnected and not parity_items
            and all(v.get('severity')=='warning' for v in erc_items+drc_items)),
        nativeChecks=dict(ercExitCode=erc_code,drcExitCode=drc_code,ercFindings=len(erc_items),drcFindings=len(drc_items),
            ercErrors=sum(v.get('severity')=='error' for v in erc_items),
            ercWarnings=sum(v.get('severity')=='warning' for v in erc_items),
            ercExcluded=sum(bool(v.get('excluded'))for v in erc_all),
            drcErrors=sum(v.get('severity')=='error' for v in drc_items),
            drcWarnings=sum(v.get('severity')=='warning' for v in drc_items),
            drcExcluded=sum(bool(v.get('excluded'))for v in drc_all),
            unconnected=len(unconnected),schematicParityFindings=len(parity_items),schematicParityChecked=parity))


class ManufacturingAdapter:
    kind='manufacturing.prepare'
    version='kicad10-manufacturing/0.8.2'
    read_only=True

    def __init__(self,config,store):
        self.config,self.store=config,store
        names=['manufacturing.py','manufacturing_native.py','manufacturing_preflight.py','pcb_interface_legends.py','native_asserts.py','connectivity.py','importer.py']
        self.files={name:digest(Path(__file__).with_name(name)) for name in names}
        self.engine_hash=fingerprint(self.files)
        self.engine=config.local/'engines/manufacturing'/self.engine_hash
        self.engine.mkdir(parents=True,exist_ok=True)
        for name,h in self.files.items():
            target=self.engine/name
            if not target.exists():shutil.copyfile(Path(__file__).with_name(name),target)
            if digest(target)!=h:raise DomainError('input_changed','制造引擎缓存摘要不一致。',409)
        self.tools={name:dict(path=str(Path(path).resolve()),sha256=digest(Path(path))) for name,path in
            [('cli',config.kicad_cli),('python',config.kicad_python)] if path and Path(path).is_file()}

    def profile(self):
        return dict(available=set(self.tools)=={'cli','python'},adapterVersion=self.version,engineHash=self.engine_hash,
            tools=self.tools,readOnly=True,formats=['Gerber-X2','Excellon-mm-PTH-NPTH','native-bom-csv','native-position-csv'],
            supportedInput='FormForge traceable PCB, or explicitly selected complete native import; both require a complete native schematic',
            inputScopes=['formforge','complete-native-import'],origin='absolute-board-zero',
            orderingEnabled=False,quotingEnabled=False,paymentEnabled=False,
            scope='fixed-version review exports, complete pin graph and assembly file consistency; CAM/platform/procurement and engineering approval separate')

    def revision(self,project,params):
        revision=self.store.get('revisions',params['revisionId'])
        if revision['projectId']!=project or revision.get('module','eda')!='eda':raise DomainError('invalid_input','制造输入必须是同工程EDA版本。')
        native_import=params.get('inputScope','formforge')=='complete-native-import'
        if not all(revision['entrypoints'].get(k) for k in ['board','schematic']) or (not native_import and not revision.get('pcbDesign')):
            message=('完整原生导入需要同版完整原图及PCB；board-only原生草稿不能进入。'if native_import else
                     '首条制造资料流程需要可追溯FormForge PCB及完整原图；外部Gerber审核另行接入。')
            raise DomainError('unsupported_capability',message)
        source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest'])
        if native_import:
            board=within(source,revision['entrypoints']['board']);schematic=within(source,revision['entrypoints']['schematic'])
            tracked={within(source,row['path']) for row in revision['manifest']}
            if (board.suffix!='.kicad_pcb' or schematic.suffix!='.kicad_sch' or board.with_suffix('.kicad_sch')!=schematic
                    or not {board,schematic}<=tracked):
                raise DomainError('unsupported_capability','完整原生导入须在同一版本清单中绑定同目录同名的.kicad_pcb和.kicad_sch，执行真实原图一致性检查。')
        if len(revision['manifest'])>2000 or sum(row['size'] for row in revision['manifest'])>256*1024*1024 or within(source,revision['entrypoints']['board']).stat().st_size>24*1024*1024:
            raise DomainError('unsupported_capability','首条制造资料输入上限为2000文件/256MiB，原生板24MiB。')
        if digest(within(source,revision['entrypoints']['board']))!=params['expectedBoardHash']:
            raise DomainError('version_conflict','制造输入板SHA不符。',409)
        return revision,source

    def validate(self,project,params):
        if not self.profile()['available']:raise DomainError('dependency_missing','制造导出需要本机KiCad10 CLI与同版Python。')
        self.revision(project,params)

    def execute(self,ctx):
        job=ctx.job;params=job['params'];cancel=ctx.cancel;publish=ctx.publish;phase=ctx.phase
        revision,source=self.revision(job['projectId'],params)
        output=self.config.data/'projects'/job['projectId']/'jobs'/job['id'];output.mkdir(parents=True)
        frozen=output/'source';workspace=output/'workspace';native_dir=output/'native';native_dir.mkdir()
        deadline=time.monotonic()+params['timeoutSeconds'];commands=[]
        phase('冻结指定版本的全部制造输入与实际工具摘要')
        for row in revision['manifest']:
            cancel();src=within(source,row['path']);target=within(frozen,row['path'])
            if reparse(src) or any(reparse(p) for p in src.parents if p.is_relative_to(source)):
                raise DomainError('input_changed','制造输入不接受链接或重解析路径。',409)
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,target)
            if digest(target)!=row['sha256']:raise DomainError('input_changed','制造输入冻结时变化。',409)
        shutil.copytree(frozen,workspace)
        for name,h in self.files.items():
            if digest(self.engine/name)!=h:raise DomainError('input_changed','制造引擎摘要变化。',409)
            target=output/'engine'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(self.engine/name,target)
        self.verify_tools()
        save(output/'source-revision.json',revision)
        input_scope=params.get('inputScope','formforge')
        binding=dict(projectId=job['projectId'],jobId=job['id'],revisionId=revision['id'],boardSha256=params['expectedBoardHash'],inputScope=input_scope,
            parameters=params,contextSnapshot=job['contextSnapshot'],engineHash=self.engine_hash,engineFiles=self.files,tools=self.tools,
            pcbDependency=revision.get('pcbDesign'),inputManifest=revision['manifest'],origin='absolute-board-zero',units='mm',
            ignoredLocalState='Only manifest files frozen; .kicad_prl and other unregistered editor state not consumed')
        save(output/'binding.json',binding)
        scratch=output/'scratch';scratch.mkdir();env=os.environ.copy()
        env.update(TEMP=str(scratch),TMP=str(scratch),KICAD_CONFIG_HOME=str(scratch/'config'),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
        share=Path(self.tools['cli']['path']).parent.parent/'share/kicad'
        private=scratch/'config/10.0';private.mkdir(parents=True)
        for name in ['fp-lib-table','sym-lib-table']:
            if (share/'template'/name).is_file():shutil.copyfile(share/'template'/name,private/name)
        for var,folder in [('KICAD10_SYMBOL_DIR','symbols'),('KICAD10_FOOTPRINT_DIR','footprints'),('KICAD10_3DMODEL_DIR','3dmodels')]:
            env[var]=str(share/folder)

        def run(label,args,violations=False,python=False):
            phase(label);log=output/f'command-{len(commands)+1}.log'
            row=run_process([self.tools['python' if python else 'cli']['path'],*map(str,args)],workspace,log,env,cancel,deadline)
            row['name']=label;commands.append(row);publish(log,'manufacturing_log','text/plain')
            text=log.read_text(encoding='utf8',errors='replace')
            if row['exitCode'] not in ({0,5} if violations else {0}) or ('Library table' in text and 'skipping' in text):
                raise DomainError('tool_failed',label+'失败；请查看同任务日志。')
            return row['exitCode']

        sch=within(workspace,revision['entrypoints']['schematic']);board=within(workspace,revision['entrypoints']['board'])
        run('核对实际CLI版本',['--version'])
        cli_version=(output/'command-1.log').read_text(encoding='utf8').strip()
        run('原生读取全部器件、装配属性、钻孔与坐标基准',[output/'engine/manufacturing_native.py',board,native_dir/'board.json'],python=True)
        native=json.loads((native_dir/'board.json').read_text(encoding='utf8'))
        if not native['toolVersion'].startswith('10.') or native['toolVersion']!=cli_version:raise DomainError('dependency_missing','制造CLI与读取内核须为相同KiCad10版本。')
        erc_code=run('重新检查同版原理图ERC',['sch','erc','--format','json','--severity-all','--severity-exclusions','--exit-code-violations','-o',native_dir/'erc.json',sch],True)
        parity=board.with_suffix('.kicad_sch')==sch
        drc_args=['pcb','drc','--format','json','--severity-all','--severity-exclusions','--exit-code-violations']
        if parity:drc_args.append('--schematic-parity')
        drc_code=run('重新检查同版PCB规则、未连与原图一致性',[*drc_args,'-o',native_dir/'drc.json',board],True)
        run('导出完整原生引脚网络',['sch','export','netlist','--format','kicadxml','-o',native_dir/'netlist.xml',sch])
        run('导出原始逐位号BOM，不隐藏DNP或插件',['sch','export','bom','--fields','Reference,Value,Footprint,QUANTITY,DNP,MPN,LCSC',
            '--labels','Reference,Value,Footprint,Quantity,DNP,MPN,LCSC','--group-by','','--ref-range-delimiter','','--keep-tabs','--keep-line-breaks','-o',native_dir/'bom.csv',sch])
        run('导出双面全部原生坐标，毫米与绝对原点',['pcb','export','pos','--format','csv','--units','mm','--side','both','-o',native_dir/'positions.csv',board])
        layers=native['copperLayers']+['F.Mask','B.Mask','F.Paste','B.Paste','F.Silkscreen','B.Silkscreen','Edge.Cuts']
        run('按实际全部铜层导出Gerber-X2，不改变铜填充',['pcb','export','gerbers','--layers',','.join(layers),'-o',str(native_dir/'gerbers')+os.sep,board])
        run('导出分开的PTH/NPTH毫米钻孔及原生报告',['pcb','export','drill','--format','excellon','--drill-origin','absolute','--excellon-units','mm',
            '--excellon-separate-th','--generate-report','--report-path',native_dir/'drill-report.txt','-o',str(native_dir/'drills')+os.sep,board])
        phase('逐位号、全部引脚分组、原生BOM和双面坐标核对')
        result=preflight(native,native_dir/'netlist.xml',native_dir/'bom.csv',native_dir/'positions.csv',params)
        result['camFiles']=self.cam(native_dir,native)
        if not result['camFiles']['structuralChecksPassed']:
            result['problems'].append(dict(code='cam_structure',message='Gerber/钻孔结构检查未通过，见完整报告。',reference=None));result['fileConsistencyPassed']=False
        cam_files=sorted((native_dir/'gerbers').iterdir())+sorted((native_dir/'drills').glob('*.drl'))
        if len({p.name.casefold() for p in cam_files})!=len(cam_files):raise DomainError('tool_failed','制造CAM文件同名，不能扁平打包。')
        fabrication_zip=output/'fabrication-gerbers.zip'
        with zipfile.ZipFile(fabrication_zip,'w',zipfile.ZIP_DEFLATED) as z:
            for path in cam_files:cancel();z.write(path,path.name)
        with zipfile.ZipFile(fabrication_zip) as z:
            if z.testzip() is not None or any(hashlib.sha256(z.read(p.name)).hexdigest()!=digest(p) for p in cam_files):
                raise DomainError('input_changed','独立Gerber/钻孔包摘要不符。',409)
        result['fabricationArchive']=dict(sha256=digest(fabrication_zip),files=[p.name for p in cam_files],approvedForProduction=False)
        erc=json.loads((native_dir/'erc.json').read_text(encoding='utf-8-sig'));drc=json.loads((native_dir/'drc.json').read_text(encoding='utf-8-sig'))
        if 'sheets' not in erc or not all(k in drc for k in ['violations','unconnected_items','schematic_parity']):raise DomainError('tool_failed','无法识别原生ERC/DRC结构。')
        result.update(revisionId=revision['id'],jobId=job['id'],projectId=job['projectId'],boardSha256=params['expectedBoardHash'],engineHash=self.engine_hash,inputScope=input_scope,
            exportCompleted=True,checksPassed=result['fileConsistencyPassed'],**native_check_summary(erc,drc,erc_code,drc_code,parity),
            engineeringChecksPassed=False,procurementReviewed=False,readyForQuote=False,readyToSubmit=False,manufacturingApproved=False,
            platformPreviewVerified=False,hardwareVerified=False,orderingEnabled=False,
            pending=['完整电气/参考/阻抗与制造工艺资格未形成放行','采购型号/库存/替料和装配方向未审核','CAM几何/外形尺寸与实际平台预览未审核','数量/工艺等空项保持未明确；本任务只准备审核资料'],
            commands=commands,binding=binding,nativeToolVersion=cli_version,
            scope=('完整原生导入的同版原图/PCB资料导出与完整引脚/BOM/坐标核对；未推定FormForge设计审查/交接、供应商或生产批准。'
                   if input_scope=='complete-native-import' else
                   '固定版本资料导出与完整引脚/BOM/坐标一致性；原生规则、工程、采购、平台和生产资格分别记录'))
        from .pcb_interface_legends import report as legend_report
        result['interfaceLegends']=legend_report(native,(revision.get('pcbDesign')or{}).get('interfaceLegends',[]))
        if result['interfaceLegends']['status']!='passed':
            result['pending'].append('接口逐针丝印检查未完成：读取interfaceLegends核对未登记接口或实际文字/针网问题。')
        if params['fabrication'].get('thicknessMm') is not None and abs(params['fabrication']['thicknessMm']-native['nominalThicknessMm'])>1e-6:
            result['problems'].append(dict(code='thickness_choice',message='明确板厚与原生名义板厚不同。',reference=None));result['fileConsistencyPassed']=result['checksPassed']=False
        self.review_csv(output/'assembly-review.csv',result['selectedParts'])
        save(output/'manufacturing-report.json',result);save(output/'commands.json',commands)
        verify_snapshot(source,revision['manifest']);verify_snapshot(frozen,revision['manifest']);verify_snapshot(workspace,revision['manifest']);self.verify_tools();cancel()
        files=[p for base in [frozen,native_dir,output/'engine'] for p in base.rglob('*') if p.is_file()]
        files += [output/name for name in ['source-revision.json','binding.json','manufacturing-report.json','commands.json','assembly-review.csv','fabrication-gerbers.zip']]
        files += sorted(output.glob('command-*.log'))
        hashes=[dict(path=p.relative_to(output).as_posix(),sha256=digest(p),size=p.stat().st_size) for p in sorted(files)]
        save(output/'manufacturing-files.json',hashes)
        archive=output/'manufacturing-review.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for row in hashes:cancel();z.write(output/row['path'],row['path'])
            z.write(output/'manufacturing-files.json','manufacturing-files.json')
        # Check the exact archive bytes against every frozen file before publication.
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise DomainError('tool_failed','制造资料压缩CRC失败。')
            for row in hashes:
                if hashlib.sha256(z.read(row['path'])).hexdigest()!=row['sha256']:raise DomainError('input_changed','制造包内摘要不一致。',409)
        if time.monotonic()>=deadline:raise DomainError('tool_failed','制造资料任务达到总时间预算。')
        phase('发布绑定版本的审核资料；工程和采购放行仍单列')
        for p,kind,mime in [(output/'manufacturing-report.json','manufacturing_report','application/json'),(output/'assembly-review.csv','manufacturing_assembly_review','text/csv'),
                (native_dir/'bom.csv','manufacturing_bom','text/csv'),(native_dir/'positions.csv','manufacturing_positions','text/csv'),
                (native_dir/'erc.json','manufacturing_erc','application/json'),(native_dir/'drc.json','manufacturing_drc','application/json'),
                (fabrication_zip,'manufacturing_gerbers','application/zip'),
                (archive,'manufacturing_project','application/zip')]:publish(p,kind,mime)
        cancel()
        if self.store.finish_readonly(job['id'],result,'制造审核资料已生成；尚未生产放行') is None:raise Cancelled()

    def verify_tools(self):
        for row in self.tools.values():
            if digest(Path(row['path']))!=row['sha256']:raise DomainError('input_changed','制造原生工具字节变化；请重启后创建新任务。',409)

    @staticmethod
    def review_csv(path,parts):
        with path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['reference','value','footprint','mounting','layer','mpn','supplierPart','requiredQuantity','directionReviewed','procurementReviewed'])
            writer.writeheader()
            for row in parts:writer.writerow({key:row.get(key) for key in writer.fieldnames})

    @staticmethod
    def cam(folder,native):
        folder=folder.resolve()
        jobs=list((folder/'gerbers').glob('*.gbrjob'));problems=[];files=[]
        if len(jobs)!=1:return dict(structuralChecksPassed=False,problems=['缺少唯一Gerber作业文件'])
        job=json.loads(jobs[0].read_text(encoding='utf8'))
        expected_functions={f'Copper,L{i+1},'+('Top' if i==0 else 'Bot' if i==len(native['copperLayers'])-1 else 'Inr') for i in range(len(native['copperLayers']))}
        expected_functions|={'Legend,Top','Legend,Bot','SolderMask,Top','SolderMask,Bot','SolderPaste,Top','SolderPaste,Bot','Profile'}
        rows=job.get('FilesAttributes',[])
        if {r['FileFunction'] for r in rows}!=expected_functions or len(rows)!=len(expected_functions):problems.append('Gerber实际层集合与声明不同')
        for row in rows:
            path=within(folder/'gerbers',row['Path']);text=path.read_text(encoding='utf8')
            # KiCad job JSON and Gerber-X2 use different native spellings for
            # soldermask, and only the X2 Profile includes the NP qualifier.
            x2_function='Profile,NP' if row['FileFunction']=='Profile' else row['FileFunction'].replace('SolderMask,','Soldermask,').replace('SolderPaste,','Paste,')
            if '%MOMM*%' not in text or '%TF.FileFunction,'+x2_function+'*%' not in text or not text.rstrip().endswith('M02*'):
                problems.append('Gerber单位/层身份/结束标记异常：'+path.name)
            files.append(dict(path=path.relative_to(folder).as_posix(),sha256=digest(path),function=row['FileFunction']))
        drills=sorted((folder/'drills').glob('*.drl'))
        for plated,label in [(True,'PTH'),(False,'NPTH')]:
            expected_count=sum(h['plated']==plated for h in native['holes']);selected=[p for p in drills if p.stem.endswith('-'+label)]
            if expected_count and len(selected)!=1:problems.append(label+'钻孔文件缺失或重复')
            for path in selected:
                text=path.read_text(encoding='utf8')
                if not text.startswith('M48') or 'METRIC' not in text or not text.rstrip().endswith('M30'):problems.append('钻孔单位/结构异常：'+path.name)
                files.append(dict(path=path.relative_to(folder).as_posix(),sha256=digest(path),function=label,expectedNativeHoleFeatures=expected_count))
        if set(drills)!={p for p in drills if p.stem.endswith(('-PTH','-NPTH'))}:problems.append('出现未识别钻孔文件')
        return dict(structuralChecksPassed=not problems,problems=problems,files=files,gerberJobSpecs=job.get('GeneralSpecs'),
            nativeNominalThicknessMm=native['nominalThicknessMm'],allCopperLayers=native['copperLayers'],
            geometryEquivalenceProven=False,drillGeometryParsed=False,platformPreviewVerified=False,
            note='检查层/单位/文件身份和结构；Gerber作业Size可能含外形线宽，不能当作成品中心线尺寸证明。')

    def read(self,project,job_id=None):
        self.store.get('projects',project)
        jobs=[j for j in self.store.list('jobs',project) if j['kind']==self.kind]
        if job_id:
            job=self.store.get('jobs',job_id)
            if job['projectId']!=project or job['kind']!=self.kind:raise DomainError('invalid_input','制造资料任务不属于本工程或类型不符。')
        elif jobs:job=max(jobs,key=lambda j:j['createdAt'])
        else:return dict(job=None,jobs=[],result=None,artifacts=[],orderingEnabled=False)
        result=job.get('result');revision=self.store.get('revisions',job['inputRevision']);source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest'])
        current=self.store.get('projects',project)
        changed=self.store.head_revision(project)!=revision['id'] or any(current[k]!=job['contextSnapshot'][k] for k in ['specification','preferences'])
        artifacts=[a for a in self.store.list('artifacts',project) if a['jobId']==job['id'] and a['revisionId']==revision['id']]
        return dict(job=job,jobs=[dict(id=j['id'],status=j['status'],createdAt=j['createdAt'],inputRevision=j['inputRevision'],message=j['params']['message']) for j in jobs],
            result=result,artifacts=artifacts,requiresDependencyReview=changed,currentRevisionId=self.store.head_revision(project),orderingEnabled=False)
