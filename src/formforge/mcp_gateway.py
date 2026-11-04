import json
import asyncio
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .module_settings import request_params
from .pcb_planning_schemas import PlanPcb
from .rule_presets import SaveRulePreset, ApplyRulePreset
from .schemas import CreateProject, EditSchematic, ImportProject, InspectProject, UpdateProject, RenameProject, ImportLibraryRequest, ProbeLibraryRequest, InitializeSchematic, ReviewSchematic, PcbHandoff, InitializePcb, EditPcb, RoutePcb, ReferenceCheck, SyncPcb, RoutePair, DesignMechanical, ExportPcbGeometry, CheckInsertion
from .maintenance_schemas import CleanupRequest, ProjectRemovalRequest, EmptyTrashRequest
from .source_schemas import SourceTransfer
from .firmware_schemas import FirmwareDesign
from .firmware_flash_schemas import FlashBundleCreate, FlashPlanCreate, FlashInternalCreate, FlashDeviceRequest
from .manufacturing_schemas import PrepareManufacturing
from .procurement_schemas import PlanProcurement
from .external_cam_schemas import ExternalManufacturingReview,CamTableProbe
from .source_schemas import RegisteredSourceRef
from .order_schemas import AdoptExistingOrder, AppendOrderObservation, AdoptExistingSmtOrder, AppendSmtOrderObservation
from .jlc_schemas import JlcQuoteRequest,JlcSyncRequest
from .structural_schemas import PrepareStructural
from .structural_quote_schemas import CreateStructuralQuote,AppendStructuralQuoteObservation
from .component_schemas import ComponentQuery
from .component_library_schemas import LocalLibraryProbe,FreezeLocalLibrary
from .supplier_schemas import SupplierMcpSessionRequest, SupplierAttemptRequest, SupplierStepRequest
from .desktop_schemas import DesktopBrowserRequest
from .playwright_browser import PlaywrightBrowserRequest
from .print_orders import PrintOrderRequest
from .adapters.routing_rules import PCB_AGENT_INSTRUCTIONS
from .adapters.schematic_fields import AGENT_INSTRUCTIONS as SCHEMATIC_AGENT_INSTRUCTIONS
from .adapters.mechanical_explosion import AGENT_INSTRUCTIONS as EXPLOSION_AGENT_INSTRUCTIONS
from .adapters.mechanical_engineering import AGENT_INSTRUCTIONS as ENGINEERING_AGENT_INSTRUCTIONS
from .adapters.mechanical_appearance import AGENT_INSTRUCTIONS as APPEARANCE_AGENT_INSTRUCTIONS
from .adapters.mechanical_integrity import AGENT_INSTRUCTIONS as INTEGRITY_AGENT_INSTRUCTIONS
from .adapters.pcb_interface_legends import AGENT_INSTRUCTIONS as INTERFACE_LEGEND_INSTRUCTIONS
from .project_transfer import ProjectExportRequest, ProjectPackageImportRequest
from .agent_workflow import WorkflowUpdate, AGENT_INSTRUCTIONS as WORKFLOW_AGENT_INSTRUCTIONS
from .product_concepts import ConceptUpdate, ConceptSelection


