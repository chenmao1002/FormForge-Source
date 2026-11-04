from __future__ import annotations

import asyncio
import secrets
import json
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from .config import Config
from .mcp_gateway import create_mcp
from .mcp_i18n import LocalizedMcpApp, LocalizedApiApp
from .module_settings import request_params
from .rule_presets import SaveRulePreset, ApplyRulePreset
from .schemas import CreateProject, EditSchematic, ImportProject, InspectProject, UpdateProject, RenameProject, ImportLibraryRequest, ProbeLibraryRequest, InitializeSchematic, ReviewSchematic, PcbHandoff, InitializePcb, EditPcb, RoutePcb, ReferenceCheck, SyncPcb, RoutePair, DesignMechanical, ExportPcbGeometry, CheckInsertion
from .service import Service
from .pcb_planning_schemas import PlanPcb
from .store import DomainError
from .agent_workflow import WorkflowUpdate
from .product_concepts import ConceptUpdate, ConceptSelection
from .source_schemas import SourceTransfer
from .firmware_schemas import FirmwareDesign
from .firmware_flash_schemas import FlashBundleCreate, FlashPlanCreate, FlashInternalCreate, FlashDeviceRequest
from .manufacturing_schemas import PrepareManufacturing
from .procurement_schemas import PlanProcurement
from .external_cam_schemas import ExternalManufacturingReview,ExternalManufacturingProbe
from .order_schemas import AdoptExistingOrder, AppendOrderObservation, AdoptExistingSmtOrder, AppendSmtOrderObservation
from .jlc_schemas import JlcQuoteRequest,JlcSyncRequest
from .structural_schemas import PrepareStructural
from .structural_quote_schemas import CreateStructuralQuote,AppendStructuralQuoteObservation
from .component_schemas import ComponentQuery
from .component_library_schemas import LocalLibraryProbe,FreezeLocalLibrary
from .supplier_schemas import SupplierSessionRequest, SupplierAttemptRequest, SupplierStepRequest
from .desktop_schemas import DesktopBrowserRequest
from .playwright_browser import PlaywrightBrowserRequest
from .print_orders import PrintOrderRequest
from . import __version__
from .maintenance_schemas import CleanupRequest, ProjectRemovalRequest, EmptyTrashRequest
from .project_transfer import ProjectExportRequest, ProjectPackageImportRequest
from .settings_schemas import ToolchainActivationRequest, ToolchainProfileRequest, WorkspaceDefaultsUpdate


