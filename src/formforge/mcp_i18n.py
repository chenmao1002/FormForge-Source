"""Per-connection MCP localization without changing business data or tool names."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

SYSTEM_MESSAGES_EN = json.loads(Path(__file__).with_name('system_messages_en.json').read_text(encoding='utf8'))


def english_system_text(value: str) -> str:
    if value in SYSTEM_MESSAGES_EN:
        return SYSTEM_MESSAGES_EN[value]
    for pattern, render in (
        (r'^修改(前|后)：(.+)$', lambda m: ('Before' if m[1] == '前' else 'After') + ' edit: ' + english_system_text(m[2])),
        (r'^KiCad CLI 导出已冻结器件 (.+)$', lambda m: 'Export frozen component ' + m[1] + ' with KiCad CLI'),
        (r'^自研搜索 (\d+)/(\d+) · (.+) · 已找到 (\d+) 处连接候选$', lambda m: f'Route search {m[1]}/{m[2]} · {m[3]} · {m[4]} connection candidates found'),
    ):
        match = re.match(pattern, value)
        if match:
            return render(match)
    return value


MCP_INSTRUCTIONS_EN = """FormForge is a local product-engineering workbench. Read project_module_settings for effective schematic, mechanical, firmware and manufacturing defaults and their sources. Omit task parameters you do not intend to override; explicit task parameters take priority. Settings affect future tasks, not historical revisions. Analyze the user's requirements before acting. Read project_context and capabilities_get first. Keep requirements, preferences, versions, tasks, events, and artifacts traceable. The web UI, HTTP API, and MCP use the same business operations. A successful call, process exit, animation, or generated artifact is not engineering acceptance. Unsupported design, recovery, ordering, payment, or production capabilities must be reported as unavailable.

Read project_context.agentWorkflow before acting. Preserve confirmed decisions and original user words; do not ask the same questions again. Use project_workflow_update for incremental decisions, assumptions, usage checks and the concrete next action. Only ask material unresolved questions after reading existing evidence; continue independent work. Do not mark Agent proposals as user-confirmed. Before layout, plan normal user viewpoints, visibility, readability, reach, operation, feedback and maintenance. Propose and carry out authorized improvements instead of waiting for every detail. Before delivery, compare each requirement and use scenario with current artifacts, methods, observations and limits; geometry or electrical checks do not prove usability. Missing, pending, issue or stale checks are incomplete; non-applicability needs a reason. These are Agent-declared records, not certification or permission. Reconcile changed requirements without re-asking unchanged choices; product requirement changes still use project_update. Read every needed page. Inspect pcb_read.objectCoverage: the last page is not the whole board. zoneInventory lists all zone identities on every page, but grants no automatic yield/delete permission. Read project_context.processReview before repeating unsuccessful work on the same input; compare parameters and reports, distinguish completed from adopted, and keep historical failures separate from current blockers. Before freezing the design, record applicable charging/runtime duration, standby drain, weight, noise and key performance tradeoffs as measurable usage expectations; unknown values need a verification method. Check supported firmware/platform and delivery paths early. After a failure change the approach based on evidence, not repeated identical attempts. Save blockers and the next concrete action for continuation.

For a new schematic, schematic_initialize freezes explicit requirements and creates an empty native document. The Agent selects parts and defines the complete electrical target. Organize schematics by functional group: signal flow normally runs left to right, power and return paths vertically, and repeated channels use consistent layouts. Prefer real short wires inside a group for series, divider, decoupling, and driver branches; retain labels across groups or sheets. Place, rotate, and align symbols from their real pin directions before wiring. Use add_wire/add_junction for explicit relationships and connect_local_labels only for selected same-sheet labels. Read schematic_read.readability, inspect the native SVG/PDF, and review labelOnlySymbols; wireQuality reports separate physical length, bends, unconnected crossings, overlaps and two-pin path detour estimates with coverage and budget limits, never label-name shortcuts. functionalGroups reports per-block measures, whole-sheet estimated field findings and declared physical branch continuity from registered symbol UUIDs; shared/unassigned wires and cross-block crossings remain separate. Undeclared blocks, missing members or exhausted budgets never imply completion. Multi-pin driver/protection branches require an explicit single allowed angle and same-net source/target evidence; readability advice is not an electrical verdict. Preserve connectivity targets and require native ERC. Failed checks remain candidates and never overwrite the adopted revision. For layout review, use adoption=review with includeBaselineDiagnostics=true, read schematic_candidate_read and native before/after drawings, then explicitly use schematic_candidate_adopt with the job, base revision and project version. Completed native jobs stay immutable; later adoption has a separate receipt. Use instance_path for shared drawing scenes. organization.pagePlanning assigns flexible groups from actual block envelopes, per-page symbol limits and native-net crossing costs; explicit page and samePage are hard constraints. netWeights require a real native net and evidence and never change connectivity. It uses bounded candidates, not a global-optimality or impossibility proof. Named functional overviews support one to nine pages with real parent-pin wires. Rebuild rejects unhandled UUID-less root metadata, including bus_alias; use complete-block preservation or retain the original. Rebuild preserves resolved native constituent classes through hidden Netclass fields after geometry, without changing project rules; ambiguous compound names are rejected. New layout candidates also require preserved native names/classes, symbol data/NC/libraries and protected project/PCB files. Report instance-path migration separately and require downstream PCB review without updating copper. Numeric/letter unit notation, separators and embedded unit_name come from frozen project/library inputs; unsupported notation requires native preview.

