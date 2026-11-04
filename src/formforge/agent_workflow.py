"""Persistent human-use planning. Observations are Agent claims, not certification."""
from __future__ import annotations

import copy
import hashlib
from typing import Literal

from pydantic import Field, model_validator

from .schemas import StrictModel
from .store import DomainError, encode, now


AGENT_INSTRUCTIONS = (
    "产品定方案先读project_context.productConcepts：同类产品图片调研、2至3个生成概念、用户选定后再进入CAD；登记来源/借鉴与限制/原提示词及图片SHA，保留原CAD。概念不是制造证据；服务不隐式搜索或调用收费模型。"
    "先读 project_context.agentWorkflow：延续已确认需求和待办，用户的新消息只修改涉及的部分。"
    "通过 project_workflow_update 增量记录决定、来源原话、假设、使用场景和下一步；不得把自己的建议标成用户确认。"
    "已确认事项不要重复询问；先查现有需求、偏好与记录，只有影响结果且无法合理判断的缺项才集中提问，其他工作继续。"
    "主动从使用者的位置推演看见、读懂、够到、操作、反馈和维护，而不只凑齐零件或通过检查。"
    "在布局前写出场景、动作和可检查的预期；例如坐在桌前是否看得见全部指示灯、握住产品能否调节旋钮。"
    "有更方便的方案应说明取舍并落实已授权改进，不等用户逐项提醒；低风险可逆选择标明假设后推进。"
    "交付前逐项对照需求及场景，引用当前版本产物和实际观察；碰撞/DRC通过不能代替可见性、可操作性或实物验证。"
    "记录缺失、pending、issue、stale 均不算完成；不适用要说明原因。外部Agent负责判断，服务只校验记录与版本。"
    "检查分页是否完整；失败后根据证据改变方法，不原样反复重试。暂停前保存具体下一步和阻塞原因。"
    "读取 project_context.processReview：同一输入多次未采纳时比较相关job的参数和反馈，不能把completed当采纳，也不要把历史问题当当前阻塞。"
    "PCB读取看objectCoverage，末页不等于整板；zoneInventory在每页列出全部铺铜身份，但不授权自动避让或删除。"
    "方案冻结前把充电/续航时间、待机耗电、重量、噪声和关键性能等适用取舍写成可检查的场景预期；未知值保留未知并记录验证方法。"
    "提前核对工具支持的平台和交付路径；不要到最后才发现固件或工艺不在原生支持范围。"
    "这些记录是工作依据，不是执行权限或工程规格的替代；改变产品要求仍须用 project_update 保留并更新 specification。"
)

ASPECTS = {
    'visibility': '从正常使用位置能看见并读懂信息吗？',
    'reachability': '手指、插头和工具够得到，操作时不会被挡住吗？',
    'operation': '主要操作步骤、方向和力度合理吗？',
    'feedback': '操作后能判断档位、电量、状态和异常吗？',
    'maintenance': '充电、清洁、拆装或更换是否方便？',
}