class LocalBoundary:
    def __init__(self, app, config):
        self.app, self.config = app, config

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        host = headers.get("host", "")
        allowed_hosts = {f"127.0.0.1:{self.config.port}", f"localhost:{self.config.port}"}
        origin = headers.get("origin")
        expected_origins = {"http://" + h for h in allowed_hosts}
        reason = None
        session_required = False
        if host not in allowed_hosts:
            reason = "不允许的本机主机名。"
        elif origin and origin not in expected_origins:
            reason = "不允许跨站访问本地工程服务。"
        path = scope["path"]
        bearer = headers.get("authorization", "").removeprefix("Bearer ")
        bearer_ok = secrets.compare_digest(bearer, self.config.token)
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
            cookie = cookies.get("formforge_session")
            cookie_ok = bool(cookie and secrets.compare_digest(cookie.value, self.config.token))
        except Exception:
            cookie_ok = False
        if path == "/api/session":
            if origin != "http://" + host or headers.get("sec-fetch-site") != "same-origin":
                reason = "请从本机 FormForge 页面建立会话。"
        elif path.startswith("/api/") or path == "/ws" or path.startswith("/mcp"):
            if not (bearer_ok or (cookie_ok and not path.startswith("/mcp"))):
                reason = "需要有效的本机会话或 MCP 访问令牌。"
                # A browser may recover this rejection before any handler ran.
                # Never advertise that recovery to cross-site callers or MCP.
                session_required = (path.startswith('/api/') and host in allowed_hosts
                                    and origin in (None, 'http://' + host)
                                    and headers.get('sec-fetch-site') == 'same-origin'
                                    and (scope.get('method') in ('GET', 'HEAD') or origin == 'http://' + host)
                                    and not bearer)
            elif scope["type"] == "websocket" and origin != "http://" + host:
                reason = "实时连接需要同源网页。"
            elif scope.get("method") not in (None, "GET", "HEAD") and not bearer_ok and origin != "http://" + host:
                reason = "修改操作需要同源网页。"
        if path.startswith('/api/procurement/approvals/') and scope.get('method') == 'POST':
            if bearer or not cookie_ok or origin != 'http://' + host or headers.get('sec-fetch-site') != 'same-origin':
                reason = '提交确认仅接受本机工作台会话，请用户在网页确认；MCP令牌不可签发授权。'
        length = headers.get("content-length", "0")
        if not length.isdigit() or int(length) > 1024 * 1024:
            reason = "请求过大，请使用工程目录导入。"
            session_required = False
        if reason:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                result = {"error": "access_denied", "message": reason}
                if session_required:
                    result['reasonCode'] = 'session_required'
                await JSONResponse(result, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_app(config=None):
    config = config or Config.default()
    service = Service(config)
    mcp = create_mcp(service)

    @asynccontextmanager
    async def lifespan(app):
        service.store.recover()
        service.supplier_executor.recover()
        async with mcp.session_manager.run():
            yield
            await service.close()

    app = FastAPI(title="FormForge", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.add_middleware(LocalBoundary, config=config)
    app.add_middleware(LocalizedApiApp)
    from .rendering.transport import register_http as register_drawing_http
    from .schematic_candidates import register_http as register_schematic_candidates
    register_schematic_candidates(app,service)
    register_drawing_http(app, service)

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)

    @app.get("/health")
    async def health():
        return {"ok": True, "app": "FormForge", "version": __version__}

    @app.post("/api/session")
    async def session():
        response = JSONResponse({"ok": True})
        response.set_cookie("formforge_session", config.token, httponly=True, samesite="strict", max_age=28800)
        return response

    @app.get("/api/capabilities")
    async def capabilities():
        return service.capabilities()

    @app.get('/api/settings')
    async def settings():
        return JSONResponse(service.settings.snapshot(), headers={'Cache-Control': 'no-store'})

    @app.get('/api/settings/storage')
    async def settings_storage():
        return JSONResponse(await service.settings.storage(), headers={'Cache-Control': 'no-store'})

    @app.get('/api/projects/{id}/module-settings')
    async def project_module_settings(id: str):
        return service.settings.project_modules(id)

    @app.get('/api/settings/rule-presets')
    async def rule_presets_read():
        return service.rule_presets.read()

    @app.post('/api/settings/rule-presets')
    async def rule_presets_save(body: SaveRulePreset):
        return service.rule_presets.save(body)

    @app.post('/api/projects/{id}/rule-presets/apply')
    async def rule_presets_apply(id: str, body: ApplyRulePreset):
        return service.rule_presets.apply(id,body)

    @app.put('/api/settings/defaults')
    async def settings_defaults(body: WorkspaceDefaultsUpdate):
        return JSONResponse(await asyncio.to_thread(service.settings.update_defaults, body),
                            headers={'Cache-Control': 'no-store'})

    @app.post('/api/settings/toolchains')
    async def settings_toolchain_add(body: ToolchainProfileRequest):
        return JSONResponse(await asyncio.to_thread(service.settings.add_toolchain, body),
                            headers={'Cache-Control': 'no-store'})

    @app.put('/api/settings/toolchains/active')
    async def settings_toolchain_activate(body: ToolchainActivationRequest):
        return JSONResponse(await asyncio.to_thread(service.settings.activate_toolchain, body),
                            headers={'Cache-Control': 'no-store'})

    @app.get('/api/settings/cleanup')
    async def cleanup_status():
        return JSONResponse(service.maintenance.status(), headers={'Cache-Control': 'no-store'})

    @app.post('/api/settings/cleanup/scan')
    async def cleanup_scan():
        return await service.maintenance.scan()

    @app.post('/api/settings/cleanup/run')
    async def cleanup_run(body: CleanupRequest):
        return await service.maintenance.clean(body.scanId, body.itemIds)

    @app.get('/api/settings/cleanup/history')
    async def cleanup_history():
        return await asyncio.to_thread(service.maintenance.history)

    @app.get('/api/settings/project-trash')
    async def project_trash():
        return await asyncio.to_thread(service.maintenance.trash)

    @app.post('/api/settings/project-trash/empty')
    async def project_trash_empty(body: EmptyTrashRequest):
        return await service.maintenance.empty_trash([row.model_dump() for row in body.projects])

    @app.post('/api/projects/{id}/delete')
    async def project_delete(id: str, body: ProjectRemovalRequest):
        return await asyncio.to_thread(service.maintenance.project_change, id, body.expectedVersion, body.confirmName)

    @app.post('/api/projects/{id}/restore')
    async def project_restore(id: str, body: ProjectRemovalRequest):
        return await asyncio.to_thread(service.maintenance.project_change, id, body.expectedVersion, body.confirmName, True)

    @app.post('/api/projects/{id}/purge')
    async def project_purge(id: str, body: ProjectRemovalRequest):
        return await asyncio.to_thread(service.maintenance.project_purge, id, body.expectedVersion, body.confirmName)

    @app.get('/api/components/schema')
    async def component_schema():
        return await asyncio.to_thread(service.components.schema)

    @app.get('/api/supplier/session')
    async def supplier_session_read():
        return service.supplier_browser.profile()

    @app.get('/api/desktop/browser')
    async def desktop_browser_read():
        return await service.desktop_browser.status()

    @app.get('/api/desktop/browser/schema')
    async def desktop_browser_schema():
        return service.desktop_browser.schema()

    @app.post('/api/desktop/browser')
    async def desktop_browser_control(request:DesktopBrowserRequest):
        return await service.desktop_browser.control(request)

    @app.get('/api/manufacturing/print-order/schema')
    async def print_order_schema():
        return service.print_orders.schema()

    @app.post('/api/manufacturing/print-order')
    async def print_order_control(request:PrintOrderRequest):
        return await service.print_orders.control(request)

    @app.get('/api/procurement/browser')
    async def procurement_browser_read():
        return await service.playwright_browser.status()

    @app.get('/api/procurement/browser/schema')
    async def procurement_browser_schema():
        return service.playwright_browser.schema()

    @app.get('/api/procurement/approvals')
    async def submission_approvals(projectId: str):
        return await asyncio.to_thread(service.store.list, 'submission_approvals', projectId)

    @app.post('/api/procurement/approvals/{approval_id}/{decision}')
    async def submission_decision(approval_id: str, decision: str):
        if decision not in {'approve','deny'}:raise DomainError('invalid_input','未知确认操作。')
        return await asyncio.to_thread(service.playwright_browser.approvals.decide, approval_id, decision=='approve')

    @app.post('/api/procurement/browser')
    async def procurement_browser_control(request:PlaywrightBrowserRequest):
        return await service.playwright_browser.control(request)

    @app.post('/api/supplier/session')
    async def supplier_session(request:SupplierSessionRequest):
        if service.supplier_executor.active:raise DomainError('busy','浏览器步骤正在执行，请等待后再管理会话。',409)
        return await service.supplier_browser.command(request.action)

    @app.get('/api/supplier/schema')
    async def supplier_schema():
        return service.supplier_executor.schema()

    @app.get('/api/projects/{id}/supplier-attempts')
    async def supplier_attempts(id:str):
        return await asyncio.to_thread(service.supplier_executor.read,id)

    @app.get('/api/projects/{id}/supplier-attempts/{attemptId}')
    async def supplier_attempt_read(id:str,attemptId:str):
        return await asyncio.to_thread(service.supplier_executor.read,id,attemptId)

    @app.post('/api/projects/{id}/supplier-attempts')
    async def supplier_prepare(id:str,request:SupplierAttemptRequest):
        return await asyncio.to_thread(service.supplier_executor.prepare,id,request.model_dump(mode='json'))

    @app.post('/api/projects/{id}/supplier-attempts/{attemptId}/step')
    async def supplier_step(id:str,attemptId:str,request:SupplierStepRequest):
        return await service.supplier_executor.step(id,attemptId,request.model_dump(mode='json'))

    @app.get('/api/component-library/schema')
    async def component_library_schema():
        return await asyncio.to_thread(service.component_library.schema)

    @app.post('/api/component-library/probe')
    async def component_library_probe(request:LocalLibraryProbe):
        return await asyncio.to_thread(service.component_library.probe,request.model_dump(mode='json'))

    @app.get('/api/projects/{id}/component-library')
    async def component_library_read(id:str,jobId:str|None=None):
        return await asyncio.to_thread(service.component_library.read,id,jobId)

    @app.post('/api/projects/{id}/component-library/freeze')
    async def component_library_freeze(id:str,request:FreezeLocalLibrary):
        return await service.start_job(id,'components.freeze_local',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @app.get('/api/projects/{id}/components')
    async def component_read(id: str, jobId: str | None=None):
        return await asyncio.to_thread(service.components.read,id,jobId)

    @app.post('/api/projects/{id}/components/query')
    async def component_query(id: str, request: ComponentQuery):
        return await service.start_job(id,'components.query',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @app.get("/api/projects")
    async def projects():
        return service.store.list("projects")

    @app.post("/api/projects")
    async def create_project(body: CreateProject):
        return await asyncio.to_thread(service.create_project, body.name, body.description, body.preferences)

    @app.post("/api/project-imports")
    async def import_project_package(body: ProjectPackageImportRequest):
        return await asyncio.to_thread(service.project_transfer.import_package, body.packagePath, body.name)

    @app.post("/api/projects/{id}/exports")
    async def export_project_package(id: str, body: ProjectExportRequest):
        return await asyncio.to_thread(service.project_transfer.export, id, body.expectedVersion)

    @app.get("/api/project-exports/{exportId}/file")
    async def project_export_file(exportId: str):
        receipt, path = await asyncio.to_thread(service.project_transfer.export_file, exportId)
        return FileResponse(path, media_type="application/zip", filename=receipt["filename"],
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/projects/{id}")
    async def project(id: str, compact: bool=False, revisionId: str|None=None):
        return await asyncio.to_thread(service.context,id,compact,revisionId)

    @app.post('/api/projects/{id}/source-transfers')
    async def source_transfer(id: str, body: SourceTransfer):
        return await asyncio.to_thread(service.sources.transfer,id,body.model_dump())

    @app.get('/api/projects/{id}/sources')
    async def source_list(id: str, offset: int=0, limit: int=40, includeUploads: bool=False):
        return await asyncio.to_thread(service.sources.list,id,offset,limit,includeUploads)

    @app.get('/api/projects/{id}/sources/{sourceId}')
    async def source_read(id: str, sourceId: str, encoding: str='metadata', offset: int=0, limit: int=65536):
        return await asyncio.to_thread(service.sources.read,id,sourceId,encoding,offset,limit)

    @app.get('/api/projects/{id}/sources/{sourceId}/file')
    async def source_file(id: str, sourceId: str):
        row,path=await asyncio.to_thread(service.sources.get_record,id,sourceId)
        return FileResponse(path,media_type='application/octet-stream',filename=row['name'],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.post("/api/projects/{id}/rename")
    async def rename_project(id: str, body: RenameProject):
        return service.store.rename_project(id, body.expectedVersion, body.name)

    @app.put("/api/projects/{id}")
    async def update_project(id: str, body: UpdateProject):
        return service.store.update_project(id, body.expectedVersion, body.specification, body.preferences)

    @app.get('/api/projects/{id}/agent-workflow')
    async def agent_workflow_read(id: str):
        return await asyncio.to_thread(service.agent_workflow.read, id)

    @app.get('/api/projects/{id}/product-concepts')
    async def product_concepts_read(id: str):
        return await asyncio.to_thread(service.product_concepts.read,id)

    @app.put('/api/projects/{id}/product-concepts')
    async def product_concepts_update(id: str, body: ConceptUpdate):
        return await asyncio.to_thread(service.product_concepts.update,id,body)

    @app.post('/api/projects/{id}/product-concepts/selection')
    async def product_concepts_select(id: str, body: ConceptSelection):
        return await asyncio.to_thread(service.product_concepts.select,id,body)

    @app.get('/api/projects/{id}/concept-images/{sourceId}')
    async def concept_image(id: str, sourceId: str):
        state=await asyncio.to_thread(service.product_concepts.read,id)
        if sourceId not in {r['image']['sourceId'] for r in state['references']+state['concepts']}:
            raise DomainError('invalid_input','图片不属于当前概念方案。')
        row,path=await asyncio.to_thread(service.sources.get_record,id,sourceId)
        from PIL import Image
        with Image.open(path) as im: mime='image/png' if im.format=='PNG' else 'image/jpeg'
        return FileResponse(path,media_type=mime,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.put('/api/projects/{id}/agent-workflow')
    async def agent_workflow_update(id: str, body: WorkflowUpdate):
        return await asyncio.to_thread(service.agent_workflow.update, id, body)

    @app.get('/api/projects/{id}/schematic')
    async def schematic(id: str, revisionId: str | None = None, sheet: str | None = None,
                        offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=300)):
        return await asyncio.to_thread(service.schematic, id, revisionId, sheet, offset, limit)

    @app.post('/api/projects/{id}/schematic-edits')
    async def edit_schematic(id: str, body: EditSchematic):
        return await service.start_job(id, 'schematic.edit', body.model_dump(exclude={'requestId'}, exclude_none=True), body.requestId)

    @app.post('/api/projects/{id}/schematic-initializations')
    async def initialize_schematic(id: str, body: InitializeSchematic):
        return await service.start_job(id,'schematic.initialize',request_params(body,'schematic.initialize'),body.requestId)

    @app.get('/api/projects/{id}/schematic-connectivity')
    async def schematic_connectivity(id: str, revisionId: str | None = None, offset: int = 0, limit: int = 100):
        return await asyncio.to_thread(service.connectivity, id, revisionId, offset, limit)

    @app.post('/api/projects/{id}/schematic-reviews')
    async def review_schematic(id: str, body: ReviewSchematic):
        return await service.start_job(id, 'schematic.review', body.model_dump(exclude={'requestId'}), body.requestId)

    @app.post('/api/projects/{id}/pcb-handoffs')
    async def handoff_pcb(id: str, body: PcbHandoff):
        return await service.start_job(id, 'pcb.handoff', body.model_dump(exclude={'requestId'}), body.requestId)

    @app.get('/api/projects/{id}/symbol-libraries')
    async def symbol_libraries(id: str, revisionId: str | None = None, libraryId: str | None = None,
                               symbol: str | None = None, offset: int = 0, limit: int = 50):
        return await asyncio.to_thread(service.libraries, id, revisionId, libraryId, symbol, offset, limit)

    @app.get('/api/projects/{id}/pcb')
    async def read_pcb(id: str, revisionId: str | None = None, offset: int = Query(default=0,ge=0),limit: int = Query(default=100,ge=1,le=300)):
        return await asyncio.to_thread(service.board,id,revisionId,offset,limit)

    @app.post('/api/projects/{id}/pcb-initializations')
    async def initialize_pcb(id: str,body: InitializePcb):
        return await service.start_job(id,'pcb.initialize',body.model_dump(exclude={'requestId'}),body.requestId)

    @app.post('/api/projects/{id}/pcb-planning')
    async def plan_pcb(id: str,body: PlanPcb):
        return await asyncio.to_thread(service.pcb_planning,id,body.model_dump(exclude_none=True))

    @app.post('/api/projects/{id}/pcb-edits')
    async def edit_pcb(id: str,body: EditPcb):
        return await service.start_job(id,'pcb.edit',request_params(body,'pcb.edit'),body.requestId)

    @app.post('/api/projects/{id}/pcb-routes')
    async def route_pcb(id: str,body: RoutePcb):
        return await service.start_job(id,'pcb.route',body.model_dump(exclude={'requestId'}),body.requestId)

    @app.post('/api/projects/{id}/pcb-pair-routes')
    async def route_pair(id: str,body: RoutePair):
        return await service.start_job(id,'pcb.pair_route',body.model_dump(exclude={'requestId'}),body.requestId)

    @app.post('/api/projects/{id}/pcb-syncs')
    async def sync_pcb(id: str,body: SyncPcb):
        return await service.start_job(id,'pcb.sync',body.model_dump(exclude={'requestId'}),body.requestId)

    @app.post('/api/projects/{id}/pcb-reference-checks')
    async def reference_check(id: str,body: ReferenceCheck):
        return await service.start_job(id,'pcb.reference_check',body.model_dump(exclude={'requestId'}),body.requestId)

    @app.post('/api/symbol-library-probe')
    async def symbol_library_probe(body: ProbeLibraryRequest):
        return await asyncio.to_thread(service.probe_library, body.sourcePath,body.projectId)

    @app.get('/api/projects/{id}/pcb-geometry')
    async def pcb_geometry_read(id: str,revisionId: str | None=None):
        return await asyncio.to_thread(service.pcb_geometry.read,service.revision(id,revisionId))

    @app.post('/api/projects/{id}/pcb-geometry-exports')
    async def pcb_geometry_export(id: str,body: ExportPcbGeometry):
        return await service.start_job(id,'pcb.geometry_export',body.model_dump(exclude={'requestId'}),body.requestId)

    @app.get('/api/mechanical-schema')
    async def mechanical_schema(kind: str):
        return await asyncio.to_thread(service.mechanical.schema,kind)

    @app.get('/api/procurement-schema')
    async def procurement_schema(projectId:str|None=None,manufacturingJobId:str|None=None):
        return await asyncio.to_thread(service.procurement.schema,projectId,manufacturingJobId)

    @app.get('/api/projects/{id}/procurement')
    async def procurement_read(id:str,revisionId:str|None=None,offset:int=0,limit:int=100):
        return await asyncio.to_thread(service.procurement.read,id,revisionId,offset,limit)

    @app.post('/api/projects/{id}/procurement-plans')
    async def procurement_plan(id:str,request:PlanProcurement):
        return await service.start_job(id,'manufacturing.procurement',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @app.get('/api/manufacturing-profile')
    async def manufacturing_profile():
        return service.manufacturing.profile()

    @app.get('/api/projects/{id}/manufacturing')
    async def manufacturing_read(id:str,jobId:str|None=None):
        return await asyncio.to_thread(service.manufacturing.read,id,jobId)

    @app.post('/api/projects/{id}/manufacturing-preparations')
    async def manufacturing_prepare(id:str,request:PrepareManufacturing):
        return await service.start_job(id,'manufacturing.prepare',request_params(request,'manufacturing.prepare'),request.requestId)

    @app.get('/api/structural-quote-schema')
    async def structural_quote_schema(projectId:str|None=None,jobId:str|None=None):
        return await asyncio.to_thread(service.structural_quotes.schema,projectId,jobId)

    @app.get('/api/projects/{id}/structural-quotes')
    async def structural_quote_read(id:str,quoteId:str|None=None,offset:int=0,limit:int=40):
        return await asyncio.to_thread(service.structural_quotes.read,id,quoteId,offset,limit)

    @app.post('/api/projects/{id}/structural-quotes')
    async def structural_quote_create(id:str,request:CreateStructuralQuote):
        return await asyncio.to_thread(service.structural_quotes.create,id,request.model_dump(mode='json'))

    @app.post('/api/projects/{id}/structural-quotes/{quoteId}/observations')
    async def structural_quote_observe(id:str,quoteId:str,request:AppendStructuralQuoteObservation):
        return await asyncio.to_thread(service.structural_quotes.observe,id,quoteId,request.model_dump(mode='json'))

    @app.get('/api/structural-schema')
    async def structural_schema(projectId:str|None=None,revisionId:str|None=None):
        return await asyncio.to_thread(service.structural.schema,projectId,revisionId)

    @app.get('/api/projects/{id}/structural')
    async def structural_read(id:str,jobId:str|None=None):
        return await asyncio.to_thread(service.structural.read,id,jobId)

    @app.post('/api/projects/{id}/structural-preparations')
    async def structural_prepare(id:str,request:PrepareStructural):
        return await service.start_job(id,'manufacturing.mechanical_prepare',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @app.get('/api/jlc-schema')
    async def jlc_schema(projectId:str|None=None,manufacturingJobId:str|None=None):
        return await asyncio.to_thread(service.jlc.schema,projectId,manufacturingJobId)

    @app.get('/api/jlc-profile')
    async def jlc_profile():
        return await asyncio.to_thread(service.jlc.profile)

    @app.put('/api/jlc-configuration')
    async def jlc_configuration(request:Request):
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>16384:raise DomainError('invalid_input','接口配置超过长度限制。')
        try:
            from .adapters.jlc_client import unique_object
            value=json.loads(raw.decode('utf8'),object_pairs_hook=unique_object)
        except (ValueError,UnicodeError):raise DomainError('invalid_input','接口配置须为有效JSON，不回显密钥。') from None
        return await asyncio.to_thread(service.jlc_vault.save,value)

    @app.get('/api/projects/{id}/jlc')
    async def jlc_read(id:str,jobId:str|None=None):
        return await asyncio.to_thread(service.jlc.read,id,jobId)

    @app.post('/api/projects/{id}/jlc-quotes')
    async def jlc_quote(id:str,request:JlcQuoteRequest):
        return await service.start_job(id,'manufacturing.jlc_quote',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @app.post('/api/projects/{id}/jlc-order-syncs')
    async def jlc_sync(id:str,request:JlcSyncRequest):
        return await service.start_job(id,'manufacturing.jlc_sync',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @app.get('/api/order-schema')
    async def order_schema():
        return service.orders.schema()

    @app.get('/api/smt-order-schema')
    async def smt_order_schema():
        return service.orders.smt_schema()

    @app.post('/api/projects/{id}/smt-orders/adopt-existing')
    async def smt_order_adopt_existing(id:str, request:AdoptExistingSmtOrder):
        return await asyncio.to_thread(service.orders.adopt_smt,id,request.model_dump(mode='json'))

    @app.post('/api/projects/{id}/smt-orders/{orderId}/observations')
    async def smt_order_observe(id:str, orderId:str, request:AppendSmtOrderObservation):
        return await asyncio.to_thread(service.orders.observe_smt,id,orderId,request.model_dump(mode='json'))

    @app.get('/api/projects/{id}/orders')
    async def orders_read(id:str, orderId:str|None=None, offset:int=0, limit:int=40, latest:bool=False):
        return await asyncio.to_thread(service.orders.read,id,orderId,offset,limit,latest)

    @app.post('/api/projects/{id}/orders/adopt-existing')
    async def order_adopt_existing(id:str, request:AdoptExistingOrder):
        return await asyncio.to_thread(service.orders.adopt,id,request.model_dump(mode='json'))

    @app.post('/api/projects/{id}/orders/{orderId}/observations')
    async def order_observe(id:str, orderId:str, request:AppendOrderObservation):
        return await asyncio.to_thread(service.orders.observe,id,orderId,request.model_dump(mode='json'))

    @app.get('/api/external-manufacturing-schema')
    async def external_manufacturing_schema():
        return service.external_cam.schema()

    @app.post('/api/projects/{id}/external-manufacturing-schema')
    async def external_manufacturing_probe(id:str,request:ExternalManufacturingProbe):
        return await asyncio.to_thread(service.external_cam.schema,id,
            request.archive.model_dump() if request.archive else None,request.table.model_dump() if request.table else None)

    @app.get('/api/projects/{id}/external-manufacturing')
    async def external_manufacturing_read(id:str,jobId:str|None=None,offset:int=0,limit:int=100):
        return await asyncio.to_thread(service.external_cam.read,id,jobId,offset,limit)

    @app.post('/api/projects/{id}/external-manufacturing-reviews')
    async def external_manufacturing_review(id:str,request:ExternalManufacturingReview):
        return await service.start_job(id,'manufacturing.external_review',request.model_dump(mode='json',exclude={'requestId'}),request.requestId)

    @app.get('/api/firmware-profile')
    async def firmware_profile():
        return service.firmware.profile()

    @app.get('/api/firmware-flash-profile')
    async def firmware_flash_profile():
        return await asyncio.to_thread(service.firmware_flash.profile)

    @app.get('/api/projects/{id}/flash-bundles')
    async def firmware_flash_list(id:str):
        return await asyncio.to_thread(service.firmware_flash.list,id)

    @app.get('/api/projects/{id}/flash-build-artifacts')
    async def firmware_flash_builds(id:str):
        return await asyncio.to_thread(service.firmware_flash.internal_candidates,id)

    @app.post('/api/projects/{id}/flash-internal-bundles')
    async def firmware_flash_internal(id:str,body:FlashInternalCreate):
        return await asyncio.to_thread(service.firmware_flash.create_internal,id,body.model_dump())

    @app.post('/api/projects/{id}/flash-device-jobs')
    async def firmware_flash_device(id:str,body:FlashDeviceRequest):
        fields=body.model_dump(); request_id=fields.pop('requestId')
        return await service.start_job(id,'firmware.device',dict(fields,requestId=request_id),request_id)

    @app.post('/api/projects/{id}/flash-bundles')
    async def firmware_flash_bundle_create(id:str,body:FlashBundleCreate):
        return await asyncio.to_thread(service.firmware_flash.create_bundle,id,body.model_dump())

    @app.post('/api/projects/{id}/flash-plans')
    async def firmware_flash_plan(id:str,body:FlashPlanCreate):
        return await asyncio.to_thread(service.firmware_flash.create_plan,id,body.model_dump())

    @app.get('/api/projects/{id}/flash-bundles/{bundle_id}')
    async def firmware_flash_bundle_read(id:str,bundle_id:str):
        return await asyncio.to_thread(service.firmware_flash.read,id,'flash_bundles',bundle_id)

    @app.get('/api/projects/{id}/flash-plans')
    async def firmware_flash_plan_list(id:str):
        return dict(plans=await asyncio.to_thread(service.store.list,'flash_plans',id))

    @app.get('/api/projects/{id}/flash-plans/{plan_id}')
    async def firmware_flash_plan_read(id:str,plan_id:str):
        return await asyncio.to_thread(service.firmware_flash.read,id,'flash_plans',plan_id)

    @app.get('/api/projects/{id}/firmware-hardware')
    async def firmware_hardware_read(id:str,revisionId:str|None=None,targetReference:str='U1',checkJobId:str|None=None):
        revision=service.revision(id,revisionId)
        return await asyncio.to_thread(service.firmware.hardware_read,id,revision['id'],targetReference,checkJobId)

    @app.get('/api/projects/{id}/firmware')
    async def firmware_read(id:str,revisionId:str|None=None,file:str|None=None,offset:int=0,limit:int=12000):
        return await asyncio.to_thread(service.firmware.read,id,revisionId,file,offset,limit)

    @app.post('/api/projects/{id}/firmware-designs')
    async def firmware_design(id:str,body:FirmwareDesign):
        return await service.start_job(id,'firmware.design',request_params(body,'firmware.design'),body.requestId)

    @app.get('/api/projects/{id}/mechanical')
    async def mechanical_read(id: str,revisionId: str | None=None):
        return await asyncio.to_thread(service.mechanical_read,id,revisionId)

    @app.post('/api/projects/{id}/mechanical-designs')
    async def mechanical_design(id: str,body: DesignMechanical):
        return await service.start_job(id,'mechanical.design',request_params(body,'mechanical.design'),body.requestId)

    @app.post('/api/projects/{id}/insertion-checks')
    async def insertion_check(id:str,body:CheckInsertion):
        return await service.start_job(id,'mechanical.insertion_check',body.model_dump(exclude={'requestId'}),body.requestId)

    @app.post('/api/projects/{id}/symbol-libraries')
    async def import_symbol_library(id: str, body: ImportLibraryRequest):
        return await service.import_library(id, body)

    @app.get('/api/projects/{id}/revision-comparison')
    async def revision_comparison(id: str, before: str, after: str):
        return await asyncio.to_thread(service.compare, id, before, after)

    @app.post("/api/projects/{id}/imports")
    async def import_eda(id: str, body: ImportProject):
        return await service.start_job(id, "eda.import", body.model_dump(exclude={"requestId"}), body.requestId)

    @app.post("/api/projects/{id}/inspections")
    async def inspect_eda(id: str, body: InspectProject):
        return await service.start_job(id, "eda.inspect", body.model_dump(exclude={"requestId"},exclude_none=True), body.requestId)

    @app.get("/api/jobs/{id}")
    async def job(id: str):
        return service.store.get("jobs", id)

    @app.post("/api/jobs/{id}/cancel")
    async def cancel(id: str):
        return service.cancel(id)

    @app.get("/api/artifacts/{id}/content")
    async def content(id: str):
        return service.read_artifact(id)

    @app.get("/api/artifacts/{id}/file")
    async def artifact_file(id: str, download: bool = False):
        artifact, path = await asyncio.to_thread(service.artifact_path, id)
        return FileResponse(path, media_type=artifact["mimeType"], filename=artifact["name"] if download else None,
                            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
                                     "Content-Security-Policy": "default-src 'none'; sandbox"})

    @app.get("/api/events")
    async def events(after: int = Query(default=0, ge=0), projectId: str | None = None):
        return service.store.events(after, projectId)

    @app.get('/api/event-snapshot')
    async def event_snapshot(projectId: str | None = None, limit: int = Query(default=40,ge=1,le=150)):
        return service.store.event_snapshot(projectId,limit)

    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        try:
            after = max(0, int(websocket.query_params.get("after", "0")))
        except ValueError:
            await websocket.close(code=1008)
            return
        project = websocket.query_params.get("projectId")
        await websocket.accept()
        try:
            while True:
                batch = service.store.events(after, project)
                if batch:
                    after = batch[-1]["seq"]
                    await websocket.send_json({"type": "events", "events": batch, "cursor": after})
                else:
                    await websocket.send_json({"type": "heartbeat", "cursor": after})
                await asyncio.sleep(0.5)
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass

    @app.get("/")
    async def index():
        path = config.root / "web" / "index.html"
        if not path.exists():
            return Response("FormForge 前端尚未构建。", status_code=503)
        return FileResponse(path, headers={"Cache-Control": "no-cache"})

    if (config.root / "web").exists():
        app.mount("/web", StaticFiles(directory=config.root / "web"), name="web")
    app.mount("/", LocalizedMcpApp(mcp.streamable_http_app()))
    return app