Before enclosure-related PCB work, read mechanical_schema(kind="enclosure").pcbPlanning and establish the outline, real mounting method and holes, connector orientation, mating envelope, and corner radius. Rounded outlines are the default: new or changed boards use a 3 mm radius, limited to one quarter of the short edge. Use cornerRadius=0 only for an explicit square-corner request and save squareCornerReason. A selected standoff recipe requires at least three non-collinear pure NPTH holes; this is not a universal rule for every board. A PCB, all fitted components, and their native geometry form one PCBA assembly. pcb_geometry_export produces one assembly.step feature/output part for mechanical import while retaining native colors, holes, and internal component identity for checks.

Before layout or routing, read the native pcb_read objects plus layoutFeedback.placementReview, layoutFeedback.routingQuality, layoutFeedback.padExits, and powerFeeds. Optimize placement before accepting long detours. Compare rotation, position swaps, and horizontal/vertical series arrangements for connected two-terminal groups, align connected pad centers, and plan power entry, load exits, local decoupling, feedback, and return paths together. Placement feedback is advisory and does not prove routability or global optimality.

For a new board or an explicitly requested shrink, compare feasible compact arrangements before routing. Use actual component bodies/courtyards, fixation and tool envelopes, connector access, and power/routing corridors; compare adjacent-edge and opposite-edge connector arrangements, rotations, and local moves before selecting a smaller outline through existing initialization/edit tools. Record old/new dimensions, area change, and preserved constraints. Example dimensions are not universal defaults and old boards are not silently shrunk. Respect the user's or project's copper-layer choice; ordinary projection findings do not authorize extra layers, and one project's two-layer requirement does not apply to every board. This policy guides the external Agent; it is not an automatic board shrinker or layer selector.

Prefer interface function names, complete pin tables, and pin-1 identification on the component's side, registering the actual observation layer in interfaceLegends. Compare visible free areas and multiple columns before using the other side. Avoid component bodies, mask openings, fasteners, and tool envelopes; space underneath a body is not readable. Record a reason for an opposite-side exception and update the layer contract when moving labels.

Calculate power and reference temperature from the actual supply branches. Separate directly supplied chip cores, external IO rails, regulator output loads, power exported to remote loads, and heat delivered by a peer into local termination. Do not assign the whole board input current to an LDO output. Use schematic_review sources/facts/calculations for qualified inputs, load/ambient assumptions, and thermal resistance; reference thetaJA is not the actual board value, and missing parameters remain unknown. Scope reference review by current, actual edge speed, board paths, and use: low baud rate does not imply slow edges. Full reference-copper projection coverage is not a universal completion gate for ordinary low-power short-board designs. Retain raw findings and declared hard constraints, record applicability and disposition, and preserve USB/fast-edge, local decoupling/ESD-return and physical-ground-connectivity review. Short-line screening is not SI/PI or EMC qualification.

Select ground/reference return nets explicitly; never infer electrical roles from a name such as GND. For declared return nets, prefer thermal copper zones, save thermalGap and thermalSpoke, refill, then inspect physical connectivity groups and add only the required short tracks or vias. A same-name zone is not connectivity evidence. Do not place vias on every ground pad in advance. Full zone connection requires a specific electrical or thermal reason. DRC, decoupling-loop review, reference coverage, solderability, and manufacturing qualification remain separate checks.

