"""Official price/read tasks. A successful query can append an internal API observation."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time

from .importer import digest,within
from .jlc_client import JlcClient,ENDPOINT
from .jlc_basis import quote_basis,quote_payload,quote_fields,match_order,integer_id
from ..jlc_schemas import JlcQuoteRequest,JlcSyncRequest
from ..jlc_receipt import API_EVIDENCE,VerifiedJlcObservation
from ..store import DomainError,now
from ..jobs import Cancelled


def safe_json(value):
    return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False,default=lambda x:str(x) if isinstance(x,Decimal) else (_ for _ in ()).throw(TypeError()))


class JlcAdapter:
    version='jlc-official-read-quote/0.16.1'
    read_only=True

    def __init__(self,config,store,sources,orders,vault):
        self.config,self.store,self.sources,self.orders,self.vault=config,store,sources,orders,vault
        self.client_factory=JlcClient
        root=Path(__file__).resolve().parents[3]
        names=['src/formforge/jlc_schemas.py','src/formforge/jlc_credentials.py','src/formforge/jlc_receipt.py',
               'src/formforge/adapters/jlc.py','src/formforge/adapters/jlc_client.py','src/formforge/adapters/jlc_basis.py']
        self.files={name:digest(root/name) for name in names};self.engine_hash=hashlib.sha256(safe_json(self.files).encode()).hexdigest()
        self.code_root=root

    def profile(self):
        return dict(**self.vault.profile(),adapterVersion=self.version,endpoint=ENDPOINT,engineHash=self.engine_hash,
            quoteImplemented=True,existingOrderQueryImplemented=True,createOrderImplemented=False,
            paymentAllowed=False,productionReleaseAllowed=False,uploadsFiles=False,
            quoteScope='明确FR4裸板工艺计价；原件未上传，运费未知，不覆盖既有订单金额')

    def schema(self,project=None,manufacturing_job_id=None):
        result=dict(profile=self.profile(),quote=JlcQuoteRequest.model_json_schema(),sync=JlcSyncRequest.model_json_schema(),
            settingsHttpOnly=True,secretsInMcp=False,notes=[
                '先通过本机网页保存DPAPI加密配置，再提交真实官方任务。',
                '报价绑定原始制造任务和Gerber SHA；毫米长宽无损换算为文档要求的厘米。',
                '查单只匹配本工程既有订单，明确日期窗、类型和查询响应尺寸单位。',
                '不从我方已确认推断待付款，不把计价字段称为已支付或明确运费。'])
        if bool(project)!=bool(manufacturing_job_id):raise DomainError('invalid_input','制造基准需同时指定工程和任务。')
        if project:
            job=self.store.get('jobs',manufacturing_job_id)
            if job['projectId']!=project:raise DomainError('not_found','制造任务不属于此工程。',404)
            if job['kind']=='manufacturing.external_review':gerber_hash=job['params']['archive']['expectedSha256']
            elif job['kind']=='manufacturing.prepare':gerber_hash=self.orders._artifact(project,job,'manufacturing_gerbers')[0]['sha256']
            else:raise DomainError('invalid_input','请指定制造资料审核任务。')
            result['basis']=quote_basis(self.orders,project,dict(manufacturingJobId=job['id'],expectedGerberSha256=gerber_hash))
        return result

    def verify_engine(self):
        if any(digest(self.code_root/name)!=h for name,h in self.files.items()):
            raise DomainError('input_changed','官方接口代码在本服务启动后已变化，请在任务结束后重启服务。',409)

    def validate(self,project,params):
        self.verify_engine()
        current=self.store.get('projects',project)
        if 'choices' in params:
            JlcQuoteRequest.model_validate({**params,'requestId':'validation'})
            if current['version']!=params['expectedProjectVersion']:raise DomainError('version_conflict','工程版本已变化。',409)
            quote_basis(self.orders,project,params)
        else:
            JlcSyncRequest.model_validate({**params,'requestId':'validation'})
            record=self.store.get('orders',params['orderId'])
            if record['projectId']!=project:raise DomainError('not_found','订单不属于此工程。',404)
            if record['identity']['provider']!='jlc-pcb':raise DomainError('unavailable','当前官方查单入口仅支持 PCB；SMT 请使用独立订单观察入口。',400)
            order=self.orders.read(project,params['orderId'])['order']
            if order['version']!=params['expectedOrderVersion']:raise DomainError('version_conflict','订单版本已变化。',409)
            binding,*_=self.orders._binding(project,order['sourceBinding']['manufacturingJobId'],order['sourceBinding']['gerber']['sha256'])
            if binding!=order['sourceBinding']:raise DomainError('input_changed','原制造资料已变化。',409)
        self.vault.load()

    def _discover(self,client,order,params,phase):
        pages=1;matches=set();previous_meta=None
        for page in range(1,4):
            if page>pages:break
            phase(f'请求官方订单列表，第{page}页')
            reply=client.post('/pcb/order/getOrderPageList',dict(orderType=params['orderType'],produceCode=order['identity']['displayNumber'],
                orderDateBegin=params['orderDateBegin'],orderDateEnd=params['orderDateEnd'],orderByFlag=True,queryFilterFlag=False,pageNum=page,pageSize=20))
            data=reply.response
            if not isinstance(data.get('data'),list) or len(data['data'])>20 or data.get('pageNum')!=page or data.get('pageSize')!=20 or type(data.get('pages'))!=int or type(data.get('total'))!=int:
                raise DomainError('provider_response_invalid','官方分页结构与文档不符。')
            pages=data['pages']
            if not 0<=pages<=3 or not 0<=data['total']<=60:raise DomainError('query_range_too_large','同编号查询范围过大，请缩小日期窗口。')
            meta=(pages,data['total'])
            if previous_meta is not None and previous_meta!=meta:raise DomainError('provider_response_changed','官方列表在分页期间变化，请重新核对。')
            previous_meta=meta
            for row in data['data']:
                if not isinstance(row,dict):raise DomainError('provider_response_invalid','官方分页条目无效。')
                if row.get('produceCode')!=order['identity']['displayNumber']:continue
                if row.get('orderType')!=params['orderType']:raise DomainError('provider_identity_mismatch','列表订单类别与查询不一致。',409)
                matches.add(integer_id(row.get('customerOrderId')))
        if len(matches)!=1:raise DomainError('provider_order_not_unique','没有唯一的同编号官方订单；保留网页观察，不创建第二张订单。',409)
        return next(iter(matches))

    def execute(self,ctx):
        job=ctx.job;project=job['projectId'];params=job['params'];self.verify_engine();ctx.cancel()
        output=self.config.data/'projects'/project/'jobs'/job['id'];output.mkdir(parents=True,exist_ok=True)
        credential=self.vault.load();binding=None;order=None
        if job['kind']=='manufacturing.jlc_quote':binding=quote_basis(self.orders,project,params)
        else:order=self.orders.read(project,params['orderId'])['order']
        client=self.client_factory(credential,self.vault,cancel=ctx.cancel,deadline=time.monotonic()+45)
        try:
            if job['kind']=='manufacturing.jlc_quote':
                payload=quote_payload(params['choices']);ctx.phase('请求官方工艺计价；未上传文件')
                reply=client.post('/pcb/onlinePrice/get',payload);result=quote_fields(reply.response.get('data'))
                if quote_basis(self.orders,project,params)!=binding:raise DomainError('input_changed','计价期间原件或来源已改变。',409)
                normalized=dict(operation='quote',quote=result,sourceBinding=binding,choices=params['choices'])
            else:
                customer_id=self._discover(client,order,params,ctx.phase)
                ctx.phase('请求官方订单详情并核对编号、文件及参数')
                reply=client.post('/pcb/order/get',dict(customerOrderId=int(customer_id),orderType=params['orderType']))
                matched=match_order(reply.response.get('data'),order,params,customer_id)
                normalized=dict(operation='sync',orderId=order['id'],expectedOrderVersion=params['expectedOrderVersion'],
                    sourceBinding=order['sourceBinding'],sourceBindingSha256=order['sourceBindingSha256'],matchedOrder=matched)
            ctx.cancel();self.verify_engine();self.vault.unchanged(credential)
            receipt=dict(schemaVersion='formforge.jlc-api-receipt/1',jobId=job['id'],projectId=project,receivedAt=reply.receivedAt,
                endpoint=ENDPOINT,evidenceLevel=API_EVIDENCE,applicationFingerprint=credential.applicationFingerprint,
                configVersion=credential.version,engineHash=self.engine_hash,engineFiles=self.files,
                calls=[dict(path=r.path,request=r.request,requestSha256=r.requestSha256,responseSha256=r.responseSha256,
                            traceId=r.traceId,receivedAt=r.receivedAt) for r in client.replies],result=normalized,
                rawResponseRetained=False,providerSignatureVerified=False,providerBytesVerified=False,
                paymentExecuted=False,productionReleased=False,engineeringApproved=False)
            text=safe_json(receipt);path=output/'jlc-api-receipt.json';path.write_text(text,encoding='utf8')
            ctx.publish(path,'jlc_api_receipt','application/json')
            self.vault.verified(credential);ctx.cancel()
            if job['kind']=='manufacturing.jlc_quote':
                result.update(evidenceLevel=API_EVIDENCE,receiptSha256=digest(path),sourceBinding=binding,
                    receivedAt=reply.receivedAt,providerBytesVerified=False,engineeringApproved=False,manufacturingApproved=False,
                    paymentExecuted=False,productionReleased=False)
                if self.store.finish_readonly(job['id'],result,'官方工艺计价已返回；未下单或付款') is None:raise Cancelled()
            else:
                ctx.phase('登记本服务核对的API回执并追加订单历史')
                source=self.sources.transfer(project,dict(action='register_text',name='JLC_API_'+job['id']+'.json',text=text,
                    uri=ENDPOINT+'/pcb/order/get',version=reply.receivedAt,origin='project-authored',
                    license='Private service-normalized provider receipt; preserve provider rights',
                    note='Service received HTTPS API success and matched this existing order. Only selected fields and raw response SHA retained; no provider signature or file bytes verification.',
                    requestId='jlc-api-receipt-'+job['id']))['source']
                status=matched['orderStatus'];labels={0:'已取消',1:'等待嘉立创审核',2:'我方已确认',3:'客户已支付',4:'厂方生产',5:'已发货'}
                observation=dict(evidenceLevel='agent-observed-browser-ui',observedAt=reply.receivedAt,
                    reviewStatus='cancelled' if status==0 else 'under-review' if status==1 else 'approved',providerStatusLabel=labels[status],
                    paymentStatus='paid' if status==3 else 'unknown',paymentStatusLabel='已支付' if status==3 else '本次API未明确支付状态',
                    productionStatus='in-production' if status==4 else 'shipped' if status==5 else 'unknown',
                    productionStatusLabel=labels[status] if status in {4,5} else '本次API未明确生产状态',awaitingPaymentObserved=False,
                    manualOrderConfirmation=None,manualProductionProof=None,platformPreviewObserved=False,fileNameObserved=matched['fileName'],prices=None,
                    evidence=[dict(sourceId=source['id'],expectedSha256=source['sha256'],expectedProvenanceSha256=source['provenanceSha256'])],
                    note='本服务通过官方HTTPS接口核对既有订单、文件名与参数后记录；接口没有明确的支付/手动确认字段时保持未知。未执行付款、创建订单或生产放行。')
                ctx.cancel();self.vault.unchanged(credential)
                proof=VerifiedJlcObservation(job['id'],order['sourceBindingSha256'],
                    {**matched, 'applicationFingerprint':credential.applicationFingerprint},observation,reply.responseSha256)
                if self.orders.finish_api_job(proof) is None:raise Cancelled()
        except DomainError as error:
            if client.attempts:
                diagnostic=dict(schemaVersion='formforge.jlc-api-diagnostic/1',jobId=job['id'],projectId=project,
                    endpoint=ENDPOINT,engineHash=self.engine_hash,engineFiles=self.files,errorCode=error.code,
                    operationSucceeded=False,sourceBinding=binding if binding is not None else order['sourceBinding'],
                    attempts=client.attempts,rawRequestRetained=False,rawResponseRetained=False,
                    orderObservationAppended=False,paymentExecuted=False,productionReleased=False)
                path=output/'jlc-api-diagnostic.json';path.write_text(safe_json(diagnostic),encoding='utf8')
                ctx.publish(path,'jlc_api_diagnostic','application/json')
            raise
        finally:client.close()

    def read(self,project,job_id=None):
        current=self.store.get('projects',project)
        jobs=[j for j in self.store.list('jobs',project) if j['kind'] in {'manufacturing.jlc_quote','manufacturing.jlc_sync'}]
        job=self.store.get('jobs',job_id) if job_id else max(jobs,key=lambda j:(j['createdAt'],j['id'])) if jobs else None
        if job and (job['projectId']!=project or job['kind'] not in {'manufacturing.jlc_quote','manufacturing.jlc_sync'}):raise DomainError('not_found','没有本工程的此官方接口任务。',404)
        artifacts=[a for a in self.store.list('artifacts',project) if job and a['jobId']==job['id']]
        diagnostic=None
        for artifact in artifacts:
            if artifact['kind']!='jlc_api_diagnostic':continue
            path=within(self.config.data,artifact['relativePath'])
            if not path.is_file() or path.stat().st_size>64*1024 or digest(path)!=artifact['sha256']:
                raise DomainError('input_changed','本次官方接口诊断产物已变化，无法读取。',409)
            diagnostic=json.loads(path.read_text('utf8'))
        return dict(profile=self.profile(),job=job,jobs=jobs,result=job.get('result') if job else None,artifacts=artifacts,diagnostic=diagnostic,
            requiresContextReview=bool(job and any(current[k]!=job['contextSnapshot'][k] for k in ['specification','preferences'])))
