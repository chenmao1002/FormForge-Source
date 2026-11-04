"""3D quotation and reviewed unpaid ordering through independent Playwright."""
import asyncio
import hashlib
from pathlib import Path
import time
from typing import Literal

from pydantic import Field, model_validator

from .desktop_schemas import DesktopUploadFile
from .source_schemas import Strict
from .store import DomainError, now
from .submission_approval import SubmissionApprovals
from .stl_topology import inspect_bytes, report as topology_report


def check_print_stls(spec):
    rows=[]
    for item in spec['files']:
        raw=Path(item['path']).read_bytes();sha=hashlib.sha256(raw).hexdigest()
        if sha!=item['sha256']:
            raise DomainError('file_mismatch','STL在拓扑检查时与指定SHA-256不一致，未发送文件。',409)
        try:row=inspect_bytes(raw,item['path'])
        except ValueError as error:
            row=dict(file=item['path'],sha256=sha,passed=False,error=str(error))
        rows.append(row)
    return topology_report(rows,projectId=spec['projectId'],projectVersion=spec['projectVersion'])


class PrintOrderSpecification(Strict):
    projectId: str = Field(min_length=1, max_length=100)
    projectVersion: int = Field(strict=True, ge=1)
    files: list[DesktopUploadFile] = Field(min_length=1, max_length=12)
    copiesPerPart: int = Field(strict=True, ge=1, le=10000)
    material: Literal['9000HE']
    color: Literal['white']
    finish: Literal['no-sanding-no-paint-no-inserts']
    leadTimeHours: Literal[48]
    replacesOrders: list[str] | None = Field(default=None, min_length=1, max_length=12)
    quotedExcludingTaxCny: float | None = Field(default=None, gt=0, le=1000000, allow_inf_nan=False)
    expectedPayableCny: float | None = Field(default=None, gt=0, le=1000000, allow_inf_nan=False)

    @model_validator(mode='after')
    def model_files(self):
        from pathlib import Path
        import re
        if self.replacesOrders and (len(set(self.replacesOrders))!=len(self.replacesOrders) or any(not re.fullmatch(r'G\d{10,20}',n) for n in self.replacesOrders)):
            raise ValueError('替代订单必须逐一提供不重复的原生3D订单号。')
        names=[Path(f.path).name.casefold() for f in self.files]
        if len(set(names))!=len(names) or any(not n.endswith('.stl') for n in names):
            raise ValueError('本入口使用文件名唯一且SHA已核对的STL制作件。')
        return self


class PrintOrderReview(Strict):
    summary: str = Field(min_length=1, max_length=4000)
    evidence: list[DesktopUploadFile] = Field(min_length=1, max_length=20)


class PrintOrderRequest(Strict):
    action: Literal['quote', 'prepare', 'submit-unpaid', 'observe']
    requestId: str = Field(min_length=1, max_length=160)
    specification: PrintOrderSpecification
    agentReview: PrintOrderReview | None = None
    resume: bool = False

    @model_validator(mode='after')
    def review_scope(self):
        if self.action in {'prepare','submit-unpaid'} and self.specification.quotedExcludingTaxCny is None:
            raise ValueError('准备或提交必须绑定quote观察的实际未税报价。')
        if self.action=='submit-unpaid' and (self.agentReview is None or self.specification.expectedPayableCny is None):
            raise ValueError('提交需要Agent复核及已观察的含税运费应付总额，无需人工确认卡。')
        if self.action!='submit-unpaid' and self.agentReview is not None:
            raise ValueError('准备或观察页面不签发提交复核记录。')
        if self.resume and self.action!='submit-unpaid':
            raise ValueError('仅重新复核后的未付款提交接受恢复标记。')
        return self