Via rules must be explicit and versioned with diameter, drill, and reason, and must match the native project minima. Routers must not silently reduce clearance, width, via, or annular-ring rules. A bounded search failure is evidence about the attempted configuration, not proof that a net is impossible to route. maxPairAttemptsPerConnection and searchHeuristicWeight may control bounded exploration; weight greater than 1 is approximate and never proves shortest or optimal routing.

Manual and automatic routing share the same geometry policy. New exposed two-branch copper must use straight segments and 45-degree transitions; do not add right angles, acute corners, or saw-tooth detours. A segment being horizontal, vertical, or 45 degrees does not by itself make the combined corner legal. Start continuously at the native pad center. A genuinely circular, concentric native copper pad may exit at 45 degrees; other pads first exit along board up/down/left/right axes. Do not use off-center exits or turn inside a pad to create a shifted exit. Continue at least 0.15 mm beyond the real copper edge before a planar 45-degree turn, except a direct terminal connection with no planar turn. Width changes, split add_track operations, and joining existing copper do not bypass corner or pad-exit checks. Read cornerPolicy, padExitPolicy, routingQuality.cornerFindings, and padExits.findings after every submission.

Power topology is explicit. Form an inductor output-capacitor node before distributing loads, register every concentrated output capacitor in powerFeeds, and include the feedback endpoint in downstream conditions. On multilayer boards, record each layer's purpose. Signal or power copper on an inner layer does not automatically provide a continuous reference plane. Preserve all prior physical connections in the same candidate when moving parts, replacing footprints, cleaning copper, or reconnecting routes; same-net names, a matching pad graph, or a lower unrouted count do not replace sourceConnectivity verification. ESD and decoupling roles require frozen evidence, exact part/pin identity, or an explicit user role statement; do not infer them from net names.

After a PCB task, read job_get.adopted and all native policy reports. Fix rejected candidates by terminal, net, layer, and coordinate. DRC zero alone never proves complete routing, correct topology, reference integrity, manufacturing readiness, or hardware function. Deliver native previews of the whole board and the changed area and list any untouched existing findings.

Before placement, read pcb_read.layoutFeedback.connectorAccess. Use native PCB Edge datums to detect inward or buried connectors and plan outward placement plus affected-copper rerouting. Unknown direction needs source-backed mating evidence, never a guess from a USB/header name. Native DRC is not insertion-access proof. Changed inaccessible datums reject adoption; actual plug/enclosure envelopes remain independent. For interface delivery, register interfaceLegends for download/debug, power, and external connectors using the real pad numbering, nets, and viewed board side. Record the function, every pin as number:signal, pin-1 identification, and voltage, polarity, or supply restrictions where needed. A label such as SWD or UART alone is insufficient. Read pcb_read.layoutFeedback.interfaceLegends and the native preview. Missing, moved, wrong-side, deleted, or net-mismatched registered legends reject the candidate. Independently review mating-face orientation, obstruction, solder-mask openings, and manufacturing readability.

Mechanical explosion views follow assembly relationships, never part-list order. feature_model or assembly may declare explosion.fixed_parts and groups with id, parts, CAD direction, and distance_mm. Buttons or standoffs in one group use one displacement; attached legends follow their enclosure; the PCBA stays whole. Use compact, physically meaningful directions for the rear cover, battery, and side controls. Geometry-height fallback is display-only and does not prove disassembly order, motion, strength, or manufacturing. Sliding or retained parts declare their real blockers and displacement in engineering_plan.retentions.

Before claiming a physical mechanical design is manufacturable, select the process, exact material, finish, fit type, and fastening method. Read mechanical_schema.manufacturingPlanning and use a versioned process profile. Do not reuse SLA clearances for other processes. engineering_plan records the profile, made/purchased/reference parts, critical wall sections, fits, fasteners, and retentions. A concept may remain unplanned but cannot be called printable or assembly-ready. Check final solids after all Boolean operations for continuous screw access, head bearing, tool clearance, engagement, receiver contact, anti-rotation, and tip clearance. Do not assume heat-set inserts or supplier support for M2 inserts in SLA resin. Sliding and clamping designs must state insertion order and real retention stops; declared-pose checks do not prove continuous motion, rotational escape resistance, strength, tolerances, shrinkage, coating allowance, or supplier approval. Check STL mesh topology separately from STEP solid validity and separate wall thickness from overall minimum part size.

