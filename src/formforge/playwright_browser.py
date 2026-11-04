"""Independent Playwright quotation worker; shared HTTP/MCP business entry."""
from __future__ import annotations

import asyncio
import time
from typing import Literal

from pydantic import Field, model_validator

from .desktop_browser import DesktopBrowser
from .desktop_schemas import DesktopUploadFile
from .source_schemas import Strict
from .store import DomainError, now
from .submission_approval import SubmissionApprovals


class QuotePart(Strict):
    supplierCode: str = Field(pattern=r'^C[0-9]+$')
    mpn: str = Field(min_length=1,max_length=200)
    designators: list[str] = Field(min_length=1,max_length=200)
    perBoard: int = Field(ge=1,le=1000)
    mounting: Literal['smd','through-hole']

    @model_validator(mode='after')
    def reference_count(self):
        refs = [value.strip().upper() for value in self.designators]
        if any(not value for value in refs) or len(set(refs)) != len(refs) or self.perBoard != len(refs):
            raise ValueError('每板数量必须等于不重复的位号数量。')
        return self


class ProcurementBoard(Strict):
    widthMm: float = Field(gt=0, le=500, allow_inf_nan=False)
    heightMm: float = Field(gt=0, le=500, allow_inf_nan=False)
    layers: Literal[2] = 2
    thicknessMm: Literal[1.6] = 1.6
    side: Literal['top'] = 'top'

class AgentOrderReview(Strict):
    summary: str = Field(min_length=1,max_length=4000)
    evidence: list[DesktopUploadFile] = Field(min_length=1,max_length=20)


class PlaywrightBrowserRequest(Strict):
    action: Literal['open', 'inspect', 'quote', 'prepare-order', 'request-approval', 'submit-unpaid', 'restart', 'handoff', 'resume', 'close', 'review-retry']
    orderBinding: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    requestId: str = Field(min_length=1, max_length=160)
    projectId: str | None = Field(default=None, min_length=1, max_length=100)
    projectVersion: int | None = Field(default=None, ge=1)
    quantity: int | None = Field(default=None, ge=1, le=10000, strict=True)
    assemblyQuantity: int | None = Field(default=None, ge=1, le=10000, strict=True)
    assemblyScope: Literal['all','smd-only','pcb-only'] | None = None
    board: ProcurementBoard | None = None
    files: list[DesktopUploadFile] | None = Field(default=None, min_length=2, max_length=2)
    parts: list[QuotePart] | None = Field(default=None,min_length=1,max_length=200)
    resume: bool | None = None
    browserMode: Literal['visible','minimized','headless'] | None = None
    gerber: DesktopUploadFile | None = None
    approvalId: str | None = Field(default=None, min_length=1, max_length=100)
    agentReview: AgentOrderReview | None = None

    @model_validator(mode='after')
    def quote_binding(self):
        if (self.action == 'review-retry') != (self.orderBinding is not None):
            raise ValueError('仅review-retry需要指定审核队列的orderBinding。')
        if self.parts:
            refs = [ref.strip().upper() for part in self.parts for ref in part.designators]
            if len(set(refs)) != len(refs):
                raise ValueError('不同物料不能重复使用同一位号。')
        if self.assemblyQuantity is not None and (self.quantity is None or self.assemblyQuantity>self.quantity):
            raise ValueError('SMT数量不能超过PCB数量。')
        if self.assemblyScope=='smd-only' and any(p.mounting!='smd' for p in self.parts or []):
            raise ValueError('仅SMT装配的物料清单不能包含通孔器件。')
        pcb_only=self.assemblyScope=='pcb-only'
        if pcb_only and (self.action!='prepare-order' or self.assemblyQuantity is not None or self.files is not None or self.parts is not None):
            raise ValueError('pcb-only仅支持裸板准备，不接受SMT文件、物料、装配数量或提交。')
        fields = (self.projectId, self.projectVersion, self.quantity) if pcb_only else (self.projectId, self.projectVersion, self.quantity, self.files,self.parts)
        if self.action in {'quote','prepare-order','request-approval','submit-unpaid'}:
            if any(value is None for value in fields):
                raise ValueError('询价必须绑定工程、版本、数量及原BOM/坐标文件。')
            if self.action != 'quote' and self.board is None:
                raise ValueError('正式PCB流程必须提供实际板尺寸和工艺。')
        elif any(value is not None for value in fields) or self.resume is not None or self.board is not None or self.assemblyQuantity is not None or self.assemblyScope is not None:
            raise ValueError('仅quote、prepare-order、request-approval和submit-unpaid接受工程、制造资料和恢复参数。')
        if self.browserMode is not None and self.action != 'restart':
            raise ValueError('仅restart接受browserMode。')
        if (self.action in {'prepare-order','request-approval','submit-unpaid'}) != (self.gerber is not None):
            raise ValueError('仅正式订单准备或未支付提交必须指定实际Gerber及SHA。')
        if self.gerber and not self.gerber.path.lower().endswith('.zip'):
            raise ValueError('Gerber必须为审核过的ZIP。')
        if self.approvalId is not None and self.action != "submit-unpaid":
            raise ValueError("仅submit-unpaid接受approvalId。")
        if self.agentReview is not None and self.action!='submit-unpaid':
            raise ValueError('Agent复核仅用于submit-unpaid。')
        if self.action=='submit-unpaid' and (bool(self.approvalId)==bool(self.agentReview)):
            raise ValueError('默认提供agentReview直接提交；兼容人工流程时仅提供approvalId，不能同时使用。')
        return self


