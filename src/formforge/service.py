from __future__ import annotations

import asyncio
import copy
from collections import OrderedDict
import json
import mimetypes
import threading
import time
import zipfile
from pathlib import Path

from .adapters.importer import digest, import_project, within, verify_snapshot
from .adapters.kicad import Cancelled, KiCadAdapter
from .adapters.schematic import compare_revisions, create_candidate, read_schematic
from .adapters.schematic_read_cache import SchematicReadCache
from .adapters.connectivity import read_graph, expected_graph, compare_graphs, assemble, fingerprint
from .adapters.initialize import initialize_schematic
from .adapters.design_review import review as review_design, handoff as pcb_handoff
from .adapters.pcb import PcbAdapter, draft_result, finalize_checks, pad_graph, routing_proof, pair_proof, text_proof, board_text_proof, sync_proof, rework_drc_proof, initialization_replacement
from .adapters.pcb_read_session import PcbReadSession
from .adapters.pcb_source_connectivity import proof as source_connectivity_proof
from .job_timings import JobStageTimer
from .adapters.schematic_fields import AGENT_INSTRUCTIONS as SCHEMATIC_AGENT_INSTRUCTIONS
from .adapters.schematic_policy import defaults as schematic_presentation_defaults
from .adapters.pcb_silkscreen import proof as silkscreen_proof
from .adapters.board_stackup import proof as stackup_proof
from .adapters.reference_preview import render as reference_preview
from .adapters.routing_rules import PCB_AGENT_INSTRUCTIONS, PCB_WORKFLOW_POLICY
from .adapters.mechanical import MechanicalAdapter
from .adapters.pcb_geometry import PcbGeometryAdapter
from .adapters.insertion import InsertionAdapter
from .adapters.topology import TOPOLOGY_OPERATIONS
from .adapters.symbol_library import read_libraries, probe_library
from .store import DomainError, Store, now, uid
from .source_registry import SourceRegistry, SUFFIXES
from .order_registry import OrderRegistry
from .jlc_credentials import JlcVault
from .adapters.jlc import JlcAdapter
from .adapters.components import ComponentAdapter
from .adapters.component_library import ComponentLibraryAdapter
from .module_tasks import ModuleTasks, ModuleTaskContext
from .adapters.firmware import FirmwareAdapter
from .firmware_flash import FirmwareFlash
from .adapters.firmware_device import FirmwareDevice
from .adapters.manufacturing import ManufacturingAdapter
from .adapters.procurement import ProcurementAdapter
from .adapters.external_cam import ExternalCamAdapter
from .adapters.structural import StructuralAdapter
from .structural_quote_registry import StructuralQuoteRegistry
from .supplier_session import SupplierBrowser
from .supplier_executor import SupplierExecutor
from .desktop_browser import DesktopBrowser
from .playwright_browser import PlaywrightBrowser
from .print_orders import PrintOrders
from .settings import WorkspaceSettings
from .maintenance import WorkspaceMaintenance
from .project_transfer import ProjectTransfer
from . import native_pcb_workflow
from . import __version__
from .agent_workflow import AgentWorkflow, workflow_context, AGENT_INSTRUCTIONS as WORKFLOW_AGENT_INSTRUCTIONS
from .product_concepts import ProductConcepts, concept_context
from .process_review import process_review, pcb_read_coverage


