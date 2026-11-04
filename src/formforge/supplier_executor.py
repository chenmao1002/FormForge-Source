"""Durable supplier attempts, immutable bindings and append-only checkpoints.

An attempt is independent of CAD heads and of adopted order observations.
Retries never erase a failure or imply that a supplier action succeeded.
"""
import asyncio
import json
from .order_registry import fingerprint
from .supplier_schemas import SupplierAttemptRequest, SupplierStepRequest
from .supplier_web import JlcWebAdapter
from .store import DomainError, now, uid


class SupplierExecutor:
    def __init__(self,config,store,orders,browser,adapter=None):
        self.config,self.store,self.orders,self.browser=config,store,orders,browser
        self.adapter=adapter or JlcWebAdapter(browser)
        self.lock=asyncio.Lock();self.active=0

    def schema(self):
        return dict(schemaVersion='formforge.supplier-executor/1',
            prepare=SupplierAttemptRequest.model_json_schema(),step=SupplierStepRequest.model_json_schema(),
            adapter=self.adapter.profile(),activeOperations=self.active,
            durableAttempts=True,appendOnlyCheckpoints=True,versionedSourceBinding=True,
            automaticSubmit=False,automaticPayment=False,productionRelease=False,
            notes=['重放返回原步骤；新requestId保留新步骤。既有同资料订单自动关联，只读核对，禁止重复上传或提交。',
                   '网页表单不支持的动作记为manual-required；不是下单成功。真实订单观察仍由独立order_read保存。',
                   '缓存不保证永久有效。登录、账号及页面需重新核对；不向Agent返回Cookie或私有页面正文。'])

    def _owned(self,db,table,id,project):
        value=self.store._get(db,table,id)
        if value['projectId']!=project:raise DomainError('not_found','执行任务不属于此工程。',404)
        return value

    def _history(self,db,attempt):
        rows=db.execute("SELECT data FROM supplier_steps WHERE project_id=? AND json_extract(data,'$.attemptId')=? ORDER BY json_extract(data,'$.attemptVersion')",(attempt['projectId'],attempt['id']))
        history=[json.loads(r[0]) for r in rows];previous=None
        for version,item in enumerate(history,1):
            raw={k:v for k,v in item.items() if k!='sha256'}
            if item['attemptVersion']!=version or item['previousSha256']!=previous or fingerprint(raw)!=item['sha256']:
                raise DomainError('input_changed','执行步骤历史摘要不符，已停止。',409)
            previous=item['sha256']
        if (len(history)!=attempt['version'] or previous!=attempt.get('latestStepSha256')
                or fingerprint(attempt['sourceBinding'])!=attempt['sourceBindingSha256']
                or fingerprint(dict(provider=attempt['provider'],sourceBinding=attempt['sourceBinding'],parameters=attempt['parameters']))!=attempt['intentSha256']
                or not history or history[0]['result'].get('requestSha256')!=fingerprint(attempt['request'])
                or history[0]['result'].get('intentSha256')!=attempt['intentSha256']
                or history[0]['result'].get('existingOrder')!=attempt.get('existingOrder')
                or history[-1]['id']!=attempt.get('latestStepId') or history[-1]['result']!=attempt.get('latestResult')
                or history[-1]['result']['status']!=attempt['status']):
            raise DomainError('input_changed','执行任务或历史不完整，已停止。',409)
        accounts=[h['result'].get('snapshot',{}).get('accountFingerprint') for h in history]
        accounts=[a for a in accounts if a]
        if (accounts and (len(set(accounts))!=1 or accounts[0]!=attempt.get('accountFingerprint'))
                or not accounts and attempt.get('accountFingerprint')):
            raise DomainError('input_changed','执行账号绑定与步骤历史不符。',409)
        return history

    def read(self,project,attempt_id=None):
        with self.store.tx() as db:
            self.store._get(db,'projects',project)
            if attempt_id:
                attempt=self._owned(db,'supplier_attempts',attempt_id,project)
                return dict(attempt=attempt,steps=self._history(db,attempt),capabilities=self.adapter.profile())
            rows=db.execute('SELECT data FROM supplier_attempts WHERE project_id=? ORDER BY rowid DESC LIMIT 100',(project,))
            attempts=[json.loads(r[0]) for r in rows]
            for attempt in attempts:self._history(db,attempt)
            return dict(attempts=attempts,limit=100,capabilities=self.adapter.profile())

    def _append(self,db,attempt,action,result,request_id=None,signature=None):
        step=dict(id=uid('ss'),projectId=attempt['projectId'],attemptId=attempt['id'],attemptVersion=attempt['version']+1,
                  action=action,result=result,createdAt=now(),previousSha256=attempt.get('latestStepSha256'))
        step['sha256']=fingerprint(step)
        attempt.update(version=step['attemptVersion'],status=result['status'],latestStepSha256=step['sha256'],
                       latestStepId=step['id'],updatedAt=step['createdAt'],latestResult=result)
        self.store._put(db,'supplier_steps',step);self.store._put(db,'supplier_attempts',attempt)
        if request_id:db.execute('INSERT INTO supplier_requests VALUES(?,?,?,?)',(request_id,signature,attempt['id'],step['id']))
        self.store._event(db,'supplier.attempt',attempt['projectId'],dict(attemptId=attempt['id'],version=attempt['version'],action=action,status=attempt['status']))
        return dict(attempt=attempt,step=step,replayed=False)

    def _replay(self,db,project,key,signature):
        row=db.execute('SELECT * FROM supplier_requests WHERE key=?',(key,)).fetchone()
        if not row:return None
        if row['fingerprint']!=signature:raise DomainError('version_conflict','请求编号已用于不同执行操作。',409)
        attempt=self._owned(db,'supplier_attempts',row['attempt_id'],project);self._history(db,attempt)
        return dict(attempt=attempt,step=self._owned(db,'supplier_steps',row['step_id'],project),replayed=True)

    def _binding(self,project,request):
        if request['provider']=='jlc-smt':
            binding,sources,jobs,artifacts,revision=self.orders._smt_binding(project,request)
        else:
            binding,sources,job,artifacts=self.orders._binding(project,request['manufacturingJobId'],request['expectedGerberSha256'],request['parameters'])
            jobs=[job];revision=None
        return binding,sources,jobs,artifacts,revision

    def prepare(self,project,request):
        request=SupplierAttemptRequest.model_validate(request).model_dump(mode='json')
        signature=fingerprint([project,'prepare',request]);key=request['requestId']
        with self.store.tx() as db:
            replay=self._replay(db,project,key,signature)
            if replay:return replay
        binding,sources,jobs,artifacts,revision=self._binding(project,request)
        intent=fingerprint(dict(provider=request['provider'],sourceBinding=binding,parameters=request['parameters']))
        with self.store.tx() as db:
            replay=self._replay(db,project,key,signature)
            if replay:return replay
            p=self.store._get(db,'projects',project)
            if p['version']!=request['expectedProjectVersion']:raise DomainError('version_conflict','工程版本变化，请重新核对下单资料。',409)
            for job in jobs:
                if self.store._get(db,'jobs',job['id'])!=job:raise DomainError('input_changed','制造任务已变化。',409)
            self.orders._artifacts_unchanged(db,artifacts);self.orders._unchanged(db,sources)
            if revision and self.store._get(db,'revisions',revision['id'])!=revision:raise DomainError('input_changed','采购版本已变化。',409)
            existing=db.execute("SELECT data FROM supplier_attempts WHERE project_id=? AND json_extract(data,'$.intentSha256')=?",(project,intent)).fetchone()
            if existing:
                a=json.loads(existing[0]);history=self._history(db,a)
                db.execute('INSERT INTO supplier_requests VALUES(?,?,?,?)',(key,signature,a['id'],history[0]['id']))
                return dict(attempt=a,step=history[0],replayed=True)
            related=[]
            for row in db.execute('SELECT data FROM orders WHERE project_id=?',(project,)):
                order=json.loads(row[0]);old=order['sourceBinding']
                if (request['provider']=='jlc-pcb' and order['identity']['provider']=='jlc-smt'
                        and old['gerber']['sha256']==binding['gerber']['sha256'] and order.get('pcbOrderNumber')):
                    self.orders._verify_chain(order,db)
                    linked=dict(orderId=order['id'],displayNumber=order['pcbOrderNumber'],provider='jlc-pcb',associationKind='linked-pcb-from-smt')
                    if not any(x['displayNumber']==linked['displayNumber'] for x in related):related.append(linked)
                if (order['identity']['provider']==request['provider'] and old['gerber']['sha256']==binding['gerber']['sha256']
                        and (request['provider']=='jlc-pcb' or (old.get('procurement',{}).get('bom',{}).get('sha256')==request['expectedBomSha256']
                        and old.get('procurement',{}).get('cpl',{}).get('sha256')==request['expectedCplSha256']))):
                    self.orders._verify_chain(order,db)
                    related=[x for x in related if x['displayNumber']!=order['identity']['displayNumber']]
                    related.append(dict(orderId=order['id'],displayNumber=order['identity']['displayNumber'],provider=request['provider'],associationKind='registered-order'))
            if len(related)>1:raise DomainError('order_exists','同资料关联多张订单，需要先人工核对，未创建新执行任务。',409)
            a=dict(id=uid('sa'),projectId=project,version=0,provider=request['provider'],parameters=request['parameters'],
                request=request,sourceBinding=binding,sourceBindingSha256=fingerprint(binding),intentSha256=intent,
                existingOrder=related[0] if related else None,createdAt=now(),adoptedProjectVersion=p['version'],
                accountFingerprint=None,target='stop-before-payment',adapter=self.adapter.profile())
            result=dict(status='existing-order' if related else 'prepared',message='已关联既有同资料订单；只允许打开、检查和查单，禁止重复上传或提交。' if related else '资料已固定；尚未上传或提交供应商。',providerActionExecuted=False,
                        requestSha256=fingerprint(request),intentSha256=intent,existingOrder=a['existingOrder'])
            return self._append(db,a,'prepare',result,key,signature)

    def recover(self):
        # A started external step whose process died is never silently rerun.
        with self.store.tx() as db:
            for row in list(db.execute("SELECT data FROM supplier_attempts WHERE json_extract(data,'$.status')='running'")):
                a=json.loads(row[0]);self._history(db,a)
                self._append(db,a,'recover',dict(status='interrupted',message='上次执行在服务退出时未返回；请打开并核对平台状态，不自动重放。',providerActionExecuted=False))

    async def step(self,project,attempt_id,request):
        request=SupplierStepRequest.model_validate(request).model_dump(mode='json')
        signature=fingerprint([project,attempt_id,request]);key=request['requestId'];action=request['action']
        # Fail fast instead of building an unbounded queue of MCP operations.
        if self.lock.locked():raise DomainError('busy','另一个浏览器步骤正在执行，请等待结果。',409)
        async with self.lock:
            with self.store.tx() as db:
                replay=self._replay(db,project,key,signature)
                if replay:return replay
                a=self._owned(db,'supplier_attempts',attempt_id,project);self._history(db,a)
                if a['version']!=request['expectedAttemptVersion']:raise DomainError('version_conflict','执行任务已更新，请重新读取。',409)
                if a['status']=='running':raise DomainError('busy','此步骤尚未结束；不要重复执行。',409)
                if a.get('existingOrder') and action not in {'open','inspect','reconcile'}:
                    return self._append(db,a,action,dict(status='duplicate-blocked',message='已存在同资料订单；只读核对，未重复上传或提交。',providerActionExecuted=False),key,signature)
            # Verify real bytes again before every interaction, including after restart.
            binding,*_=self._binding(project,a['request'])
            if binding!=a['sourceBinding']:raise DomainError('input_changed','执行绑定资料变化，未操作供应商。',409)
            with self.store.tx() as db:
                a=self._owned(db,'supplier_attempts',attempt_id,project)
                self._append(db,a,action,dict(status='running',message='正在核对专用浏览器。',providerActionExecuted=False),key,signature)
            self.active+=1
            try:
                result=await self.adapter.execute(a,action)
            except asyncio.CancelledError:
                result=dict(status='interrupted',message='浏览器执行被中断；需要重新核对平台，不自动重试。',providerActionExecuted=False)
            except DomainError as e:
                result=dict(status='failed',code=e.code,message=e.message,providerActionExecuted=False)
            except Exception:
                # Never persist an exception repr: Playwright errors can contain
                # private URL queries, page excerpts, file paths or auth values.
                result=dict(status='failed',code='browser_failed',message='页面核对未完成；请检查专用浏览器后重新核对。',providerActionExecuted=False)
            finally:self.active-=1
            with self.store.tx() as db:
                a=self._owned(db,'supplier_attempts',attempt_id,project)
                snap=result.get('snapshot',{})
                if not a.get('accountFingerprint') and snap.get('accountFingerprint'):a['accountFingerprint']=snap['accountFingerprint']
                response=self._append(db,a,action+'-result',result)
                # Request replay points to the completed result, not a new action.
                db.execute('UPDATE supplier_requests SET step_id=? WHERE key=?',(response['step']['id'],key))
            return response