class PlaywrightBrowser(DesktopBrowser):
    """Reuse the existing authenticated transport and upload-byte validation."""
    def __init__(self, config, store=None):
        super().__init__(config)
        self.store = store
        self.workspace_settings = None
        self.approvals = SubmissionApprovals(store) if store else None
        self.directory = config.local / 'playwright'
        self.descriptor = self.directory / 'runtime.json'
        self.audit = self.directory / 'bridge-audit.jsonl'

    def profile(self):
        return dict(adapter='playwright-independent/1',
                    runtimeDescriptorPresent=self.descriptor.is_file(),
                    quoteOnly=False, orderingEnabled=True, paymentEnabled=False,
                    submissionApprovalRequired=False, submissionReviewRequired=True, submissionReviewMode='agent-review', approvalDecisionChannel="agent-review",
                    submissionScope="two-layer-1.6mm-top-side-assembly-up-to-pcb-quantity",
                    pcbOnlyPreparation=True,pcbOnlySubmission=False,
                    productionReleaseEnabled=False, humanLoginRequired=True,
                    credentialsReadable=False, automaticMutationRetry=False,
                    statusEndpoint='/api/procurement/browser',
                    availableActions=list(PlaywrightBrowserRequest.model_json_schema()['properties']['action']['enum']))

    def schema(self):
        return dict(**self.profile(), requestSchema=PlaywrightBrowserRequest.model_json_schema(),
                    instructions='先运行Start-ProcurementBrowser.ps1。默认无头；handoff用同一配置重开可见Edge，登录与验证码由用户完成；resume重开无头并交回控制。'
                    'quantity指定PCB数量，assemblyQuantity指定SMT数量（省略沿用PCB数量）；assemblyScope=smd-only时只上传贴片BOM/CPL，通孔件自焊。正式流程必须提供board尺寸，支持双层1.6mm顶面；quote演示计价不能当实板报价。'
                    'assemblyScope=pcb-only仅用于prepare-order：绑定实际Gerber、工程版本、数量和board，无需BOM/CPL/parts，不接SMT，不支持此分支提交。'
                    'restart重启浏览器，可选visible/minimized/headless；quote(resume=true)核实并恢复业务。'
                    '审核等待返回waiting并释放执行器；无头且智能体控制时自动只读查单，重启保留队列，通过status.reviews读取结果。'
                    '审核变化后需相同任务类型及原始输入、resume=true与新requestId，重新验证版本、文件和提交授权后继续；查询不会自动提交。'
                    'quote只报价；prepare-order准备实际PCB。默认由Agent对抗性复核后，submit-unpaid提供agentReview.summary及带SHA的本地evidence直接提交至待付款，无需用户点击确认卡。'
                    '复核记录标记agent-review，按当前版本、文件、PCB/SMT数量和装配范围一次性绑定，不冒充用户点击。结果未知先查回执；恢复仍须显式resume及新的Agent复核。'
                    'request-approval和approvalId仅兼容用户另行明确要求的人工模式，不是默认流程。'
                    '不填写或修改PCB、SMT及3D供应商备注，平台自动生成内容原样保留。付款和生产放行不可用。')

    async def status(self):
        try:
            result = await self._send(self._runtime(),'GET')
            return {**result,'paymentEnabled':False,'productionReleaseEnabled':False}
        except DomainError as exc:
            return dict(available=False,error=exc.code,message=exc.message,**self.profile())

    def _runtime(self):
        try:
            return super()._runtime()
        except DomainError:
            raise DomainError('playwright_unavailable', '独立询价执行器未启动，请运行Start-ProcurementBrowser.ps1。', 503) from None

    async def control(self, request):
        parsed = request if isinstance(request, PlaywrightBrowserRequest) else PlaywrightBrowserRequest.model_validate(request)
        body = parsed.model_dump(mode='json', exclude_none=True)
        async with self.lock:
            runtime = None if body["action"] == "request-approval" else self._runtime()
            self._load_used()
            if body['action'] != 'inspect' and body['requestId'] in self._used:
                raise DomainError('request_reused', '该浏览器请求已经执行或结果未知，请先读取状态。', 409)
            if body['action'] in {'quote','prepare-order','request-approval','submit-unpaid'}:
                if self.store:
                    project = await asyncio.to_thread(self.store.get, 'projects', body['projectId'])
                    if project['version'] != body['projectVersion']:
                        raise DomainError('version_conflict', '工程版本已变化，未开始询价。', 409)
                if body.get('files'):
                    body['files'] = await asyncio.to_thread(self._uploads, body['files'])
                if body.get('gerber'):
                    body['gerber'] = (await asyncio.to_thread(self._uploads,[body['gerber']]))[0]
                if body['action'] in {'prepare-order','request-approval','submit-unpaid'} and self.workspace_settings:
                    body['manufacturingProfile'] = await asyncio.to_thread(self.workspace_settings.manufacturing_snapshot)
            if body['action'] == 'request-approval':
                if not self.approvals:raise DomainError('approval_unavailable', '缺少工程授权存储，不能申请提交。', 503)
                record = await asyncio.to_thread(self.approvals.request, body)
                return dict(approvalRequired=True, approval=record, reviewUrl=f'/?project={body["projectId"]}#submission', nextStep='打开reviewUrl直达提交确认；Agent不可代为确认。')
            if body['action'] == 'submit-unpaid':
                if not self.approvals:raise DomainError('approval_unavailable', '缺少工程授权存储，不能提交。', 503)
                if body.get('agentReview'):
                    review=body['agentReview']
                    review['evidence']=await asyncio.to_thread(self._uploads,review['evidence'],{'.json','.md','.txt','.log','.png'},8*1024*1024)
                    body['submissionAuthorization']=await asyncio.to_thread(self.approvals.authorize_agent,body,review)
                    body['approvalId']=body['submissionAuthorization']['approvalId']
                    del body['agentReview']
                else:body['submissionAuthorization'] = await asyncio.to_thread(self.approvals.consume, body)
            started, clock = now(), time.perf_counter()
            audit = dict(action=body['action'], requestId=body['requestId'], startedAt=started,
                         projectId=body.get('projectId'), projectVersion=body.get('projectVersion'))
            self._append({**audit, 'phase':'started'})
            if body['action'] != 'inspect':
                self._used.add(body['requestId'])
            try:
                result = await self._send(runtime, 'POST', body)
            except DomainError as exc:
                self._append({**audit, 'phase':'failed', 'error':exc.code,
                              'finishedAt':now(), 'elapsedSeconds':time.perf_counter()-clock})
                raise
            self._append({**audit, 'phase':'completed', 'finishedAt':now(),
                          'elapsedSeconds':time.perf_counter()-clock})
            return result
