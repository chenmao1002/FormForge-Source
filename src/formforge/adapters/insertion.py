"""Version-bound, read-only insertion tasks over an immutable mechanical package."""
import json,shutil,time,zipfile,stat
from pathlib import Path
from .importer import digest,within,verify_snapshot
from .mechanical import freeze_sources
from .mechanical_inputs import validate_references
from ..store import DomainError

class InsertionAdapter:
    def __init__(self,config,mechanical):self.config=config;self.mechanical=mechanical;self.sha=digest(Path(__file__))
    def run(self,revision,job,artifact,params,output,cancel,phase,publish):
        deadline=time.monotonic()+params['timeoutSeconds'];left=lambda:max(.1,deadline-time.monotonic())
        source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest'])
        if revision['mechanicalDesign']['designSha256']!=params['expectedDesignSha256']:raise DomainError('version_conflict','插拔检查的机械设计SHA已变化。',409)
        output.mkdir(parents=True);phase('冻结机械工程包、配对件与明确插接基准')
        package=within(self.config.data,artifact['relativePath'])
        if digest(package)!=artifact['sha256']:raise DomainError('input_changed','机械工程包SHA发生变化。',409)
        frozen=output/'bound-mechanical.zip';shutil.copyfile(package,frozen)
        if digest(frozen)!=artifact['sha256'] or digest(package)!=artifact['sha256']:raise DomainError('input_changed','机械工程包冻结时变化。',409)
        with zipfile.ZipFile(frozen) as z:
            metadata=json.loads(z.read('formforge-manifest.json'));hashes=json.loads(z.read('formforge-output-hashes.json'));total=0
            if metadata['id']!=revision['id'] or metadata['manifest']!=revision['manifest']:raise DomainError('input_changed','机械包与版本输入不同。',409)
            expected={'source/'+r['path']:r['sha256'] for r in metadata['manifest']}|{'outputs/'+n:h for n,h in hashes.items()}
            if len(z.namelist())!=len(set(z.namelist())):raise DomainError('invalid_input','机械包存在重复文件。')
            for name,h in expected.items():
                cancel();info=z.getinfo(name);total+=info.file_size
                if info.file_size>128*1024*1024 or total>512*1024*1024 or stat.S_ISLNK(info.external_attr>>16):raise DomainError('invalid_input','机械包超限或包含链接。')
                target=within(output/'bound',name);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
                if digest(target)!=h:raise DomainError('input_changed','机械包内文件SHA不符。',409)
        design=json.loads((output/'bound/outputs/design.json').read_text(encoding='utf8'));dep=json.loads((output/'bound/outputs/source-provenance.json').read_text(encoding='utf8')).get('pcbDependency') or {}
        if design['kind']!='enclosure' or not dep.get('geometryIncluded'):raise DomainError('unsupported_capability','当前插拔检查需要实际PCB几何绑定的外壳版本。')
        files,records=freeze_sources(params['sources'],output,cancel)
        plan=json.loads(json.dumps(dict(interfaces=params['interfaces'])));references=dict(**plan,evidence_files=[i['evidencePath'] for i in plan['interfaces']])
        try:validate_references(references,files)
        except ValueError as x:raise DomainError('invalid_input',str(x)) from x
        passages={p['footprintId']:p['openingIndex'] for p in dep.get('passages',[])}
        ids=[i['id'] for i in plan['interfaces']];fids=[i['footprintId'] for i in plan['interfaces']]
        if len(set(ids))!=len(ids) or len(set(fids))!=len(fids):raise DomainError('invalid_input','接口ID或接头重复；请分任务比较不同配对件。')
        for i in plan['interfaces']:
            opening=i['openingIndex']
            if passages.get(i['footprintId'])!=opening or opening>=len(design['openings']) or design['openings'][opening]['face']!=i['face']:raise DomainError('invalid_input','插拔接口必须明确对应实际接头及已验证的侧面或顶面开口。')
            if not i['evidencePath'].endswith('.json') or (output/i['evidencePath']).stat().st_size>256*1024:raise DomainError('invalid_input','插接参数资料须为至多256KB的冻结JSON。')
        binding=dict(revisionId=revision['id'],designSha256=params['expectedDesignSha256'],mechanicalJobId=job['id'],mechanicalPackageSha256=artifact['sha256'],design=design,pcbDependency=dep,boundFiles=expected,inputFiles=files,sources=records)
        for name,data in [('binding.json',binding),('plan.json',plan)]: (output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
        phase('原生实体核对完整直线行程、外壁退出及局部接合区域')
        first=self.mechanical.worker(dict(action='insertion_check',root=str(output)),output/'native-check',cancel,left())
        phase('独立进程重做连续保守扫掠与冻结产物回读')
        before={p.relative_to(output).as_posix():digest(p) for p in output.rglob('*') if p.is_file()}
        second=self.mechanical.worker(dict(action='insertion_verify',root=str(output)),output/'native-replay',cancel,left())
        if any(digest(within(output,n))!=h for n,h in before.items()):raise DomainError('input_changed','插拔回读期间冻结输入或输出被改变。',409)
        result={**first,'revisionId':revision['id'],'designSha256':params['expectedDesignSha256'],'mechanicalPackageSha256':artifact['sha256'],'sourceManifest':records,'independentReplayMatched':second['replayMatched'],'verification':second,'adapterSha256':self.sha}
        if first['checksPassed']!=second['checksPassed']:raise DomainError('tool_failed','两次原生插拔检查结论不同。')
        report=output/'insertion-report.json';report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        for p,kind,mime in [(report,'mechanical_insertion_report','application/json'),(output/'assembly-view.json','mechanical_insertion_view','application/json'),(output/'assembly.glb','mechanical_insertion_model','model/gltf-binary'),(output/'assembly.step','mechanical_insertion_step','model/step')]:publish(p,kind,mime)
        archive=output/'insertion-project.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for p in sorted(output.rglob('*')):
                if p.is_file() and p!=archive and not any(n in p.relative_to(output).parts for n in ['native-check','native-replay']):cancel();z.write(p,p.relative_to(output).as_posix())
            z.writestr('insertion-files.json',json.dumps([dict(path=p.relative_to(output).as_posix(),sha256=digest(p)) for p in sorted(output.rglob('*')) if p.is_file() and p!=archive and not any(n in p.relative_to(output).parts for n in ['native-check','native-replay'])],indent=2))
        publish(archive,'mechanical_insertion_project','application/zip');verify_snapshot(source,revision['manifest']);cancel()
        if time.monotonic()>=deadline:raise DomainError('tool_failed','插拔检查达到总时间预算。')
        return result
