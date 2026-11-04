"""Freeze explicit installed KiCad assets, independent from design revisions."""
import hashlib,json,os,re,shutil,time,zipfile
from pathlib import Path
from xml.etree import ElementTree

from .components import encoded
from .importer import digest,reparse,within
from .native_syntax import parse
from .schematic import child_value,safe_string
from .symbol_library import external_library,describe_symbol
from .symbol_inheritance import resolve_symbol
from ..component_library_schemas import LocalLibraryProbe,FreezeLocalLibrary
from ..jobs import Cancelled
from ..processes import run_process
from ..store import DomainError,now

LICENSE='CC-BY-SA-4.0 WITH KiCad-libraries-exception'
LICENSE_URI='https://www.kicad.org/libraries/license/'


def fingerprint(value):return hashlib.sha256(encoded(value).encode('utf8')).hexdigest()


def asset_id(value):
    parts=value.split(':')
    if len(parts)!=2 or any(not re.fullmatch(r'[A-Za-z0-9_+.,()# -]{1,200}',p) or p in {'.','..'} for p in parts):
        raise DomainError('invalid_input','库身份必须为安全的“库名:器件名”，不接受路径或环境变量。')
    return parts


def checked(path,limit):
    if not path.is_file() or any(reparse(p) for p in [path,*path.parents]):
        raise DomainError('invalid_input','本地库文件缺失或经过重解析路径。')
    if path.stat().st_size>limit:raise DomainError('invalid_input','本地库文件超过大小限制。')
    return dict(path=str(path.resolve()),sha256=digest(path),size=path.stat().st_size)


def pin_mapping(symbol_pins,pad_numbers,mapping):
    pins={p['number'] for p in symbol_pins};pairs=[(p['pin'],p['pad']) for p in mapping]
    if len(pairs)!=len(pins) or {p for p,_ in pairs}!=pins or {p for _,p in pairs}!=set(pad_numbers):
        raise DomainError('invalid_input','引脚映射必须完整覆盖全部符号引脚和非空电气焊盘编号。')
    if len({p for _,p in pairs})!=len(pairs):raise DomainError('invalid_input','首版要求符号引脚与焊盘编号一一对应。')
    if any(pin!=pad for pin,pad in pairs):raise DomainError('unsupported_capability','直接复用要求引脚号与焊盘号一致；不能只在说明里重编号而不改原生文件。')
    return mapping


