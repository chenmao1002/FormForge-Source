"""Versioned procurement decisions over immutable native manufacturing packages."""
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import time
import xml.etree.ElementTree as ET
import zipfile

from pydantic import ValidationError

from .connectivity import fingerprint
from .importer import digest,within,verify_snapshot,reparse
from .procurement_rules import evaluate,offer_status
from .procurement_matching import write_matching_files,BOM_NAME,CPL_NAME,NOTES_NAME
from ..procurement_schemas import CataloguePart,PlanProcurement
from ..source_registry import SourceRegistry
from ..store import DomainError,now,uid
from ..jobs import Cancelled
from .. import __version__


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')


def verified_source(config,revision):
    if revision.get('module')!='manufacturing' or not revision.get('procurementDesign'):raise DomainError('invalid_input','指定版本不是采购计划。')
    root=within(config.data,revision['relativePath'])
    if any(reparse(p) for p in root.rglob('*')):raise DomainError('input_changed','采购版本不接受重解析路径。',409)
    if {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}!={r['path'] for r in revision['manifest']}:
        raise DomainError('input_changed','采购版本文件集合已改变。',409)
    verify_snapshot(root,revision['manifest']);return root


def read_basis(package,job,revision):
    with zipfile.ZipFile(package) as z:
        entries=z.infolist()
        if len(entries)>10000 or len({r.filename.casefold() for r in entries})!=len(entries) or sum(r.file_size for r in entries)>512*1024*1024:
            raise DomainError('invalid_input','制造包文件集合重复或超限。')
        manifest=json.loads(z.read('manufacturing-files.json'));names={r.filename for r in entries}
        if len(manifest)!=len(names)-1 or names!={r['path'] for r in manifest}|{'manufacturing-files.json'}:raise DomainError('input_changed','制造包清单不完整。',409)
        for row in manifest:
            if hashlib.sha256(z.read(row['path'])).hexdigest()!=row['sha256']:raise DomainError('input_changed','制造包内文件SHA不符。',409)
        report=json.loads(z.read('manufacturing-report.json'));binding=json.loads(z.read('binding.json'));source_revision=json.loads(z.read('source-revision.json'))
        if binding['jobId']!=job['id'] or binding['revisionId']!=revision['id'] or source_revision['id']!=revision['id'] or report['jobId']!=job['id'] or report['revisionId']!=revision['id']:
            raise DomainError('input_changed','制造包版本或任务绑定不符。',409)
        if any(row['projectId']!=job['projectId'] for row in [binding,report,source_revision]):raise DomainError('input_changed','制造包工程绑定不符。',409)
        if not report['fileConsistencyPassed'] or report['boardSha256']!=job['params']['expectedBoardHash'] or source_revision['manifest']!=revision['manifest']:
            raise DomainError('invalid_input','制造包必须有同版完整且一致的原生资料。')
        tree=ET.fromstring(z.read('native/netlist.xml'))
        symbols={c.attrib['ref']:dict(c.find('libsource').attrib) for c in tree.findall('components/comp')}
        native=json.loads(z.read('native/board.json'))
        return dict(report=report,binding=binding,native=native,symbols=symbols,sourceRevision=source_revision)


def safe_cell(value):
    text='' if value is None else str(value)
    return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text


