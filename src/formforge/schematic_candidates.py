"""Review and explicitly adopt the same checked schematic candidate across clients."""
import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .adapters.importer import within,verify_snapshot
from .adapters.schematic import compare_revisions
from .schemas import AdoptSchematicCandidate
from .store import DomainError,now


class SchematicCandidates:
    def __init__(self,service):self.service=service;self.store=service.store

    def _records(self,db,project_id,job_id):
        project=self.store._get(db,'projects',project_id);job=self.store._get(db,'jobs',job_id)
        if job['projectId']!=project_id or job['kind']!='schematic.edit':raise DomainError('invalid_input','任务不是此工程的原理图候选。')
        result=job.get('result') or {};rid=result.get('revisionId')
        revision=self.store._get(db,'revisions',rid) if rid else None
        if revision and (revision['projectId']!=project_id or revision.get('createdByJobId')!=job_id):raise DomainError('invalid_input','候选与原生任务绑定不一致。')
        return project,job,revision,result

    def _state(self,db,project,job,revision,result):
        if job['status'] in {'queued','running'}:return 'checking','候选正在生成与检查。'
        if not revision or job['status']!='completed':return 'failed',job.get('error') or '没有可供采纳的候选。'
        validation=revision.get('validation',{})
        if validation.get('adopted'):return 'adopted','此候选已采纳；原生任务和检查记录保留。'
        if not (result.get('checksPassed') and result.get('nativeChecksPassed') and result.get('connectivityMatched')):
            return 'failed','候选未通过原生检查、完整连接目标或数据保持检查。'
        if validation.get('status')!='ready' or job.get('params',{}).get('adoption')!='review':return 'unavailable','此任务未生成等待审阅的候选。'
        if project['version']!=job['contextSnapshot']['version'] or self.store._head(db,project)!=revision.get('parentRevisionId'):
            return 'stale','工程已变化，请基于当前版本重新生成候选。'
        if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND json_extract(data,'$.status') IN ('queued','running') LIMIT 1",(project['id'],)).fetchone():
            return 'busy','此工程还有任务正在执行，请完成后重读候选。'
        return 'ready','原生检查通过，尚未采纳；请结合前后图复核可读性。'

    def read(self,project_id,job_id):
        with self.store.read_tx() as db:
            project,job,revision,result=self._records(db,project_id,job_id)
            state,message=self._state(db,project,job,revision,result)
            artifacts=self.store.list('artifacts',project_id)
            before_id=job.get('inputRevision');after_id=revision['id'] if revision else None
            previews=lambda kind,rid:[a for a in artifacts if a['jobId']==job_id and a.get('revisionId')==rid and a['kind']==kind]
            unmet=[]
            if job.get('error'):unmet.append(dict(kind='task',description=job['error']))
            for check in result.get('checks',[]):
                if check.get('status')!='passed':
                    unmet.append(dict(kind=check['kind'],description='检查未通过',errors=check.get('errors'),warnings=check.get('warnings'),items=check.get('items',[])))
            notes=[]
            for change in result.get('changes',[]):
                for fields in change.get('fieldLayout',[]) or []:
                    for key in ['unresolved','unavailable']:
                        for item in fields.get(key,[]):notes.append(dict(kind='field',status=key,**item))
            file_changes=[]
            if revision and before_id:
                parent=self.store._get(db,'revisions',before_id)
                file_changes=compare_revisions(parent,revision)['files']
            return dict(schemaVersion=1,projectId=project_id,jobId=job_id,jobStatus=job['status'],
                state=state,message=message,canAdopt=state=='ready',expectedProjectVersion=project['version'],
                beforeRevisionId=before_id,afterRevisionId=after_id,changes=result.get('changes',[]),checks=result.get('checks',[]),
                checksPassed=bool(result.get('checksPassed')),connectivityPreserved=result.get('connectivityPreserved'),
                connectivityMatched=result.get('connectivityMatched'),unmetConstraints=unmet,reviewNotes=notes,
                fileChanges=file_changes,beforePreviews=previews('baseline_schematic_preview',before_id),afterPreviews=previews('schematic_preview',after_id),
                reportArtifacts=[a for a in artifacts if a['jobId']==job_id and a['kind'] in {'edit_report','change_report','connectivity_report','preservation_report','report'}],
                adoption=revision.get('adoption') if revision else None,
                scope='Native electrical checks and declared changes; visual review remains necessary. Original completed job is immutable.')

    def adopt(self,project_id,revision_id,request):
        request=AdoptSchematicCandidate.model_validate(request)
        revision=self.service.revision(project_id,revision_id)
        verify_snapshot(within(self.service.config.data,revision['relativePath']),revision['manifest'])
        with self.store.tx() as db:
            project,job,current,result=self._records(db,project_id,request.jobId)
            if not current or current['id']!=revision_id or current.get('parentRevisionId')!=request.baseRevisionId:
                raise DomainError('invalid_input','采纳请求与候选、源版本或原生任务不匹配。')
            receipt=current.get('adoption')
            if receipt and receipt['baseProjectVersion']==request.expectedProjectVersion and self.store._head(db,project)==revision_id:
                return dict(**receipt,alreadyAdopted=True)
            state,message=self._state(db,project,job,current,result)
            if state!='ready':raise DomainError('version_conflict' if state in {'stale','busy','adopted'} else 'invalid_input',message,409 if state in {'stale','busy','adopted'} else 400)
            if project['version']!=request.expectedProjectVersion or job['contextSnapshot']['version']!=request.expectedProjectVersion:
                raise DomainError('version_conflict','工程版本已变化，请重读候选。',409)
            receipt=dict(projectId=project_id,revisionId=revision_id,baseRevisionId=request.baseRevisionId,jobId=request.jobId,
                baseProjectVersion=project['version'],projectVersion=project['version']+1,adoptedAt=now(),source='review')
            current['validation'].update(status='passed',adopted=True);current['adoption']=receipt
            self.store._put(db,'revisions',current)
            project.update(headRevisionId=revision_id,version=receipt['projectVersion'],updatedAt=receipt['adoptedAt'])
            self.store._put(db,'projects',project)
            self.store._event(db,'revision.adopted',project_id,receipt,request.jobId)
            return dict(**receipt,alreadyAdopted=False)


def register_http(app,service):
    router=APIRouter();business=SchematicCandidates(service)
    @router.get('/api/projects/{project_id}/schematic-candidates/{job_id}')
    async def schematic_candidate(project_id:str,job_id:str):
        return JSONResponse(await asyncio.to_thread(business.read,project_id,job_id),headers={'Cache-Control':'no-store'})
    @router.post('/api/projects/{project_id}/schematic-candidates/{revision_id}/adopt')
    async def adopt_candidate(project_id:str,revision_id:str,request:AdoptSchematicCandidate):
        return await asyncio.to_thread(business.adopt,project_id,revision_id,request)
    app.include_router(router)


def register_mcp(mcp,service):
    business=SchematicCandidates(service)
    @mcp.tool()
    async def schematic_candidate_read(project_id:str,job_id:str)->dict:
        """读取同一原生任务的候选前后图、约束/检查与采纳状态；候选与源版本明确绑定。"""
        return await asyncio.to_thread(business.read,project_id,job_id)
    @mcp.tool()
    async def schematic_candidate_adopt(project_id:str,revision_id:str,request:AdoptSchematicCandidate)->dict:
        """采纳adoption=review生成且原生检查通过的同版候选。核对源版本、工程版本及文件；过期候选拒绝。保留原任务及结束时间。"""
        return await asyncio.to_thread(business.adopt,project_id,revision_id,request)
