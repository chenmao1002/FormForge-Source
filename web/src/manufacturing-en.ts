// Manufacturing workspace copy. Original engineering inputs and supplier
// evidence are preserved by their renderers rather than translated as UI copy.
export const manufacturingEnglish:Record<string,string>=Object.fromEntries(`
3D下单状态	3D order status
BOM器件清单	BOM component list
匹配	Matched
工程条件已更新，请重新核对后提交。	Project conditions changed; review before submitting again.
已确认的铜层数量	Confirmed copper-layer count
已解析	Parsed
打开此任务的专用浏览器	Open this task's dedicated browser
检查当前页面	Inspect current page
此报告形成后需求或偏好已变化，需要重新核对。	Requirements or preferences changed after this report; review again.
装配坐标	Placement coordinates
资料任务结束	File task finished
复核原文	Review statement (original)
装配方向/采购未审核	Assembly orientation / procurement not reviewed
原生board-only机械对象，不推测为待装器件	Native board-only mechanical object; not inferred to be an assembly component
Agent复核与待付款提交	Agent review and unpaid submission
后台采购与审核	Background procurement and review
本地下单执行器	Local order executor
通过 FormForge 专用浏览器操作嘉立创。首次在新窗口登录，之后尝试复用保存的会话。	Use the dedicated FormForge browser for JLCPCB. Sign in in its window initially; subsequent sessions try to reuse the saved session.
登录 / 人工接管	Sign-in / manual control
打开登录页面	Open sign-in page
检查登录状态	Check sign-in status
保存登录缓存	Save sign-in cache
关闭浏览器	Close browser
清除本机会话	Clear local session
密码、短信验证码和扫码操作请在专用浏览器完成。登录快照按当前 Windows 账号加密保存，Agent 只能读取状态。	Enter passwords, SMS codes and scan QR codes in the dedicated browser. Session snapshots are encrypted for the current Windows account; the Agent can only read status.
执行记录与设计版本分开保存。相同资料已有订单时，只核对该订单，避免重复下单。	Execution records are separate from design revisions. When an order already exists for the same files, reconcile that order to avoid duplicate submissions.
新资料可由 Agent 通过 MCP 创建执行任务。当前支持专用浏览器、固定资料与只读订单核对；上传、工艺、匹配和提交暂留人工检查点。	The Agent can create execution tasks for new files through MCP. This entry supports a dedicated browser, frozen files and read-only order reconciliation; upload, process setup, matching and submission retain manual checkpoints.
默认由Agent完成对抗性复核后直接提交至待付款，无需点击确认卡。付款和生产放行不自动执行；供应商备注不填写、不修改。	By default the Agent performs an adversarial review and submits to awaiting payment without a confirmation card. Payment and production release are not automatic; supplier remarks are never filled or modified.
等待Agent完成复核并执行；实际结果见后台任务和平台订单回执。	Waiting for Agent review and execution; actual results are recorded in background tasks and supplier order receipts.
刷新执行状态	Refresh execution status
刷新后台状态	Refresh background status
交回后台执行	Return control to background executor
可恢复的执行任务	Recoverable execution tasks
还没有执行任务。	No execution tasks yet.
从已有订单建立执行任务	Create execution task from an existing order
建立只读核对任务	Create read-only reconciliation task
新草案把该场景全部器件明确标为未确定，原采购版本保留。随后由Agent登记事实和依据，再生成新的采购版本。	The new draft marks all components in this scenario unresolved and preserves the previous procurement revision. The Agent can then register facts and evidence to create a new procurement revision.
独立记录准确型号、资料依据与待确定项。服务核对声明数据和原生约束，工程、供应商平台匹配和生产资格另验。	Record exact part numbers, evidence and unresolved items independently. The service checks declared data and native constraints; engineering, supplier matching and production qualification require separate review.
采购计划版本	Procurement plan revision
正在跟随采购更新	Following procurement updates
跟随采购更新	Follow procurement updates
还没有采购计划。Agent可通过MCP登记器件事实和原始依据，或先建立明确待选项的草案。	No procurement plan yet. Register component facts and original evidence through MCP, or first create a draft with explicit unresolved selections.
基准制造资料	Manufacturing file basis
采购基准制造资料	Procurement manufacturing basis
先准备包含待装器件且资料一致的制造场景。仅PCB场景不需要采购计划。	First prepare a consistent manufacturing scenario containing the components to assemble. A PCB-only scenario does not require a procurement plan.
建立新的待选草案	Create a new unresolved draft
待选原因	Reason for unresolved selection
创建待选采购草案	Create unresolved procurement draft
计划数量（可留空）	Planned quantity (optional)
等待准确料号、原始依据及质心/方向审核	Awaiting exact part numbers, original evidence and centroid/orientation review
平台订单记录	Supplier order records
查看已关联订单的本地记录。刷新本地记录不会查询嘉立创。	View local records of linked orders. Refreshing these records does not query JLCPCB.
刷新本地记录	Refresh local records
还没有关联订单。Agent 可通过 MCP 关联已有订单，原始制造文件和平台证据会一起保留。	No linked orders yet. The Agent can link existing orders through MCP while preserving original manufacturing files and supplier evidence.
结构件制造资料	Mechanical manufacturing files
选择制作件和数量，回读原始 STEP，再生成有来源的逐件资料包。	Select fabricated parts and quantities, verify the original STEP files, and generate traceable per-part packages.
机械版本	Mechanical revision
当前机械版本	Current mechanical revision
处置	Disposition
制作	Fabricate
已有	Provided
排除	Exclude
数量	Quantity
材料	Material
制造要求	Manufacturing requirements
依赖策略	Dependency policy
依赖说明	Dependency explanation
用途或原因	Purpose or reason
留空表示尚未明确	Leave blank if unspecified
结构资料机械版本	Mechanical revision for structural files
结构件资料记录	Mechanical file records
结构制作材料	Mechanical fabrication material
结构制造要求	Mechanical manufacturing requirements
结构依赖策略	Mechanical dependency policy
结构依赖说明	Mechanical dependency explanation
结构任务说明	Mechanical task description
结构件资料任务	Mechanical file task
工艺：3D 打印。材料可留空；供应商方向、支撑、薄壁、公差和强度仍需复核。	Process: 3D printing. Material may be unspecified; supplier orientation, supports, thin walls, tolerances and strength still require review.
检查并准备结构资料	Check and prepare mechanical files
此操作只检查和打包本地原件，完成后可下载；平台计价另行接入。	This action only checks and packages local originals for download; supplier quoting is handled separately.
尚无结构件资料任务。	No mechanical file tasks yet.
先在机械设计中建立并检查独立模型，再准备结构件资料。	First build and check an independent mechanical model, then prepare mechanical files.
结构件报价与订单	Mechanical quotes and orders
先建立绑定原始 STEP 的本地资料记录，再由 Agent 登记平台实际观察。此处不会上传文件、下单或付款。	First create a local record bound to original STEP files, then let the Agent record actual supplier observations. This entry does not upload, order or pay.
已检查的结构资料	Checked mechanical files
按当前版本重新填写	Reset form to current revision
先完成一次结构件资料检查，再建立报价记录。	Complete a mechanical file check before creating a quote record.
报价与订单记录	Quote and order records
正在跟随新记录	Following new records
尚无记录。原始 STEP 准备完成后，可在上方建立本地资料记录。	No records yet. After preparing original STEP files, create a local record above.
报价使用的结构资料	Mechanical files used for quoting
结构报价记录	Mechanical quote record
嘉立创官方接口	JLCPCB official API
获取工艺计价、核对已经存在的订单。不会创建第二张订单、付款或放行生产。	Request process pricing and reconcile existing orders. This does not create duplicate orders, pay or release production.
本机应用配置	Local application configuration
应用名称	Application name
应用编号（AppID）	Application ID (AppID)
访问标识（AccessKey）	Access identifier (AccessKey)
密钥（SecretKey）	Secret key (SecretKey)
从已授权的嘉立创应用取得这些信息。保存后按当前 Windows 账号加密，网页不提供密钥回读。不要把密钥发送到聊天。	Obtain these values from the authorized JLCPCB application. They are encrypted for the current Windows account; the page cannot read back the secret. Do not send secrets in chat.
加密保存本机配置	Encrypt and save local configuration
清空输入并重读配置	Clear inputs and reload configuration
核对已存在的订单	Reconcile existing orders
先把已经在平台提交的订单关联到此工程。此处不会创建新订单。	First link an order already submitted on the supplier platform to this project. This entry does not create new orders.
计价与查单记录	Pricing and order-query records
把明确的数量和工艺告诉你的 Agent，计价返回后会显示在这里。原文件与既有订单金额各自保留。	Give explicit quantities and process choices to the Agent. Returned pricing appears here; original files and existing order amounts remain separate.
正在跟随新任务	Following new tasks
还没有官方接口任务。	No official API tasks yet.
官方接口任务	Official API task
任务说明	Task description
3D后台任务：已返回结果	3D background task: result returned
结算已核对，尚未提交	Checkout verified; not submitted
无头运行	Headless
空闲	Idle
登录尚未核实	Sign-in not verified
尚未核实账号	Account not verified
尚未完成真实接口验证。	Actual API verification has not completed yet.
还没有保存登录缓存。	No saved sign-in cache yet.
已检测到登录	Signed-in state detected
需要本机 Edge 或 Chrome，以及浏览器执行依赖；请运行环境安装脚本。	Local Edge or Chrome and the browser execution dependencies are required; run the environment setup script.
独立采购执行器尚未启动。	The independent procurement executor has not started.
会话状态已更新。	Session status updated.
已打开专用浏览器，请在新窗口登录。	Dedicated browser opened; sign in in its window.
清除此电脑中 FormForge 专用浏览器的登录会话和缓存？下次需要重新登录。	Clear the dedicated FormForge browser session and cache on this computer? You will need to sign in again.
审核状态已变化	Review status changed
审核结束	Review finished
审核结果已更新；Agent核对资料并重新复核后继续。	Review results changed; continue after the Agent checks the files and reviews them again.
等待审核，下次自动查询	Awaiting review; will query again
查询暂停，需要处理	Query paused; attention required
重试审核查询	Retry review query
正在准备	Preparing
正在执行	Running
正在查询	Querying
正在检查	Checking
正在准备结算	Preparing checkout
正在核对提交结果	Reconciling submission result
已返回结果	Result returned
尚未准备	Not prepared
报价已核对	Quote verified
平台已响应，待独立查单	Supplier responded; independent order query pending
需要核对平台结果	Supplier result needs reconciliation
已核对未付款订单	Unpaid order verified
Agent先准备本次提交资料；请核对工程、工艺、物料和文件后确认。确认允许本次PCB及SMT提交，不包含付款或生产放行。	The Agent first prepares submission files. Review the project, process, materials and files before confirming this PCB/SMT submission. Confirmation excludes payment and production release.
提交至待付款：用户确认	Submit to awaiting payment: user confirmation
刷新提交确认	Refresh submission confirmation
暂无待确认的提交。	No submissions awaiting confirmation.
仅SMT贴片，通孔件自行焊接	SMT assembly only; through-hole parts hand-soldered
仅SMT贴片，通孔自焊	SMT only; through-hole parts hand-soldered
按列出的全部物料装配	Assemble all listed materials
已过期，需重新准备提交确认	Expired; prepare a new submission confirmation
旧工程版本，不能提交	Older project revision; submission blocked
已确认，等待Agent执行	Confirmed; awaiting Agent execution
等待用户确认	Awaiting user confirmation
此资料已有提交尝试。请先核对平台已有订单；仅在确认恢复范围后继续。	These files have a prior submission attempt. Reconcile existing supplier orders first; continue only after confirming the recovery scope.
展开核对文件与逐项物料	Expand files and individual materials
通孔	Through-hole
贴片	SMD
确认本次提交至待付款	Confirm submission to awaiting payment
拒绝本次提交	Reject submission
已确认，Agent可执行本次提交。	Confirmed; the Agent can execute this submission.
已拒绝本次提交。	Submission rejected.
提交	Submit
固定资料	Freeze files
打开工作页	Open work page
检查页面	Inspect page
核对订单	Reconcile order
中断恢复	Recover interrupted execution
上传资料	Upload files
工艺配置	Configure process
器件匹配	Match components
提交前核对	Pre-submission review
结果	Result
执行记录已更新。	Execution record updated.
需要登录	Sign-in required
需要人工接管	Manual control required
需要重新打开	Reopen required
执行失败	Execution failed
执行已中断，需核对	Execution interrupted; reconcile before continuing
已阻止重复操作	Duplicate operation blocked
等待人工表单操作	Awaiting manual form operation
资料已固定	Files frozen
已关联订单	Order linked
已关联既有订单	Existing order linked
选择工程后查看下单执行记录。	Select a project to view execution records.
建立本地报价资料记录	Create local quote-file record
用途与说明	Purpose and notes
供应商工艺	Supplier process
颜色 / 表面	Color / finish
表面处理	Surface finish
材料等可留空，未知值会保留；建立记录不表示平台已经报价。	Material and other unknown values may remain blank. Creating a record does not mean the supplier has quoted.
报价依赖策略	Quote dependency policy
报价依赖说明	Quote dependency explanation
结构报价说明	Mechanical quote notes
要求当前版本和依赖一致	Require current revision and matching dependencies
明确跟踪历史模型报价	Explicitly track a historical model quote
本地报价资料已记录，尚未上传平台。	Local quote files recorded; not uploaded to supplier.
正在读取所选报价记录…	Loading selected quote record…
正在读取最新报价记录…	Loading latest quote record…
跟随最新记录	Follow latest record
本地资料已准备	Local files prepared
记录平台状态	Record supplier status
记录已上传	Upload recorded
记录平台报价	Supplier quote recorded
订单已提交	Order submitted
审核中	Under review
平台退回	Returned by supplier
观察到待付款	Awaiting payment observed
出现支付或生产状态	Payment or production activity detected
仅本地资料：尚未上传平台，没有实际报价或订单。	Local files only: not uploaded; no actual quote or order.
历史观察出现支付或生产状态；如实保留，请停止后续执行。	Historical observations include payment or production activity; preserve the record and stop further execution.
输入原件无法重新核对。	Original inputs could not be reverified.
上游已变化；本记录仍绑定原始资料，使用前需复核。	Upstream inputs changed. This record remains bound to its original files and requires review before use.
零件 / 数量	Part / quantity
零件	Part
工艺	Process
本次金额	Current amount
此前金额	Previous amount
打开嘉立创 3D 报价页 ↗	Open JLC3DP quoting page ↗
记录	Record
观察	Observation
检查未完成	Check incomplete
STEP 文件回读检查通过	STEP round-trip checks passed
STEP 文件检查有未通过项	Some STEP checks failed
STEP/STL 文件检查通过	STEP/STL file checks passed
STEP/STL 文件检查有未通过项	Some STEP/STL file checks failed
处置 / 数量	Disposition / quantity
回读尺寸 mm	Verified dimensions (mm)
体积 mm³	Volume (mm³)
不制作	Do not fabricate
无独立单实体 STEP，不能直接作为制作件。	No independent single-solid STEP is available; cannot directly classify as a fabricated part.
此机械版本或其依赖已变化。请重新验证模型，或明确选择历史计价并说明原因。	The mechanical revision or its dependencies changed. Reverify the model or explicitly select historical quoting and provide a reason.
明确为历史模型准备计价资料	Explicitly prepare quote files for a historical model
此记录形成后需求或偏好已变化；它仍属于当时的输入。	Requirements or preferences changed after this record; it remains bound to the original inputs.
结构件核对已开始。	Mechanical file checks started.
原始资料、版本与摘要	Original files, revisions and hashes
无商业快照	No commercial snapshot
资料时效内	Snapshot within validity period
需刷新资料	Snapshot needs refresh
声明库存覆盖	Declared stock covers demand
声明库存不足	Declared stock is insufficient
库存未知	Stock unknown
供应商选料	Supplier-selected components
客供器件	Customer-supplied components
尚未确定	Unresolved
未给供应商编码	Supplier code not specified
供应商质心和方向尚未审核	Supplier centroid and orientation not reviewed
器件事实原件 ↗	Original component facts ↗
继续读取器件	Load more components
调用方整理	Compiled by caller
来源性质由调用方声明	Source classification declared by caller
原件 ↗	Original file ↗
小计仅来自可计算行；料号、数量、价格或库存仍有缺项，不能作为整机报价。	Subtotal includes calculable lines only. Missing part numbers, quantities, prices or stock prevent a complete product quote.
所选采购行的声明快照可计算；不含PCB、装配、税费、运费及平台最终损耗。	Selected procurement snapshots can be calculated; PCB, assembly, tax, shipping and final supplier attrition are excluded.
快照不是预留库存或正式平台报价；币种分别计算。页面每分钟重查已登记快照时效，不联网更新库存。	Snapshots are not reserved stock or formal supplier quotes; currencies are calculated separately. Registered snapshot freshness is checked every minute without querying live stock.
没有可用的材料小计。	No material subtotal available.
材料费快照与时效	Material cost snapshots and freshness
原图/PCB版本或需求已变化，当前采购依据需要复核。	Schematic/PCB revisions or requirements changed; review the procurement basis.
外部调用方的审核声明 · 采购检查不包含平台下单或生产放行，订单状态请另行查看。	External caller review statement. Procurement checks exclude supplier ordering and production release; inspect order status separately.
已选器件与来源	Selected components and sources
完整计划与来源 ↗	Full plan and provenance ↗
下载采购计划包 ↗	Download procurement plan package ↗
下载候选诊断包 ↗	Download candidate diagnostics ↗
采购检查报告 ↗	Procurement check report ↗
完整采购审核表 ↗	Full procurement review table ↗
嘉立创选型 BOM（非生产）↗	JLCPCB selection BOM (not for production) ↗
嘉立创选型坐标（非生产）↗	JLCPCB selection placement (not for production) ↗
逐件选型与坐标待核说明 ↗	Per-component selection and placement review notes ↗
更多问题见完整报告。	See the full report for more issues.
更多器件见完整报告。	See the full report for additional components.
更多来源见完整采购计划或工程资料。	See the complete procurement plan or Project documents for additional sources.
新草案把该场景全部器件明确标为未确定，原采购版本保留。随后由Agent登记事实和依据，再生成新的采购版本。	The draft marks every component in this scenario unresolved and preserves prior procurement revisions. The Agent can register facts and evidence before creating a new revision.
料号资料检查通过	Part-data checks passed
先准备包含待装器件且资料一致的制造场景。仅PCB场景不需要采购计划。	Prepare a consistent manufacturing scenario containing assembly components first. PCB-only scenarios do not require a procurement plan.
已提交待选采购草案。	Unresolved procurement draft submitted.
待选采购草案：	Unresolved procurement draft:
供应商质心和方向尚未审核	Supplier centroid and orientation have not been reviewed
电阻值	Resistance
电容量	Capacitance
公差	Tolerance
额定功率	Rated power
额定电压	Rated voltage
介质	Dielectric
官方 HTTPS 接口 · 本机服务核对	Official HTTPS API · verified by local service
浏览器观察 · Agent 记录	Browser observation · recorded by Agent
来源：Agent 记录的浏览器观察，未验证平台原始文件字节。	Source: Agent-recorded browser observation; original supplier file bytes are unverified.
来源：本机服务核对的官方 HTTPS 回执。未验证平台原始文件字节或回执签名。	Source: official HTTPS receipt checked by the local service. Original supplier file bytes and receipt signature are unverified.
历史观察出现已支付或生产状态，已保留该事实并停止后续动作。	Historical observations include payment or production activity; this fact is retained and further actions are stopped.
设计、采购或需求已变化；此订单仍绑定原始资料，请核对差异。	Design, procurement or requirements changed. This order remains bound to the original files; review the differences.
订单审核和器件匹配不代表 DFM、方向或生产批准；此页面不执行下单、付款或放行。	Order review and component matching do not constitute DFM, orientation or production approval. This page does not submit, pay or release production.
付款、DFM、方向和生产状态尚未核实。	Payment, DFM, orientation and production status are not verified.
上一页订单	Previous orders
下一页订单	Next orders
跟随最新观察	Follow latest observation
查看订单与证据历史	View order and evidence history
原始 BOM	Original BOM
原始 Gerber	Original Gerber
原始坐标 CPL	Original placement CPL
状态未明确	Status unspecified
已提交	Submitted
等待平台审核	Awaiting supplier review
订单审核通过	Order review passed
平台显示待付款	Supplier shows awaiting payment
发现支付或生产状态，停止后续动作	Payment or production activity detected; stop further actions
未明确	Unspecified
待确认	Awaiting confirmation
已确认	Confirmed
未通过	Failed
本机加密配置已保存。下一次真实请求会验证接口。	Encrypted configuration saved locally. The next actual request will verify the API.
请先配置本机应用，密钥只保存在此电脑。	Configure the local application first; secrets stay on this computer.
请先选择本工程订单。	Select an order from this project first.
开始核对现有订单。	Existing-order reconciliation started.
工艺计价	Process pricing
核对现有订单	Reconcile existing order
下单执行任务	Order execution task
本次接口诊断	API diagnostics for this attempt
下载本次失败诊断 ↗	Download attempt failure diagnostics ↗
下载本次接口回执 ↗	Download attempt API receipt ↗
未取得完整响应，未生成响应摘要。	No complete response received; no response digest generated.
此记录用于定位失败，不代表已核对订单；原请求和响应正文未保存。	This record diagnoses a failure and does not verify an order. Original request and response bodies were not saved.
核对已有订单	Reconcile existing order
工艺计价	Process pricing
未核定	Unverified
尚无	None yet
本机应用配置	Local application configuration
正在读取所选任务…	Loading selected task…
正在读取最新任务…	Loading latest task…
本次费用字段与服务项	Cost fields and services for this attempt
运费尚未明确；原件未上传审核，这不是付款记录。	Shipping is unspecified; originals have not been uploaded for review. This is not a payment record.
订单类别	Order category
平台下单日期（中国时间）	Supplier order date (China time)
接口尺寸单位	API dimension units
毫米	Millimeters
厘米	Centimeters
按平台下单日期核对同编号订单。查询文档没有注明尺寸单位，需按实际回执明确选择；与原文件不符会停止关联。	Reconcile matching order numbers by supplier order date. The query documentation omits dimension units; select them from the actual receipt. Differences from the original files block linking.
关联 PCB 单号来自登记的网页观察；当前官方查单入口仅支持 PCB。	The linked PCB order number comes from a registered browser observation. The current official order-query entry supports PCB orders only.
已核对制造任务明确的数量与板厚约定，外形和层数需另审。	The manufacturing task's declared quantity and thickness were checked; outline and layer count require separate review.
层数和外形已与本地 CAM 报告核对；板厚和数量为本次订单的明确约定。	Layer count and outline match the local CAM report; thickness and quantity are explicitly declared for this order.
独立制造文件，无 CAD 版本	Independent manufacturing files, without a CAD revision
原输入无法重新核对。	Original inputs could not be reverified.
当前上游与本记录不一致，保留当时的报告；使用前需复核。	Current upstream inputs differ from this record. The original report is retained and requires review before reuse.
该场景缺少完整制造审核包。	This scenario lacks a complete manufacturing review package.
尚无检查结论	No check verdict yet
资料需要处理	Files need attention
设计版本或需求已变化；本场景仍属于原输入，继续使用前需重新核对。	Design revision or requirements changed; this scenario remains bound to the original inputs. Review before reuse.
先选择工程。	Select a project first.
先选择一个工程。	Select a project first.
请先选择工程。	Select a project first.
所列文件检查通过；尚未生产放行	Listed file checks passed; production not released
首条流程需要可追溯的原图与PCB。外部Gerber/BOM/坐标独立审核将在后续接入。	This workflow requires traceable schematic and PCB inputs. Independent Gerber/BOM/placement review is handled separately.
固定版本制造资料审核	Review manufacturing files for a fixed revision
已开始准备制造审核资料。	Manufacturing review file preparation started.
外部制造资料审核；工程与生产资格另列。	External manufacturing file review; engineering and production qualification are assessed separately.
根据所选DNP列明确装配；空白表示装配。	Assembly is determined by the selected DNP column; blank means assemble.
独立解析组件尚未就绪，请让Agent核对安装状态。	The independent parser is not ready; ask the Agent to check installation.
外部文件审核已开始；原始资料和设计保持。	External file review started; original files and design are preserved.
先读取工作表	Read worksheets first
选择表格	Select a table
原始表格	Original table
文字编码	Text encoding
中文GB18030	Chinese GB18030
分隔符	Delimiter
逗号	Comma
分号	Semicolon
制表符	Tab
工作表	Worksheet
读取	Read
表头行号	Header row number
表头	Header
先读取工作表	Read worksheets first
位号列	Reference column
每板数量列	Quantity per board column
数值列（可空）	Value column (optional)
封装列（可空）	Footprint column (optional)
制造商料号列（可空）	Manufacturer part-number column (optional)
供应商编码列（可空）	Supplier-code column (optional)
X坐标列	X coordinate column
Y坐标列	Y coordinate column
角度列	Rotation column
装配面列	Assembly-side column
DNP列	DNP column
位号可按逗号、分号或空格拆分，并展开同前缀范围（如R1-R3）。每个位号每板1件；选择数量列时必须与位号数量相符。	References may be separated by commas, semicolons or spaces; same-prefix ranges such as R1-R3 are expanded. Each reference represents one component per board; a selected quantity column must match the reference count.
允许锚点在板外的位号	References allowed to have anchors outside the board
每行：位号 = 原因	One per line: reference = reason
明确省略的位号	Explicitly omitted references
不装件规则	DNP rules
按明确DNP列	Use explicit DNP column
明确声明没有DNP	Explicitly declare no DNP
表示不装的列值（逗号分隔）	Values meaning do not assemble (comma separated)
表示装配的列值（首个逗号表示空白）	Values meaning assemble (initial comma denotes blank)
DNP规则说明	DNP rule explanation
封装名称核对	Footprint-name comparison
忽略库名前缀，只比较封装名称	Ignore library prefix; compare footprint name only
文本完全相同	Exact text match
坐标单位	Coordinate units
英寸	Inches
正面列值（逗号分隔）	Front-side values (comma separated)
背面列值（逗号分隔）	Back-side values (comma separated)
正面坐标约定	Front-side coordinate convention
背面坐标约定	Back-side coordinate convention
本文件X正方向	Positive X direction in this file
本文件Y正方向	Positive Y direction in this file
图中向上	Up in drawing
图中向下	Down in drawing
图中向右	Right in drawing
图中向左	Left in drawing
源表角度正方向	Positive rotation direction in source table
逆时针	Counterclockwise
顺时针	Clockwise
零角度方向补偿 / °	Zero-angle offset (°)
坐标整体旋转 / °	Global coordinate rotation (°)
原点平移X / mm	Origin X offset (mm)
原点平移Y / mm	Origin Y offset (mm)
元数据建议：	Metadata suggestion:
无明确层身份，请核对原始资料。	No explicit layer identity; inspect the original files.
请选择用途	Select purpose
镀通钻孔/槽	Plated drill holes/slots
非镀通钻孔/槽	Non-plated drill holes/slots
正面阻焊开窗	Front solder-mask openings
背面阻焊开窗	Back solder-mask openings
正面丝印	Front silkscreen
背面丝印	Back silkscreen
正面锡膏	Front solder paste
背面锡膏	Back solder paste
成品外形	Finished outline
Gerber作业元数据	Gerber job metadata
装配表（下方选择）	Assembly table (select below)
明确忽略	Explicitly ignore
缺少钻孔单位/数字格式时补充	Supply missing drill units/number format
补充单位	Additional units
使用文件内明确声明	Use the file's explicit declaration
整数位	Integer digits
小数位	Decimal digits
省略的零位	Omitted zeros
前导零	Leading zeros
尾随零	Trailing zeros
忽略原因	Reason for ignoring
正在读取所选原件…	Loading selected original file…
位号说明请使用“位号 = 原因”。	Use “reference = reason” for reference explanations.
请填写有效数字。	Enter a valid number.
资料已变化，请重新选择已登记文件。	Files changed; select registered files again.
读取	Read
请先读取两份表格的表头并确认列映射。	Read both table headers and confirm column mappings first.
请选择BOM和坐标表。	Select BOM and placement tables.
请读取文件清单并确认映射。	Read the file list and confirm mappings.
请选择全部文件的用途。	Select a purpose for every file.
请填写忽略原因。	Provide a reason for ignoring the file.
已明确选择本次使用的全部钻孔文件。	All drill files used in this review have been explicitly selected.
已核对本包没有独立钻孔文件；未证明外部设计绝无其他孔。	No independent drill file was found in this package; this does not prove the external design has no other holes.
制造层图	Manufacturing layer preview
实际Gerber、钻孔及装配锚点预览	Actual Gerber, drill and assembly-anchor preview
拖动平移 · 滚轮缩放 · 所有层使用同一俯视坐标；箭头只表示声明的角度。	Drag to pan · wheel to zoom · all layers share top-view coordinates; arrows show declared angles only.
装配锚点与正面铜层	Assembly anchors and front copper
孔与轮廓的曲线几何误差上限为 0.005mm。装配点和箭头来自明确的坐标约定，不代表供应商质心或一脚方向已经核实。	Hole and outline curve approximation error is bounded by 0.005 mm. Assembly points and arrows use the declared coordinate convention; supplier centroids and pin-1 orientation remain unverified.
所列检查通过	Listed checks passed
文件存在待处理项；当前设计保持	Files need attention; current design preserved
完整检查报告 ↗	Complete check report ↗
下载完整审核包 ↗	Download complete review package ↗
JLCPCB BOM 审核副本 ↗	JLCPCB BOM review copy ↗
JLCPCB CPL 审核副本 ↗	JLCPCB CPL review copy ↗
其余问题见完整报告。	See the complete report for remaining issues.
本次未明确确认方式	Confirmation method unspecified for this attempt
手动确认	Manual confirmation
允许自动确认	Automatic confirmation allowed
手动	Manual
非手动	Non-manual
本次未显示	Not shown in this observation
本页未看到	Not seen on this page
本页未看到订单号	No order number seen on this page
已看到订单号	Order number observed
已看到	Observed
无	None
不使用	Do not use
尚未明确	Unspecified
尚未核实账号	Account not verified
未计算	Not calculated
未支付	Unpaid
已支付	Paid
未声明数值	Value not declared
未取得或已排除	Unavailable or excluded
失败	Failed
任务失败	Task failed
已完成	Completed
已结束	Finished
排队中	Queued
部分	Partial
完整	Complete
有问题	Issues found
有待处理项	Needs attention
类型未明确	Type unspecified
底层	Bottom layer
顶层	Top layer
浏览器已关闭	Browser closed
人工接管	Manual control
可见或最小化窗口	Visible or minimized window
方向校验	Orientation verification
自行校验	Self-verification
工程师协助（不代表已完成）	Engineer assistance (not yet completed)
SMT 状态	SMT status
PCB 参数与备注	PCB parameters and remarks
贴装范围与备注	Assembly scope and remarks
此前器件匹配	Previous component matching
器件匹配：本次尚未观察。	Component matching: not observed in this attempt.
订单审核	Order review
支付	Payment
本次观察说明	Notes for this observation
当前设计	Current design
历史设计	Historical design
历史/候选	History/candidate
历史/候选	History/candidate
审核状态已变化	Review status changed
本次未明确确认方式	Confirmation method unspecified
较早记录	Earlier records
较新记录	Later records
跟随最新任务	Follow latest task
跟随后续任务	Follow subsequent tasks
正在跟随最新	Following latest
记录摘要	Record summary
金额观察	Amount observation
记录的正式报价	Recorded formal quote
平台估算	Supplier estimate
平台页面估算	Supplier page estimate
身份仅由调用者声明	Identity declared by caller only
材料未显示	Material not shown
待核价	Price pending
未明确	Unspecified
样板	Prototype
小批量	Small batch
标准型	Standard
经济型	Economy
按列出物料装配	Assemble listed materials
选装	Optional assembly
明确省略	Explicitly omitted
当前计划 ·	Current plan ·
候选 ·	Candidate ·
未采纳 ·	Not adopted ·
· 当前机械版本	· Current mechanical revision
· 第	· Page
第	Page
行	Rows
个对象	objects
元	CNY
铜层	copper layers
ZIP内 ·	Within ZIP ·
文件用途	File purpose
；工程已变化，提交前请明确重新填写。	; the project changed; explicitly reset the form before submitting.
`.trim().split('\n').map(row=>{const i=row.indexOf('\t');return [row.slice(0,i).trim(),row.slice(i+1)];}));