class ProcurementAdapter:
    module='manufacturing'
    kind='manufacturing.procurement'
    version='source-bound-procurement/0.19.1'
    expected_head_field='expectedManufacturingRevisionId'

    def __init__(self,config,store):
        self.config,self.store=config,store;self.sources=SourceRegistry(config,store)
        names=['src/formforge/adapters/procurement.py','src/formforge/adapters/procurement_rules.py','src/formforge/procurement_schemas.py','src/formforge/adapters/procurement_matching.py']
        code_root=Path(__file__).resolve().parents[3]
        self.engine_files={name:digest(code_root/name) for name in names};self.engine_hash=fingerprint(self.engine_files)
        self.engine_root=config.local/'engines/procurement'/self.engine_hash
        for name,h in self.engine_files.items():
            target=within(self.engine_root,name);target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():shutil.copyfile(code_root/name,target)
            if digest(target)!=h:raise DomainError('input_changed','采购领域引擎缓存变化。',409)

    def schema(self,project=None,manufacturing_job_id=None):
        if bool(project)!=bool(manufacturing_job_id):raise DomainError('invalid_input','基准模板需要同时给工程和制造任务ID。')
        result=dict(available=True,adapterVersion=self.version,engineHash=self.engine_hash,requestSchema=PlanProcurement.model_json_schema(),cataloguePartSchema=CataloguePart.model_json_schema(),
            independentHead='moduleHeads.manufacturing',sourceBinding='source_transfer bindings.procurement; same-project bytes and provenance SHA',
            engineScope='four domain modules; common runtime separately identified by FormForge version',
            supportedPassiveSymbols=['R','R_Small','C','C_Small'],otherParts='explicit same-device/orderable-variant declaration, package and pad-contact mapping',
            fileFormats=['review CSV','JLCPCB BOM/CPL review pair when complete and placements explicitly reviewed','JLC domestic SMT matching-only BOM/CPL plus explicit unresolved/native-anchor notes; no production approval'],
            matchingBomGrouping='Identical selected JLC C code and all declared BOM fields; per-board quantity; full reference and CPL roundtrip',
            quotesEnabled=False,orderingEnabled=False,manufacturingApproved=False,
            factsTrust='caller-declared catalogue transcription, immutable evidence verified; no automatic PDF interpretation, supplier authentication or electrical-equivalence certification')
        if project:
            artifacts=[a for a in self.store.list('artifacts',project) if a['jobId']==manufacturing_job_id and a['kind']=='manufacturing_project']
            if len(artifacts)!=1:raise DomainError('invalid_input','缺少明确制造审核包。')
            basis=self.basis(project,dict(manufacturingJobId=manufacturing_job_id,expectedPackageSha256=artifacts[0]['sha256']))
            native={f['reference']:f for f in basis['data']['native']['footprints']}
            from collections import Counter
            selected=basis['data']['report']['selectedParts']
            result['basisContext']=dict(projectId=project,manufacturingJobId=manufacturing_job_id,revisionId=basis['revision']['id'],packageSha256=artifacts[0]['sha256'],
                targetParts=[dict(reference=p['reference'],value=p['value'],footprint=p['footprint'],mounting=p['mounting'],position=p['position'],symbol=basis['data']['symbols'][p['reference']],
                    padContactCounts=dict(Counter(pad['number'] for pad in native[p['reference']]['pads']))) for p in selected],
                physicalEquivalenceCertified=False,positionFrame='native-position-export-mm, not assumed supplier centroid',currentEdaMatches=self.store.head_revision(project)==basis['revision']['id'])
        return result

    def describe(self):
        return dict(available=True,adapterVersion=self.version,engineHash=self.engine_hash,independentHead='moduleHeads.manufacturing',
            registeredSourcesRequired=True,nativeDesignMutated=False,quotesEnabled=False,orderingEnabled=False)

    def basis(self,project,params):
        job=self.store.get('jobs',params['manufacturingJobId']);result=job.get('result') or {}
        if job['projectId']!=project or job['kind']!='manufacturing.prepare' or job['status']!='completed' or not result.get('fileConsistencyPassed'):
            raise DomainError('invalid_input','需要同工程已完成且资料一致的制造任务。')
        revision=self.store.get('revisions',job['inputRevision'])
        if revision['projectId']!=project:raise DomainError('invalid_input','制造输入版本不属于此工程。')
        verify_snapshot(within(self.config.data,revision['relativePath']),revision['manifest'])
        artifacts=[a for a in self.store.list('artifacts',project) if a['jobId']==job['id'] and a['revisionId']==revision['id'] and a['kind']=='manufacturing_project']
        if len(artifacts)!=1 or artifacts[0]['sha256']!=params['expectedPackageSha256']:raise DomainError('version_conflict','制造审核包缺失、重复或预期SHA不符。',409)
        path=within(self.config.data,artifacts[0]['relativePath'])
        if reparse(path) or digest(path)!=artifacts[0]['sha256']:raise DomainError('input_changed','制造审核包原件已改变。',409)
        data=read_basis(path,job,revision)
        if not data['report']['selectedParts']:raise DomainError('invalid_input','此制造场景没有待装器件，不需要采购计划。')
        return dict(job=job,revision=revision,artifact=artifacts[0],path=path,data=data)

    def source_inputs(self,project,params):
        records={};paths={};total=0
        for ref in params['sources']:
            row,path=self.sources.get_record(project,ref['sourceId'])
            if row['sha256']!=ref['expectedSha256'] or row['provenanceSha256']!=ref['expectedProvenanceSha256']:
                raise DomainError('input_changed','采购资料字节或来源摘要与请求不符。',409)
            if path.suffix.lower() not in {'.json','.pdf','.md','.txt','.csv','.png','.jpg','.jpeg'}:raise DomainError('invalid_input','采购仅接受已登记的数据资料，不执行代码。')
            total+=row['size'];records[row['id']]=row;paths[row['id']]=path
        if total>256*1024*1024:raise DomainError('invalid_input','单次采购资料超过256MiB。')
        part_ids={line['partSourceId'] for line in params['lines'] if line['partSourceId']};parts={};used=set(part_ids)
        for id in part_ids:
            if id not in paths or paths[id].suffix.lower()!='.json' or paths[id].stat().st_size>192*1024:
                raise DomainError('invalid_input','器件事实须引用至多192KiB的同工程登记JSON。')
            try:part=CataloguePart.model_validate_json(paths[id].read_text(encoding='utf-8-sig')).model_dump(mode='json')
            except (ValidationError,ValueError) as error:raise DomainError('invalid_input','器件JSON不符合已发布schema：'+str(error)[:1600]) from error
            parts[id]=part
            citations=part['documents']+([part['offer']['evidence']] if part['offer'] else [])
            if any(c['sourceId'] in part_ids for c in citations):raise DomainError('invalid_input','器件事实JSON不能充当自己的或其他器件的原始依据。')
            used.update(c['sourceId'] for c in citations)
        if any(p['evidence']['sourceId'] in part_ids for p in params['placements']):
            raise DomainError('invalid_input','质心/方向依据不能以器件事实JSON自身代替原始资料。')
        used.update(p['evidence']['sourceId'] for p in params['placements'])
        if used!=set(records):raise DomainError('invalid_input','引用资料缺失或包含未使用来源；请完整列出事实、文档和方向依据。')
        return records,paths,parts

    def validate(self,project,params):
        basis=self.basis(project,params);self.source_inputs(project,params)
        parent=params['expectedManufacturingRevisionId']
        if parent:
            revision=self.store.get('revisions',parent)
            if revision['projectId']!=project or not revision.get('validation',{}).get('adopted'):raise DomainError('invalid_input','采购父版本必须是同工程已采纳版本。')
            verified_source(self.config,revision)
        return basis

    def candidate(self,job,cancel):
        params=job['params'];basis=self.validate(job['projectId'],params);records,paths,parts=self.source_inputs(job['projectId'],params)
        rid=uid('r');root=self.config.data/'projects'/job['projectId']/'revisions'/rid/'source';root.mkdir(parents=True)
        package=root/'basis/manufacturing-review.zip';package.parent.mkdir();shutil.copyfile(basis['path'],package)
        if digest(package)!=params['expectedPackageSha256']:raise DomainError('input_changed','制造包冻结时变化。',409)
        source_metadata=[];frozen_parts={}
        for id,row in records.items():
            cancel();path=root/'evidence'/id/'original'/row['name'];path.parent.mkdir(parents=True);shutil.copyfile(paths[id],path)
            if digest(path)!=row['sha256']:raise DomainError('input_changed','采购资料冻结时变化。',409)
            provenance=within(self.config.data,row['provenancePath']);copy=root/'evidence'/id/'record.json';shutil.copyfile(provenance,copy)
            if digest(copy)!=row['provenanceSha256']:raise DomainError('input_changed','采购来源记录冻结时变化。',409)
            source_metadata.append(dict(id=id,name=row['name'],path=path.relative_to(root).as_posix(),sha256=row['sha256'],provenanceSha256=row['provenanceSha256'],
                uri=row['uri'],version=row['version'],license=row['license'],note=row['note'],origin=row['origin'],provenanceTrust='caller-declared; original bytes/provenance verified'))
            if id in parts:frozen_parts[id]=CataloguePart.model_validate_json(path.read_text(encoding='utf-8-sig')).model_dump(mode='json')
        for name,h in self.engine_files.items():
            cancel();src=within(self.engine_root,name);target=within(root/'engine',name);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,target)
            if digest(target)!=h:raise DomainError('input_changed','采购领域引擎冻结时变化。',409)
        design=dict(schemaVersion=1,parameters=params,sources=source_metadata,parts=frozen_parts,
            manufacturingDependency=dict(jobId=basis['job']['id'],revisionId=basis['revision']['id'],packageSha256=params['expectedPackageSha256'],artifact=basis['artifact']),
            contextBinding=fingerprint({k:job['contextSnapshot'][k] for k in ['specification','preferences']}),
            engineHash=self.engine_hash,engineFiles=self.engine_files,commonRuntimeVersion=__version__,reviewAuthority='external caller declaration; service checks listed contracts')
        save(root/'plan.json',design)
        manifest=[dict(path=p.relative_to(root).as_posix(),sha256=digest(p),size=p.stat().st_size) for p in sorted(root.rglob('*')) if p.is_file()]
        revision=dict(id=rid,projectId=job['projectId'],module='manufacturing',adapter='source-bound-procurement',origin='procurement_planned',
            parentRevisionId=params['expectedManufacturingRevisionId'],createdByJobId=job['id'],relativePath=root.relative_to(self.config.data).as_posix(),
            entrypoints={'manufacturing':'plan.json'},manifest=manifest,totalBytes=sum(r['size'] for r in manifest),createdAt=now(),message=params['message'],
            validation={'status':'pending'},changes=[dict(kind='procurement_plan',mode=params['mode'],lineGroups=len(params['lines']))],
            procurementDesign=dict(mode=params['mode'],manufacturingDependency=design['manufacturingDependency'],contextBinding=design['contextBinding'],engineHash=self.engine_hash,lineGroups=len(params['lines'])))
        return revision,basis['data'],design

    def execute(self,task):
        job=task.job;deadline=time.monotonic()+job['params']['timeoutSeconds']
        task.phase('冻结基准制造包、采购决策及不可变资料')
        revision,basis,design=self.candidate(job,task.cancel);task.register_candidate(revision)
        source=verified_source(self.config,revision)
        output=self.config.data/'projects'/job['projectId']/'jobs'/job['id'];output.mkdir(parents=True)
        task.publish(source/'plan.json','procurement_plan','application/json')
        task.phase('核对全部位号、封装/端子、原生阻容要求和商业快照')
        result=evaluate(basis,job['params'],design['parts']);task.cancel()
        result.update(revisionId=revision['id'],jobId=job['id'],projectId=job['projectId'],engineHash=self.engine_hash,
            manufacturingDependency=design['manufacturingDependency'],mode=job['params']['mode'],boardQuantity=job['params']['boardQuantity'],sources=design['sources'],
            reviewStatement=job['params']['reviewStatement'],adoption='candidate; adoption recorded separately by the project transaction')
        self.review_csv(output/'procurement-review.csv',result['rows'])
        files=[output/'procurement-review.csv']
        if result['supplierFilesReady']:
            task.phase('生成配对的JLCPCB BOM/CPL审核副本，保留原始坐标和声明修正')
            errors=self.supplier_files(output,result,design['parts'])
            if errors:
                result['problems']+=errors
                for field in ['checksPassed','planComplete','procurementReviewed','supplierFilesReady','indicativeMaterialsEstimateComplete']:result[field]=False
            else:files += [output/'jlcpcb-bom-review.csv',output/'jlcpcb-cpl-review.csv']
        if result['matchingFilesReady']:
            task.phase('生成全量贴装选型资料，保留未确定器件与坐标待核说明')
            errors=write_matching_files(output,result)
            if errors:
                result['problems']+=errors
                for field in ['checksPassed','planComplete','procurementReviewed','matchingFilesReady','indicativeMaterialsEstimateComplete']:result[field]=False
            else:files += [output/BOM_NAME,output/CPL_NAME,output/NOTES_NAME]
        save(output/'procurement-report.json',result);files.append(output/'procurement-report.json')
        matching_kinds={BOM_NAME:'procurement_matching_bom',CPL_NAME:'procurement_matching_cpl',NOTES_NAME:'procurement_matching_notes'}
        for p in files:
            task.cancel();task.publish(p,matching_kinds.get(p.name) or ('procurement_report' if p.suffix=='.json' else 'procurement_jlc_bom' if p.name=='jlcpcb-bom-review.csv' else 'procurement_jlc_cpl' if p.name=='jlcpcb-cpl-review.csv' else 'procurement_review'),'application/json' if p.suffix=='.json' else 'text/csv')
        verify_snapshot(source,revision['manifest']);task.cancel()
        archive=output/'procurement-project.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for row in revision['manifest']:task.cancel();z.write(within(source,row['path']),'source/'+row['path'])
            for p in files:z.write(p,'outputs/'+p.name)
            z.writestr('formforge-manifest.json',json.dumps({k:v for k,v in revision.items() if k!='validation'},ensure_ascii=False,indent=2))
            z.writestr('formforge-output-hashes.json',json.dumps({p.name:digest(p) for p in files},indent=2))
        task.publish(archive,'procurement_project','application/zip');task.cancel()
        if time.monotonic()>=deadline:raise DomainError('tool_failed','采购计划达到时间预算；候选不采纳。')
        if self.store.finish_module(job['id'],revision['id'],result,self.module,
            '采购计划已采纳；草案/资料审核与工程生产资格分别记录','采购资料需处理，当前计划保持') is None:raise Cancelled()

    @staticmethod
    def review_csv(path,rows):
        fields=['reference','disposition','nativeValue','footprint','manufacturer','mpn','supplier','supplierPartNumber','partSourceId','reason']
        with path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
            for row in rows:writer.writerow({field:safe_cell(row.get(field)) for field in fields})

    @staticmethod
    def supplier_files(output,result,parts):
        errors=[];bom=[];cpl=[];groups={}
        for row in result['rows']:
            key=(row['partSourceId'],row['disposition']);groups.setdefault(key,[]).append(row)
            pos=row['supplierPosition'];cpl.append(dict(Designator=row['reference'],**{'Mid X':format(Decimal(pos['xMm']),'f'),'Mid Y':format(Decimal(pos['yMm']),'f'),
                'Layer':pos['layer'],'Rotation':format(Decimal(pos['rotationDegrees']),'f')}))
        for (id,disposition),rows in groups.items():
            part=parts[id];bom.append({'Comment':'; '.join(sorted({r['nativeValue'] for r in rows})),'Designator':','.join(sorted(r['reference'] for r in rows)),
                'Footprint':part['packageName'],'LCSC Part #':part.get('supplierPartNumber') or '' if disposition=='selected' else '', 'Manufacturer':part['manufacturer'],'MPN':part['mpn']})
        for row in bom:
            if any(str(value).lstrip().startswith(('=','+','-','@')) for value in row.values()):errors.append(dict(code='unsafe_supplier_csv',message='供应商文本字段不能作为表格公式；请先核对原数据。',reference=None))
        if any(row['Designator'].lstrip().startswith(('=','+','-','@')) for row in cpl):errors.append(dict(code='unsafe_supplier_csv',message='供应商位号不允许表格公式。',reference=None))
        if errors:return errors
        for name,rows,fields in [('jlcpcb-bom-review.csv',bom,['Comment','Designator','Footprint','LCSC Part #','Manufacturer','MPN']),('jlcpcb-cpl-review.csv',cpl,['Designator','Mid X','Mid Y','Layer','Rotation'])]:
            with (output/name).open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
            with (output/name).open(encoding='utf-8-sig',newline='') as stream:read=list(csv.DictReader(stream))
            refs=[ref for row in read for ref in row['Designator'].split(',')]
            if len(set(refs))!=len(refs) or set(refs)!={row['reference'] for row in result['rows']}:raise DomainError('tool_failed','供应商CSV回读位号集合不符。')
        return []

    def read(self,project,revision_id=None,offset=0,limit=100):
        p=self.store.get('projects',project);rid=revision_id or p.get('moduleHeads',{}).get('manufacturing')
        if not rid:return dict(revisionId=None,currentManufacturingRevisionId=None,plan=None,result=None,rows=[],total=0)
        revision=self.store.get('revisions',rid)
        if revision['projectId']!=project:raise DomainError('invalid_input','采购版本不属于此工程。')
        source=verified_source(self.config,revision);design=json.loads((source/'plan.json').read_text(encoding='utf8'))
        if offset<0 or not 1<=limit<=300:raise DomainError('invalid_input','采购行分页范围无效。')
        job=self.store.get('jobs',revision['createdByJobId']);result=job.get('result');rows=(result or {}).get('rows',[])
        changed=self.store.head_revision(project)!=design['manufacturingDependency']['revisionId'] or fingerprint({k:p[k] for k in ['specification','preferences']})!=design['contextBinding']
        from datetime import datetime,timezone
        freshness={id:offer_status(part.get('offer'),datetime.now(timezone.utc),design['parameters']['maxOfferAgeSeconds'],None) for id,part in design['parts'].items()}
        page=rows[offset:offset+limit];part_ids={r.get('partSourceId') for r in page if r.get('partSourceId')}
        summary={k:v for k,v in result.items() if k not in {'rows','sources','problems','warnings','commercial'}} if result else None
        if result:summary.update(problems=result['problems'][:100],problemCount=len(result['problems']),warnings=result['warnings'][:100],warningCount=len(result['warnings']),
            commercial=[c for c in result['commercial'] if c['partSourceId'] in part_ids][:100])
        plan=dict(schemaVersion=design['schemaVersion'],manufacturingDependency=design['manufacturingDependency'],engineHash=design['engineHash'],commonRuntimeVersion=design['commonRuntimeVersion'],
            mode=design['parameters']['mode'],targetFormat=design['parameters']['targetFormat'],boardQuantity=design['parameters']['boardQuantity'],reviewStatement=design['parameters']['reviewStatement'],
            maxOfferAgeSeconds=design['parameters']['maxOfferAgeSeconds'],sourceCount=len(design['sources']),lineGroups=len(design['parameters']['lines']))
        return dict(revisionId=rid,currentManufacturingRevisionId=p.get('moduleHeads',{}).get('manufacturing'),validation=revision['validation'],
            plan=plan,result=summary,rows=page,total=len(rows),nextOffset=offset+limit if offset+limit<len(rows) else None,
            sourceRecords=design['sources'][:100],sourceRecordsTruncated=len(design['sources'])>100,
            currentOfferFreshness={id:{k:v for k,v in state.items() if k in {'state','fresh','observedAt','effectiveExpiry'}} for id,state in freshness.items() if id in part_ids},
            requiresDependencyReview=changed,orderingEnabled=False,manufacturingApproved=False)
