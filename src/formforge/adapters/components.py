"""Independent supply observations; no CAD writes, orders or stock reservation."""
import hashlib
import json
from pathlib import Path

from .importer import digest, within
from .jlc_client import JlcClient, ENDPOINT, SMT_COMPONENT_PATH
from ..component_schemas import ComponentQuery
from ..jobs import Cancelled
from ..store import DomainError


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def component_rows(reply, codes):
    data = reply.response.get('data')
    if not isinstance(data, list) or len(data) > len(codes):
        raise DomainError('provider_response_invalid', '器件查询没有返回有效的逐项列表。')
    found = {}
    for item in data:
        if not isinstance(item, dict):
            raise DomainError('provider_response_invalid', '器件查询包含无效项目。')
        code = item.get('componentCode')
        if not isinstance(code, str) or code not in codes or code in found:
            raise DomainError('provider_response_invalid', '响应器件编号重复或不属于本次请求。')
        quantity = item.get('stockNum')
        if quantity is not None and (type(quantity) != int or not 0 <= quantity <= 9007199254740991):
            raise DomainError('provider_response_invalid', '响应库存数量不是有效的非负整数。')
        fields = {}
        for key in ['componentModel', 'componentName', 'componentBrand', 'componentType', 'componentSpecification', 'paramTextAll']:
            value = item.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 12000):
                raise DomainError('provider_response_invalid', '响应器件身份或参数格式无效。')
            fields[key] = value
        found[code] = dict(componentCode=code, **fields, stockQuantity=quantity,
            stockStatus='observed' if quantity is not None else 'unknown', libraryTier='unknown',
            inventoryScope='jlc-smt-api-reported', reserved=False)
    return [found.get(code, dict(componentCode=code, componentModel=None, componentName=None,
        componentBrand=None, componentType=None, componentSpecification=None, paramTextAll=None,
        stockQuantity=None, stockStatus='not-returned', libraryTier='unknown',
        inventoryScope='jlc-smt-api-reported', reserved=False)) for code in codes]


