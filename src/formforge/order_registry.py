"""Independent, append-only order observations; no provider executor or payment code."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

from .adapters.importer import digest, within
from .order_schemas import AdoptExistingOrder, AppendOrderObservation, AdoptExistingSmtOrder, AppendSmtOrderObservation
from .store import DomainError, encode, now, uid
from .jlc_receipt import API_EVIDENCE, VerifiedJlcObservation
from .order_schemas import OrderObservation


OFFICIAL_ROUTE = 'https://www.jlc.com/newOrder/#/pcb/pcbOrderList'
SMT_ROUTE = 'https://member.jlc.com/center/smt/home/order/smt_list/'


def fingerprint(value):
    return hashlib.sha256(encode(value).encode('utf8')).hexdigest()


def fail(message, code='invalid_input', status=400):
    raise DomainError(code, message, status)


class OrderRegistry:
    def __init__(self, config, store, sources):
        self.config, self.store, self.sources = config, store, sources
        self.api_profile=None

    def profile(self):
        api=self.api_profile() if self.api_profile else {}
        return dict(available=True, mode='adopt-existing-and-append-observations',
                    providers=['jlc-pcb', 'jlc-smt'], evidenceLevels=['agent-observed-browser-ui',API_EVIDENCE],
                    providerExecutorAvailable=False, officialApiConnected=api.get('authenticated',False),
                    officialReadClientConfigured=api.get('configured',False),
                    paymentAllowed=False, productionReleaseAllowed=False,
                    supplierAuthenticated=False, providerBytesVerified=False,
                    engineeringApprovalInferred=False, projectVersionChanged=False,
                    providerCapabilities={'jlc-pcb': dict(officialReadConfigured=api.get('configured',False)),
                                          'jlc-smt': dict(browserObservations=True, officialReadConfigured=False)})

    def schema(self):
        return dict(schemaVersion='formforge.order-observation/1', capabilities=self.profile(),
                    adopt=AdoptExistingOrder.model_json_schema(), observe=AppendOrderObservation.model_json_schema(),
                    officialRoute=OFFICIAL_ROUTE, notes=[
                        '只关联已经存在的订单；不会上传、报价、提交或付款。',
                        '首次使用expectedProjectVersion；追加观察使用expectedOrderVersion，不阻断旧订单跟踪。',
                        '支持已完成且文件一致的原生制造任务或独立外部制造审核；固定原始Gerber字节。',
                        '截图/文本/JSON先通过source_transfer登记；来源和字节摘要不认证观察叙述。',
                        '未支付不等于待付款；只有明确审核通过、未支付及待付款观察才显示该阶段。',
                        '已支付/生产等意外事实照实记录并提示停止后续动作；不提升工程或生产资格。'])

    def smt_schema(self):
        return dict(schemaVersion='formforge.smt-order-observation/1', capabilities=self.profile(),
                    adopt=AdoptExistingSmtOrder.model_json_schema(), observe=AppendSmtOrderObservation.model_json_schema(),
                    officialRoute=SMT_ROUTE, notes=[
                        '只登记已存在的 SMT 订单；共用 order_read 和追加历史，不提交、付款或放行生产。',
                        '身份使用平台实际显示的客户编码和 SMT 单号，不伪造 opaque ID。PCB 关联单号是浏览器观察声明。',
                        '固定同工程原生制造任务、已采纳采购版本及原始 Gerber/BOM/CPL 三份摘要；保留全部选定器件及插件。',
                        '匹配和勾选位号需属于绑定文件；complete 必须覆盖全部位号；不等于方向、DFM 或生产批准。',
                        'reviewStatus 只表示订单审核，DFM 和方向状态独立；工程师方向服务表示所选服务，非校验完成。',
                        '登记使用工程版本，追加使用独立订单版本；相同请求可重放，冲突不得覆盖旧观察。',
                        '当前官方 PCB 查单入口不能查询 SMT；本契约证据为登记的浏览器观察。'])

    def _smt_binding(self, project, request):
        from .smt_order_binding import bind
        return bind(self, project, request)

    def _smt_unchanged(self, db, jobs, artifacts, sources, revision):
        for job in jobs:
            if self.store._get(db, 'jobs', job['id']) != job:
                fail('记录前制造或采购任务已变化。', 'input_changed', 409)
        if self.store._get(db, 'revisions', revision['id']) != revision:
            fail('记录前采购版本已变化。', 'input_changed', 409)
        self._artifacts_unchanged(db, artifacts)
        self._unchanged(db, sources)

    def adopt_smt(self, project, request):
        from .smt_order_binding import validate_matching
        request = AdoptExistingSmtOrder.model_validate(request).model_dump(mode='json')
        signature = fingerprint([project, 'adopt-existing-smt', request])
        with self.store.tx() as db:
            replay = self._replay(db, request['requestId'], signature, project)
        if replay:
            return replay
        binding, sources, jobs, artifacts, revision = self._smt_binding(project, request)
        validate_matching(binding, request['observation'])
        evidence = self._evidence(project, request['observation'])
        with self.store.tx() as db:
            replay = self._replay(db, request['requestId'], signature, project)
            if replay:
                return replay
            current = self.store._get(db, 'projects', project)
            if current['version'] != request['expectedProjectVersion']:
                fail('工程版本已变化，请重新核对 SMT 绑定资料。', 'version_conflict', 409)
            self._smt_unchanged(db, jobs, artifacts, sources + evidence, revision)
            identity = request['identity']
            unique = ':'.join([identity['provider'], identity['customerCode'], identity['displayNumber']])
            existing = db.execute('SELECT order_id FROM order_identities WHERE key=?', (unique,)).fetchone()
            if existing:
                order = self._owned(db, 'orders', existing['order_id'], project)
                history = self._verify_chain(order, db)
                if (order['sourceBinding'] != binding or order['parameters'] != request['parameters']
                        or order['pcbOrderNumber'] != request['pcbOrderNumber'] or history[0]['observation'] != request['observation']):
                    fail('该 SMT 订单已有不同记录；请读取既有订单并追加观察。', 'order_exists', 409)
                db.execute('INSERT INTO order_requests VALUES(?,?,?,?)', (request['requestId'], signature, order['id'], history[0]['id']))
                return dict(order=order, observation=history[0], replayed=True, alreadyAssociated=True, providerActionExecuted=False)
            order = dict(id=uid('o'), projectId=project, identity=identity, pcbOrderNumber=request['pcbOrderNumber'],
                         parameters=request['parameters'], sourceBinding=binding, sourceBindingSha256=fingerprint(binding),
                         createdAt=now(), adoptedProjectVersion=current['version'], version=0,
                         target='workflow-trial-stop-before-payment', officialRoute=SMT_ROUTE)
            observation = self._append(db, order, request['observation'], evidence)
            db.execute('INSERT INTO order_identities VALUES(?,?)', (unique, order['id']))
            db.execute('INSERT INTO order_requests VALUES(?,?,?,?)', (request['requestId'], signature, order['id'], observation['id']))
            self.store._event(db, 'order.adopted', project, dict(orderId=order['id'], displayNumber=identity['displayNumber']))
        return dict(order=order, observation=observation, replayed=False, providerActionExecuted=False)

    def observe_smt(self, project, order_id, request):
        from .smt_order_binding import validate_matching
        request = AppendSmtOrderObservation.model_validate(request).model_dump(mode='json')
        signature = fingerprint([project, order_id, 'observe-smt', request])
        with self.store.tx() as db:
            replay = self._replay(db, request['requestId'], signature, project)
            order = self._owned(db, 'orders', order_id, project)
            if not replay:
                self._verify_chain(order, db)
        if replay:
            return replay
        if order['identity'] != request['identity']:
            fail('SMT 订单身份与原记录不一致。', 'version_conflict', 409)
        old = order['sourceBinding']
        binding, sources, jobs, artifacts, revision = self._smt_binding(project, dict(
            manufacturingJobId=old['manufacturingJobId'], expectedGerberSha256=old['gerber']['sha256'],
            procurementRevisionId=old['procurement']['revisionId'], expectedBomSha256=old['procurement']['bom']['sha256'],
            expectedCplSha256=old['procurement']['cpl']['sha256'], parameters=order['parameters']))
        if binding != old or fingerprint(binding) != order['sourceBindingSha256']:
            fail('SMT 订单绑定资料已改变。', 'input_changed', 409)
        validate_matching(binding, request['observation'])
        evidence = self._evidence(project, request['observation'])
        with self.store.tx() as db:
            replay = self._replay(db, request['requestId'], signature, project)
            if replay:
                return replay
            order = self._owned(db, 'orders', order_id, project)
            if order['version'] != request['expectedOrderVersion']:
                fail('订单已追加新观察，请重新读取订单版本。', 'version_conflict', 409)
            previous = self._owned(db, 'order_observations', order['latestObservationId'], project)
            if datetime.fromisoformat(request['observation']['observedAt'].replace('Z', '+00:00')) < datetime.fromisoformat(previous['observation']['observedAt'].replace('Z', '+00:00')):
                fail('新增观察时间不得早于当前观察。', 'version_conflict', 409)
            self._smt_unchanged(db, jobs, artifacts, sources + evidence, revision)
            observation = self._append(db, order, request['observation'], evidence)
            db.execute('INSERT INTO order_requests VALUES(?,?,?,?)', (request['requestId'], signature, order['id'], observation['id']))
        return dict(order=order, observation=observation, replayed=False, providerActionExecuted=False)

    def _source(self, project, ref):
        row, _ = self.sources.get_record(project, ref['sourceId'])
        if row['sha256'] != ref['expectedSha256'] or row['provenanceSha256'] != ref['expectedProvenanceSha256']:
            fail('订单资料的字节或来源摘要不符。', 'input_changed', 409)
        return row

    def _artifact(self, project, job, kind):
        artifacts = [a for a in self.store.list('artifacts', project) if a['jobId'] == job['id'] and a['kind'] == kind]
        if len(artifacts) != 1 or artifacts[0].get('revisionId') != job.get('inputRevision'):
            fail('制造产物身份不唯一或版本不匹配。', 'input_changed', 409)
        row = artifacts[0]
        path = within(self.config.data, row['relativePath'])
        if not path.is_file() or path.stat().st_size != row['size'] or digest(path) != row['sha256']:
            fail('制造产物字节已改变。', 'input_changed', 409)
        return row, path

    def _binding(self, project, job_id, gerber_hash, parameters=None):
        job = self.store.get('jobs', job_id)
        if job['projectId'] != project:
            fail('制造任务不属于此工程。', 'not_found', 404)
        kind = job['kind']
        if kind not in {'manufacturing.external_review', 'manufacturing.prepare'} or job['status'] != 'completed':
            fail('需要已完成的制造文件审核任务。')
        if not (job.get('result') or {}).get('fileConsistencyPassed'):
            fail('制造资料一致性尚未通过。')
        sources = []
        artifacts = []
        if kind == 'manufacturing.external_review':
            report, path = self._artifact(project, job, 'external_cam_report')
            archive_ref = job['params']['archive']
            archive = self._source(project, archive_ref)
            if not archive['name'].lower().endswith('.zip') or archive['sha256'] != gerber_hash:
                fail('原始Gerber ZIP摘要不匹配。', 'input_changed', 409)
            sources.append(archive)
            gerber = dict(source=archive_ref, sha256=archive['sha256'], name=archive['name'])
            body = json.loads(path.read_text('utf8'))
            if body.get('jobId') != job_id or body.get('projectId') != project or body.get('contextSnapshot') != job['contextSnapshot']:
                fail('制造报告与任务上下文不符。', 'input_changed', 409)
            # Freeze every original report source, not just the Gerber ZIP.
            for source in body['sources']:
                ref = dict(sourceId=source['id'], expectedSha256=source['sha256'], expectedProvenanceSha256=source['provenanceSha256'])
                row = self._source(project, ref)
                if not any(s['id'] == row['id'] for s in sources):
                    sources.append(row)
            if parameters:
                outline = body['outline']
                if parameters['copperLayers'] != job['params']['copperLayerCount'] or any(
                    abs(parameters[k] - outline[k]) > .01 for k in ['widthMm', 'heightMm']):
                    fail('订单声明的层数或外形尺寸与制造审核不一致。')
        else:
            report, path = self._artifact(project, job, 'manufacturing_report')
            gerber_row, _ = self._artifact(project, job, 'manufacturing_gerbers')
            if gerber_row['sha256'] != gerber_hash:
                fail('原生Gerber ZIP摘要不匹配。', 'input_changed', 409)
            gerber = dict(artifactId=gerber_row['id'], sha256=gerber_hash, name=gerber_row['name'])
            artifacts.append(gerber_row)
            body = json.loads(path.read_text('utf8'))
            if body.get('jobId') != job_id or body.get('projectId') != project or body.get('revisionId') != job.get('inputRevision') or body.get('fabricationArchive', {}).get('sha256') != gerber_hash:
                fail('原生制造报告与任务或Gerber字节不符。', 'input_changed', 409)
        if body.get('fileConsistencyPassed') is not True:
            fail('制造原始报告未通过资料一致性。', 'input_changed', 409)
        if parameters:
            quantity=job['params'].get('boardQuantity')
            thickness=job['params'].get('fabrication',{}).get('thicknessMm')
            if quantity is not None and parameters['boardQuantity'] != quantity or thickness is not None and abs(parameters['thicknessMm']-thickness)>1e-6:
                fail('订单数量或明确板厚与制造任务约定不一致。')
        artifacts.append(report)
        return dict(manufacturingJobId=job_id, manufacturingKind=kind, inputRevision=job.get('inputRevision'),
                    contextSnapshot=job['contextSnapshot'], jobFingerprint=fingerprint(job),
                    gerber=gerber, reportArtifactId=report['id'], reportSha256=report['sha256'],
                    parameterVerification='external-cam-layer-count-and-outline' if kind == 'manufacturing.external_review' else 'explicit-manufacturing-choices-only',
                    sourceRefs=[dict(sourceId=s['id'], expectedSha256=s['sha256'], expectedProvenanceSha256=s['provenanceSha256']) for s in sources]), sources, job, artifacts

    def _evidence(self, project, observation):
        observed = datetime.fromisoformat(observation['observedAt'].replace('Z', '+00:00'))
        if observed > datetime.now(timezone.utc) + timedelta(minutes=5):
            fail('不能记录未来的平台观察。')
        return [self._source(project, ref) for ref in observation['evidence']]

    def _owned(self, db, table, id, project):
        row = self.store._get(db, table, id)
        if row['projectId'] != project:
            fail('该订单或观察不属于此工程。', 'not_found', 404)
        return row

    def _replay(self, db, request_id, signature, project):
        row = db.execute('SELECT fingerprint, order_id, observation_id FROM order_requests WHERE key=?', (request_id,)).fetchone()
        if not row:
            return None
        if row['fingerprint'] != signature:
            fail('这个请求编号已用于另一项订单操作。', 'version_conflict', 409)
        return dict(order=self._owned(db, 'orders', row['order_id'], project),
                    observation=self._owned(db, 'order_observations', row['observation_id'], project), replayed=True,
                    providerActionExecuted=False)

    def _unchanged(self, db, sources):
        for row in sources:
            if self.store._get(db, 'sources', row['id']) != row:
                fail('订单记录前资料登记已变化。', 'input_changed', 409)

    def _artifacts_unchanged(self, db, artifacts):
        for row in artifacts:
            if self.store._get(db, 'artifacts', row['id']) != row:
                fail('记录前制造产物登记已变化。', 'input_changed', 409)

    def _append(self, db, order, observation, evidence):
        version = order['version'] + 1
        previous = order.get('latestObservationSha256')
        row = dict(id=uid('ob'), projectId=order['projectId'], orderId=order['id'], orderVersion=version,
                   recordedAt=now(), previousSha256=previous, observation=observation,
                   evidence=[self.sources.metadata(s) for s in evidence])
        row['sha256'] = fingerprint(row)
        db.execute('INSERT INTO order_observations(id,project_id,data) VALUES(?,?,?)', (row['id'], order['projectId'], encode(row)))
        order.update(version=version, latestObservationId=row['id'], latestObservationSha256=row['sha256'], updatedAt=row['recordedAt'])
        self.store._put(db, 'orders', order)
        self.store._event(db, 'order.observed', order['projectId'], dict(orderId=order['id'], orderVersion=version,
            displayNumber=order['identity']['displayNumber'], observationId=row['id'], evidenceLevel=observation['evidenceLevel']))
        return row

    def adopt(self, project, request):
        request = AdoptExistingOrder.model_validate(request).model_dump(mode='json')
        signature = fingerprint([project, 'adopt-existing', request])
        with self.store.tx() as db:
            replay = self._replay(db, request['requestId'], signature, project)
        if replay:
            return replay
        binding, sources, job, artifacts = self._binding(project, request['manufacturingJobId'], request['expectedGerberSha256'], request['parameters'])
        evidence = self._evidence(project, request['observation'])
        with self.store.tx() as db:
            replay = self._replay(db, request['requestId'], signature, project)
            if replay:
                return replay
            current = self.store._get(db, 'projects', project)
            if current['version'] != request['expectedProjectVersion']:
                fail('工程版本已变化，请重新核对要关联的制造资料。', 'version_conflict', 409)
            if self.store._get(db, 'jobs', job['id']) != job:
                fail('关联前制造任务或报告已变化。', 'input_changed', 409)
            self._artifacts_unchanged(db, artifacts)
            self._unchanged(db, sources + evidence)
            identity = request['identity']
            unique = identity['provider'] + ':' + identity['recordId']
            if db.execute('SELECT order_id FROM order_identities WHERE key=?', (unique,)).fetchone():
                fail('该平台订单已经关联，读取既有记录或追加观察；不要重复关联。', 'order_exists', 409)
            order = dict(id=uid('o'), projectId=project, identity=identity, parameters=request['parameters'],
                         sourceBinding=binding, sourceBindingSha256=fingerprint(binding),
                         createdAt=now(), adoptedProjectVersion=current['version'], version=0,
                         target='workflow-trial-stop-before-payment', officialRoute=OFFICIAL_ROUTE)
            observation = self._append(db, order, request['observation'], evidence)
            db.execute('INSERT INTO order_identities(key,order_id) VALUES(?,?)', (unique, order['id']))
            db.execute('INSERT INTO order_requests VALUES(?,?,?,?)', (request['requestId'], signature, order['id'], observation['id']))
            self.store._event(db, 'order.adopted', project, dict(orderId=order['id'], displayNumber=identity['displayNumber']))
        return dict(order=order, observation=observation, replayed=False, providerActionExecuted=False)

    def observe(self, project, order_id, request):
        request = AppendOrderObservation.model_validate(request).model_dump(mode='json')
        signature = fingerprint([project, order_id, 'observe', request])
        with self.store.tx() as db:
            replay = self._replay(db, request['requestId'], signature, project)
            order = self._owned(db, 'orders', order_id, project)
            if not replay:
                self._verify_chain(order, db)
        if replay:
            return replay
        if order['identity'] != request['identity']:
            fail('平台订单身份与原记录不一致。', 'version_conflict', 409)
        binding, sources, job, artifacts = self._binding(project, order['sourceBinding']['manufacturingJobId'], order['sourceBinding']['gerber']['sha256'])
        if binding != order['sourceBinding'] or fingerprint(binding) != order['sourceBindingSha256']:
            fail('订单绑定的制造资料已改变。', 'input_changed', 409)
        evidence = self._evidence(project, request['observation'])
        with self.store.tx() as db:
            replay = self._replay(db, request['requestId'], signature, project)
            if replay:
                return replay
            order = self._owned(db, 'orders', order_id, project)
            if order['version'] != request['expectedOrderVersion']:
                fail('订单已追加新观察，请重新读取订单版本。', 'version_conflict', 409)
            previous = self._owned(db, 'order_observations', order['latestObservationId'], project)
            if datetime.fromisoformat(request['observation']['observedAt'].replace('Z', '+00:00')) < datetime.fromisoformat(previous['observation']['observedAt'].replace('Z', '+00:00')):
                fail('新增观察时间不得早于当前观察。', 'version_conflict', 409)
            if self.store._get(db, 'jobs', job['id']) != job:
                fail('记录前制造任务或报告已变化。', 'input_changed', 409)
            self._artifacts_unchanged(db, artifacts)
            self._unchanged(db, sources + evidence)
            observation = self._append(db, order, request['observation'], evidence)
            db.execute('INSERT INTO order_requests VALUES(?,?,?,?)', (request['requestId'], signature, order['id'], observation['id']))
        return dict(order=order, observation=observation, replayed=False, providerActionExecuted=False)

    def _verify_chain(self, order, db):
        rows=db.execute("SELECT data FROM order_observations WHERE project_id=? AND json_extract(data,'$.orderId')=? "
                        "ORDER BY json_extract(data,'$.orderVersion')",(order['projectId'],order['id']))
        history = [json.loads(row[0]) for row in rows]
        previous = None
        for version, row in enumerate(history, 1):
            if row['orderVersion'] != version or row['previousSha256'] != previous or row['sha256'] != fingerprint({k:v for k,v in row.items() if k != 'sha256'}):
                fail('订单观察历史摘要或顺序不一致。', 'input_changed', 409)
            previous = row['sha256']
        if not history or order['version'] != len(history) or order.get('latestObservationId') != history[-1]['id'] or order.get('latestObservationSha256') != previous or order['sourceBindingSha256'] != fingerprint(order['sourceBinding']):
            fail('订单版本与观察历史不一致。', 'input_changed', 409)
        return history

    def finish_api_job(self,proof):
        if not isinstance(proof,VerifiedJlcObservation):fail('官方观察仅接受内部已核对回执。')
        job=self.store.get('jobs',proof.jobId)
        if job['kind']!='manufacturing.jlc_sync':fail('该任务不是官方订单查询。')
        project=job['projectId'];params=job['params']
        order=self.read(project,params['orderId'])['order']
        if order['identity']['provider'] != 'jlc-pcb':fail('当前官方查询回执仅支持 PCB 订单。')
        if proof.sourceBindingSha256!=order['sourceBindingSha256']:fail('官方观察的资料绑定不符。','input_changed',409)
        binding,sources,basis_job,artifacts=self._binding(project,order['sourceBinding']['manufacturingJobId'],order['sourceBinding']['gerber']['sha256'])
        if binding!=order['sourceBinding']:fail('官方查询期间制造资料已变化。','input_changed',409)
        # Reuse common fact validation while keeping the API origin unrepresentable by public input.
        observation=OrderObservation.model_validate({**proof.observation,'evidenceLevel':'agent-observed-browser-ui'}).model_dump(mode='json')
        observation['evidenceLevel']=API_EVIDENCE
        evidence=self._evidence(project,observation)
        with self.store.tx() as db:
            job=self._owned(db,'jobs',proof.jobId,project)
            if job['status']=='completed':return job
            if job['status'] not in {'queued','running'} or job['cancelRequested']:return None
            current=self._owned(db,'orders',params['orderId'],project);self._verify_chain(current,db)
            if current['version']!=params['expectedOrderVersion']:fail('订单已被新观察更新，本次API回执没有覆盖它。','version_conflict',409)
            previous=self._owned(db,'order_observations',current['latestObservationId'],project)
            if datetime.fromisoformat(observation['observedAt'].replace('Z','+00:00'))<datetime.fromisoformat(previous['observation']['observedAt'].replace('Z','+00:00')):
                fail('API回执时刻早于当前观察。','version_conflict',409)
            if self.store._get(db,'jobs',basis_job['id'])!=basis_job:fail('API查询期间制造任务已变化。','input_changed',409)
            self._unchanged(db,sources+evidence);self._artifacts_unchanged(db,artifacts)
            api=proof.apiIdentity
            if api['produceCode']!=current['identity']['displayNumber']:fail('官方生产编号不符。','version_conflict',409)
            key='jlc-pcb:'+api['orderType']+':'+api['customerOrderId']
            existing=db.execute('SELECT order_id FROM order_api_identities WHERE key=?',(key,)).fetchone()
            if existing and existing['order_id']!=current['id']:fail('同一官方数值订单已经关联到其他记录。','order_exists',409)
            if not existing:db.execute('INSERT INTO order_api_identities VALUES(?,?)',(key,current['id']))
            old=current.get('apiIdentity')
            if old and any(old[k]!=api[k] for k in ['customerOrderId','produceOrderId','orderType']):fail('已有官方身份不一致。','version_conflict',409)
            current['apiIdentity']=api
            row=self._append(db,current,observation,evidence)
            result=dict(orderId=current['id'],orderVersion=current['version'],observationId=row['id'],
                evidenceLevel=API_EVIDENCE,observationSha256=row['sha256'],responseSha256=proof.responseSha256,
                providerBytesVerified=False,paymentExecuted=False,productionReleased=False,engineeringApproved=False)
            job.update(status='completed',phase='官方订单状态已核对并追加；未付款或生产放行',result=result,finishedAt=now())
            self.store._put(db,'jobs',job)
            self.store._event(db,'job.finished',project,dict(status='completed',phase=job['phase']),job['id'])
            return job

    def _summary(self, order, current, history):
        obs = history[-1]['observation']
        unexpected = any(row['observation']['paymentStatus'] == 'paid' or row['observation']['productionStatus'] in {'in-production', 'shipped'} for row in history)
        stage = 'unexpected-payment-or-production' if unexpected else 'awaiting-payment' if obs['awaitingPaymentObserved'] else obs['reviewStatus']
        binding = order['sourceBinding']
        stale = any(current[k] != binding['contextSnapshot'][k] for k in ['specification', 'preferences']) or (
            binding['inputRevision'] is not None and current.get('headRevisionId') != binding['inputRevision'])
        if order['identity']['provider'] == 'jlc-smt':
            stale = stale or current.get('moduleHeads', {}).get('manufacturing') != binding['procurement']['revisionId']
        return dict(**order, latestObservation=obs, stage=stage, sourceRequiresReview=stale,
                    projectVersionAdvanced=current['version'] != order['adoptedProjectVersion'],
                    supplierAuthenticated=obs['evidenceLevel']==API_EVIDENCE, providerBytesVerified=False, observationClaimsVerified=obs['evidenceLevel']==API_EVIDENCE,
                    lastManualConfirmation={k:next((dict(value=r['observation'][k],observedAt=r['observation']['observedAt'],evidenceLevel=r['observation']['evidenceLevel']) for r in reversed(history) if r['observation'].get(k) is not None),None) for k in ['manualOrderConfirmation','manualProductionProof']},
                    lastPriceObservation=next((dict(prices=r['observation']['prices'],observedAt=r['observation']['observedAt'],evidenceLevel=r['observation']['evidenceLevel']) for r in reversed(history) if r['observation'].get('prices') is not None),None),
                    lastMatchingObservation=next((dict(status=r['observation']['bomMatchingStatus'],matchedReferences=r['observation']['matchedReferences'],checkedReferences=r['observation']['checkedReferences'],observedAt=r['observation']['observedAt']) for r in reversed(history) if r['observation'].get('bomMatchingStatus','unknown')!='unknown'),None),
                    engineeringApproved=False, productionApproved=False, paymentAllowed=False, productionReleaseAllowed=False,
                    actionHalted=unexpected, providerActionExecuted=False)

    def read(self, project, order_id=None, offset=0, limit=40, latest=False):
        if not 0 <= offset <= 100000 or not 1 <= limit <= 100:
            fail('订单读取范围须为offset≥0、limit=1..100。')
        if latest and (not order_id or offset != 0):
            fail('latest 仅用于单笔订单的最新观察，不能同时指定非零 offset。')
        # A single database snapshot prevents concurrent append from looking like history corruption.
        with self.store.read_tx() as db:
            current = self.store._get(db,'projects', project)
            if order_id:
                order = self._owned(db,'orders',order_id,project)
                history = self._verify_chain(order,db)
                if latest:
                    offset = max(0, len(history)-limit)
                return dict(capabilities=self.profile(), order=self._summary(order, current, history),
                            observations=history[offset:offset+limit], total=len(history), offset=offset, limit=limit)
            rows=db.execute('SELECT data FROM orders WHERE project_id=?',(project,))
            orders = sorted([json.loads(row[0]) for row in rows], key=lambda o:(o['createdAt'], o['id']), reverse=True)
            return dict(capabilities=self.profile(), orders=[self._summary(o, current, self._verify_chain(o,db)) for o in orders[offset:offset+limit]],
                        total=len(orders), offset=offset, limit=limit)
