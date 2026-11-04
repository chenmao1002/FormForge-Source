"""Source-bound manufacturing review with no dependency on a CAD revision."""
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

from .connectivity import fingerprint
from .importer import digest,within
from .external_cam_inputs import archive_index,inspect_zip,path_name
from ..external_cam_schemas import ExternalManufacturingReview
from ..source_registry import SourceRegistry
from ..processes import run_process
from ..store import DomainError,uid
from ..jobs import Cancelled


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')


class ExternalCamAdapter:
    kind='manufacturing.external_review'
    version='external-cam/0.10.0'
    read_only=True

    def __init__(self,config,store):
        self.config,self.store=config,store;self.sources=SourceRegistry(config,store)
        self.code_root=Path(__file__).resolve().parents[3]
        names=['adapters/external_cam.py','adapters/external_cam_inputs.py','adapters/external_cam_geometry.py',
               'adapters/external_cam_tables.py','adapters/external_cam_worker.py','adapters/external_cam_runtime.py',
               'external_cam_schemas.py','source_schemas.py','processes.py']
        self.engine_files={name:digest(Path(__file__).parents[1]/name) for name in names}
        self.engine_hash=fingerprint(self.engine_files)
        self.engine=config.local/'engines/external-cam'/self.engine_hash;self.engine.mkdir(parents=True,exist_ok=True)
        for name,h in self.engine_files.items():
            target=self.engine/Path(name).name
            if not target.exists():shutil.copyfile(Path(__file__).parents[1]/name,target)
            if digest(target)!=h:raise DomainError('input_changed','CAM引擎缓存摘要不一致。',409)
        self.runtime=None;self.runtime_issue=None;self.tools={}
        if not config.cam_python or not Path(config.cam_python).is_file():
            self.runtime_issue='尚未安装独立CAM运行环境；运行 scripts/Setup-Cam.ps1。'
        else:
            try:
                result=subprocess.run([config.cam_python,'-B',str(self.engine/'external_cam_runtime.py')],cwd=config.root,
                    env=self.environment(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                if result.returncode or len(result.stdout)>2*1024*1024:
                    raise ValueError(result.stderr.decode('utf8',errors='replace')[-1500:] or 'Runtime inventory failed.')
                runtime=json.loads(result.stdout)
                lock=self.code_root/'requirements-cam.lock'
                expected={line.split('==')[0].strip().lower().replace('_','-'):line.split('==')[1].strip() for line in lock.read_text('utf-8-sig').splitlines() if '==' in line}
                if runtime['versions']!=expected:raise ValueError('CAM dependencies differ from requirements-cam.lock.')
                self.runtime=runtime;self.runtime_hash=fingerprint(runtime)
                self.tools['python']=dict(path=str(Path(config.cam_python).resolve()),sha256=digest(Path(config.cam_python)))
                base=Path(runtime['basePrefix'])
                for name in ['python.exe','python312.dll']:
                    if (base/name).is_file():self.tools['base-'+name]=dict(path=str(base/name),sha256=digest(base/name))
            except (OSError,ValueError,subprocess.SubprocessError) as error:
                self.runtime=None;self.runtime_issue='独立CAM环境不可用：'+str(error)[:1600]

    def environment(self):
        env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','PYTHONPATH':str(self.engine)}
        temp=self.config.root/'.cache/tmp';temp.mkdir(parents=True,exist_ok=True)
        env['TEMP']=env['TMP']=str(temp)
        return env

    def profile(self):
        return dict(available=self.runtime is not None,issue=self.runtime_issue,adapterVersion=self.version,engineHash=self.engine_hash,
            runtimeHash=getattr(self,'runtime_hash',None),versions=self.runtime['versions'] if self.runtime else {},
            runtimeVerification='service-startup; restart after dependency changes',
            readOnly=True,requiresCadRevision=False,formats=['Gerber-RS274X-X2','Excellon','CSV','XLSX'],
            coordinateFrame='mm/XY-up/CCW; explicit per-side transforms',orderingEnabled=False,paymentEnabled=False,
            scope='source-bound CAM/profile/drill/assembly data review; no native netlist or production approval')

    def verify_runtime(self,cancel=lambda:None):
        if self.runtime is None:raise DomainError('dependency_missing',self.runtime_issue)
        for row in self.tools.values():
            cancel()
            if digest(Path(row['path']))!=row['sha256']:raise DomainError('input_changed','CAM解释器字节已变化。',409)
        prefix=Path(self.runtime['environment'])
        actual=set()
        site_roots=[prefix/name for name in self.runtime['siteRoots']]
        for folder in site_roots:
            for path in folder.rglob('*'):
                relative=path.relative_to(folder)
                if not path.is_file() or path.suffix in {'.pyc','.pyo'} or '__pycache__' in relative.parts:continue
                if relative.parts[0]=='pip' or (relative.parts[0].startswith('pip-') and relative.parts[0].endswith('.dist-info')):continue
                actual.add(path.relative_to(prefix).as_posix())
        expected={name for name in self.runtime['files'] if any((prefix/name).is_relative_to(folder) for folder in site_roots)}
        if actual!=expected:raise DomainError('input_changed','CAM解析依赖文件集合已改变。',409)
        for i,(name,h) in enumerate(self.runtime['files'].items()):
            if i%30==0:cancel()
            path=within(prefix,name)
            if not path.is_file() or digest(path)!=h:raise DomainError('input_changed','CAM解析依赖字节已变化：'+name,409)

    def require_runtime(self):
        # Startup already inventories every dependency and checks the lock.
        # Updating the managed environment requires a service restart.
        if self.runtime is None:raise DomainError('dependency_missing',self.runtime_issue)

    def inputs(self,project,params):
        refs=[params['archive']]
        for key in ['bom','placements']:
            if params.get(key) and params[key]['file'].get('source'):refs.append(params[key]['file']['source'])
        records={};paths={}
        for ref in refs:
            row,path=self.sources.get_record(project,ref['sourceId'])
            if row['sha256']!=ref['expectedSha256'] or row['provenanceSha256']!=ref['expectedProvenanceSha256']:
                raise DomainError('input_changed','制造资料字节或来源摘要与请求不符。',409)
            if row['id'] in records and records[row['id']]!=row:raise DomainError('input_changed','资料引用不一致。',409)
            records[row['id']]=row;paths[row['id']]=path
        archive_path=paths[params['archive']['sourceId']]
        if archive_path.suffix.lower()!='.zip':raise DomainError('invalid_input','CAM主输入必须是已登记的Gerber ZIP。')
        for key in ['bom','placements']:
            if params.get(key):
                location=params[key]['file'];name=paths[location['source']['sourceId']].name if location.get('source') else location['archivePath']
                if Path(name).suffix.lower()!='.'+location['format']:raise DomainError('invalid_input','表格格式声明与文件扩展名不同。')
                if location.get('source') and paths[location['source']['sourceId']].stat().st_size>16*1024*1024:
                    raise DomainError('invalid_input','装配表格上限16MiB。')
        return records,paths

    def validate(self,project,params):
        self.require_runtime()
        current=self.store.get('projects',project)
        if current['version']!=params['expectedProjectVersion']:raise DomainError('version_conflict','工程版本已变化，请重新读取上下文。',409)
        records,paths=self.inputs(project,params)
        try:
            z,infos=archive_index(paths[params['archive']['sourceId']].read_bytes())
            with z:names={i.filename for i in infos}
            accounted={v['path'] for v in params['layers']}|{v['path'] for v in params['ignoredFiles']}
            table_paths=set()
            for key in ['bom','placements']:
                if params.get(key) and params[key]['file'].get('archivePath'):
                    name=params[key]['file']['archivePath'];path_name(name)
                    if name in accounted:raise ValueError('Table entry is also mapped as a layer or ignored file.')
                    table_paths.add(name)
            for name in accounted:path_name(name)
            if names!=accounted|table_paths:raise ValueError('ZIP entries must all be assigned once; missing or unknown paths remain.')
        except (OSError,ValueError,zipfile.BadZipFile) as error:raise DomainError('invalid_input','制造ZIP映射不完整或不安全：'+str(error)[:1000]) from error

    def schema(self,project=None,archive=None,table=None):
        result=dict(profile=self.profile(),requestSchema=ExternalManufacturingReview.model_json_schema(),
            mappingConfirmationRequired=True,tableLimits=dict(maxRows=10000,maxColumns=100,maxBytes=16*1024*1024),
            presets=dict(kicadBom=dict(reference='Reference',quantity='Quantity',dnp='DNP',value='Value',footprint='Footprint',mpn='MPN',supplierPart='LCSC'),
                         kicadPlacements=dict(reference='Ref',x='PosX',y='PosY',rotation='Rot',side='Side',value='Val',footprint='Package'),
                         jlcPlacements=dict(reference='Designator',x='Mid X',y='Mid Y',rotation='Rotation',side='Layer')))
        if archive is not None:
            if not project:raise DomainError('invalid_input','探查登记资料需要工程ID。')
            row,path=self.sources.get_record(project,archive['sourceId'])
            if row['sha256']!=archive['expectedSha256'] or row['provenanceSha256']!=archive['expectedProvenanceSha256']:
                raise DomainError('input_changed','ZIP来源摘要不符。',409)
            if path.suffix.lower()!='.zip':raise DomainError('invalid_input','请选择已登记ZIP。')
            try:result['archive']=inspect_zip(path.read_bytes())
            except (ValueError,OSError,zipfile.BadZipFile) as error:raise DomainError('invalid_input','无法探查ZIP：'+str(error)) from error
        if table is not None:
            if not project:raise DomainError('invalid_input','表格探查需要工程ID。')
            if table.get('archivePath') and archive is None:raise DomainError('invalid_input','ZIP内表格需要同时指定archive。')
            self.require_runtime();references=[ref for ref in [archive,table.get('source')] if ref]
            directory=self.config.local/'cam-probes'/uid('cp');directory.mkdir(parents=True)
            paths={};records={}
            for ref in references:
                row,path=self.sources.get_record(project,ref['sourceId'])
                if row['sha256']!=ref['expectedSha256'] or row['provenanceSha256']!=ref['expectedProvenanceSha256']:
                    raise DomainError('input_changed','表格探查来源摘要不符。',409)
                target=directory/'inputs'/row['id']/row['name'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
                if digest(target)!=row['sha256']:raise DomainError('input_changed','表格探查输入冻结时变化。',409)
                records[row['id']]=row;paths[row['id']]=str(target)
            name=Path(paths[table['source']['sourceId']]).name if table.get('source') else table['archivePath']
            if Path(name).suffix.lower()!='.'+table['format']:raise DomainError('invalid_input','表格探查格式与扩展名不符。')
            save(directory/'input.json',dict(action='probe-table',table=table,sourcePaths=paths,archivePath=paths[archive['sourceId']] if archive else None,output=str(directory)))
            command=run_process([self.config.cam_python,'-B',str(self.engine/'external_cam_worker.py'),str(directory/'input.json')],
                cwd=directory,env=self.environment(),log=directory/'probe.log',deadline=time.monotonic()+30,cancel=lambda:None)
            if command['exitCode']!=0:raise DomainError('invalid_input','无法读取所选表格，请核对编码、分隔符、工作表、表头及公式。')
            for id,row in records.items():
                if digest(Path(paths[id]))!=row['sha256'] or self.sources.get_record(project,id)[0]!=row:
                    raise DomainError('input_changed','表格探查资料已改变。',409)
            result['table']=json.loads((directory/'table-probe.json').read_text('utf8'))
        return result

    def execute(self,ctx):
        job=ctx.job;params=job['params'];cancel=ctx.cancel;phase=ctx.phase;publish=ctx.publish
        self.require_runtime();records,paths=self.inputs(job['projectId'],params)
        output=self.config.data/'projects'/job['projectId']/'jobs'/job['id'];output.mkdir(parents=True)
        phase('冻结外部制造原件、来源声明与独立解析引擎')
        frozen={}
        for id,row in records.items():
            cancel();target=output/'inputs'/id/row['name'];target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(paths[id],target)
            if digest(target)!=row['sha256']:raise DomainError('input_changed','制造原件冻结时已变化。',409)
            provenance=target.parent/'source-provenance.json'
            shutil.copyfile(within(self.config.data,row['provenancePath']),provenance)
            if digest(provenance)!=row['provenanceSha256']:raise DomainError('input_changed','制造来源声明冻结时已变化。',409)
            frozen[id]=str(target)
        engine=output/'engine';engine.mkdir()
        for name,h in self.engine_files.items():
            origin=self.engine/Path(name).name
            if digest(origin)!=h:raise DomainError('input_changed','冻结CAM引擎已变化。',409)
            shutil.copyfile(origin,engine/origin.name)
        save(output/'request.json',params);save(output/'sources.json',list(records.values()))
        save(output/'runtime-manifest.json',dict(hash=self.runtime_hash,**self.runtime,tools=self.tools))
        save(output/'engine-manifest.json',dict(hash=self.engine_hash,files=self.engine_files))
        shutil.copyfile(self.code_root/'requirements-cam.lock',output/'requirements-cam.lock')
        save(output/'binding.json',dict(jobId=job['id'],projectId=job['projectId'],inputRevision=None,contextSnapshot=job['contextSnapshot'],
            adapterVersion=self.version,engineHash=self.engine_hash,runtimeHash=self.runtime_hash,sourceIds=sorted(records)))
        native=output/'cam';native.mkdir()
        save(output/'worker-input.json',dict(request=params,archivePath=frozen[params['archive']['sourceId']],sourcePaths=frozen,output=str(native)))
        last_phase=None
        def monitor():
            nonlocal last_phase
            cancel()
            try:
                row=json.loads((native/'progress.json').read_text('utf8'));message=row['phase']
                if isinstance(message,str) and message!=last_phase:
                    last_phase=message;phase(message[:700])
            except (OSError,ValueError,KeyError):pass
        deadline=time.monotonic()+params['timeoutSeconds']
        try:
            command=run_process([self.config.cam_python,'-B',str(engine/'external_cam_worker.py'),str(output/'worker-input.json')],
                cwd=output,env=self.environment(),log=output/'parser.log',deadline=deadline,cancel=monitor)
        except DomainError as error:
            if (output/'parser.log').is_file():publish(output/'parser.log','log')
            raise DomainError(error.code,str(error).replace('固件构建','制造文件解析').replace('构建日志','解析日志')) from error
        publish(output/'parser.log','log');save(output/'command.json',command)
        if command['exitCode']!=0:raise DomainError('tool_failed','独立制造解析失败；日志已保留，未修改设计。')
        cancel()
        for id,row in records.items():
            if digest(Path(frozen[id]))!=row['sha256']:raise DomainError('input_changed','冻结制造原件已改变。',409)
            if digest(Path(frozen[id]).parent/'source-provenance.json')!=row['provenanceSha256']:
                raise DomainError('input_changed','冻结制造来源声明已改变。',409)
            if self.sources.get_record(job['projectId'],id)[0]!=row:
                raise DomainError('input_changed','制造来源记录已改变。',409)
        report=json.loads((native/'cam-report.json').read_text('utf8'))
        report.update(jobId=job['id'],projectId=job['projectId'],inputRevision=None,contextSnapshot=job['contextSnapshot'],
            adapterVersion=self.version,engineHash=self.engine_hash,runtimeHash=self.runtime_hash,
            sources=[{k:v for k,v in row.items() if k not in {'relativePath','provenancePath'}} for row in records.values()])
        save(output/'external-cam-report.json',report)
        assembly_path=output/'assembly-review.csv'
        with assembly_path.open('w',encoding='utf-8-sig',newline='') as stream:
            fields=['reference','value','footprint','mpn','supplierPart','dnp','selected','requiredQuantity','side','xMm','yMm','rotationDegrees']
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
            for row in report['assembly']['parts']:
                data={**row,**(row.get('placement') or {})}
                safe=lambda v:"'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) else v
                # Numeric fields remain numeric; escape untrusted identifier/text cells.
                writer.writerow({k:safe(data.get(k,'')) if k in {'reference','value','footprint','mpn','supplierPart'} else data.get(k,'') for k in fields})
        publish(output/'external-cam-report.json','external_cam_report','application/json')
        publish(assembly_path,'external_cam_assembly','text/csv')
        for row in report['previews']:
            cancel();publish(within(native,row['path']),'external_cam_preview','image/svg+xml')
        payloads=[]
        for folder in [output/'inputs',engine,native]:
            payloads.extend(p for p in folder.rglob('*') if p.is_file() and p.name not in {'progress.json','progress.tmp'})
        payloads.extend(output/name for name in ['request.json','sources.json','runtime-manifest.json','engine-manifest.json','binding.json',
            'command.json','parser.log','external-cam-report.json','assembly-review.csv','requirements-cam.lock'])
        manifest=[dict(path=p.relative_to(output).as_posix(),size=p.stat().st_size,sha256=digest(p)) for p in sorted(payloads)]
        save(output/'external-cam-files.json',manifest)
        package=output/'external-cam-review.zip'
        with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as z:
            for row in manifest:
                cancel();z.write(output/row['path'],row['path'])
            z.write(output/'external-cam-files.json','external-cam-files.json')
        publish(package,'external_cam_package','application/zip')
        summary={k:report[k] for k in ['schemaVersion','checksPassed','fileConsistencyPassed','outline','coordinateFrame','engineHash','runtimeHash',
            'electricalEquivalenceVerified','engineeringChecksPassed','procurementReviewed','supplierOrientationReviewed','platformPreviewVerified',
            'readyForQuote','readyToSubmit','hardwareVerified','manufacturingApproved','orderingEnabled']}
        summary.update(jobId=job['id'],projectId=job['projectId'],inputRevision=None,problemCount=len(report['problems']),
            problems=report['problems'][:100],fileCount=len(report['files']),parsedLayerCount=sum(r['parsed'] for r in report['layers']),
            drillFeatureCount=len(report['drills']),bomReferences=len(report['assembly']['parts']),selectedReferences=len(report['assembly']['selectedReferences']),
            placementRows=len(report['assembly']['placements']),previewCount=len(report['previews']),packageSha256=digest(package),
            scope='Independent source data review; no CAD/head mutation or inferred supplier/production approval.')
        if self.store.finish_readonly(job['id'],summary,'外部制造文件审核完成；工程与生产资格另列' if summary['checksPassed'] else '外部制造文件存在待处理项；原设计保持') is None:
            raise Cancelled()

    def read(self,project,job_id=None,offset=0,limit=100):
        current=self.store.get('projects',project)
        if not 0<=offset<=100000 or not 1<=limit<=300:raise DomainError('invalid_input','读取范围须为offset≥0、limit=1..300。')
        jobs=[j for j in self.store.list('jobs',project) if j['kind']==self.kind]
        if job_id:
            job=self.store.get('jobs',job_id)
            if job['projectId']!=project or job['kind']!=self.kind:raise DomainError('not_found','没有本工程的此制造审核任务。',404)
        elif jobs:job=max(jobs,key=lambda j:(j['createdAt'],j['id']))
        else:job=None
        data=dict(job=job,jobs=[dict(id=j['id'],status=j['status'],createdAt=j['createdAt'],message=j['params']['message']) for j in jobs],
                  result=job.get('result') if job else None,artifacts=[],parts=[],drills=[],files=[],layers=[],offset=offset,limit=limit,orderingEnabled=False)
        if job is None:return data
        artifacts=[a for a in self.store.list('artifacts',project) if a['jobId']==job['id'] and a['revisionId'] is None]
        data['artifacts']=artifacts
        changed=any(current[k]!=job['contextSnapshot'][k] for k in ['specification','preferences'])
        data['requiresContextReview']=changed
        reports=[a for a in artifacts if a['kind']=='external_cam_report']
        if reports:
            if len(reports)!=1:raise DomainError('input_changed','制造报告绑定不唯一。',409)
            path=within(self.config.data,reports[0]['relativePath'])
            if digest(path)!=reports[0]['sha256']:raise DomainError('input_changed','制造审核报告已改变。',409)
            report=json.loads(path.read_text('utf8'))
            if report['jobId']!=job['id'] or report['projectId']!=project:raise DomainError('input_changed','制造报告任务身份不符。',409)
            data.update(parts=report['assembly']['parts'][offset:offset+limit],drills=report['drills'][offset:offset+limit],
                        files=report['files'],layers=report['layers'],previews=report['previews'],sources=report['sources'],
                        partCount=len(report['assembly']['parts']),drillCount=len(report['drills']))
        return data