Only execute tasks that the user has authorized. The service executes explicit work; the external Agent makes decisions. Keep failed tasks and stopped clocks. Retry by creating a new task, never rewriting a failure as success. Do not place orders, pay, release production, publish, push a repository, or operate physical hardware unless that action is explicitly authorized and supported. PCB, SMT, and 3D supplier notes must not be filled or changed automatically. For an authorized submit-unpaid operation, bind the exact files, hashes, quantities, assembly scope, process choices, payable total, and agent-review evidence; if the result is unknown, query the existing attempt before any retry. Payment and production release remain unavailable to automation."""


MCP_INSTRUCTIONS_EN += '\nFor reference-led appearance work, read mechanical_schema.appearancePolicy. The external Agent generates or reads reference images and builds editable CAD features; record appearance.brief, references, image SHA and generation prompts. Preserve dimensions and engineering constraints. materials sets per-part roughness/metalness; decals binds a frozen PNG/JPEG to one output part with CAD-mm center, outward normal, image up, width/height and projection depth. Inspect clay and textured views from several sides. GLB embeds materials and decals; STEP/STL retain solids. No automatic image-to-CAD, background model calls or printable sticker production is provided.\n'

TOOL_DESCRIPTION_EN = {
    "schematic_candidate_read": "Read the same native task candidate, before/after revision plots, changes, unmet items, checks and current adoption status.",
    "schematic_candidate_adopt": "Adopt a checked review candidate bound to its job, base revision and current project version. Verify files and reject stale inputs; preserve the completed native task and return an idempotent adoption receipt.",
 "project_rename": "Rename the project display name with an expected-version check. Preserve design files, requirements, preferences and history.",
 'rule_presets_read':'List local rule presets ordered by recorded successful project applications, not views or estimated popularity.',
 'rule_presets_save':'Save explicit supported module fields as a local future-task preset; does not modify CAD.',
 'rule_presets_apply':'Apply a preset to version-checked project defaults. Preserve original snapshots and historical CAD; record usage only on successful commit.',
    'project_module_settings': 'Read effective module defaults, inherited values and project override sources. Applies to future tasks; explicit task parameters take priority. Does not modify historical versions.',
    "project_workflow_read": "Read persistent decisions, usage checks, stale evidence, blockers and the next action. Agent-declared observations are not certification.",
    "project_workflow_update": "Incrementally merge decisions and usage checks using independent version and current design binding. Preserve original user words, omitted entries and history. Checked observations need current project artifacts, method and limits. Does not change CAD or authorization.",
    "capabilities_get": "Read supported modules, detected tool versions, and explicitly unavailable capabilities.",
    "desktop_browser_control": "Control the legacy quote-only supplier browser. Inspect current controls first; never handle credentials, submit orders, pay, or release production.",
    "procurement_browser_control": "Prepare PCB/SMT quotes or an explicitly authorized unpaid submission. Keep PCB and SMT quantities separate, do not change supplier notes, and query an unknown result before retrying.",
    "print_order_control": "Quote, prepare checkout, or perform an explicitly authorized unpaid 3D-print submission with exact STL hashes, quantities, process, price, and agent-review evidence. Never pay or release production.",
    "supplier_session": "Open, inspect, save, or close the FormForge supplier login session. Credentials and verification codes are entered locally by the user and are never returned.",
    "supplier_attempt_prepare": "Freeze reviewed manufacturing inputs into a recoverable supplier attempt. Existing matching orders become read-only checks and are not duplicated.",
    "supplier_attempt_step": "Inspect or advance a recoverable supplier attempt with strict versioning and idempotency. Unsupported form steps remain manual; payment and production release are unavailable.",
    "jlc_quote": "Request an official process quote for an exact board revision and Gerber hash. This does not upload an order, submit, pay, or qualify manufacturing.",
    "jlc_sync_order": "Query and reconcile an existing supplier order using exact identity, file, quantity, process, amount, and dimension evidence. Never guess a match or create a new order.",
    "order_adopt_existing": "Register an existing PCB order against fixed project, manufacturing, Gerber, quantity, process, amount, dimension, and evidence fields without changing CAD or supplier state.",
    "order_observe": "Append an immutable observation to an existing PCB order using optimistic versioning; this does not operate the supplier platform.",
    "smt_order_adopt_existing": "Register an existing SMT order with fixed Gerber, BOM, CPL, PCB order, quantity, scope, and evidence; do not infer DFM or production approval.",
    "smt_order_observe": "Append an immutable SMT order observation. Placement quantity must come from the supplier response; this does not infer DFM or production approval.",
    "procurement_plan": "Create a version-bound procurement candidate from explicit manufacturing and source evidence. Unresolved identity or quantity remains unresolved; no quote, upload, order, payment, or production release occurs.",
    "manufacturing_external_review": "Review registered external Gerber/BOM/CPL inputs with explicit units, mappings, transformations, and assembly scope. The review does not modify design heads or place an order.",
    "manufacturing_prepare": "Generate review-only manufacturing files from exact EDA and manufacturing revisions. File consistency is separate from engineering, procurement, CAM, supplier-preview, and production approval.",
    "firmware_design": "Create a version-bound firmware candidate from explicit hardware evidence and source edits, then perform the supported real build and image checks. Build success does not prove behavior or hardware operation.",
    "firmware_flash_profile": "Read pinned flash tools, target profiles and software support. This never enumerates or connects hardware. OpenOCD remains unavailable.",
    "firmware_flash_build_artifacts": "List internal firmware build artifacts. Binding rechecks the build job, revision, dependencies and file hash.",
    "firmware_flash_internal_bundle": "Bind an exact internal firmware revision, successful build job, artifact and SHA-256 without accessing hardware.",
    "firmware_flash_device": "REAL HARDWARE ACCESS: obtain the user's explicit authorization first. Discover enumerates devices; identify can halt or reset a chip; program writes and reads back a bound plan. Never use this for offline validation. Read/cancel through job_get/job_cancel; retries require a new requestId.",
    "pcb_geometry_export": "Export a version-bound native PCBA assembly with complete selected models. Mechanical import uses the complete assembly.step as one PCBA feature while retaining internal identity for checks.",
    "mechanical_insertion_check": "Run declared straight-line insertion-envelope checks against exact mechanical and PCB revisions. Passing does not prove connector compatibility, tolerances, motion, strength, or manufacturing.",
    "mechanical_design": "Create a version-bound native mechanical candidate from the published schema. Manufacturing claims require a complete engineering_plan and supported checks; failures never replace the adopted head.",
    "workspace_cleanup_scan": "Preview known caches and exact regenerable KiCad inspection input copies. Verify source manifests, content equality and recorded dependencies. Preserve revisions, registered artifacts, reports and failed-job workspaces. Read workspaces and retention reasons; no deletion during scanning.",
    "workspace_cleanup_run": "Delete only explicitly selected eligible entries from a fresh scan. Recheck dependencies and frozen inputs for engineering work copies before deletion. Preserve revisions, registered artifacts, reports, credentials and runtimes; report actual partial results.",
    "project_delete": "Move the named project to the recoverable project trash after exact version and name confirmation. Supplier orders are not cancelled.",
    "project_purge": "Permanently remove the exact trashed project files after explicit confirmation. Task timing and order metadata remain; supplier orders are not cancelled.",
    "project_trash_empty": "After explicit user confirmation, permanently remove the exact displayed trash projects in the background, bound to project IDs, names and versions. Poll workspace_cleanup_read for actual results. Preserve task timing and order metadata; do not cancel supplier orders.",
    "project_create": "Create a local FormForge project. This does not initialize a design or claim engineering acceptance.",
    "source_transfer": "Register or transfer exact source bytes with provenance, purpose, license, and SHA-256. Never copy credentials, environments, Git history, or unrelated generated files.",
    "eda_import": "Import an existing native EDA project as a traceable candidate and run the supported native checks; import does not restore unknown design qualifications.",
    "eda_inspect": "Run version-bound native EDA inspection and artifact generation. Process completion is separate from checks passing.",
    "schematic_initialize": "Create an empty native schematic from frozen requirements. The Agent must provide part selection and the complete electrical target.",
    "schematic_edit": "Apply explicit, version-bound schematic operations while preserving undeclared connectivity. Native ERC and complete connection targets determine adoption.",
    "schematic_review": "Review the actual schematic, frozen evidence, and explicit Agent claims. A computed value or budget remains an estimate unless backed by specified evidence.",
    "pcb_handoff": "Create a version-bound PCB handoff from a reviewed schematic with footprints, complete pad nets, and source mappings. This is not layout or routing.",
    "pcb_initialize": "Create a real native PCB from the current reviewed handoff and explicit outline, rules, and placements. Unrouted drafts may be retained, but invalid pad nets are rejected.",
    "pcb_edit": "Apply explicit version-bound PCB edits under native connectivity, corner, pad-exit, clearance, via, zone, interface-legend, and source-preservation checks.",
    "pcb_route": "Route selected ordinary nets with bounded self-developed search and fixed rules. localPlacement supports a series/shared-node pair or explicit escape-cluster poses with one companion; select every affected net. Inspect placementBlockers before coordinating a connector relocation. Outward access may improve at a copper/via cost only if every source physical group is preserved. Escape reservations protect pending terminals during search, without adding copper or waiving checks. restoreSourceConnectionsOnly explicitly restores original pad relationships while retaining historical opens. One retry may reuse remaining time within the original deadline; escape-cluster candidates allow at most 60 seconds each. No improvement returns noop; only a winning change enters native adoption checks. No global optimality, insertion-envelope, SI/PI or manufacturing claim.",
    "pcb_pair_route": "Route an explicitly selected pair using real pad geometry, clearances, total-length, mismatch, neck, and reference constraints. It does not prove impedance or timing.",
    "pcb_sync": "Synchronize reviewed schematic changes into an exact PCB revision while retaining all undeclared copper and validating complete native connectivity.",
    "pcb_reference_check": "Check selected copper against declared native reference anchors and antipad geometry. This does not prove impedance, return-current integrity, or manufacturing.",
    "job_cancel": "Request cooperative cancellation of an owned active task. Poll job_get for the terminal state; history is never deleted.",
    "artifact_read": "Verify the artifact digest and read bounded text content or metadata. Artifacts remain bound to their task and input revision.",
}


CAPABILITY_AGENT_INSTRUCTIONS_EN = {
    "pcbRouting": "Follow the English MCP initialization rules for native PCB planning, placement, explicit power/return roles, self-developed routing, centered pad exits, 45-degree corners, versioned via and zone rules, sourceConnectivity, and post-save native checks. DRC zero is not completion or manufacturing proof.",
    "schematicEditing": "Follow the English MCP initialization rules for functional grouping, real pin-direction placement, local physical wires, explicit cross-group labels, complete connectivity targets, native ERC, and version-bound adoption.",
    "schematicCreation": "Start from frozen explicit requirements and an empty native document. The Agent selects parts, plans functional groups, and declares the complete connectivity target; native ERC and exact target matching determine adoption.",
    "schematicPresentation": "Read whole-sheet readability feedback and the native preview after each change. labelOnlySymbols is advisory; decide whether to add local wires, move symbols, or retain justified cross-group labels without changing electrical intent.",
    "manufacturingPlanning": "Select a versioned process profile, exact material, finish, fit, fastening, made/purchased/reference parts, critical wall sections, and retentions before claiming physical manufacturability. Declared checks do not prove tolerances, strength, motion, supplier acceptance, or production approval.",
    "explosionView": "Group exploded views by assembly relationship and real removal direction. Keep each group together, attached legends with their enclosure, and the entire PCBA as one assembly. Geometry-height fallback is display-only.",
}


READ_ACTIONS = {
    "read": "Read", "list": "List", "status": "Read the current status of", "schema": "Read the supported contract for",
    "profile": "Read the supported profile for", "get": "Read", "scan": "Scan", "probe": "Probe", "compare": "Compare",
    "observe": "Append an immutable observation for", "prepare": "Prepare a version-bound candidate for", "create": "Create",
    "restore": "Restore", "freeze": "Freeze", "update": "Update", "control": "Control", "export": "Export",
}


def english_tool_description(name: str) -> str:
    if name in TOOL_DESCRIPTION_EN:
        return TOOL_DESCRIPTION_EN[name]
    words = name.split("_")
    action = READ_ACTIONS.get(words[-1], "Use")
    subject_words = words[:-1] if words[-1] in READ_ACTIONS else words
    subject = " ".join(subject_words).replace("pcb", "PCB").replace("smt", "SMT").replace("jlc", "JLCPCB")
    suffix = " This operation remains bound to the declared project version, hashes, and returned evidence."
    if words[-1] in {"read", "list", "status", "schema", "profile", "get", "scan", "probe", "compare"}:
        suffix = " This is read-only and does not change project, supplier, order, or physical state."
    return f"{action} {subject}." + suffix


ERROR_EXACT = {
    '项目单元位号样式未受支持，请使用原生预览。':'This project unit-reference notation is unsupported. Use the native preview.',
    '同一单元的原生名称存在歧义，请使用原生预览。':'Native names for this unit are ambiguous. Use the native preview.',
    '绘图项目设置不在版本快照中，请重新导入。':'Drawing project settings are not in the version snapshot. Import the project again.',
    "未找到指定工程、版本、任务或文件。": "The requested project, revision, job, or file was not found.",
    "浏览器步骤正在执行，请等待后再管理会话。": "A browser step is running. Wait for it to finish before managing the session.",
    "服务未连接": "The service is not connected.",
    "请求已过期，请刷新后重试。": "The request is stale. Refresh the current state and retry.",
    "版本已变化，请刷新后重试。": "The version changed. Refresh the current state and retry.",
    "任务不存在。": "The task does not exist.",
    "工程不存在。": "The project does not exist.",
    "文件不存在。": "The file does not exist.",
}


def has_cjk(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def english_error(value: str) -> str:
    prefix = ""
    match = re.match(r"Error executing tool ([A-Za-z0-9_.-]+):\s*(.*)", value, re.S)
    if match:
        prefix, value = f"Error executing tool {match.group(1)}: ", match.group(2)
    translated = ERROR_EXACT.get(value, SYSTEM_MESSAGES_EN.get(value))
    if translated is None:
        replacements = (
            (r"^缺少字段[：:]?\s*(.+)$", r"A required field is missing: \1"),
            (r"^(.+)不可用。?$", r"\1 is unavailable."),
            (r"^(.+)不存在。?$", r"\1 does not exist."),
            (r"^(.+)不能为空。?$", r"\1 must not be empty."),
            (r"^(.+)已存在。?$", r"\1 already exists."),
        )
        translated = value
        for pattern, replacement in replacements:
            if re.match(pattern, translated):
                translated = re.sub(pattern, replacement, translated)
                break
    if has_cjk(translated):
        reference = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        identifiers = re.findall(r"(?:[A-Za-z][A-Za-z0-9_.:/-]*|\d+(?:\.\d+)?)", value)
        detail = f" Identifiers: {', '.join(identifiers[:12])}." if identifiers else ""
        translated = ("The operation failed with a diagnostic that has no English translation yet. "
                      f"Diagnostic reference: {reference}.{detail} Use a zh-CN MCP connection to read the original diagnostic.")
    return prefix + translated


def _schema_to_english(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, list):
        return [_schema_to_english(item, path) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        child_path = path + (key,)
        if key == "description" and isinstance(item, str) and has_cjk(item):
            field = next((part for part in reversed(path) if part not in {"properties", "$defs", "items"}), "value")
            result[key] = SYSTEM_MESSAGES_EN.get(item, (f"Value for {field}. Follow the declared type, units, bounds, version, hash, and evidence constraints; "
                           "omission never grants an unsupported capability."))
        else:
            result[key] = _schema_to_english(item, child_path)
    return result


def _agent_instructions_to_english(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, list):
        return [_agent_instructions_to_english(item, path) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        # These are original engineering inputs/evidence, not interface copy.
        if key in {'project', 'projects', 'specification', 'preferences', 'design', 'designSpecification',
                   'contextSnapshot', 'params', 'sources', 'manifest', 'artifacts', 'requirements', 'decisions'}:
            result[key] = item
        elif key == 'schema' and isinstance(item, dict):
            result[key] = _schema_to_english(item)
        elif key == 'phase' and isinstance(item, str) and ('status' in value or path[-1:] == ('payload',)):
            result[key] = english_system_text(item)
        elif key == 'error' and isinstance(item, str) and 'status' in value and 'kind' in value:
            result[key] = english_error(item)
        elif key == 'note' and isinstance(item, str) and (
                path[-1:] == ('readability',) or ('revisionId' in value and ('sheet' in value or 'boardSha256' in value))):
            result[key] = english_system_text(item)
        elif key == "agentInstructions" and isinstance(item, str) and has_cjk(item):
            section = path[-1] if path else ""
            result[key] = CAPABILITY_AGENT_INSTRUCTIONS_EN.get(
                section,
                "Follow the English MCP initialization instructions for this capability; the returned schema and version-bound evidence remain authoritative.",
            )
        else:
            result[key] = _agent_instructions_to_english(item, path + (key,))
    return result


class LocalizedApiApp:
    """Localize system JSON fields at read time; never alter stored or downloaded evidence."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if (scope.get('type') != 'http' or not path.startswith('/api/') or path.endswith('/file')
                or requested_language(scope.get('headers', [])) != 'en'):
            return await self.app(scope, receive, send)
        start = None
        body = bytearray()
        async def capture(message):
            nonlocal start
            if message['type'] == 'http.response.start':
                headers = dict(message.get('headers', []))
                if b'application/json' in headers.get(b'content-type', b''):
                    start = message
                    return
            if start is None or message['type'] != 'http.response.body':
                await send(message)
                return
            body.extend(message.get('body', b''))
            if message.get('more_body'):
                return
            payload = json.loads(body)
            localized = _agent_instructions_to_english(payload)
            if isinstance(localized, dict) and localized.get('error') and isinstance(localized.get('message'), str):
                localized['message'] = english_error(localized['message'])
            output = json.dumps(localized, ensure_ascii=False, separators=(',', ':')).encode('utf8')
            headers = [(k, v) for k, v in start['headers'] if k.lower() != b'content-length']
            headers.extend([(b'content-length', str(len(output)).encode('ascii')), (b'vary', b'Accept-Language')])
            await send({**start, 'headers': headers})
            await send({'type': 'http.response.body', 'body': output, 'more_body': False})
        await self.app(scope, receive, capture)


