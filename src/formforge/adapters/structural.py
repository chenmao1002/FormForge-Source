"""Independent structural manufacturing review package, without editing CAD or ordering."""
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import zipfile

from .connectivity import fingerprint
from .importer import digest,within,reparse,verify_snapshot
from ..jobs import Cancelled
from ..processes import run_process
from ..store import DomainError
from ..structural_schemas import PrepareStructural
from ..stl_topology import inspect_bytes, report as topology_report


def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')


def check_stl_exports(output,parts,**binding):
    rows=[]
    for part in parts:
        stl=part['stl'];path=within(output,stl['path']);raw=path.read_bytes()
        sha=hashlib.sha256(raw).hexdigest()
        if sha!=stl['sha256']:raise DomainError('input_changed','导出STL在拓扑检查前已变化。',409)
        try:row=inspect_bytes(raw,stl['path'])
        except ValueError as error:row=dict(file=stl['path'],sha256=sha,passed=False,error=str(error))
        rows.append(dict(**row,partId=part['partId'],sourceStepSha256=part['stepSha256']))
    return topology_report(rows,**binding)


class StructuralAdapter:
    kind='manufacturing.mechanical_prepare'
    version='structural-step-review/0.13.1'
    read_only=True

    def __init__(self,config,store,mechanical):
        self.config,self.store,self.mechanical=config,store,mechanical
        files={Path(__file__).name:Path(__file__), 'structural_native.py':Path(__file__).with_name('structural_native.py'),
               'structural_schemas.py':Path(__file__).parent.parent/'structural_schemas.py',
               'stl_topology.py':Path(__file__).parent.parent/'stl_topology.py'}
        self.hashes={name:digest(p) for name,p in files.items()};self.engine_hash=fingerprint(self.hashes)
        self.engine=config.local/'engines/structural'/self.engine_hash;self.engine.mkdir(parents=True,exist_ok=True)
        for name,path in files.items():
            target=self.engine/name
            if not target.exists():shutil.copyfile(path,target)
            if digest(target)!=self.hashes[name]:raise DomainError('input_changed','结构制造引擎缓存已变化。',409)

    def profile(self):
        return dict(available=not self.mechanical.issue,issue=self.mechanical.issue,adapterVersion=self.version,engineHash=self.engine_hash,
            input='Checked FormForge mechanical revision with per-part native STEP',formats=['STEP-mm','binary-STL-mm','CSV','JSON','ZIP'],
            fileReviewOnly=True,orderingEnabled=False,quotingEnabled=False,paymentEnabled=False,manufacturingApproved=False)

    def _artifact(self,project,job,name):
        rows=[a for a in self.store.list('artifacts',project) if a['jobId']==job['id'] and a['revisionId']==job['result']['revisionId'] and a['name']==name]
        expected=self.config.data/'projects'/project/'jobs'/job['id']
        if name.endswith('.step') or name=='bom.csv':expected=expected/'cad'/name
        else:expected=expected/name
        rows=[a for a in rows if (self.config.data/a['relativePath']).absolute()==expected.absolute()]
        if len(rows)!=1:raise DomainError('unsupported_capability','此机械版本缺少唯一的逐件原生导出：'+name)
        a=rows[0];path=self.config.data/a['relativePath']
        for parent in [path,*path.parents]:
            if parent==self.config.root:break
            if parent.exists() and reparse(parent):raise DomainError('invalid_input','结构制造资料不接受重解析路径。')
        path=within(self.config.data,a['relativePath'])
        limit=(64 if name.endswith('.step') else 256)*1024*1024
        if not path.is_file() or path.stat().st_size!=a['size'] or a['size']>limit or digest(path)!=a['sha256']:
            raise DomainError('input_changed','机械原始导出文件已变化。',409)
        return a,path

    def basis(self,project,rid):
        current=self.store.get('projects',project);revision=self.store.get('revisions',rid)
        if revision['projectId']!=project or revision.get('module')!='mechanical':raise DomainError('invalid_input','需要同工程的独立机械版本。')
        source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest'])
        job=self.store.get('jobs',revision['createdByJobId']);result=job.get('result') or {}
        if job['kind']!='mechanical.design' or job['projectId']!=project or job['status']!='completed' or not result.get('checksPassed') or result.get('revisionId')!=rid:
            raise DomainError('invalid_input','结构资料需要已完成且原生检查通过的机械设计。')
        report,rpath=self._artifact(project,job,'mechanical-report.json')
        package,ppath=self._artifact(project,job,'mechanical-project.zip')
        try:recorded=json.loads(rpath.read_text('utf8'))
        except (ValueError,UnicodeError,OSError) as error:raise DomainError('input_changed','原机械报告无法读取。',409) from error
        if any(recorded.get(k)!=result.get(k) for k in ['revisionId','designSha256','parts','checksPassed']):raise DomainError('input_changed','机械报告与原任务记录不符。',409)
        if result.get('designSha256')!=revision['mechanicalDesign']['designSha256']:raise DomainError('input_changed','机械设计版本与任务摘要不符。',409)
        parts=[]
        for part in result['parts']:
            item=dict(part=part,partHash=fingerprint(part),step=None)
            try:a,path=self._artifact(project,job,part['id']+'.step');item['step']={k:a[k] for k in ['id','sha256','size','relativePath','name']}
            except DomainError as error:
                if error.code!='unsupported_capability':raise
            parts.append(item)
        try:
            with zipfile.ZipFile(ppath) as archive:
                entries=archive.infolist()
                if len(entries)>2000 or len({i.filename for i in entries})!=len(entries):raise DomainError('unsupported_capability','机械包文件数量或身份超过审核约定。')
                info=archive.getinfo('formforge-output-hashes.json')
                if info.file_size>1024*1024:raise DomainError('unsupported_capability','机械包摘要清单超过审核限额。')
                hashes=json.loads(archive.read(info))
                if not isinstance(hashes,dict):raise ValueError('Expected hash mapping')
                for item in parts:
                    if item['step']:
                        name=item['part']['id']+'.step';info=archive.getinfo('outputs/'+name)
                        if info.file_size>64*1024*1024 or hashes.get(name)!=item['step']['sha256'] or __import__('hashlib').sha256(archive.read(info)).hexdigest()!=item['step']['sha256']:
                            raise DomainError('input_changed','逐件STEP与原机械工程包不一致。',409)
        except (zipfile.BadZipFile,KeyError,ValueError,UnicodeError,OSError) as error:raise DomainError('input_changed','原机械工程包无法完整回读。',409) from error
        if not 1<=len(parts)<=128 or len({p['part']['id'] for p in parts})!=len(parts):raise DomainError('invalid_input','原生零件数量或身份不明确。')
        dependency=revision['mechanicalDesign'].get('pcbDependency')
        stale=current.get('moduleHeads',{}).get('mechanical')!=rid or bool(dependency and current.get('headRevisionId')!=dependency['revisionId']) or any(current[k]!=job['contextSnapshot'][k] for k in ['specification','preferences'])
        binding=dict(projectId=project,revisionId=rid,designSha256=revision['mechanicalDesign']['designSha256'],mechanicalJobId=job['id'],
            mechanicalPackage={k:package[k] for k in ['id','sha256','size']},mechanicalReport={k:report[k] for k in ['id','sha256','size']},
            sourceManifest=revision['manifest'],sources=revision['mechanicalDesign']['sources'],pcbDependency=dependency,parts=parts)
        return binding,stale,revision,job

    def schema(self,project=None,revision=None):
        result=dict(profile=self.profile(),request=PrepareStructural.model_json_schema())
        if project:
            p=self.store.get('projects',project);rid=revision or p.get('moduleHeads',{}).get('mechanical')
            if rid:
                binding,stale,*_=self.basis(project,rid);result.update(basis=binding,requiresDependencyReview=stale,projectVersion=p['version'])
        elif revision:raise DomainError('invalid_input','选择版本时须同时选择工程。')
        return result

    def validate(self,project,params):
        if self.mechanical.issue:raise DomainError('dependency_missing',self.mechanical.issue)
        request=PrepareStructural.model_validate({**params,'requestId':'validation'})
        binding,stale,*_=self.basis(project,params['revisionId'])
        if binding['designSha256']!=params['expectedDesignSha256']:raise DomainError('version_conflict','机械设计SHA已变化。',409)
        if self.store.get('projects',project)['version']!=params['expectedProjectVersion']:raise DomainError('version_conflict','工程版本已变化。',409)
        if stale and request.dependencyPolicy=='require-current':raise DomainError('version_conflict','机械版本或依赖已变化，请重验或明确选择历史计价。',409)
        parts={p['part']['id']:p for p in binding['parts']}
        if set(parts)!={p.partId for p in request.parts}:raise DomainError('invalid_input','须完整声明全部CAD零件的处置，不能漏掉或增加零件。')
        for row in request.parts:
            item=parts[row.partId]
            if row.expectedPartHash!=item['partHash']:raise DomainError('version_conflict','原生零件元数据SHA不符。',409)
            if row.disposition=='fabricate' and (not item['step'] or item['step']['sha256']!=row.expectedStepSha256 or item['part']['solidCount']!=1):
                raise DomainError('invalid_input','制作件需要准确的单实体逐件STEP及其SHA。')
        if sum(parts[row.partId]['step']['size'] for row in request.parts if row.disposition=='fabricate')>256*1024*1024:
            raise DomainError('unsupported_capability','结构制作件的STEP合计超过256MiB。')

    def execute(self,ctx):
        job=ctx.job;params=job['params'];self.validate(job['projectId'],params)
        binding,stale,revision,parent=self.basis(job['projectId'],params['revisionId']);parts={p['part']['id']:p for p in binding['parts']}
        output=self.config.data/'projects'/job['projectId']/'jobs'/job['id'];output.mkdir(parents=True)
        (output/'parts').mkdir();(output/'source').mkdir();shutil.copytree(self.engine,output/'engine')
        deadline=time.monotonic()+params['timeoutSeconds'];phase=ctx.phase;cancel=ctx.cancel
        phase('冻结明确的制作零件、数量与原始STEP')
        native=[];files=[]
        for row in params['parts']:
            cancel()
            if row['disposition']!='fabricate':continue
            item=parts[row['partId']];a,path=self._artifact(job['projectId'],parent,row['partId']+'.step')
            target=output/'parts'/(row['partId']+'.step');shutil.copyfile(path,target)
            if digest(target)!=a['sha256']:raise DomainError('input_changed','冻结STEP时原件已变化。',409)
            native.append(dict(path=target.relative_to(output).as_posix(),stepSha256=a['sha256'],part=item['part']))
            files.append(target)
        report,rpath=self._artifact(job['projectId'],parent,'mechanical-report.json');shutil.copyfile(rpath,output/'source/mechanical-report.json')
        bom_rows=[a for a in self.store.list('artifacts',job['projectId']) if a['jobId']==parent['id'] and a['name']=='bom.csv']
        if bom_rows:
            _,bom=self._artifact(job['projectId'],parent,'bom.csv');shutil.copyfile(bom,output/'source/original-bom.csv')
        save(output/'binding.json',binding);save(output/'choices.json',params);save(output/'source/revision.json',revision)
        save(output/'native-request.json',dict(root=str(output),parts=native,exportStl=True))
        for name,h in self.hashes.items():
            if digest(output/'engine'/name)!=h:raise DomainError('input_changed','结构制造引擎已变化。',409)
        temp=output/'scratch';temp.mkdir();env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TEMP':str(temp),'TMP':str(temp)}
        for key in ['PYTHONPATH','PYTHONHOME']:env.pop(key,None)
        phase('独立原生回读毫米STEP的实体、体积、质心和包络')
        run=run_process([self.config.mechanical_python,'-I','-B',output/'engine/structural_native.py',output/'native-request.json',output/'native.json'],
            output,output/'native.log',env,cancel,deadline)
        ctx.publish(output/'native.log','structural_log','text/plain')
        if run['exitCode'] or not (output/'native.json').is_file():raise DomainError('tool_failed','结构STEP原生核对失败，请查看同任务日志。')
        native_result=json.loads((output/'native.json').read_text('utf8'))
        phase('检查实际导出STL的开放边、非流形边、方向和连通面')
        topology=check_stl_exports(output,native_result['parts'],projectId=job['projectId'],jobId=job['id'],
            revisionId=revision['id'],designSha256=params['expectedDesignSha256'],engineHash=self.engine_hash)
        save(output/'stl-topology.json',topology)
        files.extend(within(output,row['file']) for row in topology['files'])
        current_binding,current_stale,*_=self.basis(job['projectId'],params['revisionId'])
        if current_binding!=binding or any(digest(output/r['path'])!=r['stepSha256'] for r in native):raise DomainError('input_changed','结构核对期间输入或冻结件已变化。',409)
        if params['dependencyPolicy']=='require-current' and current_stale:raise DomainError('version_conflict','核对期间机械上游已变化，保留诊断资料。',409)
        result=dict(jobId=job['id'],revisionId=revision['id'],designSha256=params['expectedDesignSha256'],
            fileChecksPassed=native_result['checksPassed'] and topology['passed'],stepChecksPassed=native_result['checksPassed'],stlTopology=topology,
            parts=native_result['parts'],choices=params,binding=binding,engineHash=self.engine_hash,engineFiles=self.hashes,native=native_result,
            requiresDependencyReview=current_stale,manufacturingApproved=False,platformQuoteVerified=False,orderingEnabled=False,paymentExecuted=False,
            productionReleased=False,pending=['打印材料/工艺/方向、支撑和薄壁由供应商复核','收缩、公差、强度和实物配合未验证','原BOM仅参考；无CAD紧固件不等于已采购','未上传平台、报价或下单'])
        save(output/'structural-report.json',result)
        with (output/'part-choices.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['Part','Disposition','Quantity','Material','Process','STEP SHA256','Reason'])
            for row in params['parts']:writer.writerow([row['partId'],row['disposition'],row['quantity'],params['material'] or '',params['process'],row.get('expectedStepSha256') or '',row['reason']])
        paths=[p for folder in ['parts','source','engine'] for p in (output/folder).rglob('*') if p.is_file()]
        paths+=[output/n for n in ['binding.json','choices.json','native.json','native.log','structural-report.json','stl-topology.json','part-choices.csv']]
        if any(digest(output/row['file'])!=row['sha256'] for row in topology['files']):raise DomainError('input_changed','STL在资料打包前已变化。',409)
        manifest=[dict(path=p.relative_to(output).as_posix(),size=p.stat().st_size,sha256=digest(p)) for p in sorted(paths)]
        save(output/'structural-files.json',manifest);archive=output/'structural-review.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for row in manifest:cancel();z.write(output/row['path'],row['path'])
            z.write(output/'structural-files.json','structural-files.json')
        with zipfile.ZipFile(archive) as z:
            if z.testzip() or any(__import__('hashlib').sha256(z.read(row['path'])).hexdigest()!=row['sha256'] for row in manifest):raise DomainError('input_changed','结构资料包的字节摘要不符。',409)
        phase('发布逐件STEP、STL和有来源的结构审核资料')
        for path in files:ctx.publish(path,'structural_stl' if path.suffix=='.stl' else 'structural_step','model/stl' if path.suffix=='.stl' else 'model/step')
        for name,kind,mime in [('structural-report.json','structural_report','application/json'),('stl-topology.json','structural_stl_report','application/json'),('part-choices.csv','structural_parts','text/csv'),('structural-review.zip','structural_project','application/zip')]:ctx.publish(output/name,kind,mime)
        cancel()
        if self.store.finish_readonly(job['id'],result,'结构件资料检查完成；平台计价与制造资格另审') is None:raise Cancelled()

    def read(self,project,job_id=None):
        self.store.get('projects',project);jobs=[j for j in self.store.list('jobs',project) if j['kind']==self.kind]
        job=self.store.get('jobs',job_id) if job_id else max(jobs,key=lambda j:(j['createdAt'],j['id'])) if jobs else None
        if job and (job['projectId']!=project or job['kind']!=self.kind):raise DomainError('not_found','没有本工程的此结构制造任务。',404)
        stale=False;issue=None
        if job:
            try:stale=self.basis(project,job['params']['revisionId'])[1]
            except DomainError as error:stale=True;issue=error.message
        summaries=[{k:j[k] for k in ['id','kind','status','phase','createdAt','inputRevision']} for j in jobs]
        return dict(profile=self.profile(),job=job,jobs=summaries,result=job.get('result') if job else None,requiresDependencyReview=stale,
            inputIntegrityVerified=issue is None,inputIssue=issue,
            artifacts=[a for a in self.store.list('artifacts',project) if job and a['jobId']==job['id']])
