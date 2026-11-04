from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class DomainError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Store:
    # Immutable offline flash inputs/results share project export and lifecycle.
    TABLES = {"projects", "revisions", "jobs", "artifacts", "sources", "source_uploads", "orders", "order_observations", "structural_quotes", "structural_quote_observations", "supplier_attempts", "supplier_steps", "submission_approvals"}

    def __init__(self, path: Path):
        self.TABLES = self.TABLES | {'flash_bundles', 'flash_plans'}
        self.path, self.lock = path, threading.RLock()
        with sqlite3.connect(self.path, timeout=15) as db:
            db.execute("PRAGMA journal_mode=WAL")
        with self.tx() as db:
            for table in self.TABLES:
                db.execute(f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, project_id TEXT, data TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS order_observations_by_order ON order_observations "
                       "(project_id, json_extract(data,'$.orderId'), json_extract(data,'$.orderVersion'))")
            db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, job_id TEXT, type TEXT, payload TEXT, created_at TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, fingerprint TEXT, job_id TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS source_requests (key TEXT PRIMARY KEY, fingerprint TEXT, upload_id TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS order_requests (key TEXT PRIMARY KEY, fingerprint TEXT, order_id TEXT, observation_id TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS order_identities (key TEXT PRIMARY KEY, order_id TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS order_api_identities (key TEXT PRIMARY KEY, order_id TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS structural_quote_requests (key TEXT PRIMARY KEY, fingerprint TEXT, quote_id TEXT, observation_id TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS structural_quote_identities (key TEXT PRIMARY KEY, quote_id TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS supplier_requests (key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, attempt_id TEXT NOT NULL, step_id TEXT NOT NULL)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS supplier_attempt_intent ON supplier_attempts(project_id,json_extract(data,'$.intentSha256'))")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS supplier_step_version ON supplier_steps(project_id,json_extract(data,'$.attemptId'),json_extract(data,'$.attemptVersion'))")
            for table in ('revisions','jobs','artifacts','sources'):
                db.execute(f'CREATE INDEX IF NOT EXISTS {table}_by_project ON {table}(project_id)')

    @contextmanager
    def tx(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @contextmanager
    def read_tx(self):
        """A stable WAL snapshot without a writer lock or per-read WAL setup."""
        db=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=15)
        db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN')
            yield db
        finally:
            db.close()

    def _get(self, db, table, id, *, include_deleted=False):
        assert table in self.TABLES
        row = db.execute(f"SELECT data FROM {table} WHERE id=?", (id,)).fetchone()
        if not row:
            raise DomainError("not_found", "未找到指定工程、版本、任务或文件。", 404)
        value = json.loads(row[0])
        if table == 'projects' and value.get('deletedAt') and not include_deleted:
            raise DomainError('project_deleted', '此工程已删除，可在设置的工程回收站中查看。', 410)
        return value

    def _put(self, db, table, value):
        assert table in self.TABLES
        db.execute(f"INSERT OR REPLACE INTO {table}(id, project_id, data) VALUES(?,?,?)",
                   (value["id"], value.get("projectId", value["id"]), encode(value)))

    def _event(self, db, type, project, payload, job=None):
        db.execute("INSERT INTO events(project_id,job_id,type,payload,created_at) VALUES(?,?,?,?,?)",
                   (project, job, type, encode(payload), now()))

    def get(self, table, id):
        with self.read_tx() as db:
            value = self._get(db, table, id)
            if table != 'projects' and value.get('projectId'):
                self._get(db, 'projects', value['projectId'])
            return value

    def list(self, table, project=None):
        assert table in self.TABLES
        with self.read_tx() as db:
            if project:
                self._get(db, 'projects', project)
            if project:
                rows = db.execute(f"SELECT data FROM {table} WHERE project_id=? ORDER BY rowid DESC", (project,))
            else:
                condition = " WHERE json_extract(data,'$.deletedAt') IS NULL" if table == 'projects' else ''
                rows = db.execute(f"SELECT data FROM {table}{condition} ORDER BY rowid DESC")
            return [json.loads(row[0]) for row in rows]

    def context_summaries(self, table, project):
        """Project history metadata; full selected records use the normal get."""
        assert table in {'revisions','jobs'}
        fields=('id','projectId','createdAt','origin','module','parentRevisionId','message','entrypoints','validation','createdByJobId','downstreamRequiresReview') if table=='revisions' else (
            'id','projectId','kind','status','phase','createdAt','startedAt','finishedAt','error','inputRevision','cancelRequested')
        pairs=','.join(f"'{f}',data->'$.{f}'" for f in fields)
        if table=='revisions':
            pairs+=",'manifest',json('[]'),'manifestFileCount',json_array_length(data,'$.manifest')"
            for design,keys in {'pcbDesign':('schematicRevisionId','handoffJobId','reviewJobId'), 'mechanicalDesign':('kind',), 'firmwareDesign':('profile',), 'procurementDesign':('mode',)}.items():
                values=','.join(f"'{k}',data->'$.{design}.{k}'" for k in keys)
                pairs+=f",'{design}',CASE WHEN json_type(data,'$.{design}')='object' THEN json_object({values}) ELSE NULL END"
        else:
            flags=('revisionId','noop','checksPassed','draftChecksPassed','adopted','readyForAdoption','adoptionMode','fileConsistencyPassed','referenceCheckPassed','fileChecksPassed','procurementReviewed')
            values=','.join(f"'{k}',data->'$.result.{k}'" for k in flags)
            pairs+=f",'result',CASE WHEN json_type(data,'$.result')='object' THEN json_object({values}) ELSE NULL END"
        with self.read_tx() as db:
            return [json.loads(r[0]) for r in db.execute(f"SELECT json_object({pairs},'_summary',json('true')) FROM {table} WHERE project_id=? ORDER BY rowid DESC",(project,))]

    def reference_check_status(self, project_id, revision_id, board_sha256):
        """Latest completed check on this revision or its nearest ancestor.

        Historical evidence is not rewritten or inherited as a current pass.
        This describes one selected scope, never all-board SI/PI acceptance.
        """
        with self.read_tx() as db:
            self._get(db,'projects',project_id)
            evidence={}
            for row in db.execute("""SELECT id,json_extract(data,'$.result.boardSha256'),
                json_extract(data,'$.result.referenceCheckPassed'),json_extract(data,'$.params.nets'),
                json_extract(data,'$.result.revisionId') FROM jobs WHERE project_id=?
                AND json_extract(data,'$.kind')='pcb.reference_check'
                AND json_extract(data,'$.status')='completed'
                ORDER BY json_extract(data,'$.finishedAt') DESC,rowid DESC""",(project_id,)):
                evidence.setdefault(row[4],row)
            current=revision_id;seen=set()
            while evidence and current and current not in seen:
                seen.add(current)
                revision=db.execute("SELECT project_id,json_extract(data,'$.parentRevisionId') FROM revisions WHERE id=?",(current,)).fetchone()
                if not revision or revision[0]!=project_id:break
                row=evidence.get(current)
                if row:
                    valid=current==revision_id and row[1]==board_sha256
                    return dict(status=('passed' if row[2] else 'failed') if valid else 'needs-review',
                        current=valid,referenceCheckPassed=bool(row[2]) if valid else None,
                        jobId=row[0],checkedRevisionId=current,checkedBoardSha256=row[1],
                        revisionId=revision_id,boardSha256=board_sha256,nets=json.loads(row[3] or '[]'),
                        reason=None if valid else 'revision_or_board_changed',scope='Latest completed selected-net reference check only; not full-board or SI/PI approval.')
                current=revision[1]
        return dict(status='not_checked',current=False,referenceCheckPassed=None,revisionId=revision_id,
            boardSha256=board_sha256,scope='No completed reference check on this revision or its ancestors.')

    def create_project(self, name, description="", preferences=None):
        name = name.strip()
        if not name or len(name) > 120:
            raise DomainError("invalid_input", "工程名称需要 1–120 个字符。")
        project = dict(id=uid("p"), name=name, description=description, version=1,
                       specification={"text": description}, preferences=preferences or {"text": "", "rules": []},
                       createdAt=now(), updatedAt=now())
        with self.tx() as db:
            self._put(db, "projects", project)
            self._event(db, "project.created", project["id"], {"name": name, "version": 1})
        return project

    def rename_project(self, id, expected_version, name):
        name = name.strip()
        if not name or len(name) > 120:
            raise DomainError("invalid_input", "工程名称需要 1–120 个字符。")
        with self.tx() as db:
            project = self._get(db, "projects", id)
            if project["version"] != expected_version:
                raise DomainError("version_conflict", "工程已被更新，请刷新后重新修改。", 409)
            if project["name"] == name:
                return project
            previous_name = project["name"]
            project.update(name=name, version=expected_version + 1, updatedAt=now())
            self._put(db, "projects", project)
            self._event(db, "project.renamed", id, {"previousName": previous_name, "name": name, "version": project["version"]})
            return project

    def update_project(self, id, expected_version, specification, preferences):
        with self.tx() as db:
            project = self._get(db, "projects", id)
            if project["version"] != expected_version:
                raise DomainError("version_conflict", "工程已被更新，请刷新后重新修改。", 409)
            project.update(specification=specification, preferences=preferences,
                           version=expected_version + 1, updatedAt=now())
            self._put(db, "projects", project)
            self._event(db, "project.updated", id, {"version": project["version"]})
            return project

    def replay_job(self,project_id,kind,params,request_key):
        signature=hashlib.sha256(encode([project_id,kind,params]).encode()).hexdigest()
        with self.tx() as db:
            self._get(db, 'projects', project_id)
            old=db.execute('SELECT fingerprint,job_id FROM requests WHERE key=?',(request_key,)).fetchone()
            if not old:return None
            if old['fingerprint']!=signature:raise DomainError('version_conflict','这个请求编号已经用于不同的操作。',409)
            return self._get(db,'jobs',old['job_id'])

    def create_job(self, project_id, kind, params, request_key, *, module=None, expected_module_head=None, adapter_version=None):
        fingerprint = hashlib.sha256(encode([project_id, kind, params]).encode()).hexdigest()
        if not request_key or len(request_key) > 160:
            raise DomainError("invalid_input", "每次任务需要一个不超过 160 字符的 requestId。")
        with self.tx() as db:
            project = self._get(db, "projects", project_id)
            old = db.execute("SELECT fingerprint,job_id FROM requests WHERE key=?", (request_key,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise DomainError("version_conflict", "这个请求编号已经用于不同的操作。", 409)
                return self._get(db, "jobs", old[1]), False
            if kind in {'components.query','components.freeze_local'} and project['version']!=params['expectedProjectVersion']:
                raise DomainError('version_conflict','工程上下文已变化，请刷新后查询器件。',409)
            if kind in {'manufacturing.prepare','manufacturing.external_review','manufacturing.jlc_quote','manufacturing.mechanical_prepare'} and project['version']!=params['expectedProjectVersion']:
                raise DomainError('version_conflict','工程版本已变化，请重新读取上下文后准备制造资料。',409)
            if kind=='manufacturing.jlc_sync':
                order=self._get(db,'orders',params['orderId'])
                if order['projectId']!=project_id:raise DomainError('not_found','订单不属于此工程。',404)
                if order['version']!=params['expectedOrderVersion']:raise DomainError('version_conflict','订单观察版本已变化。',409)
            if module and (project['version']!=params['expectedProjectVersion'] or project.get('moduleHeads',{}).get(module)!=expected_module_head):
                raise DomainError('version_conflict','工程或模块当前版本已改变，请重新读取上下文。',409)
            if kind=='mechanical.design' and (project['version']!=params['expectedProjectVersion'] or project.get('moduleHeads',{}).get('mechanical')!=params['expectedMechanicalRevisionId']):
                raise DomainError('version_conflict','工程或当前机械版本已改变，请重新读取机械上下文。',409)
            if kind in {'schematic.edit', 'schematic.review', 'pcb.handoff', 'pcb.initialize', 'pcb.edit', 'pcb.route','pcb.pair_route','pcb.sync'}:
                if project['version'] != params['expectedProjectVersion'] or self._head(db, project) != params['revisionId']:
                    raise DomainError('version_conflict', '工程或当前原理图版本已变化，请重新读取上下文。', 409)
            if kind == 'schematic.initialize' and (project['version'] != params['expectedProjectVersion'] or self._head(db, project)):
                raise DomainError('version_conflict', '初始化要求工程尚无当前设计，且工程版本一致。', 409)
            if db.execute("SELECT 1 FROM jobs WHERE project_id=? AND json_extract(data,'$.status') IN ('queued','running') LIMIT 1",(project_id,)).fetchone():
                raise DomainError("busy", "此工程还有一个任务在执行，完成或取消后再启动新任务。", 409)
            job = dict(id=uid("j"), projectId=project_id, kind=kind, params=params,
                       inputRevision=expected_module_head if module else params.get("revisionId") or params.get('expectedMechanicalRevisionId'), status="queued", phase="等待执行",
                       contextSnapshot={"version": project["version"], "specification": project["specification"],
                                        "preferences": project["preferences"]},
                       adapterVersion=adapter_version or "unrecorded", createdAt=now(), startedAt=None, finishedAt=None,
                       result=None, error=None, cancelRequested=False)
            self._put(db, "jobs", job)
            db.execute("INSERT INTO requests VALUES(?,?,?)", (request_key, fingerprint, job["id"]))
            self._event(db, "job.queued", project_id, {"kind": kind, "phase": job["phase"]}, job["id"])
            return job, True

    def job_update(self, id, event_type=None, **fields):
        with self.tx() as db:
            job = self._get(db, "jobs", id)
            # A late cancel/phase notification must never rewrite a completed
            # task or its clock. Retrying means creating a new task.
            if job["status"] not in {"queued", "running"}:
                return job
            job.update(fields)
            self._put(db, "jobs", job)
            if event_type:
                self._event(db, event_type, job["projectId"], fields, id)
            return job

    def _head(self, db, project):
        if 'headRevisionId' in project:
            return project['headRevisionId']
        rows = db.execute('SELECT data FROM revisions WHERE project_id=? ORDER BY rowid DESC', (project['id'],))
        return next((r['id'] for row in rows if (r := json.loads(row[0])).get('module','eda')=='eda' and r.get('origin') not in {'edited','initialized'}), None)

    def head_revision(self, project_id):
        with self.read_tx() as db:
            return self._head(db, self._get(db, 'projects', project_id))

    def add_revision(self, revision, job_id, adopt=True):
        with self.tx() as db:
            revision.setdefault('createdByJobId',job_id)
            self._put(db, "revisions", revision)
            if adopt:
                project = self._get(db, 'projects', revision['projectId'])
                project.update(headRevisionId=revision['id'], version=project['version'] + 1, updatedAt=now())
                self._put(db, 'projects', project)
            self._event(db, "revision.imported" if adopt else 'revision.candidate', revision["projectId"],
                        {"revisionId": revision["id"], "fileCount": len(revision["manifest"])}, job_id)

    def finish_edit(self, job_id, revision_id, result, pcb_draft=False):
        """Publish validation, head and terminal task in one database transaction."""
        with self.tx() as db:
            job = self._get(db, 'jobs', job_id)
            if job['status'] not in {'running', 'queued'} or job['cancelRequested']:
                return None
            revision = self._get(db, 'revisions', revision_id)
            project = self._get(db, 'projects', revision['projectId'])
            if self._head(db, project) != revision['parentRevisionId'] or project['version'] != job['contextSnapshot']['version']:
                raise DomainError('version_conflict', '当前输入已改变，候选版本未被采纳。', 409)
            manual=job['kind']=='schematic.edit' and job.get('params',{}).get('adoption')=='review'
            adopted = (result['draftChecksPassed'] if pcb_draft else result['checksPassed']) and not manual
            if manual:
                result.update(adoptionMode='review',readyForAdoption=bool(result['checksPassed']))
            result['adopted'] = adopted
            revision['validation'] = dict(status=('draft' if pcb_draft and not result['checksPassed'] else 'passed') if adopted else 'ready' if manual and result['checksPassed'] else 'needs_review', jobId=job_id,
                                          checksPassed=result['checksPassed'], adopted=adopted, draftChecksPassed=result.get('draftChecksPassed'), connectivityPreserved=result['connectivityPreserved'],
                                          connectivityMatched=result.get('connectivityMatched', result['connectivityPreserved']),
                                          connectivityPolicy=result.get('connectivityPolicy', 'preserve'))
            if manual:revision['validation']['adoptionMode']='review'
            self._put(db, 'revisions', revision)
            if adopted:
                project.update(headRevisionId=revision_id, version=project['version'] + 1, updatedAt=now())
                self._put(db, 'projects', project)
            job.update(status='completed', phase=('PCB 草稿已采纳；检查与未连线情况单列' if pcb_draft else '修改已采纳') if adopted else '修改待处理，当前版本未变',
                       result=result, finishedAt=now())
            if manual and result['checksPassed']:job['phase']='候选已就绪，等待审阅采纳'
            self._put(db, 'jobs', job)
            self._event(db, 'revision.validated', project['id'], {'revisionId': revision_id, 'adopted': adopted}, job_id)
            self._event(db, 'job.finished', project['id'], {'status': job['status'], 'phase': job['phase']}, job_id)
            return job

    def reject_candidate(self, revision_id, status, message):
        with self.tx() as db:
            revision = self._get(db, 'revisions', revision_id)
            if revision.get('validation', {}).get('status') != 'pending':
                return
            revision['validation'] = {'status': status, 'message': message}
            self._put(db, 'revisions', revision)
            self._event(db, 'revision.validated', revision['projectId'],
                        {'revisionId': revision_id, 'adopted': False, 'status': status}, revision.get('createdByJobId'))

    def finish_mechanical(self,job_id,revision_id,result):
        return self.finish_module(job_id,revision_id,result,'mechanical','机械模型已采纳；制造与实物检查另列','机械检查需处理，当前版本未变')

    def finish_module(self,job_id,revision_id,result,module,success_phase,failure_phase):
        with self.tx() as db:
            job=self._get(db,'jobs',job_id)
            if job['status'] not in {'queued','running'} or job['cancelRequested']:return None
            revision=self._get(db,'revisions',revision_id);project=self._get(db,'projects',revision['projectId']);heads=project.get('moduleHeads',{})
            if revision.get('module')!=module or revision['projectId']!=job['projectId'] or revision['createdByJobId']!=job_id:
                raise DomainError('invalid_input','模块候选、工程和创建任务不匹配。')
            if project['version']!=job['contextSnapshot']['version'] or heads.get(module)!=revision['parentRevisionId']:
                raise DomainError('version_conflict','工程或模块当前版本已改变；候选未采纳。',409)
            adopted=result['checksPassed'];result['adopted']=adopted
            revision['validation']=dict(status='passed' if adopted else 'needs_review',jobId=job_id,checksPassed=adopted,adopted=adopted)
            self._put(db,'revisions',revision)
            if adopted:
                project.update(moduleHeads={**heads,module:revision_id},version=project['version']+1,updatedAt=now())
                self._put(db,'projects',project)
            job.update(status='completed',phase=success_phase if adopted else failure_phase,result=result,finishedAt=now())
            self._put(db,'jobs',job)
            self._event(db,'revision.validated',project['id'],dict(revisionId=revision_id,module=module,adopted=adopted),job_id)
            self._event(db,'job.finished',project['id'],dict(status=job['status'],phase=job['phase']),job_id)
            return job

    def finish_readonly(self,job_id,result,phase):
        with self.tx() as db:
            job=self._get(db,'jobs',job_id)
            if job['status'] not in {'queued','running'} or job['cancelRequested']:return None
            if job['kind']in {'pcb.edit','pcb.route'} and result.get('noop'):
                current=self._get(db,'projects',job['projectId']);params=job['params']
                if current['version']!=params['expectedProjectVersion'] or self._head(db,current)!=params['revisionId']:
                    raise DomainError('version_conflict','无变化任务结束时工程条件或当前版本已变化，未报告为当前结果。',409)
            if job['kind']=='manufacturing.mechanical_prepare' and job['params']['dependencyPolicy']=='require-current':
                current=self._get(db,'projects',job['projectId']);params=job['params'];dependency=result['binding'].get('pcbDependency')
                if current['version']!=params['expectedProjectVersion'] or current.get('moduleHeads',{}).get('mechanical')!=params['revisionId'] or (dependency and self._head(db,current)!=dependency['revisionId']):
                    raise DomainError('version_conflict','结构资料结束时机械版本或依赖已变化，未登记为当前结果。',409)
            job.update(status='completed',phase=phase,result=result,finishedAt=now());self._put(db,'jobs',job)
            self._event(db,'job.finished',job['projectId'],dict(status='completed',phase=phase),job_id)
            return job

    def add_artifact(self, artifact):
        with self.tx() as db:
            self._put(db, "artifacts", artifact)
            self._event(db, "artifact.created", artifact["projectId"], artifact, artifact["jobId"])

    def events(self, after=0, project=None, limit=150):
        with self.read_tx() as db:
            where, args = "seq>?", [after]
            if project:
                where += " AND project_id=?"
                args.append(project)
            args.append(limit)
            rows = db.execute(f"SELECT * FROM events WHERE {where} ORDER BY seq LIMIT ?", args).fetchall()
            return [dict(seq=r["seq"], projectId=r["project_id"], jobId=r["job_id"], type=r["type"],
                         payload=json.loads(r["payload"]), createdAt=r["created_at"]) for r in rows]

    def event_snapshot(self,project=None,limit=40):
        if not 1<=limit<=150:raise DomainError('invalid_input','最近事件数量须为1..150。')
        with self.read_tx() as db:
            cursor=db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
            where='WHERE project_id=?' if project else ''
            args=[project,limit] if project else [limit]
            rows=db.execute(f'SELECT * FROM events {where} ORDER BY seq DESC LIMIT ?',args).fetchall()
            events=[dict(seq=r['seq'],projectId=r['project_id'],jobId=r['job_id'],type=r['type'],payload=json.loads(r['payload']),createdAt=r['created_at']) for r in reversed(rows)]
            return dict(cursor=cursor,events=events)

    def recover(self):
        for job in self.list("jobs"):
            if job["status"] in ("queued", "running"):
                if job['kind'] == 'firmware.device':
                    result = dict(job.get('result') or {})
                    result.update(checksPassed=False, interrupted=True,
                                  outcome='unknown-after-restart' if result.get('deviceProcessStarted') else 'no-write-attempted')
                    self.job_update(job['id'], result=result)
                self.job_update(job["id"], "job.finished", status="interrupted", phase="上次运行中断",
                                finishedAt=now(), error="服务已重新启动。该版本不支持计算续跑，请创建新任务。")
        for revision in self.list('revisions'):
            if revision.get('validation', {}).get('status') == 'pending':
                self.reject_candidate(revision['id'], 'interrupted', '服务重启，候选未被采纳；请基于当前版本重新提交。')