class Service:
    def __init__(self, config):
        self.config = config
        self.store = Store(config.data / "formforge.sqlite3")
        self.sources = SourceRegistry(config, self.store)
        self.orders = OrderRegistry(config, self.store, self.sources)
        self.jlc_vault=JlcVault(config)
        self.jlc=JlcAdapter(config,self.store,self.sources,self.orders,self.jlc_vault)
        self.orders.api_profile=self.jlc.profile
        self.eda = KiCadAdapter(config)
        self.pcb = PcbAdapter(config)
        # Reuse a native read only for later pages of an explicitly pinned
        # revision. First pages and all design/check jobs still read natively.
        self._board_page_snapshots = OrderedDict()
        self._board_page_lock = threading.Lock()
        self._schematic_read_cache = SchematicReadCache()
        self.mechanical = MechanicalAdapter(config)
        self.pcb_geometry = PcbGeometryAdapter(config,self.mechanical)
        self.insertion=InsertionAdapter(config,self.mechanical)
        self.firmware=FirmwareAdapter(config,self.store)
        self.firmware_flash=FirmwareFlash(config,self.store,self.sources)
        self.module_tasks=ModuleTasks()
        self.components=ComponentAdapter(config,self.store,self.jlc_vault)
        self.module_tasks.register('components.query',self.components)
        self.component_library=ComponentLibraryAdapter(config,self.store,self.components)
        self.module_tasks.register('components.freeze_local',self.component_library)
        self.module_tasks.register('manufacturing.jlc_quote',self.jlc)
        self.module_tasks.register('manufacturing.jlc_sync',self.jlc)
        self.module_tasks.register('firmware.design',self.firmware)
        self.firmware_device=FirmwareDevice(config,self.store,self.firmware_flash)
        self.module_tasks.register('firmware.device',self.firmware_device)
        self.manufacturing=ManufacturingAdapter(config,self.store)
        self.module_tasks.register('manufacturing.prepare',self.manufacturing)
        self.procurement=ProcurementAdapter(config,self.store)
        self.module_tasks.register('manufacturing.procurement',self.procurement)
        self.external_cam=ExternalCamAdapter(config,self.store)
        self.module_tasks.register('manufacturing.external_review',self.external_cam)
        self.structural=StructuralAdapter(config,self.store,self.mechanical)
        self.module_tasks.register('manufacturing.mechanical_prepare',self.structural)
        self.structural_quotes=StructuralQuoteRegistry(config,self.store,self.sources,self.structural)
        self.tasks = {}
        self.supplier_browser=SupplierBrowser(config,on_change=self._supplier_session_changed)
        self.supplier_executor=SupplierExecutor(config,self.store,self.orders,self.supplier_browser)
        self.desktop_browser=DesktopBrowser(config)
        self.playwright_browser=PlaywrightBrowser(config,self.store)
        self.print_orders=PrintOrders(self.store,self.playwright_browser)
        self.cancellations = {}
        self.slots = asyncio.Semaphore(2)
        self.settings = WorkspaceSettings(self)
        from .rule_presets import RulePresets
        self.rule_presets = RulePresets(self)
        self.playwright_browser.workspace_settings = self.settings
        self.maintenance = WorkspaceMaintenance(self)
        self.project_transfer = ProjectTransfer(config, self.store)
        self.agent_workflow = AgentWorkflow(self.store)
        self.product_concepts = ProductConcepts(self.store,self.sources)

    def capabilities(self):
        return dict(app="FormForge", version=__version__, localOnly=True,
                    agentWorkflow=dict(persistent=True, independentVersion=True, incrementalUpdates=True,
                        tool='project_workflow_update', contextField='agentWorkflow',
                        agentInstructions=WORKFLOW_AGENT_INSTRUCTIONS, usabilityCertified=False),
                    mcp={"sdk": "mcp-python/1.27.2", "protocols": ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"],
                         "transport": "streamable-http", "endpoint": "/mcp", "longTasks": "job.get polling"},
                    supplierSession=self.supplier_browser.profile(),
                    supplierExecutor=self.supplier_executor.schema(),
                    desktopBrowser=self.desktop_browser.profile(),
                    printOrders=self.print_orders.profile(),
                    projectTransfer=self.project_transfer.profile(),
                    procurementBrowser=self.playwright_browser.profile(),
                    componentSupply=self.components.profile(),
                    componentLibrary=self.component_library.profile(),
                    modules=[dict(id="components", name="器件查询", status="exact-code-supply-observation", detail="按C编号查询SMT报告的器件身份和库存；独立快照，不改CAD或订单"),
                             dict(id="schematic", name="原理图", status="bounded-editing", detail="冻结符号库、器件与接线修改、预期连接、新版本及 ERC"),
                             dict(id="pcb", name="PCB", status="bounded-selected-net-routing", detail="原生板创建/编辑、自研普通选网续布、完整原生检查和物理连接反馈；草稿与制造分开"),
                             dict(id="mechanical", name="机械结构", status="bounded-native-cad" if not self.mechanical.issue else "unavailable", detail="通用参数特征建模与专用配方、STEP/GLB、网页参数及颜色修改；实体与独立回读检查，工程审查按需"),
                             dict(id="firmware", name="固件", status="bounded-native-build" if not self.firmware.compiler.issue else "unavailable", detail="独立硬件绑定、登记C/H编辑、ARM真实编译和ELF/HEX/BIN检查；行为/实物另列"),
                             dict(id="manufacturing", name="制造", status="review-available" if self.manufacturing.profile()['available'] or self.external_cam.profile()['available'] else "unavailable", detail="制造文件审核、采购与既有订单观察；独立Playwright支持Agent对抗性复核后提交PCB/SMT至待付款，无需人工确认卡；付款和生产放行不可用")],
                    adapters=[self.eda.describe(),self.mechanical.describe()], pcbEditing=self.pcb.describe(), mechanicalEditing=self.mechanical.describe(), insertionChecks=dict(policy='continuous-conservative-straight-translation',explicitDatum=True,readOnly=True,physicalCompatibilityCertified=False), pcbGeometry=dict(export='native-cli-step',explicitModelSources=True,completePcbaInferred=False), resumeSupported=False,
                    pcbPlanning=dict(tool='pcb_plan',readOnly=True,functionalGroups='explicit',nativeInterfaceProbe=True,explicitCorridors=True,routeEvidence='same-revision-and-board-hash',groupOnlyRead='native-pin-placement-only',interfaceProbe='automatic-unless-group-only-or-explicitly-disabled',agentWorkflowPolicy=PCB_WORKFLOW_POLICY),
                    pcbRouting=dict(scope='selected-ordinary-nets',movesFootprints='optional-bounded-declared-series-pair',removesExistingCopper='explicit-rework-or-local-pair-impact',
                        agentInstructions=PCB_AGENT_INSTRUCTIONS,cornerPolicy='no-new-exposed-sharp-corners-v1',
                        padExitPolicy='centered-native-pad-exit-v3',minimumPadExitStraightMm=.15,
                        placementFeedback='two-terminal-placement-review-v1',
                        savedCopperTangents=True,pairedRouteDiagnostics=True,maxGridCellsPerLayer=400000,
                        maxPairAttemptsPerConnectionDefault=12,maxPairAttemptsPerConnectionRange=[1,12],
                        searchHeuristicWeightDefault=1.,searchHeuristicWeightRange=[1.,2.],weightedSearchProvesOptimality=False,
                        search='self-developed-native-geometry-a-star',adoption='native-drc-and-copper-target-and-physical-progress',
                        differentialPairRouting=False,agentSpinePairedSections=True,impedanceVerified=False,referenceReturnVerified=False),
                    schematicEditing=dict(operations=['set_property', 'move_field', 'move_group', 'tidy_fields', 'connect_local_labels', 'set_text', 'move_text', 'reroute_wire', 'add_text', 'set_document_date', 'import_symbol_library', 'import_footprint', *sorted(TOPOLOGY_OPERATIONS)],
                        presentation=schematic_presentation_defaults(),
                        agentInstructions=SCHEMATIC_AGENT_INSTRUCTIONS,
                        localConnections=dict(policy='selected-local-label-wires-v1',maxLabels=80,maxLengthMmDefault=25.4,labelsRetained=True,connectivity='preserve'),
                        fieldArrangement=dict(policy='bounded-field-wire-avoidance-v1',scope='selected Reference/Value positions',maxSymbols=50,estimatedEnvelopes=True,electricalAnchorsMoved=False),
                        autoLayout=dict(rebuildSource='independent-single-sheet-ordinary-symbols',functionalPageLimit=9,
                            existingHierarchy='explicit-complete-block-translation',sharedInstances='preserved-with-explicit-impact',
                            newSharedSheetFiles=False,multiPinBranches='single-angle-explicit-driver-or-protection',
                            dataPreservation='native-classes-symbol-data-and-protected-files',readabilityGroups='declared-symbol-uuids-with-shared-and-unassigned-wire-buckets',netclassRebuild='hidden-label-fields-from-resolved-native-constituents',rootMetadata='reject-unhandled-rebuild-or-preserve-existing-blocks',
                            readabilityMeasures='selected-sheet-known-geometry-with-coverage-and-budget',nativeReviewRequired=True),
                        maxAtomicOperations=1500,maxReviewProperties=600,maxReviewConnections=200,paperSizes=['A4','A3','A2','A1','A0'],hierarchy='explicit-single-instance-pages-and-global-labels',
                        connectivity='preserve-or-explicit-replace-groups', adoption='erc-and-exact-target-match',
                        symbolInheritance=dict(scope='same-frozen-library-properties', maxChain=32, embeddedDefinition='flattened-with-sha256', multiUnit=False),
                        symbolSource=['same-sheet-template', 'frozen-project-library'],
                        libraryProvenance='declared-source-and-version; verified-sha256; immutable-revision',
                        requiresCurrentRevision=True, arbitraryConnectivityChanges=False,maxOperations=1500),
                    schematicCreation=dict(mode='empty-document-and-agent-planned-operations',requiresExplicitRequirements=True,
                        presentation=schematic_presentation_defaults(),agentInstructions=SCHEMATIC_AGENT_INSTRUCTIONS,
                        overwritesExistingDesign=False,nativeDocumentation=['schematic_pdf','bom']),
                    engineeringReview=dict(facts='agent-declared', arithmetic='bounded-deterministic', units='caller-declared',
                        pinReview='revision-sheet-hash-v1',
                        automaticDatasheetReasoning=False, pcbHandoff='current-revision-native-checks-and-review', hardwareProof=False),
                    sourceRegistry=dict(projectScoped=True, referenceScheme='source://', immutable=True, maxFileBytes=64*1024*1024,
                        maxChunkBytes=192*1024, projectQuotaBytes=512*1024*1024, allowedSuffixes=sorted(SUFFIXES),
                        provenanceTrust='caller-declared; original-bytes-sha256-verified', executesFiles=False, downloadsUris=False),
                    firmwareEditing=self.firmware.profile(), firmwareFlashing=self.firmware_flash.profile(), manufacturingPreparation=self.manufacturing.profile(),procurementPlanning=self.procurement.describe(),externalManufacturing=self.external_cam.profile(), orderTracking=self.orders.profile(),jlcOfficial=self.jlc.profile(),structuralManufacturing=self.structural.profile(),structuralQuotes=self.structural_quotes.profile(), designAutomation=False, orderingEnabled=True, paymentEnabled=False, productionReleaseEnabled=False,
                    ordering=dict(submitUnpaid=self.playwright_browser.profile(), printSubmitUnpaid=self.print_orders.profile(), payment=dict(supported=False), productionRelease=dict(supported=False)))

    def _supplier_session_changed(self,state):
        with self.store.tx() as db:
            self.store._event(db,'supplier.session',None,{k:state.get(k) for k in ['loginStatus','browserRunning','cacheSaved','savedAt','accountLabel']})

    def create_project(self, name, description='', preferences=None):
        effective = self.settings.new_project_preferences(preferences)
        return self.store.create_project(name, description, effective)

    def context(self, project_id, compact=False, revision_id=None):
        project = self.store.get('projects', project_id)
        project['headRevisionId'] = self.store.head_revision(project_id)
        revisions = self.store.context_summaries('revisions',project_id) if compact else self.store.list('revisions',project_id)
        revisions = sorted(revisions, key=lambda r: r['createdAt'], reverse=True)
        jobs = self.store.context_summaries('jobs',project_id) if compact else self.store.list('jobs',project_id)
        if compact:
            selected={project.get('headRevisionId'),*project.get('moduleHeads',{}).values()}
            if revision_id:selected.add(self.revision(project_id,revision_id)['id'])
            # Follow-latest also includes pending or rejected candidates.
            modules=set()
            for r in revisions:
                module=r.get('module') or 'eda'
                if module not in modules:selected.add(r['id']);modules.add(module)
            revisions=[self.store.get('revisions',r['id']) if r['id'] in selected else r for r in revisions]
            detail_jobs=set()
            for r in revisions:
                if r['id'] not in selected:continue
                detail_jobs.add(r.get('createdByJobId'))
                detail_jobs.update((r.get('pcbDesign') or {}).get(k) for k in ('handoffJobId','reviewJobId'))
            latest=set()
            for j in jobs:
                binding=(j.get('result') or {}).get('revisionId') or j.get('inputRevision')
                key=(j['kind'],j['status']=='completed',binding if binding in selected else None)
                if key not in latest:detail_jobs.add(j['id']);latest.add(key)
            jobs=[self.store.get('jobs',j['id']) if j['id'] in detail_jobs else j for j in jobs]
        return dict(project=project, revisions=revisions,
                    agentWorkflow=workflow_context(project),
                    productConcepts=concept_context(project),
                    processReview=process_review(project, jobs, revisions),
                    jobs=jobs, artifacts=self.store.list("artifacts", project_id),
                    sourceRegistry=self.sources.list(project_id, limit=12),
                    capabilities=self.capabilities())

    def revision(self, project_id, revision_id=None):
        revision_id = revision_id or self.store.head_revision(project_id)
        if not revision_id:
            raise DomainError('invalid_input', '工程尚无输入版本，请先导入。')
        revision = self.store.get('revisions', revision_id)
        if revision['projectId'] != project_id:
            raise DomainError('invalid_input', '版本不属于此工程。')
        return revision

    def mechanical_read(self,project_id,revision_id=None):
        project=self.store.get('projects',project_id);rid=revision_id or project.get('moduleHeads',{}).get('mechanical')
        if not rid:raise DomainError('not_found','工程尚无当前机械模型。')
        revision=self.revision(project_id,rid);result=self.mechanical.read(revision)
        job=self.store.get('jobs',revision['createdByJobId']);result['result']=job.get('result');result['currentMechanicalRevisionId']=project.get('moduleHeads',{}).get('mechanical')
        result['rebuildPcbBinding']={k:v for k,v in job.get('params',{}).items() if k in {'pcbRevisionId','expectedBoardHash','pcbGeometryJobId','pcbPart','pcbPassages','pcbMounts','pcbFastener'} and v is not None} if result['pcbDependency'] else {}
        dependency=result['pcbDependency'];changed=bool(dependency and self.store.head_revision(project_id)!=dependency['revisionId'])
        context_changed=revision['mechanicalDesign']['contextBinding']!=fingerprint({k:project[k] for k in ['specification','preferences']})
        result.update(requiresDependencyReview=changed or context_changed,pcbRevisionChanged=changed,requirementsOrPreferencesChanged=context_changed)
        return result

    def insertion_context(self,project_id,params):
        revision=self.revision(project_id,params['revisionId'])
        if revision.get('module')!='mechanical' or revision['mechanicalDesign']['designSha256']!=params['expectedDesignSha256']:raise DomainError('version_conflict','插拔检查需要明确同SHA机械版本。',409)
        job=self.store.get('jobs',revision['createdByJobId'])
        if job['projectId']!=project_id or job['kind']!='mechanical.design' or job['status']!='completed' or not (job.get('result') or {}).get('checksPassed'):raise DomainError('invalid_input','需要同工程、已完成且原生检查通过的机械版本。')
        artifacts=[a for a in self.store.list('artifacts',project_id) if a['jobId']==job['id'] and a['revisionId']==revision['id'] and a['kind']=='mechanical_project']
        if len(artifacts)!=1:raise DomainError('invalid_input','缺少唯一、同版本机械工程包。')
        return revision,job,artifacts[0]

    def mechanical_pcb_dependency(self,project_id,params):
        if not params.get('pcbRevisionId'):return None
        pcb=self.revision(project_id,params['pcbRevisionId'])
        if not pcb['entrypoints'].get('board'):raise DomainError('invalid_input','机械依赖必须是明确PCB版本。')
        source=within(self.config.data,pcb['relativePath']);verify_snapshot(source,pcb['manifest']);board=within(source,pcb['entrypoints']['board'])
        if digest(board)!=params['expectedBoardHash']:raise DomainError('version_conflict','所绑定PCB摘要与指定版本不一致。',409)
        dependency=dict(revisionId=pcb['id'],boardSha256=params['expectedBoardHash'],validation=pcb.get('validation'),geometryIncluded=False,
            note='Explicit PCB revision dependency only; PCB solids/connector geometry must be separately declared frozen STEP inputs. Not manufacturing approval.')
        if params.get('pcbGeometryJobId'):
            job=self.store.get('jobs',params['pcbGeometryJobId']);result=job.get('result') or {}
            if job['projectId']!=project_id or job['kind']!='pcb.geometry_export' or job['status']!='completed' or not result.get('checksPassed') or result['revisionId']!=pcb['id'] or result['boardSha256']!=params['expectedBoardHash']:
                raise DomainError('invalid_input','需要本工程同板版本/SHA、已完成并通过原生几何核对的交接。')
            design=params['design'];board=design.get('board') or {};proof=result['nativeProof'];substrate=proof['substrate']
            if design.get('kind')=='feature_model':
                if not params.get('pcbPart') or not result['coverage']['complete']:
                    raise DomainError('invalid_input','通用特征绑定须指定pcbPart，并使用完整器件覆盖的PCBA交接。')
                if params.get('pcbPassages') or params.get('pcbMounts') or params.get('pcbFastener'):
                    raise DomainError('invalid_input','通用PCBA绑定不套用enclosure专用窗口/支柱；请用真实工程接头和干涉检查。')
                from .adapters.mechanical_inputs import feature_pcba_binding
                sources={f"inputs/{r['id']}{Path(r['path']).suffix.lower()}":r for r in params.get('sources',[])}
                try:binding=feature_pcba_binding(design,params['pcbPart'],proof['view']['stepSha256'],sources)
                except (ValueError,KeyError,TypeError) as error:raise DomainError('invalid_input',str(error)) from error
                dependency.update(geometryIncluded=True,geometryJobId=job['id'],geometryStepSha256=proof['view']['stepSha256'],substrate=substrate,
                    coverage=result['coverage'],geometryParts=result['geometry']['parts'],partSolidIndices=proof['partSolidIndices'],
                    bindingKind='feature-model-pcba-v1',pcbaBinding=binding,
                    note='Complete PCBA source and output binding with PCB revision tracking. No automatic enclosure mount/window recipe; explicit native engineering checks remain required.')
                return dependency
            if design.get('kind')!='enclosure' or not board.get('step'):raise DomainError('invalid_input','首条几何绑定要求enclosure.board明确导入总装STEP，通用装配映射后续接入。')
            step_sha=proof['view']['stepSha256'];sources={f"inputs/{r['id']}{Path(r['path']).suffix.lower()}":r for r in params.get('sources',[])}
            source=sources.get(board['step'])
            if not source or source['sha256']!=step_sha or board.get('step_sha256')!=step_sha:raise DomainError('invalid_input','外壳的板STEP必须使用该交接的实际同SHA总装。')
            for key in ['length','width','thickness']:
                if not isinstance(board.get(key),(int,float)) or abs(board[key]-substrate[key])>.002:raise DomainError('invalid_input','板参数与交接实体不一致：'+key)
            if board.get('substrate_solid_index')!=proof['substrateSolidIndex'] or len(board.get('origin',[]))!=3 or any(abs(a-b)>.002 for a,b in zip(board['origin'],substrate['centerUndersideCADmm'])):raise DomainError('invalid_input','板原点/基体索引必须与原生几何证据一致。')
            dependency.update(geometryIncluded=True,geometryJobId=job['id'],geometryStepSha256=step_sha,substrate=substrate,coverage=result['coverage'],
                geometryParts=result['geometry']['parts'],partSolidIndices=proof['partSolidIndices'],passages=params.get('pcbPassages',[]),
                note='Native substrate and explicitly selected component solids imported into enclosure; incomplete model coverage and physical fit/manufacture remain separate.')
            mounts=params.get('pcbMounts',[]);holes=board.get('mounting_holes',[])
            if bool(mounts)!=bool(holes):raise DomainError('invalid_input','几何绑定的支柱孔必须通过pcbMounts明确指向PCB机械特征。')
            if mounts:
                features={f['id']:f for f in result['geometry'].get('mountingFeatures',[])}
                if len(set(mounts))!=len(mounts) or len(mounts)<3 or not set(mounts)<=set(features):raise DomainError('invalid_input','固定至少需要三个独立、已追溯的纯NPTH孔；不能借用接头SH孔。')
                selected=[features[i] for i in mounts];origin=substrate['centerUndersideCADmm']
                target=[dict(x=f['at'][0]-origin[0],y=-f['at'][1]-origin[1],diameter=f['drill']) for f in selected]
                if len(holes)!=len(target) or any(not any(set(h)==set(t) and all(abs(h[k]-t[k])<1e-6 for k in t) for h in holes) for t in target):raise DomainError('invalid_input','支柱孔坐标/孔径必须与指定原生PCB孔一一对应。')
                area=max(abs((b['x']-a['x'])*(c['y']-a['y'])-(b['y']-a['y'])*(c['x']-a['x'])) for a in target for b in target for c in target)
                if area<1:raise DomainError('invalid_input','支撑孔不能共线或集中在退化区域。')
                fastener=params.get('pcbFastener') or {};evidence=sources.get(fastener.get('evidencePath'))
                if not evidence or Path(evidence['path']).suffix.lower()!='.json' or fastener['evidencePath'] not in design.get('evidence_files',[]):raise DomainError('invalid_input','紧固件工程包络须明确引用已冻结JSON资料。')
                path=Path(evidence['path'])
                if not path.is_file() or path.stat().st_size>256*1024 or digest(path)!=evidence['sha256']:raise DomainError('input_changed','紧固件工程包络来源不存在或摘要不符。',409)
                record=json.loads(path.read_text(encoding='utf-8-sig'))
                if record.get('units')!='mm' or record.get('kind')!='engineering-fastener-envelope' or record.get('parameters')!={k:v for k,v in fastener.items() if k!='evidencePath'}:raise DomainError('invalid_input','紧固件尺寸必须与工程包络资料完全一致，不推断供应商资格。')
                dependency.update(mounts=[{**f,'localHole':t} for f,t in zip(selected,target)],fastener=fastener,fastenerEvidenceSha256=evidence['sha256'])
        return dependency

    def schematic(self, project_id, revision_id=None, sheet=None, offset=0, limit=100):
        if offset < 0 or not 1 <= limit <= 300:
            raise DomainError('invalid_input', '对象分页范围不正确。')
        return read_schematic(self.config, self.revision(project_id, revision_id), sheet, offset, limit, cache=self._schematic_read_cache)

    def compare(self, project_id, before_id, after_id):
        before, after = self.revision(project_id, before_id), self.revision(project_id, after_id)
        for revision in (before, after):
            verify_snapshot(within(self.config.data, revision['relativePath']), revision['manifest'])
        return compare_revisions(before, after)

    def board(self,project_id,revision_id=None,offset=0,limit=100):
        if offset<0 or not 1<=limit<=300:raise DomainError('invalid_input','PCB 对象分页范围不正确。')
        revision=self.revision(project_id,revision_id)
        key=(project_id,revision['id'],self.pcb.engine_hash,fingerprint([revision['manifest'],revision.get('pcbDesign'),revision['entrypoints']]))
        snapshot=None;cached=False
        if offset and revision_id is not None:
            with self._board_page_lock:
                snapshot=self._board_page_snapshots.get(key)
                if snapshot is not None:self._board_page_snapshots.move_to_end(key);cached=True
        source=within(self.config.data,revision['relativePath'])
        if cached:verify_snapshot(source,revision['manifest'])
        else:
            snapshot=self.pcb.read(revision)
            with self._board_page_lock:
                self._board_page_snapshots[key]=snapshot
                self._board_page_snapshots.move_to_end(key)
                # Two concurrent revisions cover the UI and Agent readers;
                # eviction falls back to a fresh native read, never partial data.
                while len(self._board_page_snapshots)>2:self._board_page_snapshots.popitem(last=False)
        result={k:snapshot[k] for k in ('revisionId','board','boardSha256','units','thickness','engineVersion','outline','padGraphHash','physicalGroups','copperLayers','physicalStackup','layoutFeedback','powerFeeds')} | dict(
            rules=(revision.get('pcbDesign') or {}).get('rules'),
            stackupProfile=(revision.get('pcbDesign') or {}).get('stackup'),
            objects=snapshot['objects'][offset:offset+limit],total=len(snapshot['objects']),offset=offset,
            nextOffset=offset+limit if offset+limit<len(snapshot['objects']) else None,
            componentCount=sum(not f.get('mechanicalFeature') for f in snapshot['footprints']),mechanicalFeatureCount=len(snapshot['mountingFeatures']),mountingFeatures=snapshot['mountingFeatures'],trackCount=len(snapshot['tracks']),zoneCount=len(snapshot.get('zones',[])),
            pairedSections=[dict(name=c['request']['name'],sha256=fingerprint(c),request=c['request'],segmentIds=[s['id'] for r in c['rails'] for s in r['segments']]) for c in (revision.get('pcbDesign') or {}).get('pairedSections',[])],
            note='真实原生对象与焊盘坐标。编辑保持全部焊盘网络；DRC和未连单列，不表示可制造。')
        result=copy.deepcopy(result)
        result.update(pcb_read_coverage(snapshot, offset, limit))
        # Read job evidence afresh even on a paginated geometry cache hit.
        result['referenceCheckStatus']=self.store.reference_check_status(project_id,revision['id'],snapshot['boardSha256'])
        return result

    def pcb_planning(self, project_id, request):
        from .adapters.pcb_planning import report, route_followup, requested_checks
        revision=self.revision(project_id,request['revisionId'])
        basis=revision.get('pcbDesign')
        if not basis or not basis.get('rules') or not basis.get('outline'):
            raise DomainError('unsupported','布局规划需要有明确板框和设计规则的PCB版本。')
        folder=self.config.local/'pcb-planning'/uid('plan')
        checks=requested_checks(request)
        snapshot=(self.pcb.read if checks['geometry'] else self.pcb.read_planning)(revision,folder/'read')
        if snapshot['boardSha256']!=request['expectedBoardHash']:
            raise DomainError('input_changed','PCB已改变，请基于当前版本重新规划。',409)
        request=copy.deepcopy(request);route_evidence=None
        request['probeInterfaces']=checks['probeInterfaces']
        if request.get('routeJobId'):
            job=self.store.get('jobs',request['routeJobId'])
            if job['projectId']!=project_id or job.get('inputRevision')!=revision['id'] or job.get('params',{}).get('expectedBoardHash')!=snapshot['boardSha256']:
                raise DomainError('input_changed','布线证据必须属于同工程、同源版本及PCB摘要。',409)
            feedback=(job.get('result')or{}).get('layoutFeedback',{})
            follow=feedback.get('nextActions') or route_followup(snapshot,dict(attempts=feedback.get('unresolvedConnections',[])),job.get('params',{}).get('nets',[]))
            targets={(r['reference'],r['pin']):r for r in request.get('escapeTargets',[])}
            for target in follow.get('escapeTargets',[]):
                key=(target['reference'],target['pin'])
                if key not in targets:targets[key]=target
            if len(targets)>16:raise DomainError('invalid_input','一次最多诊断16个出口，请明确缩小目标范围。')
            request['escapeTargets']=list(targets.values())
            route_evidence=dict(jobId=job['id'],status=job['status'],followup=follow)
        fps={f['reference']:f for f in snapshot['footprints']}
        selected={ref for group in request.get('groups',[])for ref in group['references']}
        selected.update(t['reference']for t in request.get('escapeTargets',[]))
        if selected-fps.keys():raise DomainError('invalid_input','规划包含不存在的器件：'+', '.join(sorted(selected-fps.keys())))
        for target in request.get('escapeTargets',[]):
            if sum(p['number']==target['pin']for p in fps[target['reference']]['pads'])!=1:
                raise DomainError('invalid_input','出口目标必须唯一匹配真实焊盘。')
        netnames={p['net']for f in fps.values()for p in f['pads']if p['net']}
        for row in request.get('corridors',[])+request.get('escapeTargets',[]):
            if row['width']<basis['rules']['trackWidth'] or row['clearance']<basis['rules']['clearance']:
                raise DomainError('invalid_input','规划不能放宽板级线宽或净距。')
        for corridor in request.get('corridors',[]):
            if corridor['net'] not in netnames or set(corridor['layers'])-set(snapshot['copperLayers']):
                raise DomainError('invalid_input','通道必须引用现有网络和启用铜层。')
        board=folder/'read'/Path(revision['entrypoints']['board']).name
        native=self.pcb.worker(dict(action='planning_probe',board=str(board),basis=basis,request=request),folder/'probe',lambda:None,60) if checks['geometry'] else {}
        source=within(self.config.data,revision['relativePath']);verify_snapshot(source,revision['manifest'])
        result=report(snapshot,request,native)
        result.update(routeEvidence=route_evidence,engineBundleHash=self.pcb.engine_hash)
        result['analysisMode']='geometry' if checks['geometry'] else 'functional-groups'
        result['performedChecks']=['pin-and-placement-inventory','functional-groups']
        if checks['geometry']:result['performedChecks'].append('existing-pcb-read-checks')
        for field,label in [('probeInterfaces','interface-trials'),('escapeTargets','escape-probes'),('corridors','corridor-probes')]:
            if request.get(field):result['performedChecks'].append(label)
        result['skippedChecks']=[s for s in ('interface-trials','escape-probes','corridor-probes') if s not in result['performedChecks']]
        if not checks['geometry']:
            result['skippedChecks']+=['mounting-review','power-feeds','pad-exits','routing-quality']
            result['scope']='Complete native pin/placement inventory only. No interface, corridor, exit, mounting, power or routing geometry checks were performed. Source manifest and PCB SHA verified. Functional roles are caller declarations, not inferred. No edits.'
        (folder/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        return result

    def libraries(self, project_id, revision_id=None, library_id=None, symbol=None, offset=0, limit=50):
        if offset < 0 or not 1 <= limit <= 100:
            raise DomainError('invalid_input', '符号库分页范围不正确。')
        return read_libraries(self.config, self.revision(project_id, revision_id), library_id, symbol, offset, limit)

    def probe_library(self, source_path, project_id=None):
        if source_path.startswith('source://'):
            if not project_id:raise DomainError('invalid_input','登记资料探查需要projectId。')
            _,path=self.sources.resolve(project_id,source_path);source_path=str(path)
        return probe_library(source_path)

    async def import_library(self, project_id, request):
        fields = request.model_dump()
        fields.pop('projectId',None)
        params = {k: fields.pop(k) for k in ['revisionId', 'expectedProjectVersion', 'message', 'timeoutSeconds']}
        request_id = fields.pop('requestId')
        params['operations'] = [dict(kind='import_symbol_library', **fields)]
        return await self.start_job(project_id, 'schematic.edit', params, request_id)

    def connectivity(self, project_id, revision_id=None, offset=0, limit=100):
        if offset < 0 or not 1 <= limit <= 300:
            raise DomainError('invalid_input', '连接分页范围不正确。')
        revision = self.revision(project_id, revision_id)
        verify_snapshot(within(self.config.data, revision['relativePath']), revision['manifest'])
        artifacts = [a for a in self.store.list('artifacts', project_id)
                     if a['revisionId'] == revision['id'] and a['kind'] in {'netlist', 'baseline_netlist'}]
        if not artifacts:
            raise DomainError('dependency_missing', '此版本尚无原生网表，请先运行 eda_inspect 并等待网表生成。')
        artifact = max(artifacts, key=lambda a: a['createdAt'])
        _, path = self.artifact_path(artifact['id'])
        graph = read_graph(path)
        return dict(revisionId=revision['id'], graphHash=graph['hash'], artifactId=artifact['id'],
                    components=graph['components'], groups=graph['groups'][offset:offset + limit],
                    total=len(graph['groups']), offset=offset,
                    nextOffset=offset + limit if offset + limit < len(graph['groups']) else None,
                    policy='完整替换指定分组，其余分组保持原样；全部旧引脚与新增器件引脚必须各出现一次。')

    async def start_job(self, project_id, kind, params, request_id):
        self.store.get('projects', project_id)
        from .module_settings import resolve_task
        params=resolve_task(self.store.get('projects',project_id).get('preferences',{}),kind,params)
        params=self.sources.resolve_params(project_id,params)
        if kind=='pcb.initialize':
            from .pcb_rule_settings import resolve
            project=self.store.get('projects',project_id)
            params={**params,'rules':resolve(project.get('preferences',{}),params.get('rules'))}
        handler=self.module_tasks.get(kind)
        if kind in {'firmware.device','manufacturing.jlc_quote','manufacturing.jlc_sync','manufacturing.mechanical_prepare','components.query','components.freeze_local'}:
            repeated=self.store.replay_job(project_id,kind,params,request_id)
            if repeated:return repeated
        if handler:await asyncio.to_thread(handler.validate,project_id,params)
        if not handler and kind not in {"eda.import", "eda.inspect", 'schematic.edit', 'schematic.initialize', 'schematic.review', 'pcb.handoff', 'pcb.initialize', 'pcb.edit','pcb.route','pcb.pair_route','pcb.sync','pcb.reference_check','pcb.geometry_export','mechanical.design','mechanical.insertion_check'}:
            raise DomainError("unsupported_capability", "此任务尚未接入。")
        if kind=='mechanical.insertion_check':
            if self.mechanical.issue:raise DomainError('dependency_missing',self.mechanical.issue)
            self.insertion_context(project_id,params)
        if kind=='mechanical.design':
            self.product_concepts.require_selection(project_id)
            if self.mechanical.issue:raise DomainError('dependency_missing',self.mechanical.issue)
            try:encoded=json.dumps(params['design'],ensure_ascii=False,allow_nan=False)
            except (ValueError,TypeError) as error:raise DomainError('invalid_input','机械参数必须为有限数值JSON。') from error
            if len(encoded.encode('utf8'))>512*1024:raise DomainError('invalid_input','机械参数超过512KB。')
            self.mechanical_pcb_dependency(project_id,params)
        if kind=='pcb.geometry_export':
            if self.mechanical.issue:raise DomainError('dependency_missing',self.mechanical.issue)
            if self.eda.issue:raise DomainError('dependency_missing',self.eda.issue)
            revision=self.revision(project_id,params['revisionId'])
            if not revision['entrypoints'].get('board'):raise DomainError('invalid_input','需要PCB入口。')
        if kind == 'schematic.initialize' and self.eda.issue:
            raise DomainError('dependency_missing', self.eda.issue)
        if kind in {"eda.inspect", 'schematic.edit', 'schematic.review', 'pcb.handoff', 'pcb.initialize', 'pcb.edit','pcb.route','pcb.pair_route','pcb.sync','pcb.reference_check'}:
            revision = self.store.get("revisions", params["revisionId"])
            if revision["projectId"] != project_id:
                raise DomainError("invalid_input", "输入版本不属于此工程。")
            if revision.get('module','eda')!='eda':raise DomainError('invalid_input','此为独立模块版本，请使用对应模块接口。')
            if self.eda.issue:
                raise DomainError("dependency_missing", self.eda.issue)
            if kind == 'schematic.edit' and not revision['entrypoints'].get('schematic'):
                raise DomainError('invalid_input', '该版本没有原理图入口。')
            if kind == 'schematic.edit' and any(op['kind'] in TOPOLOGY_OPERATIONS for op in params['operations']) and not params.get('connectivity'):
                raise DomainError('invalid_input', '新增器件或修改电气对象必须声明 connectivity 预期连接目标。')
            if kind in {'schematic.review', 'pcb.handoff'} and not revision['entrypoints'].get('schematic'):
                raise DomainError('invalid_input', '工程审查和交接需要原理图。')
            if kind == 'pcb.handoff':
                reviewed = self.store.get('jobs', params['reviewJobId'])
                if reviewed['projectId'] != project_id:
                    raise DomainError('invalid_input', '审查任务不属于此工程。')
            if kind.startswith('pcb.') and kind!='pcb.handoff' and not self.config.kicad_python:
                raise DomainError('dependency_missing','缺少 KiCad 10 Python 板编辑引擎。')
            if kind in {'pcb.initialize','pcb.sync'}:
                handoff=self.store.get('jobs',params['handoffJobId'])
                if handoff['projectId']!=project_id:raise DomainError('invalid_input','交接任务不属于此工程。')
                if kind=='pcb.initialize':initialization_replacement(revision,params,within(self.config.data,revision['relativePath']))
            if kind in {'pcb.edit','pcb.route','pcb.pair_route','pcb.sync','pcb.reference_check'} and not revision['entrypoints'].get('board'):
                raise DomainError('invalid_input','此版本没有 PCB 入口。')
            if kind=='pcb.route':native_pcb_workflow.validate_scope(revision,params)
            if kind in {'pcb.edit','pcb.pair_route','pcb.sync'}and revision.get('pcbDesign',{}).get('acceptanceScope')==native_pcb_workflow.SCOPE:
                raise DomainError('unsupported_capability','此仅原生来源目前只支持显式native-source-preserving的pcb.route；其他修改尚未接入，不能沿用原理图契约。')
        options=(dict(adapter_version=handler.version) if getattr(handler,'read_only',False) else dict(module=handler.module,expected_module_head=params[handler.expected_head_field],adapter_version=handler.version)) if handler else {}
        if not handler:
            if kind=='pcb.geometry_export':
                options['adapter_version']='pcb-geometry/'+self.pcb_geometry.engine_hash
            elif kind=='mechanical.insertion_check':
                options['adapter_version']='mechanical-insertion/'+self.insertion.sha
            elif kind.startswith('mechanical.'):
                options['adapter_version']='cadquery-mechanical/'+self.mechanical.describe()['version']
            elif kind.startswith('pcb.') and kind!='pcb.handoff':
                options['adapter_version']='kicad10-pcb-worker/'+self.pcb.engine_hash
            else:
                options['adapter_version']='kicad10-cli/'+(self.eda.detected_version or 'unavailable')
        with self.store.lock:
            if self.maintenance.cleaning:
                raise DomainError('busy', '缓存清理正在执行，完成后再启动工程任务。', 409)
            job, created = self.store.create_job(project_id, kind, params, request_id,**options)
        if created:
            flag = threading.Event()
            self.cancellations[job["id"]] = flag
            task = asyncio.create_task(self._run(job, flag))
            self.tasks[job["id"]] = task
            task.add_done_callback(lambda _, id=job["id"]: self.tasks.pop(id, None))
        return job

    def cancel(self, id):
        job = self.store.get("jobs", id)
        if job["status"] in {"queued", "running"}:
            flag = self.cancellations.get(id)
            if flag:
                flag.set()
            job = self.store.job_update(id, "job.phase", cancelRequested=True, phase="正在停止当前任务")
        return job

    async def _run(self, job, flag):
        try:
            async with self.slots:
                await asyncio.to_thread(self._execute, job, flag)
        finally:
            self.cancellations.pop(job["id"], None)

    def _execute(self, job, flag):
        id, project_id = job["id"], job["projectId"]
        artifact_revision = job['inputRevision']
        candidate = None
        candidate_registered = False
        pcb_reads = None
        stage_timer = JobStageTimer(job)

        def check_cancel():
            if flag.is_set():
                raise Cancelled()

        def phase(message):
            check_cancel()
            stage_timer.enter(message)
            self.store.job_update(id, "job.phase", phase=message)

        def publish(path, kind, mime=None):
            check_cancel()
            path = path.resolve()
            if not path.is_relative_to(self.config.data):
                raise DomainError("invalid_input", "产物路径超出本项目数据目录。")
            artifact = dict(id=uid("a"), projectId=project_id, jobId=id, revisionId=artifact_revision,
                            name=path.name, kind=kind, mimeType=mime or mimetypes.guess_type(path)[0] or "application/octet-stream",
                            size=path.stat().st_size, sha256=digest(path), relativePath=path.relative_to(self.config.data).as_posix(),
                            createdAt=now(), status="diagnostic-or-preview")
            self.store.add_artifact(artifact)

        def register_candidate(revision):
            nonlocal candidate,candidate_registered,artifact_revision
            check_cancel();candidate=revision
            self.store.add_revision(candidate,id,adopt=False);candidate_registered=True;artifact_revision=candidate['id']

        try:
            check_cancel()
            stage_timer.enter("准备输入")
            self.store.job_update(id, "job.started", status="running", phase="准备输入", startedAt=now())
            handler=self.module_tasks.get(job['kind'])
            if handler:
                handler.execute(ModuleTaskContext(job,phase,check_cancel,publish,register_candidate))
                return
            if job['kind']=='mechanical.insertion_check':
                revision,mechanical_job,artifact=self.insertion_context(project_id,job['params']);artifact_revision=revision['id']
                output=self.config.data/'projects'/project_id/'jobs'/id
                result=self.insertion.run(revision,mechanical_job,artifact,job['params'],output,check_cancel,phase,publish)
                if self.store.finish_readonly(id,result,'插拔空间检查完成；配对资格与制造另列') is None:raise Cancelled()
                return
            elif job['kind']=='mechanical.design':
                deadline=time.monotonic()+job['params']['timeoutSeconds'];output=self.config.data/'projects'/project_id/'jobs'/id;output.mkdir(parents=True)
                phase('冻结独立机械规格、明确来源和可选PCB依赖')
                dependency=self.mechanical_pcb_dependency(project_id,job['params'])
                candidate=self.mechanical.candidate(project_id,job,dependency);self.store.add_revision(candidate,id,adopt=False);candidate_registered=True;artifact_revision=candidate['id']
                result,cad=self.mechanical.build(candidate,output,check_cancel,phase,publish,max(.1,deadline-time.monotonic()))
                package=output/'mechanical-project.zip';source=within(self.config.data,candidate['relativePath']);verify_snapshot(source,candidate['manifest'])
                with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as archive:
                    for row in candidate['manifest']:
                        check_cancel();archive.write(within(source,row['path']),'source/'+row['path'])
                    for path in sorted(cad.rglob('*')):
                        if path.is_file():check_cancel();archive.write(path,'outputs/'+path.relative_to(cad).as_posix())
                    archive.writestr('formforge-manifest.json',json.dumps({k:v for k,v in candidate.items() if k!='validation'},ensure_ascii=False,indent=2))
                    archive.writestr('formforge-output-hashes.json',json.dumps({p.relative_to(cad).as_posix():digest(p) for p in cad.rglob('*') if p.is_file()},ensure_ascii=False,indent=2))
                publish(package,'mechanical_project','application/zip');check_cancel()
                if time.monotonic()>=deadline:raise DomainError('tool_failed','机械任务达到时间预算；候选未采纳。')
                if self.store.finish_mechanical(id,candidate['id'],result) is None:raise Cancelled()
                return
            elif job['kind']=='pcb.geometry_export':
                revision=self.revision(project_id,job['params']['revisionId']);artifact_revision=revision['id'];output=self.config.data/'projects'/project_id/'jobs'/id;output.mkdir(parents=True)
                result=self.pcb_geometry.export(revision,job['params'],output,check_cancel,phase,publish)
                if self.store.finish_readonly(id,result,'板体几何交接完成；覆盖范围与配合检查另列' if result['checksPassed'] else '几何核对需处理，不能作为机械设计依据') is None:raise Cancelled()
                return
            elif job["kind"] == "eda.import":
                revision = import_project(self.config, project_id, job["params"], phase, check_cancel)
                check_cancel()
                self.store.add_revision(revision, id)
                result = {"revisionId": revision["id"], "fileCount": len(revision["manifest"]), "origin": "imported"}
            elif job['kind']=='pcb.reference_check':
                parent=self.revision(project_id,job['params']['revisionId']);output=self.config.data/'projects'/project_id/'jobs'/id;output.mkdir(parents=True)
                phase('核对保存的填充铜、参考锚点连接和信号投影')
                result=self.pcb.reference_check(parent,job['params'],output/'reference',check_cancel)
                report=output/'reference-report.json';report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');publish(report,'pcb_reference_report','application/json')
                preview=output/'reference-locations.svg';preview.write_bytes(reference_preview(result));publish(preview,'pcb_reference_preview','image/svg+xml')
            elif job['kind']=='pcb.route'and job['params'].get('acceptanceScope')==native_pcb_workflow.SCOPE:
                output=self.config.data/'projects'/project_id/'jobs'/id
                native_pcb_workflow.execute(self,job,output,phase,check_cancel,publish,register_candidate)
                return
            elif job['kind'] in {'pcb.initialize','pcb.edit','pcb.route','pcb.pair_route','pcb.sync'}:
                parent=self.revision(project_id,job['params']['revisionId'])
                output=self.config.data/'projects'/project_id/'jobs'/id;output.mkdir(parents=True)
                deadline=time.monotonic()+job['params']['timeoutSeconds']
                handoff=self.store.get('jobs',job['params']['handoffJobId']) if job['kind'] in {'pcb.initialize','pcb.sync'} else None
                handoff_root=self.config.data/'projects'/project_id/'jobs'/handoff['id']/'handoff' if handoff else None
                pcb_reads=PcbReadSession(self.pcb,output/'pcb-read-metrics.json')
                plan=None;edit_job=job
                if job['kind']=='pcb.edit':
                    edit_job=self.pcb.prepare_edit(parent,job,output/'edit-planning',check_cancel,read_snapshot=pcb_reads.read)
                    if (edit_job['params'].get('_silkscreenPlan') or edit_job['params'].get('_trackOptimization')) and not edit_job['params']['operations'] and not edit_job['params'].get('mountingPlan'):
                        result=dict(revisionId=parent['id'],adopted=False,noop=True,changeCount=0,checksPassed=False,draftChecksPassed=False,
                            silkscreenPlacement=edit_job['params'].get('_silkscreenPlan'),trackOptimization=edit_job['params'].get('_trackOptimization'),manufacturingApproved=False)
                        report=output/'pcb-report.json';report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');publish(report,'pcb_edit_report','application/json')
                        self.store.finish_readonly(id,result,'丝印排放无可采纳变化，保留原版')
                        return
                    phase('检查冗余铜清理是否实际需要修改')
                    noop=self.pcb.cleanup_noop(parent,job,output/'cleanup-noop',check_cancel,read_snapshot=pcb_reads.read)
                    if noop is not None:
                        check_cancel()
                        if time.monotonic()>=deadline:raise DomainError('tool_failed','PCB任务达到时间预算。')
                        report=output/'pcb-report.json';report.write_text(json.dumps(noop,ensure_ascii=False,indent=2),encoding='utf8')
                        publish(report,'pcb_edit_report','application/json')
                        if self.store.finish_readonly(id,noop,'清理无变化，保留当前版本；未执行新验收') is None:raise Cancelled()
                        return
                if job['kind'] in {'pcb.route','pcb.pair_route'}:
                    phase('读取原生物理连接与障碍，开始自研选网搜索')
                    planner=self.pcb.pair_plan if job['kind']=='pcb.pair_route' else self.pcb.route_plan
                    plan=planner(parent,job,output/'planning',check_cancel,phase,read_snapshot=pcb_reads.read)
                    plan_file=output/'route-plan.json';plan_file.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf8');publish(plan_file,'pcb_route_plan','application/json')
                    if not plan['operations']:
                        result=dict(
                            revisionId=parent['id'],adopted=False,plannedConnectionCount=0,checksPassed=False,draftChecksPassed=False,
                            budgetExhausted=plan['budgetExhausted'],manufacturingApproved=False,scope=plan['scope'],
                            **({'routeDiagnostics':plan['diagnostics']} if 'diagnostics' in plan else {}),
                            **({'maxPairAttemptsPerConnection':plan['maxPairAttemptsPerConnection']} if 'maxPairAttemptsPerConnection' in plan else {}),
                            **({'searchHeuristicWeight':plan['searchHeuristicWeight']} if 'searchHeuristicWeight' in plan else {}),
                            **({'localPlacement':plan['localPlacement']} if 'localPlacement' in plan else {}),
                            **({'layoutFeedback':plan['layoutFeedback']} if 'layoutFeedback' in plan else {}))
                        retained=plan.get('localPlacement',{}).get('sourceComparison',{}).get('reason')=='source_retained_no_better_candidate'
                        if retained:
                            check_cancel()
                            if time.monotonic()>=deadline:raise DomainError('tool_failed','PCB任务达到时间预算。')
                            result.update(noop=True,changeCount=0)
                            if self.store.finish_readonly(id,result,'局部搜索未找到实际改善，保留当前版本；未创建新候选') is None:raise Cancelled()
                        else:
                            self.store.job_update(id,'job.finished',status='completed',phase='本次受限搜索未生成新连接，当前版本保持不变',finishedAt=now(),result=result)
                        return
                    edit_job={**job,'params':{**job['params'],'operations':plan['operations'],'_routePlan':plan}}
                board_base=None
                if job['kind']=='pcb.sync':
                    board_base=self.revision(project_id,job['params']['boardBaseRevisionId']);cursor=parent
                    for _ in range(2000):
                        if cursor['id']==board_base['id']:break
                        if not cursor.get('parentRevisionId'):raise DomainError('invalid_input','同步板基线不是当前版本的祖先。')
                        cursor=self.revision(project_id,cursor['parentRevisionId'])
                    else:raise DomainError('invalid_input','同步版本链超过首版上限。')
                phase('冻结板输入并创建原生 PCB 候选')
                routing_baseline={}
                candidate,expected=self.pcb.candidate(parent,edit_job,output,handoff,handoff_root,check_cancel,board_base,baseline_out=routing_baseline,read_snapshot=pcb_reads.read)
                check_cancel();self.store.add_revision(candidate,id,adopt=False);candidate_registered=True;artifact_revision=candidate['id']
                phase('检查板文件、原理图一致性及真实焊盘网络')
                inspection_output=output/'inspection';inspection_output.mkdir()
                inspection=self.eda.inspect(candidate,inspection_output,
                    dict(timeoutSeconds=max(1,deadline-time.monotonic()),include3d=job['params']['include3d']),phase,check_cancel,publish)
                phase('原生重载候选并读取物理连接')
                actual=self.pcb.read(candidate,output/'after-read',check_cancel)
                raw_drc=json.loads((inspection_output/'drc.json').read_text(encoding='utf-8-sig'))
                phase('核对源连接、铜意图与几何约束')
                result=draft_result(inspection,expected,actual,raw_drc,candidate['pcbDesign']['outline'],self.pcb.copper_intent(candidate),previous=routing_baseline)
                if edit_job['params'].get('_silkscreenPlan'):result['silkscreenPlacement']=edit_job['params']['_silkscreenPlan']
                if edit_job['params'].get('_trackOptimization'):result['trackOptimization']=edit_job['params']['_trackOptimization']
                legends=actual['layoutFeedback']['interfaceLegends'];result['interfaceLegends']=legends
                if not legends['declaredPassed']:
                    result.update(draftChecksPassed=False,checksPassed=False)
                    result['blockingFindingCount']+=1
                    result['blockingFindings'].append(dict(type='interface_legends_failed',severity='error',description='已登记接口的逐针信号、针网或原生丝印不匹配；读取interfaceLegends.findings修正，旧版本保持。'))
                if job['kind']=='pcb.edit':
                    preserved=source_connectivity_proof(routing_baseline,actual)
                    result['sourceConnectivity']=preserved
                    result['connectivityPreserved'] &= preserved['passed']
                    if not preserved['passed']:
                        result.update(draftChecksPassed=False,checksPassed=False)
                        result['blockingFindingCount']+=1
                        result['blockingFindings'].append(dict(type='source_physical_connections_changed',severity='error',description='手动编辑断开了旧版真实物理连接或改变原生焊盘身份；按sourceConnectivity中的网络、端子、坐标和铜对象同批重接，旧版本保持。'))
                if candidate['pcbDesign'].get('stackup'):
                    stack=stackup_proof(candidate['pcbDesign']['stackup'],actual['physicalStackup']);result['stackup']=stack
                    result['draftChecksPassed']=result['draftChecksPassed'] and stack['passed']
                    if not stack['passed']:
                        result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='stackup_mismatch',severity='error',description='保存的物理叠层与版本契约不一致。'))
                    stack_file=output/'stackup-report.json';stack_file.write_text(json.dumps(stack,ensure_ascii=False,indent=2),encoding='utf8');publish(stack_file,'pcb_stackup_report','application/json')
                text_check=text_proof(edit_job['params'].get('operations',[]),actual)
                if text_check['fields']:
                    result['footprintText']=text_check;result['draftChecksPassed']=result['draftChecksPassed'] and text_check['passed']
                    if not text_check['passed']:
                        result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='footprint_text_unmatched',severity='error',description='原生保存后的器件文字位置/角度/可见性与明确目标不符。'))
                if any(op['kind'] in {'add_board_text','remove_board_text'}for op in edit_job['params'].get('operations',[])):
                    previous=pcb_reads.read(parent,output/'board-text-source-proof',check_cancel)
                    legend=board_text_proof(previous,actual,edit_job['params']['operations']);result['boardText']=legend
                    result['draftChecksPassed'] &= legend['passed']
                    if not legend['passed']:
                        result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='board_text_unmatched',severity='error',description='独立说明丝印新增/删除目标或既有文字保持核对失败。'))
                if any(op['kind'] in {'set_footprint_silkscreen_line','remove_footprint_silkscreen_line','remove_footprint_silkscreen_polygon'} for op in edit_job['params'].get('operations',[])):
                    source_silk=pcb_reads.read(parent,output/'silkscreen-source-proof',check_cancel)
                    silk=silkscreen_proof(source_silk,actual,edit_job['params']['operations']);result['footprintSilkscreen']=silk
                    library_match=not any(v['type']=='lib_footprint_mismatch' and not v.get('excluded',False) for v in raw_drc['violations'])
                    silk.update(derivedFootprints=candidate['pcbDesign'].get('footprintVariants',[]),nativeLibraryMatch=library_match)
                    silk['passed']=silk['passed'] and library_match
                    result['draftChecksPassed']=result['draftChecksPassed'] and silk['passed']
                    if not silk['passed']:
                        result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='silkscreen_content_changed',severity='error',description='丝印目标未匹配或其他原生/物理内容改变。'))
                if job['kind']=='pcb.sync':
                    result.update(connectivityPolicy='explicit_pcb_sync',connectivityPreserved=not bool(job['params']['expectedPadChanges']),expectedPadChanges=job['params']['expectedPadChanges'])
                    source_board=pcb_reads.read(board_base,output/'sync-source-proof',check_cancel)
                    synced=sync_proof(source_board,actual,job['params']['expectedPadChanges'],job['params'].get('footprintReplacements',[]));result['syncProgress']=synced
                    result['draftChecksPassed']=result['draftChecksPassed'] and synced['passed']
                    if not synced['passed']:
                        result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='sync_source_connections_changed',severity='error',description='同步未保持原布局/位号或非目标物理连接。'))
                if plan:
                    proof=routing_proof(plan,actual);result['routingProgress']=proof
                    if 'readability' in plan:result['routeReadability']=plan['readability']
                    if 'layoutFeedback' in plan:
                        result['layoutFeedback']=plan['layoutFeedback']
                        if plan['layoutFeedback']['enforced']:
                            from .adapters.pcb import body_gap_proof
                            body=body_gap_proof(plan,actual);result['bodyAvoidance']=body
                            result['draftChecksPassed']=result['draftChecksPassed'] and body['passed']
                            if not body['passed']:
                                result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='body_gap_violation',severity='error',description='保存后的新增铜未通过有限SMD本体间隙或封装几何保持复核。'))
                    if 'localPlacement' in plan:result['localPlacement']=plan['localPlacement']
                    if plan.get('replacement') or plan.get('copperReplacement'):
                        result['pairReplacement' if plan.get('replacement') else 'ordinaryReplacement']=plan.get('replacement') or plan['copperReplacement'];tails=rework_drc_proof(plan,raw_drc,actual);result['reworkCopperEnds']=tails
                        result['draftChecksPassed']=result['draftChecksPassed'] and tails['passed']
                        if not tails['passed']:
                            result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='rework_dangling_copper',severity='error',description='重布涉及的网络仍有未排除的悬空铜端，不能仅凭焊盘连通采纳。'))
                    result['draftChecksPassed']=result['draftChecksPassed'] and proof['passed']
                    result['routeBudgetExhausted']=plan['budgetExhausted'];result['plannedConnectionCount']=plan['plannedConnectionCount']
                    if 'maxPairAttemptsPerConnection' in plan:result['maxPairAttemptsPerConnection']=plan['maxPairAttemptsPerConnection']
                    if 'searchHeuristicWeight' in plan:result['searchHeuristicWeight']=plan['searchHeuristicWeight']
                    if 'diagnostics' in plan:result['routeDiagnostics']=plan['diagnostics']
                    if not proof['passed']:
                        result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='route_physical_progress_unproven',severity='error',description='保存后的原生物理连接未证明声明收益、明确替换或全部源连接保持。'))
                if candidate['pcbDesign'].get('pairedSections'):
                    pairs=pair_proof(candidate['pcbDesign']['pairedSections'],actual);result['pairedGeometry']=pairs
                    result['draftChecksPassed']=result['draftChecksPassed'] and pairs['passed']
                    pair_file=output/'pair-report.json';pair_file.write_text(json.dumps(pairs,ensure_ascii=False,indent=2),encoding='utf8');publish(pair_file,'pcb_pair_report','application/json')
                    if not pairs['passed']:
                        result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='paired_geometry_unproven',severity='error',description='已保存成组段的端点、铜对象或几何限值未通过；旧版本保持。'))
                if job['kind']=='pcb.initialize':
                    result['mountingFeatures']=dict(passed=True,count=len(actual['mountingFeatures']),features=actual['mountingFeatures'],
                        originalPhysicalConnectionsPreserved=None,policy='new-board-schematic-contract')
                elif actual['mountingFeatures'] or any(op['kind']=='remove_mounting_hole' for op in edit_job['params'].get('operations',[])):
                    before=pcb_reads.read(board_base if job['kind']=='pcb.sync' else parent,output/'mount-source-proof',check_cancel)
                    after_groups=[(g['net'],set(g['pads'])) for g in actual['physicalGroups']]
                    preserved=all(any(net==g['net'] and set(g['pads'])<=pads for net,pads in after_groups) for g in before['physicalGroups']) if job['kind']!='pcb.sync' else result['syncProgress']['unmodifiedPhysicalConnectionsPreserved']
                    result['mountingFeatures']=dict(passed=preserved,count=len(actual['mountingFeatures']),features=actual['mountingFeatures'],originalPhysicalConnectionsPreserved=preserved)
                    result['draftChecksPassed'] &= preserved
                    if not preserved:
                        result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='mounting_connections_changed',severity='error',description='机械特征修改破坏了原有物理连接。'))
                fresh=read_graph(inspection_output/'netlist.xml')
                if fresh['hash']!=expected['hash']:
                    result.update(draftChecksPassed=False,connectivityMatched=False,connectivityPreserved=False)
                result.update(revisionId=candidate['id'],baseRevisionId=parent['id'],changeCount=len(candidate['changes']),changes=candidate['changes'],
                    nativeChecksPassed=inspection['checksPassed'],componentCount=sum(not f.get('mechanicalFeature') for f in actual['footprints']),mechanicalFeatureCount=len(actual['mountingFeatures']),trackCount=len(actual['tracks']),zoneCount=len(actual.get('zones',[])))
                if candidate['pcbDesign'].get('powerFeeds'):
                    feeds=actual['powerFeeds'];result['powerFeeds']=feeds
                    result['draftChecksPassed'] &= feeds['passed']
                    result['checksPassed'] &= feeds['passed']
                    if not feeds['passed']:
                        result['blockingFindingCount']+=1;result['blockingFindings'].append(dict(type='power_feed_unproven',severity='error',description='声明的电容供电节点未通过中心线拓扑检查；旧版本保持。'))
                result['routingQuality']=actual['layoutFeedback']['routingQuality']
                result['padExits']=actual['layoutFeedback']['padExits']
                result['placementReview']=actual['layoutFeedback']['placementReview']
                result['connectorAccess']=actual['layoutFeedback'].get('connectorAccess')
                if routing_baseline:
                    from .adapters.placement_feedback import compare as placement_compare
                    result['placementChange']=placement_compare(routing_baseline,actual)
                finalize_checks(result)
                phase('保存报告与可恢复原生包')
                graph_file=output/'pcb-read.json';graph_file.write_text(json.dumps(actual,ensure_ascii=False,indent=2),encoding='utf8');publish(graph_file,'pcb_snapshot','application/json')
                report=output/'pcb-report.json';report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');publish(report,'pcb_edit_report','application/json')
                package=output/'pcb-project.zip'
                with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as archive:
                    source=within(self.config.data,candidate['relativePath']);verify_snapshot(source,candidate['manifest'])
                    for row in candidate['manifest']:
                        check_cancel();archive.write(within(source,row['path']),'source/'+row['path'])
                    archive.writestr('formforge-manifest.json',json.dumps({k:v for k,v in candidate.items() if k!='validation'},ensure_ascii=False,indent=2))
                publish(package,'native_project','application/zip');check_cancel()
                if time.monotonic()>=deadline:raise DomainError('tool_failed','PCB 任务达到时间预算，候选未采纳。')
                phase('原子采纳候选或保留旧版')
                if self.store.finish_edit(id,candidate['id'],result,pcb_draft=True) is None:raise Cancelled()
                return
            elif job['kind'] in {'schematic.review', 'pcb.handoff'}:
                revision = self.revision(project_id, job['params']['revisionId'])
                output = self.config.data / 'projects' / project_id / 'jobs' / id
                output.mkdir(parents=True)
                phase('重新检查待审查的原理图与实际连接')
                native_output = output / 'native'
                native_output.mkdir()
                native = self.eda.inspect(revision, native_output,
                    dict(scope='schematic', timeoutSeconds=job['params']['timeoutSeconds'], includeBoardNetlist=job['kind']=='pcb.handoff'), phase, check_cancel, publish)
                if not native['checksPassed']:
                    raise DomainError('invalid_input', '原生检查未通过，不能完成工程审查或 PCB 交接。')
                graph = read_graph(native_output / 'netlist.xml')
                if job['kind'] == 'schematic.review':
                    phase('冻结资料并核对电流、关键属性和控制连接')
                    result = review_design(self.config, revision, job['params'], graph, output, check_cancel, publish)
                else:
                    phase('核对审查版本并冻结 PCB 封装与连接交接包')
                    reviewed = self.store.get('jobs', job['params']['reviewJobId'])
                    if reviewed['contextSnapshot']['version'] != job['contextSnapshot']['version']:
                        raise DomainError('version_conflict', '设计、需求或偏好版本已变化，须重新审查。', 409)
                    result = pcb_handoff(self.config, revision, graph, reviewed, output, check_cancel, publish)
                check_cancel()
                context = self.context(project_id)['project']
                if context['headRevisionId'] != revision['id'] or context['version'] != job['contextSnapshot']['version']:
                    raise DomainError('version_conflict', '工程输入已变化，本次审查或交接不生效。', 409)
                result['nativeChecksPassed'] = True
            elif job['kind'] in {'schematic.edit','schematic.initialize'}:
                initializing = job['kind'] == 'schematic.initialize'
                parent = self.revision(project_id, job['params']['revisionId']) if not initializing else None
                output = self.config.data / 'projects' / project_id / 'jobs' / id
                output.mkdir(parents=True)
                deadline = time.monotonic() + job['params']['timeoutSeconds']
                phase('核对对象并创建修改候选')
                candidate = initialize_schematic(self.config,job,check_cancel) if initializing else create_candidate(self.config, parent, job, check_cancel)
                check_cancel()
                self.store.add_revision(candidate, id, adopt=False)
                candidate_registered = True
                artifact_revision = candidate['id']
                if initializing:
                    publish(within(self.config.data,candidate['relativePath'])/'formforge-specification.json','design_requirements','application/json')
                for change in candidate['changes']:
                    if change['kind'] in {'import_symbol_library','import_footprint'}:
                        provenance_file = output / (change['after']['id'] + '-provenance.json')
                        provenance_file.write_text(json.dumps(change['after'], ensure_ascii=False, indent=2), encoding='utf8')
                        publish(provenance_file, 'footprint_library_provenance' if change['kind']=='import_footprint' else 'symbol_library_provenance', 'application/json')

                def inspect_revision(revision, folder, baseline=False):
                    nonlocal artifact_revision
                    check_cancel()
                    artifact_revision = revision['id']
                    folder.mkdir()
                    return self.eda.inspect(revision, folder, {'scope': 'schematic', 'timeoutSeconds': deadline - time.monotonic(),
                        '_baselineNetlistOnly': baseline and not job['params'].get('includeBaselineDiagnostics',False),
                        'includeDocumentation': bool(job['params'].get('includeDocumentation')) and not baseline},
                        lambda message: phase(('修改前：' if baseline else '修改后：') + message), check_cancel,
                        lambda path, kind, mime=None: publish(path, 'baseline_' + kind if baseline else kind, mime))

                if parent:
                    inspect_revision(parent, output / 'before', True)
                    before_graph = read_graph(output / 'before' / 'netlist.xml')
                else:
                    before_graph = assemble([],[])
                intent = job['params'].get('connectivity')
                additions = [dict(reference=c['reference'], pins=c['pins']) for c in candidate['changes'] if c['kind'] in {'add_symbol', 'add_library_symbol'}]
                removals = [c['reference'] for c in candidate['changes'] if c['kind'] == 'remove_symbol']
                expected = expected_graph(before_graph, intent, additions, removals)
                artifact_revision = candidate['id']
                target_file = output / 'connectivity-target.json'
                target_file.write_text(json.dumps(dict(intent=intent, expected=expected), ensure_ascii=False, indent=2), encoding='utf8')
                publish(target_file, 'connectivity_target', 'application/json')
                inspection = inspect_revision(candidate, output / 'after')
                check_cancel()
                phase('核对实际连接与预期电气目标')
                after_graph = read_graph(output / 'after' / 'netlist.xml')
                comparison = compare_graphs(before_graph, after_graph, expected, intent)
                equivalent, matched = comparison['preserved'], comparison['matched']
                graph_file = output / 'connectivity-comparison.json'
                graph_file.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding='utf8')
                publish(graph_file, 'connectivity_report', 'application/json')
                file_comparison = compare_revisions(parent or {'id':None,'manifest':[]}, candidate)
                change_file = output / 'changes.json'
                change_file.write_text(json.dumps(file_comparison, ensure_ascii=False, indent=2), encoding='utf8')
                publish(change_file, 'change_report', 'application/json')
                summary = {**inspection, 'revisionId': candidate['id'], 'baseRevisionId': parent['id'] if parent else None,
                           'nativeChecksPassed': inspection['checksPassed'], 'connectivityPreserved': equivalent,
                           'connectivityMatched': matched, 'connectivityPolicy': comparison['policy'],
                           'connectivityDifference': {k: comparison[k] for k in ('missingGroups', 'unexpectedGroups', 'componentsMatched', 'declaredRemovedGroups', 'declaredAddedGroups')},
                           'checksPassed': inspection['checksPassed'] and matched, 'changeCount': len(candidate['changes']),
                           'changes': candidate['changes'], 'scope': '受约束修改、ERC 与完整预期引脚连接核对；不代表电路功能验证。'}
                summary['checks'] = [*inspection['checks'], dict(kind='connectivity', status='passed' if matched else 'failed',
                    findings=0 if matched else 1, errors=0 if matched else 1, warnings=0, unconnected=0,
                    items=[] if matched else [{'description': '实际引脚连接不符合完整预期目标，候选未采纳。'}])]
                if parent and any(op['kind']=='auto_layout' for op in job['params'].get('operations',[])):
                    from .adapters.schematic_preservation import compare_layout
                    preserve=compare_layout(self.config,parent,candidate,output/'before/netlist.xml',output/'after/netlist.xml',
                        bool(job['params']['operations'][0].get('blockLayout') or job['params']['operations'][0].get('preserveSheet')),check_cancel)
                    preservation_file=output/'preservation-report.json'
                    preservation_file.write_text(json.dumps(preserve,ensure_ascii=False,indent=2),encoding='utf8')
                    publish(preservation_file,'preservation_report','application/json')
                    summary['dataPreservationPassed']=preserve['passed']
                    summary['checksPassed']=summary['checksPassed'] and preserve['passed']
                    summary['checks'].append(dict(kind='preservation',status='passed' if preserve['passed'] else 'failed',findings=len(preserve['issues']),
                        errors=len(preserve['issues']),warnings=0,unconnected=0,items=preserve['issues']))
                artifact_revision = candidate['id']
                edit_report = output / 'edit-report.json'
                edit_report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
                publish(edit_report, 'edit_report', 'application/json')
                native_package = output / 'schematic-project.zip'
                with zipfile.ZipFile(native_package, 'w', zipfile.ZIP_DEFLATED) as archive:
                    source = within(self.config.data, candidate['relativePath'])
                    verify_snapshot(source, candidate['manifest'])
                    for item in candidate['manifest']:
                        check_cancel()
                        archive.write(within(source, item['path']), 'source/' + item['path'])
                    archive.writestr('formforge-manifest.json', json.dumps({k: v for k, v in candidate.items() if k != 'validation'}, ensure_ascii=False, indent=2))
                publish(native_package, 'native_project', 'application/zip')
                check_cancel()
                if time.monotonic() >= deadline:
                    raise DomainError('tool_failed', '修改任务超出时间预算，候选未采纳。')
                if self.store.finish_edit(id, candidate['id'], summary) is None:
                    raise Cancelled()
                return
            else:
                revision = self.store.get("revisions", job["params"]["revisionId"])
                output = self.config.data / "projects" / project_id / "jobs" / id
                output.mkdir(parents=True)
                result = self.eda.inspect(revision, output, job["params"], phase, check_cancel, publish)
            self.store.job_update(id, "job.finished", status="completed", phase="任务完成", result=result, finishedAt=now())
        except Cancelled:
            if candidate_registered:
                self.store.reject_candidate(candidate['id'], 'cancelled', '修改已取消，当前版本未变。')
            self.store.job_update(id, "job.finished", status="cancelled", phase="已取消", finishedAt=now())
        except Exception as exc:
            if candidate_registered:
                self.store.reject_candidate(candidate['id'], 'failed', str(exc))
            self.store.job_update(id, "job.finished", status="failed", phase="任务未完成", finishedAt=now(),
                                  error=str(exc), errorCode=exc.code if isinstance(exc, DomainError) else "tool_failed")

        finally:
            timing=stage_timer.finish(self.store.get('jobs',id))
            timing_path=self.config.data/'projects'/project_id/'jobs'/id/'stage-timings.json'
            timing_path.parent.mkdir(parents=True,exist_ok=True)
            timing_path.write_text(json.dumps(timing,ensure_ascii=False,indent=2),encoding='utf8')
            self.store.add_artifact(dict(id=uid('a'),projectId=project_id,jobId=id,
                revisionId=job['inputRevision'],name=timing_path.name,kind='job_stage_timings',
                mimeType='application/json',size=timing_path.stat().st_size,sha256=digest(timing_path),
                relativePath=timing_path.relative_to(self.config.data).as_posix(),createdAt=now(),status='diagnostic-or-preview'))
            if pcb_reads is not None and pcb_reads.metrics_path.is_file():
                # Diagnostic telemetry also survives failed/cancelled jobs.
                path=pcb_reads.metrics_path
                self.store.add_artifact(dict(id=uid('a'),projectId=project_id,jobId=id,
                    revisionId=job['inputRevision'],name=path.name,kind='pcb_read_metrics',
                    mimeType='application/json',size=path.stat().st_size,sha256=digest(path),
                    relativePath=path.relative_to(self.config.data).as_posix(),
                    createdAt=now(),status='diagnostic-or-preview'))

    def artifact_path(self, id):
        artifact = self.store.get("artifacts", id)
        path = within(self.config.data, artifact["relativePath"])
        if not path.is_file() or digest(path) != artifact["sha256"]:
            raise DomainError("input_changed", "产物文件已变化，无法作为原任务结果读取。", 409)
        return artifact, path

    def read_artifact(self, id, max_chars=20000):
        artifact, path = self.artifact_path(id)
        if artifact["mimeType"] not in {"application/json", "text/plain", "application/xml"}:
            return {"artifact": artifact, "message": "二进制/图形文件通过网页预览或文件接口读取。"}
        with path.open(encoding="utf-8-sig", errors="replace") as stream:
            content = stream.read(max_chars + 1)
        return {"artifact": artifact, "text": content[:max_chars], "truncated": len(content) > max_chars}

    async def close(self):
        await self.maintenance.close()
        await self.settings.close()
        await self.supplier_browser.shutdown()
        for flag in self.cancellations.values():
            flag.set()
        if self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
