"""Independent quote/order evidence for original STEP parts; never executes a provider action."""
from datetime import datetime,timedelta,timezone
import json
from .adapters.importer import digest,within,reparse
from .order_registry import fingerprint,fail
from .store import DomainError,encode,now,uid
from .structural_quote_schemas import CreateStructuralQuote,AppendStructuralQuoteObservation

ROUTE='https://www.jlc-3dp.cn/placeOrder'
CHOICES=('material','supplierProcess','color','surfaceFinish')


class StructuralQuoteRegistry:
    def __init__(self,config,store,sources,structural):
        self.config,self.store,self.sources,self.structural=config,store,sources,structural

    def profile(self):
        return dict(available=True,provider='jlc-3dp',mode='prepare-and-append-browser-observations',
            supplierAuthenticated=False,providerBytesVerified=False,providerExecutorAvailable=False,
            uploadingEnabled=False,orderingEnabled=False,paymentAllowed=False,productionReleaseAllowed=False)

    def schema(self,project=None,job_id=None):
        result=dict(schemaVersion='formforge.structural-quote/1',profile=self.profile(),officialRoute=ROUTE,
            create=CreateStructuralQuote.model_json_schema(),observe=AppendStructuralQuoteObservation.model_json_schema())
        if job_id and not project:fail('选择结构任务时须同时指定工程。')
        if project:
            current=self.store.get('projects',project);result['projectVersion']=current['version']
            if job_id:
                binding,stale,_,_=self._binding(project,job_id)
                result.update(basis=binding,requiresDependencyReview=stale)
        return result

    def _binding(self,project,job_id,archive_sha=None):
        read=self.structural.read(project,job_id);job=read['job'];result=read['result']
        if not job or job['status']!='completed' or not result or result.get('fileChecksPassed') is not True:fail('需要已通过原生STEP检查的结构资料任务。')
        if not read['inputIntegrityVerified']:fail(read['inputIssue'],'input_changed',409)
        artifacts=read['artifacts'];by_kind={}
        expected=self.config.data/'projects'/project/'jobs'/job_id
        for artifact in artifacts:
            path=within(self.config.data,artifact['relativePath'])
            target=expected/'parts'/artifact['name'] if artifact['kind'] in {'structural_step','structural_stl'} else expected/artifact['name']
            if path.absolute()!=target.absolute() or artifact.get('revisionId')!=job.get('inputRevision'):fail('结构产物的路径或版本绑定不符。','input_changed',409)
            if any(p.exists() and reparse(p) for p in [path,*path.parents] if p!=self.config.root and self.config.root in p.parents):fail('结构产物不接受重解析路径。','input_changed',409)
            if not path.is_file() or path.stat().st_size!=artifact['size'] or digest(path)!=artifact['sha256']:fail('结构产物原始字节已变化。','input_changed',409)
            by_kind.setdefault(artifact['kind'],[]).append(artifact)
        if any(len(by_kind.get(kind,[]))!=1 for kind in ['structural_project','structural_report']):fail('结构审核包或报告不唯一。','input_changed',409)
        archive=by_kind['structural_project'][0];report=by_kind['structural_report'][0]
        if archive_sha is not None and archive['sha256']!=archive_sha:fail('结构审核包SHA不符。','version_conflict',409)
        try:body=json.loads(within(self.config.data,report['relativePath']).read_text('utf8'))
        except (ValueError,UnicodeError,OSError):fail('结构原始报告不可读取。','input_changed',409)
        if body!=result or result.get('jobId')!=job_id:fail('结构报告与完成任务不一致。','input_changed',409)
        parts=[]
        for choice in result['choices']['parts']:
            if choice['disposition']!='fabricate':continue
            rows=[a for a in by_kind.get('structural_step',[]) if a['name']==choice['partId']+'.step']
            if len(rows)!=1 or rows[0]['sha256']!=choice['expectedStepSha256']:fail('制作件STEP与完整处置不符。','input_changed',409)
            parts.append(dict(partId=choice['partId'],quantity=choice['quantity'],step=rows[0]))
        if not parts or len(parts)!=len(by_kind.get('structural_step',[])):fail('结构制作件不完整或有额外STEP。','input_changed',409)
        binding=dict(structuralJobId=job_id,revisionId=result['revisionId'],designSha256=result['designSha256'],
            jobSha256=fingerprint(job),contextSnapshot=job['contextSnapshot'],archive=archive,report=report,parts=parts,
            mechanicalBindingSha256=fingerprint(result['binding']))
        return binding,read['requiresDependencyReview'],job,artifacts

    def _owned(self,db,table,identifier,project):
        row=self.store._get(db,table,identifier)
        if row['projectId']!=project:fail('该结构报价记录不属于此工程。','not_found',404)
        return row

    def _replay(self,db,key,signature,project):
        row=db.execute('SELECT * FROM structural_quote_requests WHERE key=?',(key,)).fetchone()
        if not row:return None
        if row['fingerprint']!=signature:fail('该请求编号已用于不同的结构报价操作。','version_conflict',409)
        return dict(quote=self._owned(db,'structural_quotes',row['quote_id'],project),
            observation=self._owned(db,'structural_quote_observations',row['observation_id'],project) if row['observation_id'] else None,
            replayed=True,providerActionExecuted=False)

    def _parts(self,expected,observed):
        current={p['partId']:p for p in expected};rows={p['partId']:p for p in observed}
        if set(current)!=set(rows):fail('须明确全部且仅有本次制作件，不能漏件或加入已有件。')
        result=[]
        for identifier in sorted(current):
            before=current[identifier]
            after=rows[identifier]
            if any(before[k]!=after[k] for k in ['expectedStepSha256','quantity']):fail('报价零件的STEP或数量与冻结资料不一致。','version_conflict',409)
            for key in CHOICES:
                if before[key] is not None and after[key] is not None and before[key]!=after[key]:fail('材料/工艺选择已变化，请建立新报价记录。','version_conflict',409)
            result.append({**before,**{k:after[k] if after[k] is not None else before[k] for k in CHOICES}})
        return result

    def _unchanged(self,db,job,artifacts,evidence=()):
        if self.store._get(db,'jobs',job['id'])!=job:fail('保存前结构任务已变化。','input_changed',409)
        for row in artifacts:
            if self.store._get(db,'artifacts',row['id'])!=row:fail('保存前结构产物登记已变化。','input_changed',409)
        for row in evidence:
            if self.store._get(db,'sources',row['id'])!=row:fail('保存前平台证据登记已变化。','input_changed',409)

    def create(self,project,request):
        request=CreateStructuralQuote.model_validate(request).model_dump(mode='json');signature=fingerprint([project,'create',request])
        with self.store.tx() as db:replay=self._replay(db,request['requestId'],signature,project)
        if replay:return replay
        binding,stale,job,artifacts=self._binding(project,request['structuralJobId'],request['expectedArchiveSha256'])
        if stale and request['dependencyPolicy']=='require-current':fail('结构上游已变化，请重新验证或明确历史计价。','version_conflict',409)
        canonical=[dict(partId=p['partId'],expectedStepSha256=p['step']['sha256'],quantity=p['quantity'],**{k:None for k in CHOICES}) for p in binding['parts']]
        resolved=self._parts(canonical,request['parts'])
        source_material=job['result']['choices']['material']
        if source_material is not None and any(p['material']!=source_material for p in resolved):fail('材料与结构制造资料的明确约定不符。')
        with self.store.tx() as db:
            replay=self._replay(db,request['requestId'],signature,project)
            if replay:return replay
            current=self.store._get(db,'projects',project)
            if current['version']!=request['expectedProjectVersion']:fail('工程版本已变化，请重新读取结构报价输入。','version_conflict',409)
            self._unchanged(db,job,artifacts)
            if request['dependencyPolicy']=='require-current':
                if current.get('moduleHeads',{}).get('mechanical')!=binding['revisionId'] or any(current[k]!=binding['contextSnapshot'][k] for k in ['specification','preferences']):
                    fail('保存前结构版本或需求已变化。','version_conflict',409)
                dependency=job['result']['binding']['pcbDependency']
                if dependency and current.get('headRevisionId')!=dependency['revisionId']:fail('保存前PCB依赖已变化。','version_conflict',409)
            quote=dict(id=uid('sq'),projectId=project,version=1,createdAt=now(),updatedAt=now(),adoptedProjectVersion=current['version'],
                request=request,sourceBinding=binding,sourceBindingSha256=fingerprint(binding),resolvedParts=resolved,orderIdentity=None,
                latestObservationId=None,latestObservationSha256=None,officialRoute=ROUTE)
            self.store._put(db,'structural_quotes',quote)
            db.execute('INSERT INTO structural_quote_requests VALUES(?,?,?,?)',(request['requestId'],signature,quote['id'],None))
            self.store._event(db,'structural_quote.created',project,dict(quoteId=quote['id'],quoteVersion=1,stage='prepared'))
        return dict(quote=quote,observation=None,replayed=False,providerActionExecuted=False)

    def _history(self,db,quote):
        rows=db.execute('SELECT data FROM structural_quote_observations WHERE project_id=?',(quote['projectId'],))
        history=sorted([r for row in rows if (r:=json.loads(row[0]))['quoteId']==quote['id']],key=lambda r:r['quoteVersion'])
        previous=None;resolved=sorted(quote['request']['parts'],key=lambda p:p['partId']);identity=None
        for version,row in enumerate(history,2):
            if row['quoteVersion']!=version or row['previousSha256']!=previous or row['sha256']!=fingerprint({k:v for k,v in row.items() if k!='sha256'}):fail('结构报价观察历史摘要或顺序不符。','input_changed',409)
            previous=row['sha256'];resolved=self._parts(resolved,row['observation']['parts'])
            seen=row['observation']['orderIdentity']
            if seen:
                if identity and identity!=seen:fail('结构报价历史订单身份不一致。','input_changed',409)
                identity=seen
        if quote['version']!=len(history)+1 or quote['latestObservationId']!=(history[-1]['id'] if history else None) or quote['latestObservationSha256']!=previous or quote['sourceBindingSha256']!=fingerprint(quote['sourceBinding']) or quote['resolvedParts']!=resolved or quote['orderIdentity']!=identity:
            fail('结构报价记录与冻结历史不一致。','input_changed',409)
        return history

    def observe(self,project,identifier,request):
        request=AppendStructuralQuoteObservation.model_validate(request).model_dump(mode='json');signature=fingerprint([project,identifier,'observe',request])
        with self.store.tx() as db:
            replay=self._replay(db,request['requestId'],signature,project);quote=self._owned(db,'structural_quotes',identifier,project)
            if not replay:self._history(db,quote)
        if replay:return replay
        observation=request['observation'];observed=datetime.fromisoformat(observation['observedAt'].replace('Z','+00:00'))
        if observed>datetime.now(timezone.utc)+timedelta(minutes=5):fail('不能记录未来的平台观察。')
        binding,_,job,artifacts=self._binding(project,quote['sourceBinding']['structuralJobId'],quote['sourceBinding']['archive']['sha256'])
        if binding!=quote['sourceBinding']:fail('结构报价冻结输入已变化。','input_changed',409)
        evidence=[]
        for ref in observation['evidence']:
            row,_=self.sources.get_record(project,ref['sourceId'])
            if row['sha256']!=ref['expectedSha256'] or row['provenanceSha256']!=ref['expectedProvenanceSha256']:fail('平台证据字节或来源摘要不符。','input_changed',409)
            evidence.append(row)
        with self.store.tx() as db:
            replay=self._replay(db,request['requestId'],signature,project)
            if replay:return replay
            quote=self._owned(db,'structural_quotes',identifier,project);history=self._history(db,quote)
            if quote['version']!=request['expectedQuoteVersion']:fail('结构报价已追加新观察，请重新读取版本。','version_conflict',409)
            if history and observed<datetime.fromisoformat(history[-1]['observation']['observedAt'].replace('Z','+00:00')):fail('新增观察时间不得早于当前观察。','version_conflict',409)
            resolved=self._parts(quote['resolvedParts'],observation['parts']);identity=observation['orderIdentity']
            if identity:
                if quote['orderIdentity'] and quote['orderIdentity']!=identity:fail('平台订单身份与原记录不一致。','version_conflict',409)
                unique='jlc-3dp:'+identity['recordId'];old=db.execute('SELECT quote_id FROM structural_quote_identities WHERE key=?',(unique,)).fetchone()
                if old and old['quote_id']!=identifier:fail('该平台订单已关联另一结构报价记录。','order_exists',409)
                if not old:db.execute('INSERT INTO structural_quote_identities VALUES(?,?)',(unique,identifier))
                quote['orderIdentity']=identity
            self._unchanged(db,job,artifacts,evidence)
            row=dict(id=uid('sqo'),projectId=project,quoteId=identifier,quoteVersion=quote['version']+1,recordedAt=now(),previousSha256=quote['latestObservationSha256'],
                observation=observation,evidence=[self.sources.metadata(s) for s in evidence])
            row['sha256']=fingerprint(row);self.store._put(db,'structural_quote_observations',row)
            quote.update(version=row['quoteVersion'],updatedAt=row['recordedAt'],resolvedParts=resolved,latestObservationId=row['id'],latestObservationSha256=row['sha256'])
            self.store._put(db,'structural_quotes',quote)
            db.execute('INSERT INTO structural_quote_requests VALUES(?,?,?,?)',(request['requestId'],signature,identifier,row['id']))
            self.store._event(db,'structural_quote.observed',project,dict(quoteId=identifier,quoteVersion=quote['version'],observationId=row['id']))
        return dict(quote=quote,observation=row,replayed=False,providerActionExecuted=False)

    def _summary(self,quote,current,history):
        obs=history[-1]['observation'] if history else None
        halted=any(r['observation']['paymentStatus']=='paid' or r['observation']['productionStatus'] in {'in-production','shipped'} for r in history)
        stage='unexpected-payment-or-production' if halted else 'awaiting-payment' if obs and obs['awaitingPaymentObserved'] else obs['phase'] if obs else 'prepared'
        issue=None;stale=True
        try:
            binding,stale,_,_=self._binding(quote['projectId'],quote['sourceBinding']['structuralJobId'],quote['sourceBinding']['archive']['sha256'])
            if binding!=quote['sourceBinding']:raise DomainError('input_changed','结构报价冻结输入已变化。',409)
        except DomainError as error:issue=error.message;stale=True
        return dict(**quote,stage=stage,latestObservation=obs,observationsCount=len(history),actionHalted=halted,
            inputIntegrityVerified=issue is None,inputIssue=issue,requiresDependencyReview=stale,
            projectVersionAdvanced=current['version']!=quote['adoptedProjectVersion'],
            lastPriceObservation=next((r['observation'] for r in reversed(history) if r['observation']['prices'] is not None),None),
            platformQuoteObserved=any(r['observation']['phase']=='quoted' for r in history),supplierAuthenticated=False,observationClaimsVerified=False,
            providerBytesVerified=False,engineeringApproved=False,productionApproved=False,paymentAllowed=False,productionReleaseAllowed=False,providerActionExecuted=False)

    def read(self,project,identifier=None,offset=0,limit=40):
        if not 0<=offset<=100000 or not 1<=limit<=100:fail('结构报价分页须为offset≥0、limit=1..100。')
        # Load chain and quote in one snapshot; never call another Store transaction while holding it.
        with self.store.tx() as db:
            current=self.store._get(db,'projects',project)
            if identifier:
                quote=self._owned(db,'structural_quotes',identifier,project);history=self._history(db,quote);quotes=None
            else:
                rows=db.execute('SELECT data FROM structural_quotes WHERE project_id=?',(project,))
                all_quotes=sorted([json.loads(r[0]) for r in rows],key=lambda r:(r['createdAt'],r['id']),reverse=True)
                quotes=[(q,self._history(db,q)) for q in all_quotes[offset:offset+limit]]
        if identifier:return dict(profile=self.profile(),quote=self._summary(quote,current,history),observations=history[offset:offset+limit],total=len(history),offset=offset,limit=limit)
        return dict(profile=self.profile(),quotes=[self._summary(q,current,h) for q,h in quotes],total=len(all_quotes),offset=offset,limit=limit)