class ComponentAdapter:
    version = 'component-supply/0.18.0'
    read_only = True

    def __init__(self, config, store, vault):
        self.config, self.store, self.vault = config, store, vault
        self.client_factory = JlcClient
        self.code_root = Path(__file__).resolve().parents[3]
        names = ['src/formforge/component_schemas.py', 'src/formforge/adapters/components.py',
                 'src/formforge/adapters/jlc_client.py', 'src/formforge/jlc_credentials.py']
        self.files = {name: digest(self.code_root/name) for name in names}
        self.engine_hash = hashlib.sha256(encoded(self.files).encode('utf8')).hexdigest()

    def profile(self):
        return dict(adapterVersion=self.version, engineHash=self.engine_hash, provider='jlc-smt',
            endpoint=ENDPOINT, path=SMT_COMPONENT_PATH, configured=self.vault.profile()['configured'],
            exactCodeQuery=True, maxCodes=50, credentialsFrom='existing-windows-dpapi',
            inventoryScope='jlc-smt-api-reported', libraryTierAvailable=False, stockReservation=False,
            cadImportImplemented=False, keywordSearchImplemented=False, automaticOrdering=False,
            needsMroBusiness=False, observationOnly=True)

    def schema(self):
        return dict(profile=self.profile(), query=ComponentQuery.model_json_schema(),
            modelPolicy=dict(preference=['frozen-project-model','verified-local-kicad-model','exact-manufacturer-model','external-cad'],
                localReusePreferred=True, genericPassiveModelsAllowed=True, packageUnitSystemRequired=True,
                dimensionsAndHeightRequiredForMechanicalUse=True, exactCriticalInterfaceGeometryRequired=True,
                automaticModelMappingImplemented=False), notes=[
            '使用已开通的SMT查询接口，独立于元器件&MRO审批；不创建或扩大密钥权限。',
            '精确C编号查询；库存只代表返回时刻的SMT接口报告值，不保证、预留或跨渠道合并库存。',
            '基础/扩展库分类、价格币种、最小采购量及损耗规则未由本接口核定，保持未知。',
            '每次查询形成新快照；不会修改CAD或订单。符号、封装和模型导入仍另行验证。'])

    def validate(self, project, params):
        ComponentQuery.model_validate({**params, 'requestId': 'validation'})
        self.verify_engine()
        current = self.store.get('projects', project)
        if current['version'] != params['expectedProjectVersion']:
            raise DomainError('version_conflict', '工程上下文已变化，请刷新后查询。', 409)
        self.vault.load()

    def verify_engine(self):
        if any(digest(self.code_root/name) != value for name, value in self.files.items()):
            raise DomainError('input_changed', '器件查询实现已变化，请在任务结束后重启服务。', 409)

    def execute(self, ctx):
        self.verify_engine()
        job = ctx.job
        output = self.config.data/'projects'/job['projectId']/'jobs'/job['id']
        output.mkdir(parents=True, exist_ok=True)
        credential = self.vault.load()
        client = self.client_factory(credential, self.vault, cancel=ctx.cancel)
        try:
            ctx.phase('正在按C编号查询嘉立创SMT器件信息')
            reply = client.post(SMT_COMPONENT_PATH, dict(componentCodeList=job['params']['componentCodes']))
            rows = component_rows(reply, job['params']['componentCodes'])
            if any(secret in encoded(rows) for secret in (credential.appId, credential.accessKey, credential.secretKey)):
                raise DomainError('provider_response_invalid', '响应包含不适合发布的内容。')
            snapshot = dict(schemaVersion='formforge.component-observation/1', observationId=job['id'],
                projectId=job['projectId'], contextVersion=job['contextSnapshot']['version'],
                adapterVersion=self.version, engineHash=self.engine_hash, engineFiles=self.files,
                requestedCodes=job['params']['componentCodes'], components=rows,
                observedAt=reply.receivedAt, source=dict(provider='jlc-smt', endpoint=ENDPOINT, path=SMT_COMPONENT_PATH,
                    requestSha256=reply.requestSha256, responseSha256=reply.responseSha256, traceId=reply.traceId,
                    originalResponseRetained=False, retainedFields='validated-identity-and-stock-only'),
                libraryTierVerified=False, pricesVerified=False, cadValidated=False,
                purchasingQualified=False, stockReserved=False)
            ctx.phase('冻结器件与库存快照，发布网页和MCP共用结果')
            ctx.cancel(); self.vault.unchanged(credential)
            path = output/'component-observation.json'
            path.write_text(encoded(snapshot), encoding='utf8')
            ctx.publish(path, 'component_observation', 'application/json')
            if self.store.finish_readonly(job['id'], snapshot, '器件查询完成；库存为本次时刻观察，未预留') is None:
                raise Cancelled()
        except DomainError as error:
            if client.attempts:
                diagnostic = dict(schemaVersion='formforge.component-diagnostic/1', jobId=job['id'],
                    errorCode=error.code, attempts=client.attempts, engineHash=self.engine_hash,
                    originalResponseRetained=False, operationSucceeded=False)
                path = output/'component-diagnostic.json'
                path.write_text(encoded(diagnostic), encoding='utf8')
                ctx.publish(path, 'component_diagnostic', 'application/json')
            raise
        finally:
            client.close()

    def read(self, project, job_id=None):
        current = self.store.get('projects', project)
        jobs = sorted([j for j in self.store.list('jobs', project) if j['kind'] == 'components.query'],
                      key=lambda j: (j['createdAt'], j['id']), reverse=True)
        job = self.store.get('jobs', job_id) if job_id else next(iter(jobs), None)
        if job and (job['projectId'] != project or job['kind'] != 'components.query'):
            raise DomainError('not_found', '此器件查询不属于本工程。', 404)
        artifacts = [a for a in self.store.list('artifacts', project) if job and a['jobId'] == job['id']]
        result = diagnostic = None
        for artifact in artifacts:
            if artifact['kind'] not in {'component_observation', 'component_diagnostic'}: continue
            path = within(self.config.data, artifact['relativePath'])
            if not path.is_file() or path.stat().st_size > 2*1024*1024 or digest(path) != artifact['sha256']:
                raise DomainError('input_changed', '器件查询快照字节已变化，无法读取。', 409)
            value = json.loads(path.read_text('utf8'))
            if artifact['kind'] == 'component_observation': result = value
            else: diagnostic = value
        return dict(profile=self.profile(), jobs=jobs, job=job, result=result, diagnostic=diagnostic,
            artifacts=artifacts, contextChanged=bool(job and current['version'] != job['contextSnapshot']['version']))