export function translateManufacturingText(value:string,translate:(text:string)=>string):string|undefined{
  let m:RegExpMatchArray|null;
  if((m=value.match(/^完整引脚连接：(匹配|需处理)。坐标已按板原点、毫米、Y轴方向及背面角度核对，供应商装配方向另审。$/u)))return `Complete pin connectivity: ${translate(m[1])}. Coordinates were checked against the board origin, millimeter units, Y-axis convention and back-side angles; supplier assembly orientation requires separate review.`;
  if((m=value.match(/^(.+) · 省略：(.+)$/u)))return `${m[1]} · omitted: ${translate(m[2])}`;
  if((m=value.match(/^(.+) (处置原因|处置|数量|报价材料|报价供应商工艺|报价颜色|报价表面处理)$/u))){const labels:Record<string,string>={处置原因:'disposition reason',处置:'disposition',数量:'quantity',报价材料:'quote material',报价供应商工艺:'quote process',报价颜色:'quote color',报价表面处理:'quote finish'};return `${m[1]} ${labels[m[2]]}`;}
  if((m=value.match(/^创建待选Procurement draft$/u)))return 'Create unresolved procurement draft';
  if((m=value.match(/^本机加密配置已保存 · (.+)$/u)))return `Encrypted local configuration saved · ${m[1]}`;
  if((m=value.match(/^最近一次接口验证：(.+)$/u)))return `Last API verification: ${m[1]}`;
  if((m=value.match(/^客编 (.+)$/u)))return `Customer ID ${m[1]}`;
  if((m=value.match(/^本机登录缓存已保存 · (.+)；是否仍然有效以平台检查为准。$/u)))return `Local sign-in cache saved · ${m[1]}; validity requires a supplier session check.`;
  if((m=value.match(/^(\d+)种零件，每种(\d+)件 · (.+)$/u)))return `${m[1]} part types, ${m[2]} copies each · ${translate(m[3])}`;
  if((m=value.match(/^3D后台任务：(.+)$/u)))return `3D background task: ${translate(m[1])}`;
  if((m=value.match(/^应付金额 (.+) 元；未执行付款。$/u)))return `Amount payable: CNY ${m[1]}; no payment performed.`;
  if((m=value.match(/^未税报价 (.+) 元。$/u)))return `Quote excluding tax: CNY ${m[1]}.`;
  if((m=value.match(/^工程版本 (\d+) · 3D (\d+)种零件 × (\d+)件$/u)))return `Project version ${m[1]} · 3D ${m[2]} part types × ${m[3]} copies`;
  if((m=value.match(/^工程版本 (\d+) · PCB (\d+)片 \/ SMT (\d+)片$/u)))return `Project version ${m[1]} · PCB ${m[2]} boards / SMT ${m[3]} boards`;
  if((m=value.match(/^复核已记录 · (\d+)份证据；已授权本次待付款提交，实际订单状态另列。$/u)))return `Review recorded · ${m[1]} evidence items; this unpaid submission is authorized; actual order status is recorded separately.`;
  if((m=value.match(/^单实体原件：(.+)$/u)))return `Single-solid original: ${m[1]}`;
  if((m=value.match(/^表单绑定工程 V(\d+) · 机械 ([\w-]+)(.*)$/u)))return `Form bound to project V${m[1]} · mechanical ${m[2]}${m[3]?translate(m[3]):''}`;
  if((m=value.match(/^(.+) · (\d+) 件$/u)))return `${m[1]} · ${m[2]} copies`;
  if((m=value.match(/^STEP (.+)；改数量请另建结构资料。$/u)))return `STEP ${m[1]}; create a separate mechanical file record to change quantities.`;
  if((m=value.match(/^订单：(.+)$/u)))return `Orders: ${m[1]}`;
  return undefined;
}

// Apply only after full-message patterns, so a translated status segment does
// not prevent a more specific revision or task message from matching.
export function translateManufacturingSegments(value:string):string|undefined{
  const segments=value.split(' · ');
  if(segments.length>1){const output=segments.map(s=>manufacturingEnglish[s.trim()]??s);if(output.join(' · ')!==value)return output.join(' · ');}
  return undefined;
}