class PrintOrders:
    def __init__(self, store, browser):
        self.store=store
        self.browser=browser
        self.approvals=SubmissionApprovals(store)

    def profile(self):
        return dict(adapter='playwright-reviewed-3d-unpaid/1', supported=True,
                    material='9000HE',color='white',finish='no-sanding-no-paint-no-inserts',
                    maxParts=12,leadTimeHours=48,submissionReviewMode='agent-review',
                    manualConfirmationCardRequired=False,paymentEnabled=False,productionReleaseEnabled=False,
                    localStlTopologyCheck=True,
                    statusEndpoint='/api/procurement/browser',controlEndpoint='/api/manufacturing/print-order')

    def schema(self):
        return dict(**self.profile(),requestSchema=PrintOrderRequest.model_json_schema(),
                    instructions='先启动Start-ProcurementBrowser.ps1；本入口与PCB/SMT共用独立Playwright浏览器和登录会话，无需Electron。'
                    'quote上传已核对STL、选择9000HE白色/无处理/48小时并返回实际报价；登录或验证码用procurement_browser_control(handoff/resume)接管。'
                    'quote/prepare/submit-unpaid先检查实际二进制STL字节拓扑，坏网格拒绝发送；本地通过不等于壁厚、强度或供应商审核通过。'
                    'prepare绑定quote返回的未税报价，核对文件、份数和工艺后进入原生结算页。'
                    '命令返回后台任务，需通过procurement_browser_status读取printTask最终结果；HTTP返回不代表报价或提交完成。'
                    '读取实际应付总额后，submit-unpaid提供同一specification、expectedPayableCny与agentReview直接提交，无需人工确认卡。'
                    '只支持当前明确列出的材料/处理/48小时交期；不支持的工艺不静默替代。供应商备注禁止填写、清空或修改，系统内容原样保留。'
                    '仅人工确认订单方式，拒绝自动扣款；未知结果先查同一批次/已有订单，不自动重发；付款和生产放行均不可用。')

    async def control(self, request):
        parsed=request if isinstance(request,PrintOrderRequest) else PrintOrderRequest.model_validate(request)
        body=parsed.model_dump(mode='json',exclude_none=True)
        browser=self.browser
        async with browser.lock:
            runtime=browser._runtime();browser._load_used()
            if body['requestId'] in browser._used:
                raise DomainError('request_reused','该3D请求已执行或结果未知，请先观察原生页面，不重发。',409)
            spec=body['specification'];project=await asyncio.to_thread(self.store.get,'projects',spec['projectId'])
            if body['action']!='observe' and project['version']!=spec['projectVersion']:
                raise DomainError('version_conflict','工程版本已变化，需重新核对3D制作文件。',409)
            # This entry produces a bound report below; do not calculate topology twice.
            spec['files']=await asyncio.to_thread(browser._uploads,spec['files'],check_stl=False)
            topology=None
            if body['action']!='observe':
                topology=await asyncio.to_thread(check_print_stls,spec)
                if not topology['passed']:
                    names=', '.join(Path(row['file']).name for row in topology['files'] if not row['passed'])
                    browser._append(dict(action=body['action'],requestId=body['requestId'],phase='stl-rejected',
                        projectId=spec['projectId'],projectVersion=spec['projectVersion'],localStlTopology=topology,finishedAt=now()))
                    raise DomainError('invalid_stl','STL拓扑检查未通过，未发送文件：'+names+'。请查看本地拓扑报告中的开放边、非流形边、方向及连通性。')
            status=await browser._send(runtime,'GET')
            if status.get('printAdapter')!='playwright-reviewed-3d-unpaid/1':
                raise DomainError('print_unavailable','独立浏览器未加载3D能力，请按项目脚本更新采购执行器；无需启动Electron。',503)
            command=dict(action={'quote':'print-quote','prepare':'print-prepare','submit-unpaid':'print-submit-unpaid','observe':'print-observe'}[body['action']],requestId=body['requestId'],specification=spec)
            if body['action']=='submit-unpaid':
                review=body['agentReview'];review['evidence']=await asyncio.to_thread(browser._uploads,review['evidence'],{'.json','.md','.txt','.log','.png'},8*1024*1024)
                intent=dict(kind='3d-print',projectId=spec['projectId'],projectVersion=spec['projectVersion'],printOrder=spec,requestId=body['requestId'],resume=body['resume'])
                command['submissionAuthorization']=await asyncio.to_thread(self.approvals.authorize_agent,intent,review)
            audit=dict(action=command['action'],requestId=body['requestId'],projectId=spec['projectId'],projectVersion=spec['projectVersion'],startedAt=now())
            if topology is not None:audit['localStlTopology']=topology
            browser._append({**audit,'phase':'started'});browser._used.add(body['requestId']);clock=time.perf_counter()
            try:result=await browser._send(runtime,'POST',command)
            except DomainError as exc:
                browser._append({**audit,'phase':'failed','error':exc.code,'finishedAt':now(),'elapsedSeconds':time.perf_counter()-clock});raise
            browser._append({**audit,'phase':'completed','finishedAt':now(),'elapsedSeconds':time.perf_counter()-clock})
            if topology is not None:result={**result,'localStlTopology':topology}
            return result