def _localize_response(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if isinstance(payload.get("error"), dict):
        error = payload["error"]
        if isinstance(error.get("message"), str):
            error["message"] = english_error(error["message"])
        if isinstance(error.get("data"), str):
            error["data"] = english_error(error["data"])
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    if "instructions" in result:
        result["instructions"] = MCP_INSTRUCTIONS_EN
    tools = result.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                continue
            tool["description"] = english_tool_description(tool["name"])
            if "inputSchema" in tool:
                tool["inputSchema"] = _schema_to_english(tool["inputSchema"], (tool["name"], "inputSchema"))
            if "outputSchema" in tool:
                tool["outputSchema"] = _schema_to_english(tool["outputSchema"], (tool["name"], "outputSchema"))
    if result.get("isError") is True:
        for block in result.get("content", []):
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                block["text"] = english_error(block["text"])
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            for key in ("message", "error", "detail", "reason"):
                if isinstance(structured.get(key), str):
                    structured[key] = english_error(structured[key])
    else:
        if "structuredContent" in result:
            result["structuredContent"] = _agent_instructions_to_english(result["structuredContent"])
        for block in result.get("content", []):
            if not isinstance(block, dict) or not isinstance(block.get("text"), str):
                continue
            try:
                content_value = json.loads(block["text"])
            except json.JSONDecodeError:
                continue
            block["text"] = json.dumps(_agent_instructions_to_english(content_value), ensure_ascii=False, separators=(",", ":"))
    return payload


def requested_language(headers: list[tuple[bytes, bytes]]) -> str:
    raw = next((value.decode("latin-1") for key, value in headers if key.lower() == b"accept-language"), "")
    candidates = []
    for index, part in enumerate(raw.split(",")):
        bits = [item.strip() for item in part.split(";")]
        tag = bits[0].lower()
        quality = 1.0
        for bit in bits[1:]:
            if bit.startswith("q="):
                try:
                    quality = float(bit[2:])
                except ValueError:
                    quality = 0.0
        candidates.append((quality, -index, tag))
    for quality, _, tag in sorted(candidates, reverse=True):
        if quality <= 0:
            continue
        if tag == "en" or tag.startswith("en-"):
            return "en"
        if tag == "zh" or tag.startswith("zh-") or tag == "*":
            return "zh-CN"
    return "zh-CN"


class LocalizedMcpApp:
    """Translate protocol metadata and errors for one HTTP connection."""

    def __init__(self, app: Callable[..., Awaitable[None]]):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or requested_language(scope.get("headers", [])) != "en":
            await self.app(scope, receive, send)
            return
        start = None
        body = bytearray()

        async def capture(message):
            nonlocal start
            if message["type"] == "http.response.start":
                start = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return
            body.extend(message.get("body", b""))
            if message.get("more_body"):
                return
            output = bytes(body)
            try:
                payload = json.loads(output)
                output = json.dumps(_localize_response(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
            response_start = start or {"type": "http.response.start", "status": 200, "headers": []}
            headers = [(key, value) for key, value in response_start.get("headers", []) if key.lower() != b"content-length"]
            headers.append((b"content-length", str(len(output)).encode("ascii")))
            await send({**response_start, "headers": headers})
            await send({"type": "http.response.body", "body": output, "more_body": False})

        await self.app(scope, receive, capture)


MCP_INSTRUCTIONS_EN += '\nRead mechanical_schema.partIntegrityPolicy. Authored output parts default to one valid solid; disconnected pieces block adoption. Set parts[].solid_policy=single for one-piece shells and imported fabricated parts. Explicit assembly requires assembly_reason; never use it to hide a broken part. Direct frozen STEP and rigid transforms preserve source assemblies such as complete PCBA. Fabricated parts in engineering_plan require one solid. Inspect measures.partIntegrity and independent STEP checks; connectivity does not establish wall thickness, PCB clearance or assembly qualification.\n'