def create_mcp(service):
    port = service.config.port
    # All transports expose the same persistent workflow and domain design rules.
    mcp = FastMCP("FormForge", instructions="本地产品工程工作台。先用project_module_settings读取原理图/机械/固件/制造的工程默认值及来源；不需要覆盖时省略任务参数，明确任务参数优先。设置只影响后续任务，不追写历史版本。先分析好用户需求再开始。先读 project_context 和 capabilities_get。新工程可 schematic_initialize 冻结明确需求并创建空文档，再从冻结库放置器件及连接；选型和完整目标由 Agent 提出。schematic_read 返回对象摘要与引脚方向，schematic_connectivity 返回基线。电气编辑含删除/移动/标签须声明完整分组目标；未声明的连接保持。PWR_FLAG 使用专用 add_power_flag，不能借此取消电气检查。ERC 和实际连接匹配才采纳，未通过保留候选。任务完成不代表功能或制造通过。"+WORKFLOW_AGENT_INSTRUCTIONS+SCHEMATIC_AGENT_INSTRUCTIONS+PCB_AGENT_INSTRUCTIONS+EXPLOSION_AGENT_INSTRUCTIONS+ENGINEERING_AGENT_INSTRUCTIONS+INTERFACE_LEGEND_INSTRUCTIONS+APPEARANCE_AGENT_INSTRUCTIONS+INTEGRITY_AGENT_INSTRUCTIONS,
                  stateless_http=True, json_response=True,
                  transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                      allowed_hosts=[f"127.0.0.1:{port}", f"localhost:{port}"],
                      allowed_origins=[f"http://127.0.0.1:{port}", f"http://localhost:{port}"]))

    @mcp.tool()
    async def capabilities_get() -> dict[str, Any]:
        """读取实际支持的模块、工具版本和未实现能力。"""
        return service.capabilities()

    @mcp.tool()
    async def desktop_browser_schema() -> dict[str,Any]:
        """Electron供应商浏览器的只报价控制契约；不读取Cookie/密码，不支持提交订单、付款或生产。"""
        return service.desktop_browser.schema()

    @mcp.tool()
    async def desktop_browser_status() -> dict[str,Any]:
        """读取Electron当前真实连接、页面和人工接管状态；缓存存在不代表已登录或报价。"""
        return await service.desktop_browser.status()

    @mcp.tool()
    async def desktop_browser_control(request:DesktopBrowserRequest) -> dict[str,Any]:
        """控制Electron供应商页面获取报价。先inspect并使用当前控件ID，每次操作后重读；人工接管时禁止自动修改。只上传指定SHA的制造文件，禁止口令/验证码、下单、付款和生产。结果未知不重发原修改请求。"""
        return await service.desktop_browser.control(request)

    @mcp.tool()
    async def procurement_browser_schema() -> dict[str,Any]:
        """独立Playwright询价与待付款下单契约；默认由Agent对抗性复核后自行确认，无需人工确认卡。付款与生产不可用。"""
        return service.playwright_browser.schema()

    @mcp.tool()
    async def print_order_schema() -> dict[str,Any]:
        """读取嘉立创3D未付款提交契约；材料/文件/数量/价格绑定，默认Agent复核，无需确认卡。"""
        return service.print_orders.schema()

    @mcp.tool()
    async def print_order_control(request:PrintOrderRequest) -> dict[str,Any]:
        """独立Playwright的3D报价/结算准备/Agent复核后未付款提交。quote自动上传指定SHA的STL并配置工艺；与PCB/SMT共用会话，无需Electron。不改备注，不付款、不放行；未知结果先查同一批次，禁止自动重发。"""
        return await service.print_orders.control(request)

    @mcp.tool()
    async def procurement_browser_status() -> dict[str,Any]:
        """读取独立浏览器、后台询价及恢复结果。登录缓存不等于业务恢复成功。"""
        return await service.playwright_browser.status()

    @mcp.tool()
    async def procurement_browser_control(request:PlaywrightBrowserRequest) -> dict[str,Any]:
        """执行PCB/SMT准备或未付款提交。默认Agent对抗性复核后，submit-unpaid提供agentReview及SHA绑定证据直接执行，无需确认卡；PCB/SMT数量独立，不填写或修改供应商备注。登录由用户处理，结果未知先查单。禁止付款及生产放行。"""
        return await service.playwright_browser.control(request)

    @mcp.tool()
    async def supplier_session(request:SupplierMcpSessionRequest) -> dict[str,Any]:
        """打开FormForge专用嘉立创登录窗口、检查/保存会话或关闭浏览器。密码/验证码由用户在本机输入；仅返回登录/缓存状态，永不返回Cookie、口令或存储正文，不操作订单。"""
        if service.supplier_executor.active:
            from .store import DomainError
            raise DomainError('busy','浏览器步骤正在执行，请等待后再管理会话。',409)
        return await service.supplier_browser.command(request.action)

    @mcp.tool()
    async def supplier_executor_schema() -> dict[str,Any]:
        """读取本地供应商浏览器执行器契约与会话能力；浏览器由FormForge管理，Agent无需自带浏览器。"""
        return dict(sessionSchema=SupplierMcpSessionRequest.model_json_schema(),session=service.supplier_browser.profile(),**service.supplier_executor.schema())

    @mcp.tool()
    async def supplier_attempt_prepare(project_id:str,request:SupplierAttemptRequest) -> dict[str,Any]:
        """固定已检查制造资料/原始Gerber及SMT原始BOM/CPL；创建独立可恢复执行任务。同资料既有订单自动转为只读核对，不重复下单。"""
        return await asyncio.to_thread(service.supplier_executor.prepare,project_id,request.model_dump(mode='json'))

    @mcp.tool()
    async def supplier_attempt_read(project_id:str,attempt_id:str|None=None) -> dict[str,Any]:
        """读取执行任务、版本和不可覆盖的步骤历史。只有检查结果可用；manual-required并非已上传/提交，不返回页面正文或会话。"""
        return await asyncio.to_thread(service.supplier_executor.read,project_id,attempt_id)

    @mcp.tool()
    async def supplier_attempt_step(project_id:str,attempt_id:str,request:SupplierStepRequest) -> dict[str,Any]:
        """通过FormForge专用浏览器打开/检查/核对订单。严格版本与幂等；未支持表单留人工检查点。当前自动提交不可用，不付款或放行生产。"""
        return await service.supplier_executor.step(project_id,attempt_id,request.model_dump(mode='json'))

    @mcp.tool()
    async def components_schema() -> dict[str,Any]:
        """Exact C-code SMT supply query contract, inventory scope and unavailable CAD/classification features."""
        return await asyncio.to_thread(service.components.schema)

    @mcp.tool()
    async def components_query(project_id:str, request:ComponentQuery) -> dict[str,Any]:
        """Query granted SMT endpoint and create an immutable identity/stock observation. No MRO approval, CAD writes or orders."""
        return await service.start_job(project_id,'components.query',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def components_read(project_id:str, job_id:str|None=None) -> dict[str,Any]:
        """Read a hash-verified supply snapshot or failed-query diagnostic; missing stock/tier remain unknown."""
        return await asyncio.to_thread(service.components.read,project_id,job_id)

    @mcp.tool()
    async def component_library_schema() -> dict[str,Any]:
        """Installed KiCad asset probe/freeze contracts; explicit pin map, original bytes, STEP scope and supply binding."""
        return await asyncio.to_thread(service.component_library.schema)

    @mcp.tool()
    async def component_library_probe(request:LocalLibraryProbe) -> dict[str,Any]:
        """Probe exact installed symbol/footprint/model identities and hashes; no download or design mutation."""
        return await asyncio.to_thread(service.component_library.probe,request.model_dump(mode='json'))

    @mcp.tool()
    async def component_library_freeze(project_id:str,request:FreezeLocalLibrary) -> dict[str,Any]:
        """Freeze original/selected CAD assets, run KiCad native checks and STEP readback, bind exact supply identity when provided."""
        return await service.start_job(project_id,'components.freeze_local',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def component_library_read(project_id:str,job_id:str|None=None) -> dict[str,Any]:
        """Read hash-verified independent asset bundle and history; no manufacturer or mechanical-fit certification."""
        return await asyncio.to_thread(service.component_library.read,project_id,job_id)

    @mcp.tool()
    async def manufacturing_structural_quote_schema(project_id:str|None=None,job_id:str|None=None) -> dict[str,Any]:
        """Read STEP-bound printing quote contracts; no provider upload or order executor."""
        return await asyncio.to_thread(service.structural_quotes.schema,project_id,job_id)

    @mcp.tool()
    async def manufacturing_structural_quote_create(project_id:str,request:CreateStructuralQuote) -> dict[str,Any]:
        """Prepare a local quote record from checked original STEP parts; this is not a supplier quote."""
        return await asyncio.to_thread(service.structural_quotes.create,project_id,request.model_dump(mode='json'))

    @mcp.tool()
    async def manufacturing_structural_quote_read(project_id:str,quote_id:str|None=None,offset:int=0,limit:int=40) -> dict[str,Any]:
        """Read independent printing quote and order observations, costs, original files and history."""
        return await asyncio.to_thread(service.structural_quotes.read,project_id,quote_id,offset,limit)

    @mcp.tool()
    async def manufacturing_structural_quote_observe(project_id:str,quote_id:str,request:AppendStructuralQuoteObservation) -> dict[str,Any]:
        """Append source-backed actual browser observations; never uploads, orders, pays or certifies supplier bytes."""
        return await asyncio.to_thread(service.structural_quotes.observe,project_id,quote_id,request.model_dump(mode='json'))

    @mcp.tool()
    async def manufacturing_structural_schema(project_id:str|None=None,revision_id:str|None=None) -> dict[str,Any]:
        """结构件制造资料契约及逐件STEP/STL/SHA；需同工程已通过原生检查的机械版本。"""
        return await asyncio.to_thread(service.structural.schema,project_id,revision_id)

    @mcp.tool()
    async def manufacturing_structural_prepare(project_id:str,request:PrepareStructural) -> dict[str,Any]:
        """完整声明全部零件制作/已有/排除、明确数量和制作STEP SHA；冻结来源并实际回读实体/单位/尺寸，导出逐件二进制STL并检查实际网格拓扑。

        不修改CAD/head、不上传平台或下单。历史计价需显式policy/reason；文件检查不证明打印、强度、公差或生产资格。
        """
        return await service.start_job(project_id,'manufacturing.mechanical_prepare',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def manufacturing_structural_read(project_id:str,job_id:str|None=None) -> dict[str,Any]:
        """读取指定结构制造任务的报告/逐件STEP/STL/拓扑报告/审核ZIP和依赖时效；网页与MCP读取同值。"""
        return await asyncio.to_thread(service.structural.read,project_id,job_id)

    @mcp.tool()
    async def jlc_schema(project_id:str|None=None,manufacturing_job_id:str|None=None) -> dict[str,Any]:
        """读取官方FR4裸板工艺计价、既有订单查询契约和本机配置/实际鉴权状态。没有配置则不可调用。密钥仅通过本机网页配置，MCP不可读写；不创建订单、支付或放行生产。"""
        return await asyncio.to_thread(service.jlc.schema,project_id,manufacturing_job_id)

    @mcp.tool()
    async def jlc_read(project_id:str,job_id:str|None=None) -> dict[str,Any]:
        """读取官方查单/计价任务、筛选回执与历史。失败任务可带独立diagnostic：固定路径、状态、时间、完整字节SHA及排除凭据回显的追踪号；没有诊断的旧任务不补填。原始私有请求/响应不整包公开，诊断不是订单观察或成功。计价不是已支付或完整正式报价。"""
        return await asyncio.to_thread(service.jlc.read,project_id,job_id)

    @mcp.tool()
    async def jlc_quote(project_id:str,request:JlcQuoteRequest) -> dict[str,Any]:
        """以明确工程版本、制造任务和原Gerber SHA请求官方工艺计价。FR4常用工艺、数量和发票须明确；毫米长宽无损转厘米。未上传文件，不包含全部旧订单选项，不覆盖旧报价、工程或生产资格，不下单付款。返回后台任务，真实阶段沿统一事件显示。"""
        return await service.start_job(project_id,'manufacturing.jlc_quote',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def jlc_sync_order(project_id:str,request:JlcSyncRequest) -> dict[str,Any]:
        """查询本工程既有订单并核对官方身份后追加API观察。明确orderId/expectedOrderVersion、订单类型、日期窗口和查询尺寸单位，不把网页UUID当官方数值ID。严格匹配编号/文件名/层数/数量/厚度/尺寸，歧义拒绝，不新建订单。支付或生产的意外事实保留并停止动作，未明确的事实不猜测。"""
        return await service.start_job(project_id,'manufacturing.jlc_sync',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def order_schema() -> dict[str,Any]:
        """读取既有PCB订单关联和追加观察契约。没有平台执行器，不会上传/提交/付款；先通过统一资料入口登记可见UI证据。"""
        return service.orders.schema()

    @mcp.tool()
    async def order_read(project_id:str,order_id:str|None=None,offset:int=0,limit:int=40,latest:bool=False) -> dict[str,Any]:
        """读取订单及分页观察历史。单笔订单可用latest=true原子读取最近limit条，不能同时给非零offset；返回真实offset。默认保持从最早记录读取。浏览器观察为Agent声明，不提升工程/生产资格。"""
        return await asyncio.to_thread(service.orders.read,project_id,order_id,offset,limit,latest)

    @mcp.tool()
    async def smt_order_schema() -> dict[str,Any]:
        """读取 SMT 订单登记/追加契约。固定原始 Gerber/BOM/CPL；客户编码+SMT单号标识。订单审核、DFM、方向、支付独立，只有本地记录能力，不创建订单。"""
        return service.orders.smt_schema()

    @mcp.tool()
    async def smt_order_adopt_existing(project_id:str,request:AdoptExistingSmtOrder) -> dict[str,Any]:
        """登记已存在 SMT 单，与同工程已完成原生制造/采纳采购版本、原始文件三份 SHA、PCB单号和观察证据关联。不改变工程/CAD版本，不执行平台动作；相同登记可重放。"""
        return await asyncio.to_thread(service.orders.adopt_smt,project_id,request.model_dump(mode='json'))

    @mcp.tool()
    async def smt_order_observe(project_id:str,order_id:str,request:AppendSmtOrderObservation) -> dict[str,Any]:
        """使用独立订单版本追加 SMT 浏览器观察；原资料及历史不可覆盖。位号须属于绑定文件，完整匹配覆盖插件。订单审核不推断DFM/方向批准；通过 order_read 读取。"""
        return await asyncio.to_thread(service.orders.observe_smt,project_id,order_id,request.model_dump(mode='json'))

    @mcp.tool()
    async def order_adopt_existing(project_id:str,request:AdoptExistingOrder) -> dict[str,Any]:
        """关联已存在的嘉立创PCB订单，不创建第二张。固定同工程已完成的制造审核任务、原始Gerber SHA及来源；核对expectedProjectVersion、平台opaque身份、尺寸层数和登记证据。独立订单版本不改CAD或工程版本。"""
        return await asyncio.to_thread(service.orders.adopt,project_id,request.model_dump(mode='json'))

    @mcp.tool()
    async def order_observe(project_id:str,order_id:str,request:AppendOrderObservation) -> dict[str,Any]:
        """以expectedOrderVersion及原平台身份追加可追溯观察。不会查询平台或执行外部动作。即使设计上游已更新，旧订单仍可跟踪并标记时效；已支付/生产的意外事实如实记录为异常。"""
        return await asyncio.to_thread(service.orders.observe,project_id,order_id,request.model_dump(mode='json'))

    @mcp.tool()
    async def procurement_schema(project_id:str|None=None,manufacturing_job_id:str|None=None) -> dict[str,Any]:
        """读取采购/器件事实schema；可同时给工程/制造任务ID，获取基准包SHA、原生器件/符号/封装/接点数/坐标模板。事实仍是调用方整理声明，不自动解释PDF。"""
        return await asyncio.to_thread(service.procurement.schema,project_id,manufacturing_job_id)

    @mcp.tool()
    async def procurement_read(project_id:str,revision_id:str|None=None,offset:int=0,limit:int=100) -> dict[str,Any]:
        """读取独立制造采购当前/历史/失败候选、冻结来源、完整性与分页器件。价格当前时效另算，不重写历史报告。"""
        return await asyncio.to_thread(service.procurement.read,project_id,revision_id,offset,limit)

    @mcp.tool()
    async def procurement_plan(project_id:str,request:PlanProcurement) -> dict[str,Any]:
        """建立独立moduleHeads.manufacturing采购候选，不改原图/PCB/机械/固件。

        基于同工程、已完成且资料一致的manufacturing.prepare任务及完整审核包SHA；先读procurement_schema。
        source_transfer登记器件事实JSON和原始依据，sources使用bindings.procurement；全部所选位号须明确选择或unresolved。
        draft允许未确定项；reviewed须完整且符合封装/接点/原生阻容要求。功能器件替料须先改图重审。
        显式逐位号质心/方向审核后才给JLCPCB BOM/CPL审核副本；保留原始文件和坐标系。
        targetFormat=jlc-smt-matching可准备全覆盖选型BOM/CPL及说明，保留未确定项；无中心审核则明确保留原生锚点，不能视为生产文件或平台方向认可。
        库存/价格仅带时间的声明快照，Decimal计算不等于官方报价或库存预留，不含PCB/装配/税/运费；不上传、下单、付款或提升生产资格。
        """
        return await service.start_job(project_id,'manufacturing.procurement',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def manufacturing_profile() -> dict[str,Any]:
        """读取固定版本制造资料能力与工具摘要。只导出/预检；没有平台报价、提交或付款。"""
        return service.manufacturing.profile()

    @mcp.tool()
    async def manufacturing_external_schema(project_id:str|None=None,archive:RegisteredSourceRef|None=None,table:CamTableProbe|None=None) -> dict[str,Any]:
        """读取独立Gerber/BOM/CPL审核契约与实际CAM环境。可探查同工程ZIP条目/X2建议或表格列与XLSX工作表；映射仍须明确确认，不要求CAD版本。"""
        return await asyncio.to_thread(service.external_cam.schema,project_id,archive.model_dump() if archive else None,table.model_dump() if table else None)

    @mcp.tool()
    async def manufacturing_external_read(project_id:str,job_id:str|None=None,offset:int=0,limit:int=100) -> dict[str,Any]:
        """读取独立制造文件审核的当前/历史任务、真实层图、外形/钻孔及分页BOM/坐标；不混用原生制造或采购报告。"""
        return await asyncio.to_thread(service.external_cam.read,project_id,job_id,offset,limit)

    @mcp.tool()
    async def manufacturing_external_review(project_id:str,request:ExternalManufacturingReview) -> dict[str,Any]:
        """独立审核已登记Gerber ZIP和CSV/XLSX，无需原理图或PCB。先读schema并明确全部层/钻孔身份、DNP、列/单位/正反面变换；保留原件和来源SHA。真实解析、闭合外形、孔/槽与装配锚点检查后给出层图和审核包。只读任务不改任何head；文件一致不表示电气/采购/方向/平台或生产批准，不上传下单付款。"""
        return await service.start_job(project_id,'manufacturing.external_review',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def manufacturing_read(project_id:str,job_id:str|None=None) -> dict[str,Any]:
        """按任务读取同版制造资料场景、完整报告与产物。job_id为空读最新；历史/数量/装配范围不混用，不改设计head。"""
        return await asyncio.to_thread(service.manufacturing.read,project_id,job_id)

    @mcp.tool()
    async def manufacturing_prepare(project_id:str,request:PrepareManufacturing) -> dict[str,Any]:
        """从明确EDA版本/板SHA准备制造审核包，原图/PCB/独立head不变。

        官方CLI导出全部铜层Gerber-X2、分开PTH/NPTH毫米钻孔、逐位号BOM与双面坐标，完整原生引脚分组/坐标/封装核对。
        assemblyScope和assembledSides须明确；不自动排除插件，排除位号须说明理由。数量/工艺空项保留未明确，不默认采购决定。
        inputScope默认formforge，沿用可追溯PCB及完整原图条件；显式complete-native-import允许未有pcbDesign的完整原生导入。
        导入模式须在同一不可变版本绑定同目录同名原图/PCB，仍完整执行ERC/DRC/原图一致性、全部引脚/BOM/坐标及Gerber/钻孔检查，不伪造设计审查或交接资格。
        checksPassed仅为资料一致性，工程/采购/方向/CAM几何/平台预览与生产放行单列；不上传平台、不下单、不付款。
        """
        return await service.start_job(project_id,'manufacturing.prepare',request_params(request,'manufacturing.prepare'),request.requestId)

    @mcp.tool()
    async def firmware_profile() -> dict[str,Any]:
        """读取精确MCU/工具链、模板角色/参数、受约束C/H编辑和真实构建能力；无烧录或行为证明。"""
        return service.firmware.profile()

    @mcp.tool()
    async def firmware_flash_profile() -> dict[str,Any]:
        """读取烧录软件支持范围和锁定环境，不枚举或连接设备；OpenOCD 不可用。"""
        return await asyncio.to_thread(service.firmware_flash.profile)

    @mcp.tool()
    async def firmware_flash_bundle_create(project_id:str,request:FlashBundleCreate) -> dict[str,Any]:
        """从 source_transfer 登记的外部 ELF/HEX/BIN 或 ZIP 创建独立烧录包，不覆盖固件 head。"""
        return await asyncio.to_thread(service.firmware_flash.create_bundle,project_id,request.model_dump())

    @mcp.tool()
    async def firmware_flash_read(project_id:str) -> dict[str,Any]:
        """读取工程外部烧录包和离线计划历史。历史通过不代表设备验证。"""
        return await asyncio.to_thread(service.firmware_flash.list,project_id)

    @mcp.tool()
    async def firmware_flash_build_artifacts(project_id:str) -> dict[str,Any]:
        """列出内部构建产物；创建包时重新核对任务、版本、依赖和摘要。"""
        return await asyncio.to_thread(service.firmware_flash.internal_candidates,project_id)

    @mcp.tool()
    async def firmware_flash_internal_bundle(project_id:str,request:FlashInternalCreate) -> dict[str,Any]:
        """绑定内部构建的明确版本、任务、产物及摘要；不连接设备。"""
        return await asyncio.to_thread(service.firmware_flash.create_internal,project_id,request.model_dump())

    @mcp.tool()
    async def firmware_flash_device(project_id:str,request:FlashDeviceRequest) -> dict[str,Any]:
        """实际设备操作：须先获得用户对实物操作的授权。discover枚举；identify可能暂停/复位芯片；program写入并回读。不可用于离线验证。使用job_get/job_cancel读取或取消，重试必须新requestId。"""
        fields=request.model_dump()
        return await service.start_job(project_id,'firmware.device',fields,fields['requestId'])

    @mcp.tool()
    async def firmware_flash_plan(project_id:str,request:FlashPlanCreate) -> dict[str,Any]:
        """冻结外部包摘要并生成写入/擦除范围，默认拒绝擦除镜像外字节；不操作设备。"""
        return await asyncio.to_thread(service.firmware_flash.create_plan,project_id,request.model_dump())

    @mcp.tool()
    async def firmware_hardware_read(project_id:str,revision_id:str|None=None,target_reference:str='U1',check_job_id:str|None=None) -> dict[str,Any]:
        """读取原生硬件身份和hardwareEvidence；可指定已完成原生检查或PCB交接任务，否则发现同版最新ERC。新固件写入须原样提交完整hardwareEvidence，无需PCB。"""
        revision=service.revision(project_id,revision_id)
        return await asyncio.to_thread(service.firmware.hardware_read,project_id,revision['id'],target_reference,check_job_id)

    @mcp.tool()
    async def firmware_read(project_id:str,revision_id:str|None=None,file:str|None=None,offset:int=0,limit:int=12000) -> dict[str,Any]:
        """读取独立固件当前/历史/失败版本、需求、硬件依赖、来源与编译结果；file可读应用C/H及旧SHA，offset按Unicode字符。"""
        return await asyncio.to_thread(service.firmware.read,project_id,revision_id,file,offset,limit)

    @mcp.tool()
    async def firmware_design(project_id:str,request:FirmwareDesign) -> dict[str,Any]:
        """创建独立固件候选，不改EDA/机械head。先读profile和hardware_read。

        template为源码起点；edit按旧文件SHA增删改C/H，source使用同工程source_transfer的bindings.firmware，固定平台/SDK不可编辑。
        必须明确hardwareEvidence（从hardware_read原样提交）、硬件版本/摘要、目标物理脚角色、需求、时钟/正常复位假设。真实ARM编译/链接、全部ELF/HEX/BIN字节及启动内存回读通过才采纳。
        失败/取消保留候选，原head不变。服务构造工具命令，不接收主机脚本，不执行ARM产物或烧录。受约束C策略不是OS沙箱；源码行为与硬件语义未证明。
        """
        return await service.start_job(project_id,'firmware.design',request_params(request,'firmware.design'),request.requestId)

    @mcp.tool()
    async def pcb_geometry_read(project_id: str,revision_id: str | None=None) -> dict[str,Any]:
        """读取原生板框/钻孔和每器件模型引用/变换/本机文件SHA；孔用途不推断。"""
        return await asyncio.to_thread(service.pcb_geometry.read,service.revision(project_id,revision_id))

    @mcp.tool()
    async def pcb_geometry_export(project_id: str,request: ExportPcbGeometry) -> dict[str,Any]:
        """绑定板SHA并明确选择器件全部模型/来源SHA。scope默认complete-pcba，缺器件来源会拒绝；部分或仅板体须显式selected-models或board-only。冻结模型后CLI导出真实钻孔板体和器件，view.parts仅一个PCBA，componentParts仅内部追溯。机械使用完整assembly.step，不拆器件。只读，不改EDA/机械head；几何通过不等于配合或制造批准。"""
        return await service.start_job(project_id,'pcb.geometry_export',request.model_dump(exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def mechanical_schema(kind: str) -> dict[str,Any]:
        """读取真实机械规格schema。通用机械/产品造型用feature_model：参数表达式和可组合实体/轮廓/拉伸/旋转/放样/扫掠/布尔/抽壳/圆角/阵列/STEP，不限产品种类；基础建模无需材料/公差/载荷证明，零件干涉默认不阻塞，check_interference可选。旧类型enclosure/panel/u_bracket/assembly/rotary_assembly/articulated_arm保留。enclosure另返回pcbPlanning，在PCB布局前明确固定与接头配对净空；支柱方案才要求至少3个非共线真实NPTH孔。单位mm，不执行任意Python。"""
        return await asyncio.to_thread(service.mechanical.schema,kind)

    @mcp.tool()
    async def mechanical_insertion_check(project_id:str,request:CheckInsertion) -> dict[str,Any]:
        """只读检查明确机械版本/SHA的配对件直线插拔，不改head。仅实际PCB绑定enclosure。

        interfaces声明接头footprintId、已验证openingIndex/face（四侧面或top顶面）、CAD基准、旋转、退出行程、净空、matingZone和配对件bodies。
        局部+X必须沿该面向外；不从名称/包络推断基准。evidencePath引用sources中JSON，其kind=insertion-interface,units=mm,interface须等于该接口除evidencePath的完整参数（含默认值）。
        支持有SHA的STEP全部实体和明确工程box；二者须声明basis。仅engaging可在声明接合区与指定接头接触，external无豁免；板/其他器件/外壳全检查。
        完整固定姿态行程以逐实体连续保守扫掠盒检查，非离散采样；STEP可能保守误报。报告和诊断GLB/STEP/交付包绑定版本与来源，另进程重验。
        通过只证明声明范围的名义净空，不认证插头兼容、公差、弯曲线缆、手/工具、插拔力或制造。
        """
        return await service.start_job(project_id,'mechanical.insertion_check',request.model_dump(exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def mechanical_read(project_id: str,revision_id: str | None=None) -> dict[str,Any]:
        """读取独立机械当前或指定版本的规格、来源、检查、零件和PCB依赖过期状态；不切换电路head。"""
        return await asyncio.to_thread(service.mechanical_read,project_id,revision_id)

    @mcp.tool()
    async def mechanical_design(project_id: str,request: DesignMechanical) -> dict[str,Any]:
        """按mechanical_schema提交明确机械规格，独立建模/STEP和GLB导出/名义检查/独立回读后才采纳机械head。
        通用建模用feature_model，features按顺序引用先前id；参数支持算术表达式，parts指定输出，省略则输出最后特征。修改参数或特征后提交新版本即可，不要求制造资料。网页可直接调整参数重建。
        实物设计先读取manufacturingPlanning并确定工艺/具体材料/处理、固定方式与间隙，engineering_plan随版本保存；standard_part使用带来源的标准件库。已声明关键壁厚、配合和螺钉通路失败时不能采纳；未规划只作为概念CAD，检查范围不等同制造放行。
        爆炸查看可声明explosion.fixed_parts和groups[{id,parts,direction,distance_mm}]；同组零件共同位移，按装配层级和退出方向设置紧凑距离。未声明按几何高度分层，不按零件序号，也不证明真实可拆卸。

        expectedProjectVersion与expectedMechanicalRevisionId必需；首次机械设计后者为null。不要求先有原理图或PCB。
        来源文件接受source_transfer返回的bindings.mechanical或明确绝对路径/SHA/来源版本/许可；design内step/evidence_files严格匹配inputs/<source-id><suffix>，不能访问未声明路径或传入代码。
        可选pcbRevisionId和expectedBoardHash绑定PCB依赖。要导入真实几何，另给pcbGeometryJobId，必须为本工程同板SHA已完成且通过的pcb_geometry_export。
        feature_model另给pcbPart，指向完整PCBA唯一输出；同SHA STEP只允许经刚性移动/旋转，不得拆分、改色或融合到其他输出。绑定后PCB改版自动提示重审，网页参数重建保留原绑定；该模式不套用enclosure的pcbMounts或pcbPassages，实际孔位/固定仍由工程接头和干涉检查核对。
        首条几何绑定要求enclosure.board使用该交接总装STEP同SHA来源，并明确length/width/thickness/origin/substrate_solid_index，与nativeProof.substrate及substrateSolidIndex相符。
        这里thickness是实际STEP基体厚度，不能用名义1.6替代。需要连接器穿壁或顶面访问窗时，明确pcbPassages[{footprintId,openingIndex,clearance}]；完整投影须容入圆角开口，原生实体碰撞继续检查。顶面窗口允许明确接头通过窗口露出或位于盖下；board.top_height仍保留全高，内部高度仅按实际STEP的未穿出器件测量，其他器件约束不放宽。窗口不证明排母可插到位，另用mechanical_insertion_check声明完整配对件及行程。
        固定板时pcbMounts明确列出交接中的至少3个非共线纯NPTH特征ID；board.mounting_holes须逐一匹配实际孔位/尺寸。pcbFastener声明螺钉工程包络，evidencePath引用sources内同参数JSON及design.evidence_files；检查名义接触/头部空间/旋入，不等同采购公差或强度。
        未选器件覆盖列表、固定/插拔/采购公差待验证，不把部分模型检查泛化为整机通过。
        名义CAD/干涉/回读不等于强度、实物、制造或连续扫掠验证；失败和旧版本保留。
        """
        return await service.start_job(project_id,'mechanical.design',request_params(request,'mechanical.design'),request.requestId)

    @mcp.tool()
    async def project_list() -> list[dict]:
        """列出本地 FormForge 工程。"""
        return service.store.list("projects")

    @mcp.tool()
    async def workspace_cleanup_read() -> dict[str, Any]:
        """读取当前缓存清理预览、进度和实际结果，不执行删除。"""
        return service.maintenance.status()

    @mcp.tool()
    async def workspace_cleanup_scan() -> dict[str, Any]:
        """扫描已知缓存、过期测试目录及依赖核对通过的KiCad检查输入副本。保留版本源/登记产物/失败记录，先预览不删除；轮询read查看workspaces类别及保留原因。"""
        return await service.maintenance.scan()

    @mcp.tool()
    async def workspace_cleanup_run(request: CleanupRequest) -> dict[str, Any]:
        """按有效scanId及用户明确选定itemIds清理。工程工作副本须在执行前再次核对引用和冻结源；保留版本/产物/报告。变化或占用目录跳过，返回后台实际结果。"""
        return await service.maintenance.clean(request.scanId, request.itemIds)

    @mcp.tool()
    async def project_trash_list() -> list[dict]:
        """读取工程回收站，不改变工程。"""
        return await asyncio.to_thread(service.maintenance.trash)

    @mcp.tool()
    async def project_trash_empty(request: EmptyTrashRequest) -> dict[str, Any]:
        """用户明确要求清空回收站时使用。绑定已确认列表的工程ID、版本和名称，后台清除文件；轮询workspace_cleanup_read。保留任务时钟、订单元数据和失败记录。"""
        return await service.maintenance.empty_trash([row.model_dump() for row in request.projects])

    @mcp.tool()
    async def project_delete(project_id: str, request: ProjectRemovalRequest) -> dict[str, Any]:
        """用户明确要求删除时使用：以当前版本和完整工程名确认，移入回收站；不释放文件空间。"""
        return await asyncio.to_thread(service.maintenance.project_change, project_id, request.expectedVersion, request.confirmName)

    @mcp.tool()
    async def project_restore(project_id: str, request: ProjectRemovalRequest) -> dict[str, Any]:
        """恢复回收站中的工程；已开始彻底删除文件的工程不能恢复。"""
        return await asyncio.to_thread(service.maintenance.project_change, project_id, request.expectedVersion, request.confirmName, True)

    @mcp.tool()
    async def project_purge(project_id: str, request: ProjectRemovalRequest) -> dict[str, Any]:
        """用户明确要求彻底删除时使用：清除已入回收站工程的本地文件、不可恢复。保留任务时钟与订单元数据，不撤销供应商订单。"""
        return await asyncio.to_thread(service.maintenance.project_purge, project_id, request.expectedVersion, request.confirmName)

    @mcp.tool()
    async def project_create(request: CreateProject) -> dict[str, Any]:
        """创建工程并保存原始需求和偏好；不启动自动设计。"""
        return await asyncio.to_thread(service.create_project, request.name, request.description, request.preferences)

    @mcp.tool()
    async def project_export(project_id: str, request: ProjectExportRequest) -> dict[str, Any]:
        """按明确工程版本导出可移植FormForge工程包。保留修订、任务时钟、失败记录、产物、来源、订单历史、事件和全部工程文件，并逐文件记录SHA-256；运行中任务或上传会拒绝。凭据、浏览器登录态、请求重放键和缓存不进入包。"""
        return await asyncio.to_thread(service.project_transfer.export, project_id, request.expectedVersion)

    @mcp.tool()
    async def project_import(request: ProjectPackageImportRequest) -> dict[str, Any]:
        """从本机FormForge工程包导入为新工程。先校验包清单、每个文件SHA-256和路径安全，再原子写入；内部身份重映射，原历史和原始字节保持。历史检查仍绑定原版本，本机环境及后续修改需重新检查；不导入凭据或请求重放授权。"""
        return await asyncio.to_thread(service.project_transfer.import_package, request.packagePath, request.name)

    @mcp.tool()
    async def project_context(project_id: str, compact: bool=True, revision_id: str|None=None) -> dict[str, Any]:
        """读取需求、偏好、agentWorkflow已确认决定/假设/使用检查/下一步和版本证据；先延续记录，避免重复询问。compact=false返回完整历史，revision_id加载指定历史详情。"""
        return await asyncio.to_thread(service.context,project_id,compact,revision_id)

    @mcp.tool()
    async def project_workflow_read(project_id: str) -> dict[str, Any]:
        """读取持续工作记录、已确认决定、使用场景、待办和过期检查。记录是Agent声明，不认证人机体验或实物。"""
        return await asyncio.to_thread(service.agent_workflow.read, project_id)

    @mcp.tool()
    async def product_concepts_read(project_id: str) -> dict[str, Any]:
        """定方案前先读：参考调研、2至3个概念方向、用户选择和需求变化状态；不是工程验收。"""
        return await asyncio.to_thread(service.product_concepts.read,project_id)

    @mcp.tool()
    async def product_concepts_update(project_id: str, request: ConceptUpdate) -> dict[str, Any]:
        """登记外部Agent搜索的图片来源、借鉴/避用分析及生成概念、提示词；图片先source_transfer登记。更新会清空旧选择，旧记录保留事件历史，不修改CAD。"""
        return await asyncio.to_thread(service.product_concepts.update,project_id,request)

    @mcp.tool()
    async def product_concepts_select(project_id: str, request: ConceptSelection) -> dict[str, Any]:
        """仅在用户明确选定后保存概念ID及用户原话；不代替结构验证，不自动执行CAD。"""
        return await asyncio.to_thread(service.product_concepts.select,project_id,request)

    @mcp.tool()
    async def project_workflow_update(project_id: str, request: WorkflowUpdate) -> dict[str, Any]:
        """按独立版本和设计绑定增量合并决定与使用检查，保留未提及条目和变更历史。确认须保留用户原话；检查须绑定当前产物、方法与局限；新要求仍需project_update同步规格。不改CAD或执行权限。"""
        return await asyncio.to_thread(service.agent_workflow.update, project_id, request)

    @mcp.tool()
    async def source_transfer(project_id: str, request: SourceTransfer) -> dict[str,Any]:
        """登记同工程不可变资料；不改设计版本，不执行文件或下载URI，不认证声明的供应商来源。

        register_text直接登记UTF-8文本<=192KiB，可给expectedSha256；需name/uri/version/license/note/origin及requestId。
        二进制或较大文件用begin声明size/sha256与相同元数据（文件<=64MiB，工程256份/512MiB），再append发送连续offset、contentBase64、块sha256（<=192KiB）。
        append按位置/字节精确重试；status读取receivedBytes可重启续传；commit核对完整长度/SHA后才可使用，abort保留取消时钟。
        返回source.ref及bindings模板供mechanical/geometry/insertion、schematic_review和symbol_library_import引用；可改模板id别名，其他来源声明须保持。
        来源随任务再次冻结并保留登记引用；既有领域格式/大小/原生检查不放宽。source_read可读回有界base64及块SHA。
        """
        return await asyncio.to_thread(service.sources.transfer,project_id,request.model_dump())

    @mcp.tool()
    async def source_list(project_id: str, offset: int=0, limit: int=40, include_uploads: bool=False) -> dict[str,Any]:
        """分页列出同工程已登记资料、来源资格与消费模板；可附最近100个上传状态用于恢复。未完成上传不是可用资料。"""
        return await asyncio.to_thread(service.sources.list,project_id,offset,limit,include_uploads)

    @mcp.tool()
    async def source_read(project_id: str, source_id: str, encoding: str='metadata', offset: int=0, limit: int=65536) -> dict[str,Any]:
        """核对同工程已登记资料/来源SHA，返回元数据；encoding=base64按字节offset读取最多192KiB并带块SHA/nextOffset。不执行或解析资料。"""
        return await asyncio.to_thread(service.sources.read,project_id,source_id,encoding,offset,limit)

    @mcp.tool()
    async def rule_presets_read() -> dict[str,Any]:
        """读取本机工程默认预设，按实际登记的应用次数排序。"""
        return service.rule_presets.read()

    @mcp.tool()
    async def rule_presets_save(request:SaveRulePreset) -> dict[str,Any]:
        """保存明确模块字段为本机预设，不改工程或执行设计。"""
        return service.rule_presets.save(request)

    @mcp.tool()
    async def rule_presets_apply(project_id:str,request:ApplyRulePreset) -> dict[str,Any]:
        """按工程版本应用预设到后续任务默认值，原始快照与历史CAD保持。"""
        return service.rule_presets.apply(project_id,request)

    @mcp.tool()
    async def project_module_settings(project_id: str) -> dict[str, Any]:
        """读取各模块工程默认值、覆盖来源；新任务显式参数优先，不修改历史版本。"""
        return service.settings.project_modules(project_id)

    @mcp.tool()
    async def project_rename(project_id: str, request: RenameProject) -> dict[str, Any]:
        """修改工程显示名称，保留设计文件、需求、偏好和历史；版本不一致时拒绝覆盖。"""
        return service.store.rename_project(project_id, request.expectedVersion, request.name)

    @mcp.tool()
    async def project_update(project_id: str, request: UpdateProject) -> dict[str, Any]:
        """按预期版本更新需求和偏好；版本不一致时拒绝覆盖。"""
        return service.store.update_project(project_id, request.expectedVersion, request.specification, request.preferences)

    @mcp.tool()
    async def eda_import(project_id: str, request: ImportProject) -> dict[str, Any]:
        """冻结 KiCad 工程目录快照，保留来源，不修改原目录。返回后台任务。"""
        return await service.start_job(project_id, "eda.import", request.model_dump(exclude={"requestId"}), request.requestId)

    @mcp.tool()
    async def eda_inspect(project_id: str, request: InspectProject) -> dict[str, Any]:
        """运行 KiCad 10 原生 ERC/DRC 和预览。可选板体 GLB 不含元件/外壳。返回 jobId。"""
        return await service.start_job(project_id, "eda.inspect", request.model_dump(exclude={"requestId"},exclude_none=True), request.requestId)

    @mcp.tool()
    async def schematic_initialize(project_id: str, request: InitializeSchematic) -> dict[str, Any]:
        """从明确需求建立空原理图和工程，paper 可选 A4/A3/A2/A1/A0（默认 A3），冻结需求及设计决策，不导入现有图纸。

        仅允许尚无当前设计的工程，要求 expectedProjectVersion。执行 CLI ERC/网表/预览检查后采纳。
        同时冻结软件默认schematicPresentation，不覆盖用户需求/decisions。后续按功能组从冻结库摆放器件，组内用真实短线、跨组/跨页用标签；电气编辑须声明完整连接目标。完成后读取schematic_read.readability并复核原生预览。
        """
        return await service.start_job(project_id,'schematic.initialize',request_params(request,'schematic.initialize'),request.requestId)

    @mcp.tool()
    async def schematic_read(project_id: str, revision_id: str | None = None, sheet: str | None = None,
                             offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """读取真实图页和对象 UUID/sha256，供受约束修改；默认当前版本，分页最多 300 对象。

        readability始终针对所选整页并绑定同一版本，不随对象分页截断。labelOnlySymbols列出有标签但未发现实体导线连接到其他可分析器件的器件；不是电气未连。先结合功能分组决定补局部线、调整摆放或保留跨组标签，不自动全页连线。unavailable/预算超限明确报告；无提示不是整页验收。
        """
        return await asyncio.to_thread(service.schematic, project_id, revision_id, sheet, offset, limit)

    @mcp.tool()
    async def schematic_connectivity(project_id: str, revision_id: str | None = None,
                                      offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """读取原生分组、完整引脚与 graphHash。没有网表时先 eda_inspect。

        修改要求 baselineHash=graphHash、removeGroups 原分组 id 和 addGroups 完整目标引脚分组。
        分页最多 300 组；没有列入替换的分组保持不变。
        """
        return await asyncio.to_thread(service.connectivity, project_id, revision_id, offset, limit)

    @mcp.tool()
    async def symbol_library_probe(request: ProbeLibraryRequest) -> dict[str, Any]:
        """只读探查本机 .kicad_sym 的真实 SHA-256、格式版本和数量，不创建版本。

        返回摘要作为 symbol_library_import 的 expectedSha256；格式版本不是发布版本。
        可用source://登记引用，此时须附projectId。不执行文件、下载地址或修改外部来源。
        """
        return await asyncio.to_thread(service.probe_library, request.sourcePath,request.projectId)

    @mcp.tool()
    async def symbol_library_import(project_id: str, request: ImportLibraryRequest) -> dict[str, Any]:
        """冻结本地 .kicad_sym或source_transfer返回的bindings.symbolLibrary，核对 expectedSha256，记录声明的来源/版本/说明/许可。

        单独创建候选版本，经 ERC 和连接保持检查后采纳。返回后台任务；同摘要复用现有库。
        来源和发布版本为调用方声明，不代表供应商认证。服务不下载 URL。
        """
        return await service.import_library(project_id, request)

    @mcp.tool()
    async def symbol_library_read(project_id: str, revision_id: str | None = None, library_id: str | None = None,
                                  symbol: str | None = None, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """列出版本冻结库；指定 library_id 可分页读符号，指定 symbol 可精确查询。

        返回 library sha256、symbol sha256、支持边界与相对引脚坐标。用于 add_library_symbol。
        支持普通单单元及同一冻结库内的属性继承，最多32层；派生符号返回symbolResolution父链和展开摘要。
        电源使用专用add_power_flag；多单元及派生图形/引脚覆盖不可放置。
        """
        return await asyncio.to_thread(service.libraries, project_id, revision_id, library_id, symbol, offset, limit)

    @mcp.tool()
    async def schematic_edit(project_id: str, request: EditSchematic) -> dict[str, Any]:
        """基于当前版本修改属性/字段位置/说明文字/导线路径，生成候选并执行 ERC 与原生连接等价检查。

        支持从冻结库新增器件、移动/删除普通单单元器件、导线/连接点/局部标签/未连接标记；电气编辑要求 connectivity 显式目标。
        move_field可显式给rotation=0或90；省略保持原角度。字段实际方向由该角度与器件姿态共同决定，不移动引脚或连线。
        删除时 addGroups 必须覆盖剩余器件引脚；move_symbol不隐式移动导线。add_text 是说明文字。
        rotate_symbol用objectId/expectedHash及rotation=0/90/180/270旋转普通单单元器件和字段位置，保留身份及字段相对角度；导线/标签不自动跟随，可同批明确重布线，完整原生连接目标与ERC仍须通过。
        move_group用sheet/expectedSheetHash、objectIds和delta=[dx,dy]显式联合移动器件/导线/连接点/标签/未连标记/说明文字，须单独成批且不能携带connectivity替换目标。外部端点固定；移动对象直接接触未选对象、内部未选分支、非正交或零长度连线拒绝。读取objects和fixedEndpoints；不自动分组、找路或证明无文字遮挡。
        tidy_fields按sheet/expectedSheetHash及symbolIds单独成批，自动将位号/数值字段移至附近无估计冲突的位置，默认步距1.27mm、最大移动5.08mm。算法只改字段位置，保留字符、方向、可见性及全部电气锚点，完整ERC/网表验收仍强制。读取changes的movedFieldCount、unresolved、unavailable及真实SVG；估计文字包络不等于精确字形或整页美观证明。有界搜索无候选不表示无解。
        connect_local_labels按sheet/expectedSheetHash及labelIds（2–80个）单独成批，将同类同名标签间明确选定的局部关系补成正交导线；maxLengthMm默认25.4、最大100。保留标签和器件位置，避让本体/字段/其他引脚及既有导线，不接受connectivity替换。读取connected/skipped/unavailable；跨组长连线继续保留标签。采纳仍须完整原生连接保持和ERC通过。
        add_sheet 从父图页SHA创建唯一空子图页及层级框，采纳后再读取新页SHA放置器件。add_global_label 明确跨页同名连接；普通add_label只在本页有效。共享实例和层级引脚连接尚未支持新增。
        add_power_flag 仅使用冻结库的 PWR_FLAG；一次最多 1500 操作，以便完整电路在同一候选中接受 ERC 和连接核对。includeDocumentation=true 交 CLI 导出 PDF/BOM。
        编辑默认对旧版重新导出原生基线网表，不重复旧版ERC/SVG；候选ERC/SVG/网表及完整目标验收保持。includeBaselineDiagnostics=true可显式恢复修改前完整ERC与预览，默认false以减少重复调用。
        auto_layout.blockLayout按原页整理声明完整块：sheets中给sheet/expectedSheetHash、groups(id/name/objectIds)、columns；共享文件须affectSharedInstances=true，会一起改变全部instancePaths的布局。仅平移，不重建层次、网名、实例或块内连接；不与organization/intent/groups重建意图混用。新单页分页仍用原organization/intent。
        import_footprint须单独成批，冻结原始.kicad_mod与声明来源；可用source://，新版本返回内容寻址的封装库身份。
        目标对象用 UUID/摘要，新增导线/连接点用图页摘要。add_symbol 的 objectId 指模板器件；不复制采购字段。
        adoption=review可只生成待审阅候选，建议includeBaselineDiagnostics=true保留同任务原生前后图。schematic_candidate_read读取候选与同版检查；目视评审后schematic_candidate_adopt显式采纳，过期工程或变更文件拒绝。默认automatic兼容旧行为。
        返回任务对象 id；job_get的result.adopted保留任务结束时事实。后续审阅采纳见候选读取状态及revision.validation，不改写原任务结束时间。未通过时当前版本不变。
        """
        return await service.start_job(project_id, 'schematic.edit', request.model_dump(exclude={'requestId'}, exclude_none=True), request.requestId)

    @mcp.tool()
    async def schematic_review(project_id: str, request: ReviewSchematic) -> dict[str, Any]:
        """冻结 Agent 提供的资料、事实和计算，核对实际器件属性与连接，重跑原生检查。

        计算式只允许事实名称、常数、加减乘除。事实单位和覆盖范围由 Agent 审查；不自动解读数据表。
        connections.mode缺省same_net要求同网；different_nets要求所列引脚两两异网；exact_net要求一个原生网络恰好含全部所列引脚。未知/重复引脚不能通过，结果返回实际分组和差异。异网不证明器件隔离耐压或爬电距离。
        可选layoutGroups声明id/title/intent、members(role/reference/可选fixedAt)、anchorRole、interfaces及repeatOf。基准组不含repeatOf；重复组须角色集合一致，核对相对位置、方向和真实引脚偏移，toleranceMm默认0.05mm。组内成员须同页，组间可跨页，不自动猜分组。接口只核对引脚存在，不证明完整性。声明与检查随本次修订/输入清单保存在审查报告，旧版结论不可用于新版。
        可选pinReviews逐条保存sheet/reference/pin、reviewedRevisionId、expectedSheetHash、decision(keep_label/accepted_layout/needs_layout)和非空reason。结果pinReview独立于电气checksPassed：修订或图页摘要变化、引脚不可用标为needs_review；needs_layout标为open，其他有效声明为recorded。重新确认须明确更新绑定和理由，不隐藏readability提示，也不自动证明美观或功能。
        资料先放在 FormForge examples/ 或 validation/ 中，并提供摘要、版本和出处。
        典型值/工程预算保留为条件，不能称为保证上限或实测。报告仅用于此版本的 PCB 设计交接。
        """
        return await service.start_job(project_id, 'schematic.review', request.model_dump(exclude={'requestId'}), request.requestId)

    @mcp.tool()
    async def pcb_handoff(project_id: str, request: PcbHandoff) -> dict[str, Any]:
        """审查通过后生成 PCB 连接交接包：冻结原理图、网表分组、封装及完整焊盘映射。

        要求当前设计与需求版本未变化、有效审查、原生检查再次通过、全部符号引脚与封装焊盘一致。
        交接不创建板框，不代表布局布线或制造验收。配置条件和待实测项随包保留。
        """
        return await service.start_job(project_id, 'pcb.handoff', request.model_dump(exclude={'requestId'}), request.requestId)

    @mcp.tool()
    async def revision_compare(project_id: str, before_revision_id: str, after_revision_id: str) -> dict[str, Any]:
        """校验两个版本输入摘要，返回变更文件、直接父子版本的操作明细和候选检查状态。"""
        return await asyncio.to_thread(service.compare, project_id, before_revision_id, after_revision_id)

    @mcp.tool()
    async def pcb_initialize(project_id: str, request: InitializePcb) -> dict[str, Any]:
        """从当前已审查交接创建真实 PCB。Agent 明确板框、规则及全部器件位置，不复用旧板。

        冻结封装，建立原生焊盘网络和原理图层级对应，再执行 ERC/DRC/原理图一致性检查。
        未连线可以保留为草稿；物理错误或网络不符不得采纳。adopted/draftChecksPassed 与 checksPassed 分开。
        已有板重建须显式传replaceExisting.expectedBoardHash及reason；产生无旧铜/线对/机械孔的新候选，旧版本保留，重新检查和交接，不继承制造资格。
        """
        return await service.start_job(project_id,'pcb.initialize',request.model_dump(exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def pcb_read(project_id: str, revision_id: str | None = None, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        """读取真实板框、器件/焊盘坐标、走线/过孔、网络和对象 sha256。分页最多300对象。

        首页执行原生回读；后页固定revision_id可复用同版原生快照，每页仍核对源文件摘要。未固定版本或缓存淘汰时重新原生回读，不影响设计采纳检查。
        objectCoverage.complete仅表示本响应是否包含整板对象；nextOffset=null的末页不等于整板。每页zoneInventory给出全部铺铜身份/摘要/网络，可据此发现后页铺铜；不是自动yieldZones同意，选择避让前仍须读完整对象并核对作用。
        localPlacement.linkTopology="escape-cluster"可显式联合两个器件和一个companion，poseCandidates最多4组完整reference/at/rotation姿态，全部受影响网络须明确选入；源板始终作为基准，只有连接/几何改善且源连接保持才采纳，不放宽工艺或出口。
        接口转向后的placementBlockers给出真实碰撞器件，须协调移动并声明受影响铜。合法朝外改善可优先于少量铜长/过孔代价，但绝不接受源物理连接退化。移动后自动为有依据的接口及待接端子预留直出通道；预留仅影响搜索，不添加铜也不证明可接通。nets.restoreSourceConnectionsOnly可明确只恢复原来已连的焊盘组，历史未连仍报告，不得据此声称全网已连通。协调候选每组最多60秒。
        编辑还要求 boardSha256 对应 expectedBoardHash；所有焊盘网络保持，修改铜连接交由完整DRC检查。
        布局前必须读取layoutFeedback.connectorAccess：原生PCB Edge基准朝内或未贴合板边时先用suggestedPlacement规划旋转/移动及受影响铜重布；未知方向先获取有来源的配对面/插拔基准，不凭USB/排针名称猜方向。普通DRC不证明可插拔。移动后基准仍错误会拒绝；实际插头/壳体包络独立核对。
        layoutFeedback包含有限SMD间隙及placementReview二端串联组摆放提示；后者按真实焊盘连接与原生线链找长绕行/背向出口，提供位号、端子、铜UUID和长度下界。先比较旋转/交换/就近串排及外部入口，不把绕线评分或下界当成可布通/摆放最优证明。逐件本体覆盖原因/轮廓/铜冲突在分页footprint对象bodyGap中。未覆盖不表示安全。
        """
        return await asyncio.to_thread(service.board,project_id,revision_id,offset,limit)

    @mcp.tool()
    async def pcb_plan(project_id: str, request: PlanPcb) -> dict[str, Any]:
        """只读布局前规划与失败诊断，绑定PCB版本/SHA，不修改板或自动提交候选。

        groups由Agent按明确功能声明name/references/source，可给region及anchorReference；返回跨组真实针网和锚点引脚板坐标侧。
        纯groups请求默认只读真实器件/引脚，不做接口试放和走线等几何检查；probeInterfaces=true可显式附带接口检查。corridors/escapeTargets/routeJobId按需启用对应诊断，performedChecks/skippedChecks明确覆盖。
        原生有依据的接口建议位姿在内存试放，汇总碰撞器件/铜对象及全部受影响网络；未知插拔基准仍未知。
        corridors明确net/points/width/clearance/layers/source，按原生几何检查直通道，仅作规划，不生成铜或禁布区。
        escapeTargets指定实际reference/pin/width/clearance，或routeJobId读取同源布线反馈自动选零出口端点。
        nextActions区分封装内部线宽冲突、外围可动二端器件和显式铜重布；锁定/接口/机械件不作自动移动建议。
        先看这些证据再选择pcb_edit/pcb_route；功能角色不按网名推断，结果不证明可布通或可制造。
        """
        return await asyncio.to_thread(service.pcb_planning,project_id,request.model_dump(exclude_none=True))

    @mcp.tool()
    async def pcb_edit(project_id: str, request: EditPcb) -> dict[str, Any]:
        """在新版本副本移动器件、修改圆角/矩形板框、增删直线铜线和通孔，保持全部焊盘网络和源物理连接。

        move_footprint可显式指定layer为F.Cu/B.Cu；省略保持当前安装面。翻面使用原生上下翻转，rotation是翻转后的绝对原生角度；必须同时重接受影响铜并调整接口丝印，翻面本身不证明装配或插拔空间。
        set_outline默认3mm圆角（小板短边1/4）；仅用户明确要求直角时cornerRadius=0并填写squareCornerReason。原生圆弧、实际净距和导出外轮廓独立检查。
        移动/清理与必要重接须在同一候选完成；sourceConnectivity按保存后原生物理分组核对旧网络，失败保留旧版，并列出网络、端子、坐标和变化铜对象。
        cleanup_tracks以nets明确网络并单独成批，自动删除同网同层同宽、被保留直段完全覆盖的重复/共线铜；保留锁定对象、差分契约整网、真实支路、调长蛇形与宽度过渡，不裁剪部分重叠、不合并端点。算法强制整数纳米共线/覆盖条件，changes返回覆盖证据和跳过原因；原生DRC、源连接、出口和转角等采纳检查不变。空清理结果不证明全板无冗余。

        仅接入本服务创建且原理图/工程条件未改变的PCB草稿。对象操作需UUID/摘要。
        新铜线指定既有网络、已启用铜层、线宽和水平/垂直/45度单段方向；组合后的裸露连续走线须45度过渡，新增直角/锐角即使DRC通过也拒绝。跨add_track对象、既有端点或线宽变化统一检查；焊盘内部转向和真实T形分支另列。读取cornerPolicy具体坐标与routingQuality.cornerFindings修正，不能仅看单段方向。过孔遵守本板最小规则。
        焊盘必须从原生中心连续直出：真实原生圆形铜焊盘允许中心45度斜出，其余焊盘按板坐标上、下、左、右。圆角矩形、椭圆、偏移铜形不使用圆形例外；禁止偏心及焊盘内部拐弯平移出口。越过真实铜边界后至少直行0.15mm再转弯，直出跨对象和线宽累计；直接直线连接邻近焊盘/过孔且无平面转弯可短于该长度。保存后原生padExitPolicy拒绝新增或重做问题，旧版未改项在padExits中保留。
        set_via_rules必须是批次第一项且仅一次，明确diameter/drill/reason，更新版本和原生工程的通孔最小外径/孔径，单边铜环至少0.075mm。普通找路不会自动放宽；不改变净距、线宽及孔铜净距，也不代表板厂能力已确认。
        拟由铺铜连接的地端按明确参考/返回网声明先铺铜，不按网名猜角色；回读填充后原生物理分组，仅对未连端子补线/孔，并保留去耦回流条件。add_zone默认padConnection=thermal，明确保存thermalGap/thermalSpoke以便焊接；full需有具体电气/热理由。矩形独立支持0.1mm起，板框仍最小5mm；铜区最小厚度/热焊辐条遵守独立生成下限。set_zone_rules须单独成批，明确minimumThickness/thermalSpoke/reason；缺省旧规则沿用trackWidth。重填后原生DRC和源连接检查不放宽；参数不表示板厂能力或可焊性资格。
        mountingPlan自动搜索四角对齐孔位并创建冻结NPTH；默认M2.5/2.7mm孔、6mm规划包络、4至10mm对称内缩，可按真实结构选择。custom或none须reason/source，随版本保存，none不自动删除已有孔；后续编辑破坏计划孔位/预留空间会拒绝。新建PCB默认自动规划，冲突不可静默省略。
        optimize_tracks单独成批提供nets/reason/maxChains/searchSeconds，对既有普通铜链执行折返/碎弯/中心出口优化，保护锁定铜与成组契约，保持线宽/层/分支/过孔及连接；可显式提供escapeTargets(reference/pin/width/clearance)，在保持阻挡铜原线宽、层和接点的前提下打开连接器/多引脚IC出口，局部窗口与绕行仍有界；contactRework可明确选择一个过孔及全部直线臂的objectId/expectedHash/reason/maxOffsetMm，保持孔径、原接触和原生外直颈，无合法改善返回noop；读取trackOptimization.escapeBefore/escapeAfter及最终原生证明，出口可用不代表已布通，随后pcb_route实际重接；不得自动缩窄细间距电源引脚线宽。optimize_interface_silkscreen单独成批提供references/maxOffsetMm，使用已有或同次明确interfaceLegends按实际针网排放功能/逐针文字，不猜电压；无空间保留并报告。
        optimize_reference_silkscreen单独成批提供references/maxOffsetMm，自动选择水平位号位置，避让原生本体/焊盘/可见文字/板边；保持内容字号笔画，锁定/隐藏或无空间的项保留并报告，最终原生检查仍决定采纳。
        add_mounting_hole提供reference/at/drill和source(path/sha256/uri/version/license/note)，仅接受冻结单个纯NPTH圆孔足迹，实例排除原理图/BOM/贴装；不能通过board-only隐藏电气器件。remove_mounting_hole仅可删除已验证机械孔，仍检查全部源物理连接。
        add_board_text增加独立说明丝印：text单行、at板坐标、layer仅F.Silkscreen/B.Silkscreen、rotation正交、size字高mm和thickness笔画mm；背面自动镜像。remove_board_text需该独立文字UUID/旧SHA。保存重载核对完整文字目标及既有文字保持，原生DRC/焊盘网络/铜意图仍独立检查。隐藏位号用set_footprint_text的Reference与visible=false，不删除电气身份。
        interfaceLegends登记接口reference/purpose/title/layer及pins(pin/net/label)，label以真实编号:开头；保存后检查逐针覆盖、针网、原生丝印面、尺寸和距离。省略继承；显式列表替换。已声明规则失败拒绝采纳，未登记连接器单列提示。
        powerFeeds可显式声明source/capacitor/loads的reference/pin及returnNet，要求供电路径经过电容连接节点；省略继承，显式列表替换并随版本保存。中心线拓扑逐版复核，失败不采纳；不支持所选电源网的铜区/圆弧，不等同串联电容、PI或去耦回路证明。
        set_footprint_silkscreen_line给出板坐标start/end/reason；remove_footprint_silkscreen_line或remove_footprint_silkscreen_polygon给出reason。仅F/B丝印直线端点及直线/多边形删除，单独成批且需UUID/旧SHA；冻结工程派生封装并保留原库，原生保存后证明其余内容及物理连接保持。多实例共享定义须一起保持一致；逐实例新封装身份尚未接入。
        物理短路/错误或过期输入不采纳；未连线作为待完成项保留，不表示制造放行。
        """
        return await service.start_job(project_id,'pcb.edit',request_params(request,'pcb.edit'),request.requestId)

    @mcp.tool()
    async def pcb_route(project_id: str, request: RoutePcb) -> dict[str, Any]:
        """Agent明确选择普通网络和线宽/净距/过孔权限，使用自研有界搜索续布。

        默认固定器件续布；nets[].replaceCopper明确同网直段/过孔UUID、旧SHA和原因，可在同一候选拆除后重连，不接受成组契约拥有的铜。
        可选localPlacement比较placementReview确认的一对简单串联器件；显式linkTopology=shared-node可选择共享多端节点上的两个二端器件，保留所有第三端和全局网络，比较各器件朝向固定外网端点的独立出口对齐。可另用companionReferences显式选择一个邻近器件做有界微调（三器件比较，非默认）。涉及的全部网络须列入nets。原器件锚定和网格候选的收益与未修改原板比较，无改善则保留原板并返回noop；共享节点的搜索窗口同时覆盖所选网的固定远端，不改变真实板框或净距。候选仅轻量筛选，优胜变更才执行既有原生保存、DRC、连通与采纳检查。
        localPlacement.copperRework可明确一条同网同宽同层共线直链的对象UUID/旧SHA、原因和最大偏移，比较至多两个45度铜线绕行；原宽/层/端点/固定过孔保持，中途接点或复杂分支明确不支持，不能用普通pad分组略过无pad铜岛。
        localPlacement.contactRework可另选一个既有过孔及全部中心线接入直线铜的UUID/旧SHA，比较有限轴向接点移动与器件姿态；直线中点接入会分为两臂，原过孔UUID/孔径/层对、逐支路宽度/铜层及远端连接保持，原有有限侧面铜接触自动登记并保持。与copperRework二选一，复用同一候选和搜索预算，不自动扩大移动器件范围。
        默认acceptanceScope=schematic-contract。只有明确标记且无原理图的native-source-preserving来源可显式选择同名范围：保存原生源连接、检查新增DRC/出口/转角及铜意图，仅可采纳draft；ERC/原图一致性未执行、checksPassed保持false，不降低已有原理图契约。
        候选继续执行铜意图、完整源物理连接保持；重布网络须全部连通且无活动悬空铜端。
        USB/RS422关键成组通道另行设计，普通网续布不代表阻抗、回流或整板布线完成。
        默认将外网填充铜视为障碍；仅nets[].yieldZones明确UUID/摘要的铜区可局部让路，随后原生重填并检查源物理连接。
        根据明确参考/返回网语义，拟由铺铜连接的地端先留待铺铜；先以add_zone默认thermal形成真实填充，再读取physicalGroups，仅为未连分组补短线/孔。普通路由使用已填充铜后的物理连接，不必把全部地端逐一布线；不能按GND网名猜角色或取消去耦/回流要求。
        无候选/预算耗尽保留诊断，不证明不可布通。查询任务终态和adopted，不能只看plannedConnectionCount。
        maxPairAttemptsPerConnection可显式设1–12；省略保持每次分组合并最多12组端子对，较小值有助于给后续网络保留全局预算，但会少探索端点选择，不证明网络不可布通。结果返回实际生效上限。
        searchHeuristicWeight可显式设1–2，省略保持1。大于1按加权优先级更积极探索目标，可能得到更长/代价更高的近似候选；goalSelectionComplete保持false，不证明最优。线宽/净距、中心出口、45度接点、可读性整理与保存后的全部采纳条件不变，最终仍须读取整板质量。
        searchArea可显式限制栅格搜索窗口，适合大板上的带状局部通道；必须位于板框包围盒内并覆盖所选网络全部焊盘及既有铜端点，结果回显实际边界。它只减少搜索空间，不放宽障碍、连通、源铜、DRC、出口、转角或整板采纳检查；窗口内无候选不证明窗口外无解。
        bodyAvoidance="smd-pad-gap-v1"显式启用有限表面SMD本体间隙避让；同网/过孔也检查，保存后复核新增铜。
        routingStyle默认整齐走线：每个固定接点间线链最多增加min(2mm,15%)以减少短折返/碎弯，bendPenaltyMm=0.25使搜索记录进入方向，padExitLengthMm默认0.15mm。焊盘从原生中心连续直出，真实原生圆形允许中心45度斜出，其余按板坐标水平/垂直；禁止偏心及焊盘内部平移出口，越过原生铜边界至少0.15mm后再转弯，短于该值的参数不能降低硬约束。直接直线连接邻近焊盘/过孔且无平面转弯可短于该长度。网格新转向最多45度，bendPenaltyMm=0不取消该限制；保存后与手动编辑共用cornerPolicy及padExitPolicy，新增裸露直角/锐角、不合规出口或过早转弯均拒绝。routingQuality及padExits单列既有问题和未覆盖焊盘，局部提案通过不代表整板美观完成。
        省略bodyAvoidance保持旧间隙避让策略。layoutFeedback绑定源版本/SHA，提供覆盖/未覆盖原因、既有铜冲突及搜索实际受阻位号；不移动器件，不是完整本体或装配检查。
        """
        return await service.start_job(project_id,'pcb.route',request.model_dump(exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def pcb_pair_route(project_id: str, request: RoutePair) -> dict[str, Any]:
        """按Agent明确的四焊盘、公共中心路径和几何限值生成单层成组走线。

        原生障碍/双线间距、总长/偏差/扇出、保存后端点物理连接及既有契约继续验证。
        已有同名段可用replaceSection明确旧契约expectedHash和reason，原子替换其拥有的直铜段；保持网络/极性/四端点及全部原物理连接。无候选不解除旧约束，不创建断开的中间版本。
        可选trimBranches须按对象摘要明确接入旧段的支路、裁短端和原直段内部的新位置；不可延长/改网/隐式删除。所查网络仍有未排除悬空铜端则不采纳。
        不隐式拆旧铜；无候选不改版本。不支持成对过孔/蛇形调谐，不验证阻抗或整网时延。
        """
        return await service.start_job(project_id,'pcb.pair_route',request.model_dump(exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def pcb_sync(project_id: str, request: SyncPcb) -> dict[str, Any]:
        """当前原理图重审/交接后同步明确的旧PCB基线，保留全部铜对象。

        expectedPadChanges完整声明焊盘网络变化；相同入口/SHA、祖先关系、当前审查及原生一致性均须通过。
        addedPlacements明确新增器件；footprintReplacements绑定旧对象SHA、新库/SHA及全部同针号实体接点映射。
        替换默认保留物理连接，releasedPadIds只可明确释放本器件的接点，必须附原因；不自动删铜或解除成组契约。
        """
        return await service.start_job(project_id,'pcb.sync',request.model_dump(exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def pcb_reference_check(project_id: str, request: ReferenceCheck) -> dict[str, Any]:
        """只读核对保存的真实地铜、原生锚点连接和所选信号投影覆盖。

        每个填充岛独立核对原生锚点分量和铜接触；不以同网名/区外框代表参考地。结果不证明阻抗、回流电流仿真或制造通过。
        terminalAntipads默认关闭；启用须声明原因及最大返回距离、净距、原生误差（mm）。仅受控、独占且真实接触的终端孔可计为满足，原始缺口仍在strictMissingTrackCount和逐段定位中保留。
        同版本发布JSON与pcb_reference_preview诊断SVG；该定位图不是制造图。
        """
        return await service.start_job(project_id,'pcb.reference_check',request.model_dump(exclude={'requestId'}),request.requestId)

    @mcp.tool()
    async def job_get(job_id: str) -> dict[str, Any]:
        """查询已保存的任务状态和工程检查结果；completed 不等于 checksPassed。"""
        return service.store.get("jobs", job_id)

    @mcp.tool()
    async def job_list(project_id: str) -> list[dict]:
        """列出工程的任务历史。"""
        return service.store.list("jobs", project_id)

    @mcp.tool()
    async def job_cancel(job_id: str) -> dict[str, Any]:
        """请求停止本服务拥有的任务；等待 job_get 返回终态确认，不删除历史。"""
        return service.cancel(job_id)

    @mcp.tool()
    async def artifact_list(project_id: str) -> list[dict]:
        """列出文件摘要、类型和所属任务/版本。"""
        return service.store.list("artifacts", project_id)

    @mcp.tool()
    async def artifact_read(artifact_id: str) -> dict[str, Any]:
        """校验摘要后读取最多 20000 字符的报告或日志，图形产物返回元数据。"""
        return service.read_artifact(artifact_id)

    @mcp.resource("formforge://capabilities")
    def capabilities_resource() -> str:
        return json.dumps(service.capabilities(), ensure_ascii=False)

    from .rendering.transport import register_mcp as register_drawing_mcp
    from .schematic_candidates import register_mcp as register_schematic_candidates
    register_schematic_candidates(mcp,service)
    register_drawing_mcp(mcp, service)
    return mcp