class Decision(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    text: str = Field(min_length=1, max_length=3000)
    state: Literal['confirmed', 'assumed', 'open', 'superseded']
    level: Literal['hard', 'soft', 'unknown'] = 'unknown'
    source: Literal['user', 'agent']
    sourceText: str = Field(min_length=1, max_length=6000)
    reason: str = Field(min_length=1, max_length=3000)

    @model_validator(mode='after')
    def confirmation_source(self):
        if self.state == 'confirmed' and self.source != 'user':
            raise ValueError('Confirmed decisions require the user source and original words.')
        return self


class UsageCheck(StrictModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    aspect: Literal['visibility', 'reachability', 'operation', 'feedback', 'maintenance', 'other']
    scenario: str = Field(min_length=1, max_length=3000)
    action: str = Field(min_length=1, max_length=3000)
    expected: str = Field(min_length=1, max_length=3000)
    decisionIds: list[str] = Field(min_length=1, max_length=100)
    state: Literal['pending', 'issue', 'checked', 'not_applicable'] = 'pending'
    observation: str = Field(default='', max_length=6000)
    method: str = Field(default='', max_length=2000)
    artifactIds: list[str] = Field(default_factory=list, max_length=30)
    limits: str = Field(default='', max_length=3000)

    @model_validator(mode='after')
    def observation_required(self):
        if self.state != 'pending' and not self.observation.strip():
            raise ValueError('A result or non-applicability needs an explicit observation/reason.')
        if self.state == 'checked' and not (self.method.strip() and self.artifactIds and self.limits.strip()):
            raise ValueError('Checked requires a method, project artifacts and evidence limits.')
        return self


class WorkflowUpdate(StrictModel):
    expectedVersion: int = Field(ge=0, description='Independent workflow version, from agentWorkflow.version.')
    expectedDesignBinding: str = Field(pattern=r'^[a-f0-9]{64}$', description='Echo agentWorkflow.designBinding; rejects changed requirements or design heads.')
    decisions: list[Decision] = Field(default_factory=list, max_length=100)
    usageChecks: list[UsageCheck] = Field(default_factory=list, max_length=100)
    nextAction: str | None = Field(default=None, max_length=6000)
    blocker: str | None = Field(default=None, max_length=3000)
    requirementsReviewed: bool = Field(default=False, description='Explicitly reconcile saved decisions with the current specification and preferences; do not re-ask unchanged decisions.')

    @model_validator(mode='after')
    def unique_ids(self):
        for rows in (self.decisions, self.usageChecks):
            if len({r.id for r in rows}) != len(rows):
                raise ValueError('Duplicate IDs in an update are not allowed.')
        return self


def sha(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def requirements_binding(project):
    return sha([project.get('specification', {}), project.get('preferences', {})])


def design_binding(project):
    return sha([requirements_binding(project), project.get('headRevisionId'), project.get('moduleHeads', {})])


def decisions_binding(decisions, ids):
    # Metadata/timestamps do not invalidate an unchanged decision.
    return sha([{k: decisions[i][k] for k in Decision.model_fields} for i in sorted(ids)])


def workflow_context(project):
    saved = copy.deepcopy(project.get('agentWorkflow', {}))
    decisions = saved.get('decisions', [])
    by_id = {d['id']: d for d in decisions}
    binding = design_binding(project)
    checks = saved.get('usageChecks', [])
    for check in checks:
        check['effectiveState'] = check['state']
        if check['state'] != 'pending' and (
            check.get('designBinding') != binding or
            any(i not in by_id or by_id[i]['state'] == 'superseded' for i in check['decisionIds']) or
            check.get('decisionsBinding') != decisions_binding(by_id, check['decisionIds'])
        ):
            check['effectiveState'] = 'stale'
    active = [d for d in decisions if d['state'] != 'superseded']
    covered = {i for c in checks if c['effectiveState'] in {'checked', 'not_applicable'} for i in c['decisionIds']}
    missing = [d['id'] for d in active if d['id'] not in covered]
    unreviewed = [k for k in ASPECTS if not any(c['aspect'] == k and c['effectiveState'] in {'checked', 'not_applicable'} for c in checks)]
    return dict(
        version=saved.get('version', 0), designBinding=binding,
        requirementsChanged=bool(saved and saved.get('requirementsBinding') != requirements_binding(project)),
        decisions=decisions, usageChecks=checks,
        nextAction=saved.get('nextAction', ''), blocker=saved.get('blocker', ''),
        confirmedDecisionIds=[d['id'] for d in active if d['state'] == 'confirmed'],
        openDecisionIds=[d['id'] for d in active if d['state'] == 'open'],
        uncoveredDecisionIds=missing, unreviewedAspects=unreviewed,
        reviewPrompts=[dict(aspect=k, question=ASPECTS[k]) for k in unreviewed],
        reviewStatus=('not_started' if not active else 'needs_attention' if missing or unreviewed or
                      any(c['effectiveState'] in {'pending', 'issue', 'stale'} for c in checks) or
                      any(d['state'] in {'open', 'assumed'} for d in active) or saved.get('blocker') or
                      saved.get('requirementsBinding') != requirements_binding(project)
                      else 'recorded'),
        evidenceTrust='agent-declared; artifact ownership and revision binding checked; usability not certified',
        agentInstructions=AGENT_INSTRUCTIONS,
    )


class AgentWorkflow:
    def __init__(self, store):
        self.store = store

    def read(self, project_id):
        with self.store.read_tx() as db:
            project = self.store._get(db, 'projects', project_id)
            project['headRevisionId'] = self.store._head(db, project)
            return workflow_context(project)

    def update(self, project_id, request):
        request = WorkflowUpdate.model_validate(request)
        with self.store.tx() as db:
            project = self.store._get(db, 'projects', project_id)
            had_head = 'headRevisionId' in project
            project['headRevisionId'] = self.store._head(db, project)
            old = project.get('agentWorkflow', {})
            binding = design_binding(project)
            if old.get('version', 0) != request.expectedVersion or binding != request.expectedDesignBinding:
                raise DomainError('version_conflict', '需求、设计或工作记录已变化，请重读上下文后合并。', 409)
            decisions = {d['id']: d for d in old.get('decisions', [])}
            checks = {c['id']: c for c in old.get('usageChecks', [])}
            for decision in request.decisions:
                decisions[decision.id] = decision.model_dump()
            for check in request.usageChecks:
                if any(i not in decisions or decisions[i]['state'] == 'superseded' for i in check.decisionIds):
                    raise DomainError('invalid_input', '使用检查必须关联有效的需求或决定。')
                heads = {project.get('headRevisionId'), *project.get('moduleHeads', {}).values()} - {None}
                for artifact_id in check.artifactIds:
                    artifact = self.store._get(db, 'artifacts', artifact_id)
                    if artifact.get('projectId') != project_id:
                        raise DomainError('invalid_input', '检查证据必须属于当前工程。')
                    if check.state == 'checked' and artifact.get('revisionId') not in heads:
                        raise DomainError('invalid_input', '已检查的证据必须来自当前设计版本。')
                checks[check.id] = dict(**check.model_dump(), designBinding=binding,
                    decisionsBinding=decisions_binding(decisions, check.decisionIds), recordedAt=now())
            if len(decisions) > 100 or len(checks) > 100:
                raise DomainError('invalid_input', '每个工程最多保留100项决定和100项使用检查。')
            updated = dict(version=request.expectedVersion + 1,
                requirementsBinding=(requirements_binding(project) if not old or request.requirementsReviewed
                                     else old.get('requirementsBinding')),
                decisions=list(decisions.values()), usageChecks=list(checks.values()),
                nextAction=old.get('nextAction', '') if request.nextAction is None else request.nextAction,
                blocker=old.get('blocker', '') if request.blocker is None else request.blocker,
                updatedAt=now())
            project['agentWorkflow'] = updated
            # Workflow bookkeeping must not invalidate CAD or interrupt active jobs.
            if not had_head:
                project.pop('headRevisionId', None)
            self.store._put(db, 'projects', project)
            self.store._event(db, 'project.workflow_updated', project_id,
                              dict(previous=old, current=updated))
            project['headRevisionId'] = self.store._head(db, project)
            return workflow_context(project)
