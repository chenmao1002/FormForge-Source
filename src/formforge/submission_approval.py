"""Version-bound unpaid submission reviews and optional legacy human consent."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from .store import DomainError, encode, now, uid


def intent(body):
    # Transport IDs and recovery mode cannot change the approved order contents.
    if body.get('kind')=='3d-print':
        return {key:body[key] for key in ['kind','projectId','projectVersion','printOrder']}
    result = {k: body[k] for k in ('projectId', 'projectVersion', 'quantity', 'files', 'parts', 'gerber')}
    if body.get('board') is not None:
        result['board'] = body['board']
    if body.get('manufacturingProfile') is not None:
        result['manufacturingProfile'] = body['manufacturingProfile']
    for field in ['assemblyQuantity','assemblyScope']:
        if body.get(field) is not None:result[field]=body[field]
    return result


def fingerprint(body):
    return hashlib.sha256(encode(intent(body)).encode('utf8')).hexdigest()


class SubmissionApprovals:
    def __init__(self, store):
        self.store = store

    def authorize_agent(self,body,review):
        key=fingerprint(body)
        with self.store.tx() as db:
            project=self.store._get(db,'projects',body['projectId'])
            if project['version']!=body['projectVersion']:raise DomainError('version_conflict','工程版本已变化，需重新复核。',409)
            rows=[json.loads(row[0]) for row in db.execute('SELECT data FROM submission_approvals WHERE project_id=?',(body['projectId'],))]
            previous=any(r['intentSha256']==key and r['status']=='consumed' for r in rows)
            if previous and not body.get('resume'):raise DomainError('submission_reconcile_required','同一资料已有提交尝试；先查单，仅明确恢复并重新复核后继续。',409)
            timestamp=now();record=dict(id=uid('approval'),projectId=body['projectId'],projectName=project['name'],intent=intent(body),intentSha256=key,status='consumed',decisionSource='agent-review',review=review,createdAt=timestamp,decidedAt=timestamp,consumedAt=timestamp,expiresAt=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(timespec='milliseconds'),requestId=body['requestId'],recovery=bool(body.get('resume')),priorSubmissionAttempt=previous,allowedSteps=['submit-print'] if body.get('kind')=='3d-print' else ['submit-pcb','submit-smt'],paymentEnabled=False,productionReleaseEnabled=False)
            for old in rows:
                if old['status'] in {'pending','approved'} and old['intent'].get('kind','pcb-smt')==body.get('kind','pcb-smt'):
                    old.update(status='superseded',supersededBy=record['id']);self.store._put(db,'submission_approvals',old)
                    self.store._event(db,'supplier.approval',body['projectId'],{'approvalId':old['id'],'status':'superseded'})
            self.store._put(db,'submission_approvals',record);self.store._event(db,'supplier.approval',body['projectId'],{'approvalId':record['id'],'status':'consumed','decisionSource':'agent-review'})
            return dict(approvalId=record['id'],intentSha256=key,allowedSteps=record['allowedSteps'])

    def _current(self, db, record):
        project = self.store._get(db, 'projects', record['projectId'])
        if project['version'] != record['intent']['projectVersion']:
            raise DomainError('version_conflict', '工程版本已变化，请重新准备提交确认。', 409)
        if record['expiresAt'] <= now():
            raise DomainError('approval_expired', '提交确认已过期，请重新申请。', 409)

    def request(self, body):
        key = fingerprint(body)
        with self.store.tx() as db:
            project = self.store._get(db, 'projects', body['projectId'])
            if project['version'] != body['projectVersion']:
                raise DomainError('version_conflict', '工程版本已变化。', 409)
            previous = [json.loads(row[0]) for row in db.execute(
                "SELECT data FROM submission_approvals WHERE project_id=? AND json_extract(data,'$.intentSha256')=?",
                (body['projectId'], key))]
            for row in previous:
                if row['status'] in {'pending', 'approved'} and row['expiresAt'] > now():
                    if row['recovery'] != bool(body.get('resume')):
                        raise DomainError('approval_mismatch', '已有待处理确认的恢复范围不同，请先处理该确认。', 409)
                    return row
            if any(row['status'] == 'consumed' for row in previous) and not body.get('resume'):
                raise DomainError('submission_reconcile_required', '同一资料已有提交尝试；先核对平台回执，仅明确恢复时可重新申请确认。', 409)
            record = dict(id=uid('approval'), projectId=body['projectId'], intent=intent(body), intentSha256=key,
                projectName=project['name'], status='pending', createdAt=now(),
                expiresAt=(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(timespec='milliseconds'),
                recovery=bool(body.get('resume')), priorSubmissionAttempt=any(r['status']=='consumed' for r in previous),
                allowedSteps=['submit-pcb', 'submit-smt'], paymentEnabled=False, productionReleaseEnabled=False)
            self.store._put(db, 'submission_approvals', record)
            self.store._event(db, 'supplier.approval', body['projectId'], {'approvalId':record['id'], 'status':'pending'})
            return record

    def decide(self, approval_id, approved):
        with self.store.tx() as db:
            record = self.store._get(db, 'submission_approvals', approval_id)
            self._current(db, record)
            if record['status'] != 'pending':
                raise DomainError('approval_used', '该确认已处理，不可重复批准。', 409)
            record.update(status='approved' if approved else 'denied', decidedAt=now(), decisionSource='local-workbench')
            self.store._put(db, 'submission_approvals', record)
            self.store._event(db, 'supplier.approval', record['projectId'], {'approvalId':approval_id, 'status':record['status']})
            return record

    def consume(self, body):
        with self.store.tx() as db:
            record = self.store._get(db, 'submission_approvals', body.get('approvalId', ''))
            self._current(db, record)
            if record['status'] != 'approved' or record['intentSha256'] != fingerprint(body) or record['recovery'] != bool(body.get('resume')):
                raise DomainError('approval_mismatch', '需要与当前工程、资料和恢复范围一致的未使用提交确认。', 409)
            record.update(status='consumed', consumedAt=now(), requestId=body['requestId'])
            self.store._put(db, 'submission_approvals', record)
            self.store._event(db, 'supplier.approval', record['projectId'], {'approvalId':record['id'], 'status':'consumed'})
            return dict(approvalId=record['id'], intentSha256=record['intentSha256'], allowedSteps=record['allowedSteps'])