class ComponentLibraryAdapter:
    version='component-local-library/0.19.0'
    read_only=True

    def __init__(self,config,store,components):
        self.config,self.store,self.components=config,store,components
        self.code_root=Path(__file__).resolve().parents[3]
        names=['src/formforge/component_library_schemas.py','src/formforge/adapters/component_library.py',
            'src/formforge/adapters/component_library_native.py','src/formforge/adapters/native_asserts.py',
            'src/formforge/adapters/symbol_library.py','src/formforge/adapters/symbol_inheritance.py','src/formforge/adapters/native_syntax.py',
            'src/formforge/adapters/components.py','src/formforge/adapters/importer.py','src/formforge/adapters/schematic.py','src/formforge/processes.py',
            'src/formforge/resources/licenses/CC-BY-SA-4.0.txt','src/formforge/resources/licenses/KiCad-library-exception.txt']
        self.files={p:digest(self.code_root/p) for p in names};self.engine_hash=fingerprint(self.files)
        self.engine=config.local/'component-library-engines'/self.engine_hash
        self.engine.mkdir(parents=True,exist_ok=True)
        for name in ['component_library_native.py','native_asserts.py']:
            target=self.engine/name;original=self.code_root/'src/formforge/adapters'/name
            if not target.exists():shutil.copyfile(original,target)
            if digest(target)!=digest(original):raise DomainError('input_changed','冻结的本地库原生引擎已变化。',409)

    def verify_engine(self):
        if any(digest(self.code_root/p)!=h for p,h in self.files.items()) or any(digest(self.engine/name)!=self.files['src/formforge/adapters/'+name] for name in ['component_library_native.py','native_asserts.py']):
            raise DomainError('input_changed','本地库实现已变化，请在任务结束后重启服务。',409)

    def profile(self):
        available=bool(self.config.kicad_cli and Path(self.config.kicad_cli).is_file() and self.config.kicad_python and Path(self.config.kicad_python).is_file())
        return dict(adapterVersion=self.version,engineHash=self.engine_hash,available=available,
            installedKiCadOnly=True,nativeSvg=True,explicitPinMap=True,
            stepReadAvailable=bool(self.config.mechanical_python),modelUse='generic-visual-or-unverified-package',
            mechanicalFitCertified=False,cadDesignHeadModified=False,externalDownloads=False)

    def schema(self):return dict(profile=self.profile(),probe=LocalLibraryProbe.model_json_schema(),freeze=FreezeLocalLibrary.model_json_schema())

    def probe(self,params):
        self.verify_engine();spec=LocalLibraryProbe.model_validate({k:params[k] for k in ['symbolId','footprintId']}|dict(includeModels=params.get('includeModels',True)))
        if not self.profile()['available']:raise DomainError('dependency_missing','本地库探查需要KiCad10 CLI和原生Python。')
        share=Path(self.config.kicad_cli).resolve().parent.parent/'share/kicad'
        sl,sn=asset_id(spec.symbolId);fl,fn=asset_id(spec.footprintId)
        sp=within(share,'symbols/'+sl+'.kicad_sym');fp=within(share,'footprints/'+fl+'.pretty/'+fn+'.kicad_mod')
        _,_,symbol_hash,tree=external_library(str(sp));symbol=next((s for s in tree.children('symbol') if s.values[1]==sn),None)
        if symbol is None:raise DomainError('not_found','本地库没有指定符号。',404)
        info=describe_symbol(symbol,tree)
        if not info.get('supported'):raise DomainError('unsupported_capability',info.get('limitation','本地符号尚不支持。'))
        footprint=checked(fp,8*1024*1024);native=parse(fp.read_text('utf-8-sig'))
        if native.kind!='footprint' or native.values[1]!=fn:raise DomainError('invalid_input','封装身份与库文件名不一致。')
        models=[]
        for i,m in enumerate(native.children('model')):
            row=dict(index=i,reference=m.values[1],transform={k:[float(v) for v in m.one(k).one('xyz').values[1:]] for k in ['offset','rotate','scale']})
            if spec.includeModels:
                if not m.values[1].startswith('${KICAD10_3DMODEL_DIR}/'):raise DomainError('unsupported_capability','首版只冻结安装库明确引用的KiCad10 STEP模型。')
                path=within(share/'3dmodels',m.values[1].split('}/',1)[1])
                if path.suffix.lower() not in {'.step','.stp'}:raise DomainError('unsupported_capability','原生模型回读需要STEP；未以WRL替代。')
                row['source']=checked(path,64*1024*1024)
            models.append(row)
        inputs=dict(symbolLibrary=checked(sp,12*1024*1024),footprint=footprint,models=models,includeModels=spec.includeModels)
        if inputs['symbolLibrary']['sha256']!=symbol_hash or digest(fp)!=footprint['sha256']:
            raise DomainError('input_changed','本地库在探查期间变化。',409)
        return dict(symbolId=spec.symbolId,footprintId=spec.footprintId,symbol=info,
            electricalPadNumbers=sorted({p.values[1] for p in native.children('pad') if p.values[1]}),
            inputs=inputs,inputsSha256=fingerprint(inputs),license=LICENSE,licenseUri=LICENSE_URI,
            libraryRelease='not-established; exact original bytes identified by SHA-256',nativeValidated=False)

    def supply(self,project,params):
        binding=params.get('supply')
        if binding is None:return None
        read=self.components.read(project,binding['jobId']);job=read['job']
        if job['status']!='completed' or not read['result']:raise DomainError('invalid_input','供应绑定要求已完成的本工程库存快照。')
        artifacts=[a for a in read['artifacts'] if a['kind']=='component_observation']
        if len(artifacts)!=1 or artifacts[0]['sha256']!=binding['expectedSnapshotSha256']:
            raise DomainError('input_changed','供应快照摘要与预期不符。',409)
        row=next((x for x in read['result']['components'] if x['componentCode']==binding['componentCode']),None)
        if row is None or row['componentModel']!=binding['expectedMpn'] or row['componentBrand']!=binding['expectedManufacturer']:
            raise DomainError('version_conflict','C编号、完整型号或厂家与所选供应快照不符。',409)
        return dict(**binding,observedAt=read['result']['observedAt'],identity=row,inventoryReserved=False)

    def validate(self,project,params):
        FreezeLocalLibrary.model_validate({**params,'requestId':'validation'});self.verify_engine()
        if self.store.get('projects',project)['version']!=params['expectedProjectVersion']:
            raise DomainError('version_conflict','工程上下文已改变，请重新读取。',409)
        probe=self.probe(params)
        if probe['inputsSha256']!=params['expectedInputsSha256']:raise DomainError('input_changed','本地库或模型已变化，请重新探查。',409)
        pin_mapping(probe['symbol']['pins'],probe['electricalPadNumbers'],params['pinMap']);self.supply(project,params)
        if params['includeModels'] and probe['inputs']['models'] and not self.config.mechanical_python:
            raise DomainError('dependency_missing','STEP回读需要本项目独立CAD环境。')

    def execute(self,ctx):
        self.verify_engine();job=ctx.job;params=job['params'];self.validate(job['projectId'],params)
        ctx.phase('冻结原始符号库、封装与本地模型')
        probe=self.probe(params);supply=self.supply(job['projectId'],params)
        folder=self.config.data/'projects'/job['projectId']/'jobs'/job['id'];folder.mkdir(parents=True,exist_ok=True)
        frozen=folder/'bundle';frozen.mkdir();files=[]
        def copy(source,relative):
            ctx.cancel();path=Path(source['path']);target=within(frozen,relative);target.parent.mkdir(parents=True,exist_ok=True)
            if digest(path)!=source['sha256']:raise DomainError('input_changed','本地来源在冻结时发生变化。',409)
            shutil.copyfile(path,target)
            if digest(path)!=source['sha256'] or digest(target)!=source['sha256']:raise DomainError('input_changed','来源冻结字节不一致。',409)
            files.append(relative);return target
        original=copy(probe['inputs']['symbolLibrary'],'originals/library.kicad_sym')
        _,_,_,tree=external_library(str(original));name=asset_id(params['symbolId'])[1]
        definition,resolution=resolve_symbol(tree,next(s for s in tree.children('symbol') if s.values[1]==name))
        selected=frozen/'selected.kicad_sym';selected.write_text('(kicad_symbol_lib (version '+str(child_value(tree,'version'))+') (generator "FormForge")\n'+definition.raw+'\n)',encoding='utf8');files.append('selected.kicad_sym')
        fn=asset_id(params['footprintId'])[1];fp=copy(probe['inputs']['footprint'],'footprints.pretty/'+fn+'.kicad_mod')
        model_files=[]
        for m in probe['inputs']['models']:
            if 'source' in m:
                rel='models/'+m['source']['sha256']+'.step';dest=copy(m['source'],rel)
                model_files.append(dict(index=m['index'],path=str(dest),sha256=m['source']['sha256'],relativePath=rel,transform=m['transform']))
        notice=frozen/'NOTICE.txt';notice.write_text('KiCad library data, KiCad community.\n'+LICENSE+'\n'+LICENSE_URI+'\nOriginal files retained. Selected symbol extracted and inheritance expanded when needed.\nNo manufacturer, pinout, recommended land pattern or mechanical fit certification.\n',encoding='utf8');files.append('NOTICE.txt')
        for license_name in ['CC-BY-SA-4.0.txt','KiCad-library-exception.txt']:
            original=self.code_root/'src/formforge/resources/licenses'/license_name
            copy(dict(path=str(original),sha256=digest(original)),'licenses/'+license_name)
        native=folder/'native';native.mkdir();deadline=time.monotonic()+180;commands=[]
        def command(argv,label):
            ctx.cancel();self.verify_engine();log=native/(label+'.log')
            try:result=run_process(argv,folder,log,{**os.environ,'PYTHONUTF8':'1'},ctx.cancel,deadline)
            except DomainError:
                if log.exists():ctx.publish(log,'component_library_diagnostic','text/plain')
                raise
            commands.append(result)
            if result['exitCode']!=0:
                ctx.publish(log,'component_library_diagnostic','text/plain')
                raise DomainError('tool_failed','本地库原生检查未通过：'+label+'，详情见任务日志。')
        ctx.phase('使用KiCad CLI原生读取选定符号与封装')
        for sub in ['symbol','footprint']:(native/sub).mkdir()
        command([self.config.kicad_cli,'sym','export','svg','--symbol',name,'--output',native/'symbol',selected],'symbol-svg')
        command([self.config.kicad_cli,'fp','export','svg','--footprint',fn,'--output',native/'footprint',fp.parent],'footprint-svg')
        previews=[]
        for sub in ['symbol','footprint']:
            images=list((native/sub).glob('*.svg'))
            if not images or any(not ElementTree.parse(p).getroot().tag.endswith('svg') for p in images):
                ctx.publish(native/(sub+'-svg.log'),'component_library_diagnostic','text/plain')
                raise DomainError('tool_failed','KiCad没有生成有效的本地库SVG。')
            previews.extend(images)
        def worker(python,request,label):
            req=native/(label+'-request.json');res=native/(label+'-response.json');req.write_text(encoded(request),encoding='utf8')
            command([python,'-I','-B',self.engine/'component_library_native.py',req,res],label)
            return json.loads(res.read_text('utf8'))
        ctx.phase('原生回读焊盘并核对显式引脚映射')
        pads=worker(self.config.kicad_python,dict(kind='footprint',path=str(fp),sha256=digest(fp)),'footprint-read')
        pin_mapping(probe['symbol']['pins'],pads['electricalPadNumbers'],params['pinMap'])
        models=worker(self.config.mechanical_python,dict(kind='models',models=model_files),'step-read') if model_files else None
        if self.probe(params)['inputsSha256']!=params['expectedInputsSha256']:raise DomainError('input_changed','原生检查期间来源改变。',409)
        ctx.cancel()
        manifest=[dict(path=p,sha256=digest(within(frozen,p)),size=within(frozen,p).stat().st_size) for p in sorted(set(files))]
        result=dict(schemaVersion='formforge.component-library/1',bundleId=job['id'],projectId=job['projectId'],contextVersion=job['contextSnapshot']['version'],
            createdAt=now(),symbolId=params['symbolId'],footprintId=params['footprintId'],inputs=probe['inputs'],inputsSha256=probe['inputsSha256'],
            supply=supply,pinMap=params['pinMap'],symbol=probe['symbol'],symbolResolution=resolution,
            nativeFootprint=pads,nativeModels=models,models=model_files,modelPurpose=params['modelPurpose'],decisionNote=params['decisionNote'],
            nativeChecksPassed=True,manufacturerPinoutVerified=False,mechanicalFitCertified=False,purchasingQualified=False,
            license=LICENSE,licenseUri=LICENSE_URI,engineHash=self.engine_hash,engineFiles=self.files,commands=commands,
            manifest=manifest,symbolSource=dict(path=str(selected),sha256=digest(selected)),footprintSource=dict(path=str(fp),sha256=digest(fp)),
            designHeadModified=False)
        report=folder/'component-library.json';report.write_text(encoded(result),encoding='utf8')
        bundle_manifest=frozen/'manifest.json';bundle_manifest.write_text(encoded(dict(files=manifest,inputsSha256=probe['inputsSha256'])),encoding='utf8')
        archive=folder/'component-library.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for p in [*manifest,dict(path='manifest.json')]:z.write(within(frozen,p['path']),p['path'])
        for image in previews:ctx.publish(image,'component_library_preview','image/svg+xml')
        ctx.publish(selected,'component_library_symbol','application/octet-stream');ctx.publish(fp,'component_library_footprint','application/octet-stream')
        for m in model_files:ctx.publish(Path(m['path']),'component_library_model','model/step')
        ctx.publish(archive,'component_library_archive','application/zip');ctx.publish(report,'component_library_report','application/json')
        if self.store.finish_readonly(job['id'],result,'本地器件资料已冻结并通过原生回读；型号适配与机械资格另审') is None:raise Cancelled()

    def read(self,project,job_id=None):
        current=self.store.get('projects',project);jobs=sorted([j for j in self.store.list('jobs',project) if j['kind']=='components.freeze_local'],key=lambda j:(j['createdAt'],j['id']),reverse=True)
        job=self.store.get('jobs',job_id) if job_id else next(iter(jobs),None)
        if job and (job['projectId']!=project or job['kind']!='components.freeze_local'):raise DomainError('not_found','本地库任务不属于本工程。',404)
        artifacts=[a for a in self.store.list('artifacts',project) if job and a['jobId']==job['id']];result=None
        for a in artifacts:
            if a['kind']!='component_library_report':continue
            path=within(self.config.data,a['relativePath'])
            if not path.is_file() or path.stat().st_size>4*1024*1024 or digest(path)!=a['sha256']:raise DomainError('input_changed','本地器件资料报告已变化。',409)
            result=json.loads(path.read_text('utf8'))
            for f in result['manifest']:
                target=within(path.parent/'bundle',f['path'])
                if not target.is_file() or digest(target)!=f['sha256']:raise DomainError('input_changed','本地器件资料包字节已变化。',409)
        return dict(profile=self.profile(),jobs=jobs,job=job,result=result,artifacts=artifacts,
            contextChanged=bool(job and current['version']!=job['contextSnapshot']['version']))
