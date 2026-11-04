import engineeringMessages from '../../src/formforge/system_messages_en.json';
import {translateSchematicText} from './schematic-candidates-en';
import {firmwareFlashEnglish} from './firmware-flash-en';
import {manufacturingEnglish,translateManufacturingText,translateManufacturingSegments} from './manufacturing-en';
export type UiLanguage='zh-CN'|'en';

const STORAGE_KEY='formforge-language';

const english:Record<string,string>={
 '功能分区分析时，附带接口与失败出口检查':'Include interface and failed-exit checks with functional group analysis',
 '仅分析功能分区；本次未执行接口、通道和出口几何检查。':'Functional groups only; interface, corridor and exit geometry checks were not performed.',
 '布局规划与阻挡诊断':'Layout planning and obstacle diagnosis',
 '按当前版本分析接口位置和已记录的失败出口。可声明功能分区；分析结果用于选择下一步。':'Analyze interface positions and recorded failed exits for this revision. Declare functional groups to help choose the next step.',
 '功能分区（可选，每行填写“名称：器件位号”，位号用逗号分隔）':'Functional groups (optional; one Name: references per line, comma-separated references)',
 '控制：U1, R1, C1':'Control: U1, R1, C1',
 '分析当前版本':'Analyze this revision',
 '每个功能分区请填写名称和器件位号。':'Enter a name and component references for each functional group.',
 '接口基准已对齐板边':'Interface datum aligned with the board edge',
 '需要调整接口位置或方向':'Review interface position or direction',
 '封装内部间距与线宽冲突，先审查器件或规则':'Internal pad spacing conflicts with trace width; review the footprint or routing rule first',
 '已有合法直出，可继续实际找路':'A legal straight exit exists; continue actual pathfinding',
 '比较外围器件局部摆放':'Compare local placement of surrounding components',
 '先审查阻挡铜线的明确重布范围':'Review the explicit rerouting scope for obstructing copper',
 '进一步核对板边、铜区、孔和本体约束':'Review board-edge, copper-zone, hole and body constraints',
 '本报告绑定所选版本；直出或通道几何检查不代替实际布通、配对件插拔和制造验收。':'This report is bound to the selected revision. Exit and corridor geometry checks do not establish routability, mating clearance or manufacturing acceptance.',
 "清理预览和删除量显示文件总大小；压缩后的磁盘占用见上方存储概览。":"Cleanup previews and deletion totals show logical file sizes. See the storage overview above for compressed disk usage.",
 "后台统计进行中，页面会自动更新；每个目录最多扫描60秒。文件总大小与磁盘占用分别统计，排除链接；环境、依赖与其他目录不计入。压缩后的实际占用以落盘结果为准，节省比例因文件而异。“至少”表示尚未统计完整。":"Background measurement is running and updates automatically; each folder is scanned for at most 60 seconds. Total file size and stored file size are measured separately. Links, environments, dependencies and unlisted folders are excluded. Compression savings vary by file and reflect flushed data. At least means the measurement is incomplete.",

 "新工程默认使用无损压缩存储。格式、内容和版本历史保持完整；已有工程文件保持原样。":"New projects use lossless compressed storage by default. Formats, contents and version history remain intact; existing project files are unchanged.",
 "新工程使用普通存储；当前平台或目录未启用无损压缩。":"New projects use standard storage; lossless compression is not enabled on this platform or folder.",
 "磁盘占用未完整统计":"Stored file size is not fully measured", "磁盘占用":"Stored file size", "文件总大小":"Total file size", 
 "依赖已核对的工程工作副本":"Dependency-checked engineering work copies",
 "扫描下载缓存、测试临时副本、原生读取缓存，以及依赖核对通过的工程工作副本。仅处理超过 1 小时未变化的目录；版本源、登记产物、报告、失败记录和登录资料保留。":"Scan download caches, temporary test copies, native read caches and dependency-checked engineering work copies. Only folders unchanged for over one hour are eligible. Preserve revision sources, registered artifacts, reports, failure records and login data.",
 "仅包含已完成 KiCad 检查的可再生成输入副本。已核对冻结源完整性、内容一致性和工程记录引用；清理前会再次核对依赖。":"Only regenerable input copies from completed KiCad inspections are included. Source integrity, identical content and project references have been checked. Dependencies are checked again before deletion.",
 "清空回收站":"Empty recycle bin",
 "确认清空回收站":"Confirm empty recycle bin",
 "回收站正在清理，完成后可扫描缓存。":"The recycle bin is being emptied. Cache scanning will be available afterward.",
 "原生图":"Native plot",
 "实时图（近似）":"Live view (approximate)",
 "设备连接与烧录须在“烧录与设备”页单独授权。":"Authorize hardware access separately in the Flash and devices tab.",
 "可独立从原理图开始，无需先制作 PCB。当前已支持 G030、G071、F103、F401、F411、F407 的已登记精确型号。":"Firmware can start from a schematic before a PCB is built. Registered exact G030, G071, F103, F401, F411 and F407 targets are supported.",
 "修改名称":"Rename",
 "修改工程名称":"Rename project",
 "保存名称":"Save name",
 "工程名称已修改。":"Project renamed.",
 "请输入工程名称。":"Enter a project name.",
 "常用规则预设（名称 · 应用次数）":"Rule presets (name \u00b7 application count)",
 "按本机工程中登记的实际应用次数排序；仅影响后续任务。已有PCB规则仍须单独修改并检查。":"Sorted by actual applications recorded in local projects. Applies to future tasks; existing PCB rules require a separate checked edit.",
 "规则预设":"Rule preset",
 "请选择预设":"Choose a preset",
 "预览应用到当前工程":"Preview for current project",
 "应用预设到当前工程":"Apply preset to current project",
 "将当前工程覆盖保存为新预设":"Save current project overrides as a new preset",
 "预设名称":"Preset name",
 "预设说明":"Preset description",
 "适用场景与限制":"Scope and limits",
 "保存工程覆盖为预设":"Save project overrides as a preset",
 "规则预设已保存。":"Rule preset saved.",
 "预设已应用，后续任务使用新默认值；历史版本保持。":"Preset applied to future tasks; historical revisions are unchanged.",

 "PCB 新铺铜默认参数":"PCB defaults for new zones",
 "新铺铜净距（mm）":"New zone clearance (mm)",
 "新铺铜最小厚度（mm）":"New zone minimum thickness (mm)",
 "热焊间隙（mm）":"Thermal relief gap (mm)",
 "热焊辐条宽度（mm）":"Thermal spoke width (mm)",
 "新特征模型干涉检查":"Interference checks for new feature models",
 "按模型声明":"Use model declaration",
 "默认启用":"Enable by default",
 "默认固件模板":"Default firmware template",
 "闪灯":"Blink",
 "串口回显":"UART echo",
 "I²C探测与串口输出":"I2C probe with UART output",
 "审核资料默认板材（留空待定）":"Review package material (blank if undecided)",
 "审核资料默认板厚（mm）":"Review package board thickness (mm)",
 "审核资料默认表面处理":"Review package surface finish",
 "审核资料默认阻焊颜色":"Review package solder mask color",

 '默认图纸大小':'Default paper size','每页器件上限':'Maximum components per sheet','排版策略':'Layout strategy','易读优先':'Readability first','快速生成':'Fast generation',
 '按工程设置自动分页与排版':'Paginate and lay out using project settings',
 '使用已保存的工程规则生成新版本。当前支持单页、普通单单元符号；分组超限或不支持对象会明确拒绝。':'Create a new revision using saved project rules. Supports single-sheet sources and ordinary single-unit symbols. Oversized groups and unsupported objects are rejected explicitly.',
 '纸张用于新建与自动排版；每页上限和策略在自动排版任务生效。易读优先增加间距与就近短线；平衡适度连线；快速生成使用标签。功能组超限须先拆组。':'Paper size applies to creation and automatic layout. Sheet limits and strategy apply to layout tasks. Readability adds spacing and local wires; balanced uses fewer wires; fast uses labels. Split oversized functional groups explicitly.',

  "通用":"General",
  "通用设置":"General settings",
  "在此浏览器立即生效，与顶部语言选择同步。":"Applies immediately in this browser and stays in sync with the language selector.",
  "原理图设置":"Schematic settings",
  "机械设计设置":"Mechanical settings",
  "固件设置":"Firmware settings",
  "制造任务默认值":"Manufacturing task defaults",
  "新原理图纸张":"Paper for new schematics",
  "创建任务时限（秒）":"Creation time limit (seconds)",
  "螺钉默认颜色（可沿用模型）":"Default screw color (or retain model color)",
  "自定义颜色":"Custom color",
  "建模任务时限（秒）":"Modeling time limit (seconds)",
  "闪灯模板周期（毫秒）":"Blink template period (milliseconds)",
  "串口模板波特率":"UART template baud rate",
  "构建任务时限（秒）":"Build time limit (seconds)",
  "资料包计划数量（留空待定）":"Planned package quantity (blank if undecided)",
  "资料包任务时限（秒）":"Package task time limit (seconds)",
  "个人默认":"Personal defaults",
  "仅复制到以后新建的工程；已有工程须单独应用。":"Copied only to new projects; apply separately to existing projects.",
  "保存此模块默认值":"Save module defaults",
  "当前工程覆盖":"Current project overrides",
  "读取工程覆盖":"Read project overrides",
  "填入个人默认":"Fill personal defaults",
  "恢复工程创建时默认":"Restore project creation defaults",
  "保存工程覆盖":"Save project overrides",
  "当前来源：工程覆盖":"Current source: project override",
  "当前来源：工程创建时默认值":"Current source: defaults at project creation",
  "将应用当前个人默认值。":"Current personal defaults will be applied.",
  "将移除此模块覆盖，恢复工程创建时默认值。":"Remove this module override and restore defaults from project creation.",
  "有未保存的工程设置。":"Project settings have unsaved changes.",
  "工程设置已保存，从后续新任务生效；历史版本不变。":"Project settings saved for future tasks; historical revisions are unchanged.",
  "纸张用于新建原理图，不调整已有图纸或关闭 ERC。":"Paper size applies to new schematics; existing sheets and ERC remain unchanged.",
  "螺钉颜色用于特征模型和总成的新任务，模型明确指定的颜色优先；材料和配合要求仍在工程设计中确定。":"Screw color applies to new feature-model and assembly tasks. Explicit model colors take priority; materials and fits remain part of the design.",
  "周期和波特率仅用于相应模板；编辑现有源码不替换代码参数。构建不执行烧录。":"Period and baud rate apply to their respective templates. Source edits preserve code parameters. Building does not flash a device.",
  "数量仅进入制造审核资料包，不替代实际下单数量；PCB、SMT及3D订单仍分别确认。":"Quantity is for manufacturing review packages only. PCB, SMT and 3D order quantities are confirmed separately.",
  "这里设置通用下限；网络类别、局部铜区等更严格的规则仍然有效。":"These are general minimums; stricter net-class and local copper rules remain effective.",

  "PCB 设置":"PCB settings",
  "通用 · 存储与清理":"General \u00b7 storage and cleanup",
  "工具与集成":"Tools and integrations",
  "输入单位":"Input unit",
  "不同网络铜间最小净距":"Minimum copper clearance between nets",
  "最小线宽":"Minimum track width",
  "过孔外径":"Via diameter",
  "过孔孔径":"Via drill",
  "铜到板边最小距离":"Minimum copper to edge clearance",
  "孔间最小净距":"Minimum hole to hole clearance",
  "PCB 数值规则 · 个人默认":"PCB numeric rules \u00b7 personal defaults",
  "保存数值默认值":"Save numeric defaults",
  "恢复系统默认值":"Restore system defaults",
  "当前工程规则":"Current project rules",
  "读取当前工程规则":"Read current project rules",
  "填入当前个人默认值":"Fill with current personal defaults",
  "应用到当前工程":"Apply to current project",
  "其他 PCB 默认偏好":"Other PCB defaults",
  "个人默认用于新工程；当前工程的数值规则需要单独应用，并由原生检查确认。":"Personal defaults apply to new projects. Apply current project rules separately with native checks.",
  "最小值是允许的下限，不要求所有走线取最小值。仅影响以后新建的工程。":"Minimum values are lower limits, not required sizes for every track. Applies only to future projects.",
  "当前PCB版本规则；修改后检查并生成新版本。":"Rules from the current PCB revision. Changes are checked in a new revision.",
  "工程覆盖值":"Project overrides",
  "创建工程时保存的个人默认值；不会随全局设置变化。":"Personal defaults frozen when the project was created; global changes do not affect them.",

  '此历史版本未执行一体零件连通性验收。':'This historical revision has no one-piece connectivity acceptance.',
  '一体零件存在断开实体，未通过验收：':'Disconnected one-piece parts failed acceptance: ',
  '一体零件连通性通过；来源总成独立保留。':'One-piece connectivity passed; source assemblies remain separate.',
  '外观参考、材质与贴花':'References, materials and decals','设计意图':'Design intent','参考用途':'Reference purpose','移除':'Remove',
  '生成提示词（如适用）':'Generation prompt (if applicable)','图片来源／使用许可':'Image source / license',
  '用户上传，授权信息未声明':'User upload; licensing not declared','选择外观图片':'Choose appearance image','贴花所属零件':'Decal target part',
  '添加参考图':'Add reference image','添加贴花':'Add decal','请先选择图片。':'Choose an image first.',
  '请选择贴花所属零件。':'Choose a target part.','正在导入图片…':'Importing image…','外观图片':'Appearance image',
  '原始材质':'Original material','哑光塑料':'Matte plastic','柔和半哑光':'Satin finish','金属':'Metal','抛光金属':'Polished metal','自定义':'Custom',
  '粗糙度':'Roughness','金属度':'Metalness','宽度 mm':'Width mm','高度 mm':'Height mm','投影深度 mm':'Projection depth mm','旋转 °':'Rotation °','不透明度':'Opacity','灰模检查':'Clay view',
  '中心 X':'Center X','中心 Y':'Center Y','中心 Z':'Center Z','朝外方向 X':'Outward X','朝外方向 Y':'Outward Y','朝外方向 Z':'Outward Z','图片朝上 X':'Image up X','图片朝上 Y':'Image up Y','图片朝上 Z':'Image up Z',
  '参考图由 Agent 生成或读取，再构建可编辑 CAD。图片不用于推断隐藏结构或精确尺寸。':'The Agent generates or reads reference images, then builds editable CAD. Images do not establish hidden structure or exact dimensions.',
  '贴花默认投影到零件顶部；调整中心、朝外方向和投影深度后重建。下载 GLB 保留贴花，STEP / STL 保留工程实体。':'Decals start at the top of the part. Adjust center, outward direction and projection depth, then rebuild. GLB retains decals; STEP / STL retain engineering solids.',
  '请选择不超过 16 MB 的 PNG / JPEG 图片。':'Choose a PNG / JPEG image up to 16 MB.','缺少零件尺寸，请先完成一次模型构建。':'Part dimensions are unavailable. Build the model first.',
"PCB 布线":"PCB routing",
"PCB 修改":"PCB editing",
"PCB 初始化":"PCB initialization",
"PCB 交接":"PCB handoff",
"原理图修改":"Schematic editing",
"电气审查":"Electrical review",
"机械建模":"Mechanical modeling",
"PCBA 几何导出":"PCBA geometry export",
"次尝试":"attempts",
"设计过程复盘":"Design process review",
"已记录任务":"Recorded jobs",
"当前需比较原因的重复尝试":"Current repeated attempts to review",
"任务执行完成不等于设计被采纳；历史问题不会自动成为当前阻塞。":"Job completion is not design adoption; historical issues are not current blockers.",
"继续前比较原因":"Compare causes before continuing",
"历史尝试记录":"Historical attempts",
"尝试次数":"Attempt count",
"失败或未采纳次数":"Failed or unadopted count",
"其余历史分组":"Additional historical groups",
"同一设计输入也可能采用不同参数；比较任务输入和反馈后再决定是否重试。":"Attempts on the same design can use different parameters. Compare inputs and feedback before retrying.",
"已停止":"Stopped",
"未知":"Unknown",
'更新设计与使用检查':'Update design and usability checks',
"设计与使用检查":"Design and usability checks",
"用户已确认":"User confirmed",
"Agent 暂定":"Agent assumption",
"待明确":"Unresolved",
"已替代":"Superseded",
"待检查":"Pending review",
"发现问题":"Issue found",
"已记录检查":"Observation recorded",
"不适用":"Not applicable",
"设计变化，需复查":"Design changed; review again",
"看得见、读得懂":"Visible and readable",
"够得着、无阻挡":"Reachable and unobstructed",
"操作是否方便":"Ease of operation",
"状态反馈是否明确":"Clear status feedback",
"充电、清洁与维护":"Charging, cleaning and maintenance",
"其他使用检查":"Other usability checks",
"尚未记录使用场景；功能检查通过不代表使用方便。":"No usage scenarios recorded; functional checks do not prove ease of use.",
"需求已变化，Agent 需核对已有决定，无需重复确认未改变的选择。":"Requirements changed. Reconcile saved decisions without re-asking unchanged choices.",
"接下来":"Next action",
"当前阻塞":"Current blocker",
"已确认与暂定的决定":"Confirmed decisions and assumptions",
"尚无当前使用检查":"No current usability check",
"原话与依据":"Original words and reasoning",
"仍有使用问题或检查待完成。":"Usability issues or checks remain open.",
"场景":"Scenario",
"操作":"Action",
"预期":"Expected result",
"观察":"Observation",
"检查方法":"Method",
"证据局限":"Evidence limits",
"还需考虑的使用角度":"Usability aspects to consider",
"以上为 Agent 的检查记录；不等于实物体验或制造验收。":"Agent-declared observations; not physical usability or manufacturing acceptance.",
...firmwareFlashEnglish,
'未绑定设计修订':'No design revision binding',
'收起需求':'Hide requirements','展开需求':'Show requirements','显示或收起需求与偏好':'Show or hide requirements and preferences',
'检查与版本详情':'Checks and revision details','工程信息':'Project information','设置分区':'Settings sections','设计默认':'Design defaults','制造设置':'Manufacturing settings','存储与清理':'Storage and cleanup','工具链':'Toolchains',
'修订与候选':'Revisions and candidates','含未采纳候选':'Includes unadopted candidates','本机':'Local','Agent 接入':'Agent connection','暂无制造资料':'No manufacturing files yet','暂无任务':'No tasks yet','暂无变化':'No activity yet',
'逐引脚复核记录':'Per-pin review records','理由按修订和图页摘要保存；待重审或待调整不代表电气错误，也不会隐藏图面提示。':'Reasons are bound to revision and sheet hash. Review or layout work is separate from electrical errors and does not hide drawing feedback.',
'已记录':'Recorded','待调整':'Layout work pending','需要重新复核':'Review required','保留标签':'Keep label','接受当前布局':'Accept current layout','需要调整布局':'Adjust layout','修订已变化':'Revision changed','图页内容已变化':'Sheet content changed','图页或引脚不可用':'Sheet or pin unavailable',
'功能组与重复通道':'Functional groups and repeated channels',
'仅检查声明的布局关系与接口引脚存在性，不证明电路功能或接口完整性。':'Checks declared layout relationships and interface pin existence, not circuit function or interface completeness.',
'成员不存在':'Member not found','引脚几何不可用':'Pin geometry unavailable','固定位置偏离':'Fixed position mismatch','成员跨图页':'Members span sheets','接口引脚不存在':'Interface pin not found','基准组检查未通过':'Reference group checks failed','角色不一致':'Role mismatch','相对位置不一致':'Relative position mismatch','方向不一致':'Orientation mismatch','引脚几何不一致':'Pin geometry mismatch','重复通道复用了同一器件':'Repeated channel reuses the same component','布局检查异常':'Layout check issue',
'图面连接提示':'Drawing connection feedback',
'选择图页后读取图面提示。':'Select a sheet to read drawing feedback.',
'正在读取图面提示…':'Loading drawing feedback…',
'本页没有可比较的双引脚实体路径。':'There are no comparable two-pin physical paths on this sheet.',
'功能块分项统计':'Per-block measures',
 '本页未登记功能块，不根据器件位置或网名猜测归属。':'No blocks are registered on this sheet; ownership is not inferred from positions or net names.',
 '本页功能块分析不可用，请查看完整报告和原生图。':'Block analysis is unavailable on this sheet; inspect the full report and native drawing.',
 '导线按实体连接归属；跨块与未归属导线单列。字段提示为全页包络估计，不代表原生遮挡结论。':'Wires are assigned by physical connection. Shared and unassigned wires are separate. Field findings use whole-sheet envelope estimates and require native review.',
 '成员或几何不完整，统计仅覆盖已知对象。':'Members or geometry are incomplete; measures cover known objects only.',
 '· 重叠':'· Overlaps',
 '功能块几何分析超出预算，其余统计不可用。':'Block geometry analysis exceeded its budget; other measures are unavailable.',
 '本功能块没有可比较的双引脚实体路径。':'This block has no comparable two-pin physical path.',
 '字段提示':'Field findings',
 '存在未覆盖的文字或符号包络，需原生图复核。':'Some text or symbol envelopes are unavailable; inspect the native drawing.',
 '声明支路实体路径':'Declared branch physical paths',
 '跨块导线':'Shared wires',
 '未归属导线':'Unassigned wires',
 '跨块非连接交叉':'Cross-block unconnected crossings',
 '实体线总长':'Drawn wire length',
'· 转弯':'· Bends',
'· 非连接交叉':'· Unconnected crossings',
'可比较路径':'Compared paths',
'· 绕行余量':'· Estimated detour',
'仅统计本页已知实体几何；绕行余量需结合路径覆盖与原生图判断。':'Known physical geometry on this sheet only; interpret detour with path coverage and the native drawing.',
'本页几何分析超出预算，其余统计不可用。':'Geometry analysis exceeded the page budget; other measures are unavailable.',
'以下提示仅反映局部实体连接；跨组标签可能合理，仍需复核原生图。':'Feedback describes local physical connections. Cross-group labels may be appropriate; review the native drawing.',
'本页逐引脚分析不可用。':'Per-pin analysis is unavailable for this sheet.',
'待复核引脚':'Pins to review',
'定位引脚':'Locate pin',
'未检测到标签独立引脚；这不代表图面复核已完成。':'No label-only pins detected; drawing review is still required.',
'以下对象无法分析：':'These objects could not be analyzed:',
'图面提示读取失败。':'Unable to load drawing feedback.',
'连接断言':'Connection assertions','· 两两异网':'· Pairwise separate nets','· 精确网络成员':'· Exact net membership','· 同网':'· Same net',
'· 未知引脚':'· Unknown pins','· 额外引脚':'· Extra pins','· 缺失引脚':'· Missing pins',
'正在切换工程…':'Switching project…','工程加载失败':'Unable to load project','请重试，或从左侧选择其他工程。':'Retry or select another project from the sidebar.','正在读取工程内容与预览，请稍候。':'Loading project content and preview. Please wait.','重试加载':'Retry loading', 
"原生 DRC":"Native DRC",
"原生 ERC":"Native ERC",
"准备制造审核资料":"Prepare manufacturing review files",

  '仅显示制造任务':'Manufacturing tasks only',
  '制造资料列表':'Manufacturing file list',
"制造总览":"Manufacturing overview",
"选择制造路径，集中查看本工程的资料与任务。":"Choose a manufacturing path and review project files and tasks.",
"从固定原图与 PCB 版本生成审核资料。":"Generate review files from a fixed schematic and PCB revision.",
"登记并审核外部 Gerber、BOM 和坐标文件。":"Register and review external Gerber, BOM and placement files.",
"选择制作件、材料与数量，检查 STEP / STL 资料。":"Select parts, material and quantities, then check STEP / STL files.",
"核对器件料号、装配范围及 BOM / CPL。":"Review part numbers, assembly scope and BOM / CPL.",
"基于已检查的结构资料准备和记录报价。":"Prepare and record quotes using checked mechanical files.",
"查看官方接口配置、计价与订单核对能力。":"Review official API settings, pricing and order verification.",
"查看供应商会话、复核记录与已授权任务。":"Review supplier sessions, review records and authorized tasks.",
"查看本地关联订单及历史观察，保留原始资料绑定。":"View linked local orders and past observations with original file bindings.",
"制造导航":"Manufacturing navigation",
"返回制造总览":"Back to manufacturing overview",
"PCB 审核资料":"PCB review files",
"结构件资料":"Mechanical files",
"查看平台订单":"View platform orders",
"从设计资料到制造交付":"From design files to manufacturing delivery",
"先准备资料，再核对匹配与报价，最后查看执行与订单记录。":"Prepare files, review matching and quotes, then inspect execution and order records.",
"资料文件":"Review files",
"包含历史版本":"Including historical revisions",
"制造任务":"Manufacturing tasks",
"本地任务记录":"Local task records",
"执行中的任务":"Active tasks",
"排队与执行中":"Queued and running",
"失败任务记录":"Failed task history",
"历史失败保留":"Historical failures retained",
"资料准备":"Prepare files",
"选择 PCB、外部文件或结构件资料。":"Choose PCB, external or mechanical files.",
"匹配与报价":"Match and quote",
"核对料号、数量、工艺及费用依据。":"Review parts, quantities, processes and pricing evidence.",
"执行与记录":"Execution and records",
"检查任务授权与平台观察记录。":"Review task authorization and platform observations.",
"制造资料中心":"Manufacturing file center",
"历史资料 · 非生产放行":"Historical files · no production release",
"按文件、任务或版本查找。文件可下载不代表已经通过检查或批准生产。":"Find files by name, task or revision. Download availability does not mean checks passed or production was approved.",
"搜索文件、任务或版本…":"Search files, tasks or revisions…",
"搜索制造资料":"Search manufacturing files",
"资料类别":"File category",
"全部类别":"All categories",
"PCB 资料":"PCB files",
"装配与采购":"Assembly and procurement",
"外部文件":"External files",
"全部资料版本":"All file revisions",
"清除筛选":"Clear filters",
"文件摘要与来源":"File hash and origin",
"任务详情未载入":"Task details not loaded",
"任务状态未知":"Task status unknown",
"下载文件":"Download file",
"没有匹配的制造资料。":"No matching manufacturing files.",
"尚无制造资料，请从上方选择资料准备入口。":"No manufacturing files yet. Choose a preparation workflow above.",
"任务已结束":"Task finished",
"PCB 资料审核":"PCB file review",
"结构件资料审核":"Mechanical file review",
"采购计划检查":"Procurement plan checks",
"官方计价":"Official pricing",
"官方订单核对":"Official order verification",
"PCB 审核资料包":"PCB review package",
"PCB 预检报告":"PCB preflight report",
"Gerber 与钻孔包":"Gerber and drill package",
"原始 BOM":"Original BOM",
"原始坐标":"Original placements",
"采购计划包":"Procurement plan package",
"采购检查报告":"Procurement check report",
"采购审核表":"Procurement review table",
"采购计划与来源":"Procurement plan and provenance",
"BOM 审核副本":"BOM review copy",
"CPL 审核副本":"CPL review copy",
"选型 BOM（非生产）":"Sourcing BOM (not for production)",
"选型坐标（非生产）":"Sourcing placements (not for production)",
"选型待核说明":"Sourcing review notes",
"结构件审核包":"Mechanical review package",
"结构件检查报告":"Mechanical check report",
"STL 网格检查报告":"STL mesh check report",
"结构件选择清单":"Mechanical part selection",
"外部文件审核包":"External file review package",
"外部文件检查报告":"External file check report",
"外部装配审核表":"External assembly review table",
"此入口需要可追溯的原图与PCB。已有外部Gerber/BOM/坐标可切换到外部文件审核。":"This workflow needs a traceable schematic and PCB. For existing external Gerber/BOM/placements, switch to external file review.",
"从设计导出 →":"Export from design →",
"外部文件审核 →":"External file review →",
"结构件 →":"Mechanical parts →",
"采购计划 →":"Procurement plan →",
"结构报价 →":"Mechanical quotes →",
"官方接口 →":"Official API →",
"下单执行 →":"Order execution →",
"平台订单 →":"Platform orders →",

"固件工作区":"Firmware workspace",
"项目概述":"Project overview",
"引脚绑定":"Pin bindings",
"构建配置":"Build configuration",
"资源与下载":"Resources and downloads",
"查看构建配置":"View build configuration",
"查看任务记录":"View task history",
"引脚与硬件依赖":"Pins and hardware dependencies",
"功能角色":"Function",
"物理脚":"Physical pad",
"此表仅显示显式绑定的角色；完整应用映射以 board.h 和原理图为准。":"Only explicitly bound roles appear here; see board.h and the schematic for the full application mapping.",
"源码修改与构建通过 Agent 提交，保留版本及原生检查。":"Submit source changes and builds through your Agent, preserving revisions and native checks.",
"当前未启用设备烧录。":"Device flashing is not enabled.",
"硬件资源":"Hardware resources",
"尚无此版本的内存检查结果":"No memory check results for this revision",
"启动与内存回读":"Startup and memory verification",
"所选版本产物":"Selected revision artifacts",
"仅通过构建检查的版本提供 ELF、HEX、BIN；诊断包保留失败信息。":"ELF, HEX and BIN are available only for revisions that passed build checks. Diagnostic packages retain failure details.",
"此版本尚无可下载产物。":"No downloadable artifacts for this revision.",
"搜索源码…":"Search source\u2026",
"搜索源码":"Search source",
"上一处":"Previous match",
"下一处":"Next match",
"复制源码":"Copy source",
"已复制源码":"Source copied",
"复制失败，请选择源码后复制。":"Copy failed. Select the source text and copy it manually.",
"导出源码文本":"Export source text",
"自动换行":"Wrap lines",
"没有匹配的源码":"No matching source text",
"重新读取":"Reload source",

  '打开结构件制造':'Open mechanical manufacturing',
  '查询器件与库存':'Query components and inventory',
  '机械原生任务达到时间预算；候选未采纳。':'Native mechanical task reached its time budget; candidate not adopted.',
  '官方接口连接失败；未输出鉴权信息或自动重试。':'Official API connection failed; no authentication data was exposed and no automatic retry was made.',

"机械设计概述":"Mechanical overview",
"装配零件":"Assembly parts",
"原生实体":"Native solids",
"版本检查":"Revision checks",
"制作材料":"Material",
"显示所选版本的真实零件与检查结果；制造资料需独立审核。":"Actual parts and checks for the selected revision. Manufacturing files require a separate review.",
"渲染预览":"Render preview",
"查看真实装配模型、零件与拆分姿态。":"Explore the actual assembly, parts, and exploded positions.",
"立体":"Perspective",
"俯视":"Top",
"正视":"Front",
"侧视":"Side",
"网格线框":"Mesh wireframe",
"导出图片":"Export image",
"展开全屏":"Expand fullscreen","退出全屏":"Exit fullscreen",
"显示设置":"Display settings",
"全部显示":"Show all",
"全部隐藏":"Hide all",
"搜索零件…":"Search parts\u2026",
"搜索零件":"Search parts",
"相关操作":"Related actions",
"所选版本":"Selected revision",
"导出装配 STEP":"Export assembly STEP",
"下载检查报告":"Download check report",
"查看版本与检查":"View revision and checks",
"准备结构件制造资料":"Prepare mechanical manufacturing files",
"搜索颜色或零件…":"Search colors or parts\u2026",
"搜索颜色或零件":"Search colors or parts",
"没有匹配的零件":"No matching parts",
"网格":"Grid",
"列表":"List",
"参数与所选版本一致":"Parameters match the selected revision",
"还原修改":"Reset changes",
"有未应用修改 · 重建后更新模型":"Unapplied changes \u00b7 rebuild to update the model",

  '尺寸参数':'Dimensions','外观颜色':'Appearance','螺钉统一颜色':'Screw color','标准件':'Standard part',
  'FormForge 本地产品工程工作台':'FormForge local product engineering workbench',
  'FormForge · 产品工程工作台':'FormForge · Product Engineering Workbench',
  '跳到工作区':'Skip to workspace','关闭导航':'Close navigation','工作空间导航':'Workspace navigation','FormForge 首页':'FormForge home',
  '产品工程工作台':'Product Engineering Workbench','新建工程':'New project','工作空间':'Workspace','工程概览':'Project overview','工程资料':'Project documents',
  '器件查询':'Component lookup','设计与制造':'Design and manufacturing','原理图':'Schematic','机械设计':'Mechanical design','机械':'Mechanical',
  '固件':'Firmware','制造':'Manufacturing','我的工程':'My projects','搜索工程':'Search projects','搜索工程…':'Search projects…',
  '没有找到工程，试试其他名称。':'No projects found. Try another name.','设置':'Settings','数据保存在本机':'Data stays on this computer',
  'KiCad 10 / 独立机械引擎':'KiCad 10 / independent mechanical engine','查看 Agent 接入说明':'View Agent connection guide','Agent 接入说明':'Agent connection guide',
  '打开工程导航':'Open project navigation','未选择工程':'No project selected','本机工作台':'Local workbench','连接中':'Connecting','收起侧栏':'Collapse sidebar','展开侧栏':'Expand sidebar',
  '显示或收起需求与最近变化':'Show or hide requirements and recent activity','让需求、设计与验证，在同一个工作空间中连接。':'Connect requirements, design, and verification in one workspace.',
  '导入工程包':'Import project package','导出工程':'Export project','导入 KiCad 工程':'Import KiCad project','运行检查':'Run checks','从需求到真实工程':'From requirements to a real project','你的下一个产品，\n从这里开始。':'Your next product starts here.',
  '你的下一个产品，':'Your next product,','从这里开始。':'starts here.','创建工程记录需求与偏好，或导入已有 KiCad 工程。\n每次检查和每份文件，都有可追溯的版本。':'Create a project to record requirements and preferences, or import an existing KiCad project. Every check and file is versioned and traceable.',
  '创建第一个工程':'Create your first project','本地工作台 · 外部 Agent 接入 · 独立设计模块':'Local workbench · external Agent access · independent design modules',
  '当前工程':'Current project','从这里查看设计、检查结果和最近任务。':'Review designs, check results, and recent tasks here.','删除工程':'Delete project',
  '设计修订':'Design revisions','含未采纳候选，非工程版本号':'Includes rejected candidates; not the project version','任务记录':'Task history','执行过程可追溯':'Execution is traceable',
  '工程产物':'Project artifacts','图纸、模型与报告':'Drawings, models, and reports','设计路径':'Design path','从真实工程进入对应模块':'Open each module from the real project',
  '按需组合':'Use as needed','构建 · 版本 · 检查':'Build · version · check','规则 · 层预览':'Rules · layer preview','建模 · 装配 · 查看':'Model · assemble · inspect',
  '源码 · 构建 · 回读':'Source · build · verify','资料 · 预检':'Files · preflight','Agent 从明确需求建立原理图，记录来源、版本与检查。机械可从独立参数开始，订单流程将逐项接入。':'The Agent builds schematics from explicit requirements and records sources, versions, and checks. Mechanical work can start from independent parameters; ordering capabilities are added step by step.',
  '检查结果':'Check results','未检查':'Not checked','当前工程的原生规则检查':'Native rule checks for the current project','等待检查':'Waiting for checks','导入工程后运行 KiCad 10 原生检查。':'Import a project, then run native KiCad 10 checks.',
  '设计修订与候选':'Design revisions and candidates','查看更早修订':'View earlier revisions','添加资料':'Add document','登记保存原始文件和来源。具体用途由对应设计任务检查，登记不代表供应商认证或工程验收。':'Register original files and their sources. The relevant design task validates their use; registration is not supplier certification or engineering acceptance.',
  '上一页':'Previous','下一页':'Next','查看版本':'View version','跟随最新修改':'Follow latest','跟随当前版本':'Follow current version','正在跟随当前版本':'Following current version','下载原生工程 ↗':'Download native project ↗','修改明细与几何依据':'Change details and geometry evidence',
  '电气约束审查与 PCB 交接':'Electrical constraint review and PCB handoff','预期连接与检查结果':'Expected connectivity and check results','外部符号库':'External symbol libraries','本版设计依据与已确认规格':'Design basis and confirmed specifications',
  '制造工作区':'Manufacturing workspace','从设计导出':'Export from design','外部文件审核':'External file review','采购计划':'Procurement plan','平台订单':'Platform orders','下单执行':'Order execution','结构件':'Mechanical parts','结构报价':'Mechanical quotes','官方接口':'Official API',
  'PCB 视图':'PCB view','2D 板图':'2D board','3D 渲染':'3D render','选择图页或模型':'Select drawing or model','缩小':'Zoom out','适应':'Fit','适应视图':'Fit view','放大':'Zoom in','下载 ↗':'Download ↗',
  '爆炸查看':'Exploded view','装配姿态':'Assembled','零件可见性':'Part visibility','还没有可显示的图纸':'No drawing available yet','导入工程并运行检查后，实际产物将显示在这里。':'Import a project and run checks to display the resulting artifacts here.',
  'KiCad 原生图纸预览':'Native KiCad drawing preview','原生工程预览':'Native project preview','拖动平移 · 滚轮缩放':'Drag to pan · wheel to zoom','独立引擎，逐步接入':'Independent engines, added step by step','尚未提供执行能力':'Execution is not available yet',
  '任务与过程':'Tasks and progress','真实执行记录与检查产物':'Real execution records and check artifacts','等待连接':'Waiting for connection','执行任务后，这里会记录真实进展。':'Real progress will appear here after a task runs.',
  '工程需求与动态':'Project requirements and activity','需求与偏好':'Requirements and preferences','产品需求':'Product requirements','设计偏好':'Design preferences','保留你的原文。任务会记录当时生效的需求和偏好。':'Your original wording is preserved. Each task records the requirements and preferences in effect at that time.',
  '保存需求和偏好':'Save requirements and preferences','最近变化':'Recent activity','事件':'Events','等待工程事件。':'Waiting for project events.','检查报告绑定具体输入版本。\n规则通过不代表实物或制造验证。':'Check reports are bound to a specific input version. Passing rules does not prove hardware or manufacturing validity.',
  '检查报告绑定具体输入版本。':'Check reports are bound to a specific input version.','规则通过不代表实物或制造验证。':'Passing rules does not prove hardware or manufacturing validity.','读取中':'Loading',
  '本地工程工作台 / 原生电路与独立机械设计':'Local project workbench / native electronics and independent mechanical design','创建工程':'Create project','关闭':'Close','工程名称':'Project name','例如：USB 转 RS422 模块':'Example: USB-to-RS422 module',
  '校验工程包清单和全部文件 SHA-256，然后导入为新的本机工程。原工程与包文件保持不变。':'Verify the package manifest and every file SHA-256, then import it as a new local project. The original project and package remain unchanged.','本机工程包路径':'Local project package path','新工程名称':'New project name','留空则保留原名称':'leave blank to keep the original name','可选':'Optional','校验并导入':'Verify and import',
  '保留修订、任务时钟、失败记录、来源、产物和订单历史；不导入 API 令牌、供应商凭据、浏览器登录态或请求重放授权。历史检查仍绑定原版本，本机环境及后续修改需重新检查。':'Preserves revisions, task timing, failures, sources, artifacts, and order history. API tokens, supplier credentials, browser sessions, and request-replay authorization are not imported. Historical checks remain bound to their original revisions; the local environment and subsequent changes require fresh checks.',
  '写下你希望产品完成的功能。':'Describe what you want the product to do.','取消':'Cancel','导入已有工程':'Import existing project','复制所需图纸与依赖，建立新的输入快照。原工程保持不变。':'Copy the required drawings and dependencies into a new input snapshot. The original project remains unchanged.',
  '本机工程目录':'Local project folder','C:\\Projects\\MyProject':'C:\\Projects\\MyProject','原理图入口':'Schematic entry','相对于上述目录':'relative to the folder above','project.kicad_sch（可选）':'project.kicad_sch (optional)','PCB 入口':'PCB entry','project.kicad_pcb（可选）':'project.kicad_pcb (optional)',
  '至少填写一个入口。层级图纸、自定义库和本地模型应一并放在工程目录内。':'Provide at least one entry. Hierarchical sheets, custom libraries, and local models should be included in the project folder.',
  '运行工程检查':'Run project checks','输入版本':'Input version','同时生成 PCB 板体 3D 预览':'Also generate a 3D board preview','板体预览不包含元件与外壳。检查会保留原生报告，并明确显示发现的问题。':'The board preview excludes components and enclosure. Checks retain native reports and clearly show detected issues.',
  '时间预算（秒）':'Time budget (seconds)','开始检查':'Start checks','添加工程资料':'Add project document','关闭资料窗口':'Close document dialog','原始文件':'Original file','资料类型':'Document type','本项目编写':'Authored in this project','供应商资料（声明）':'Supplier document (declared)','第三方资料（声明）':'Third-party document (declared)',
  '来源地址':'Source address','官网链接或项目资料地址':'Official link or project document address','资料版本':'Document version','例如：Rev. E / 项目规格 v1':'Example: Rev. E / project specification v1','许可或使用说明':'License or usage note','例如：项目自行编写 / 供应商公开手册':'Example: project-authored / public supplier manual','内容与用途':'Content and purpose','说明文件来自哪里、用于什么检查。':'Explain where the file came from and what it is used to check.',
  '每份文件最多64MiB。保存后不覆盖原资料，更新内容请另登记新版本。':'Maximum 64 MiB per file. Saving never overwrites the original; register updates as a new version.','登记资料':'Register document',
  '连接你的 Agent':'Connect your Agent','支持访问本机的 MCP 客户端。Agent 与网页操作同一份工程。':'Supports MCP clients that can access this computer. The Agent and web UI operate on the same project.','MCP 地址':'MCP address','连接方式':'Connection method','Streamable HTTP · Bearer 认证':'Streamable HTTP · Bearer authentication','令牌文件':'Token file','令牌位于 FormForge 项目目录。只将它填写在客户端连接设置中。已验证的协议版本和能力范围见项目开发记录。':'The token is stored in the FormForge project folder. Enter it only in the client connection settings. See the development log for verified protocol versions and capabilities.','知道了':'Got it',
  '设计模块':'Design modules','项目信息':'Project information','编辑需求与偏好':'Edit requirements','项目描述':'Description','最近更新':'Last updated','工程记录更新时间':'Project record update time','尚未填写':'Not specified','已有版本':'Version available','尚未建立':'Not started','Agent 从明确需求开始设计，连接原理图、PCB、机械、固件与制造；每次修改保留版本与检查依据。':'Start with clear requirements across schematics, PCB, mechanics, firmware, and manufacturing. Every change retains its version and verification evidence.',
  '原理图 / PCB':'Schematic / PCB','器件位号':'Component references','ERC 通过':'ERC passed','ERC 需处理':'ERC needs attention','候选未采纳':'Candidate not adopted','本版检查通过':'Checks passed for this revision','版本绘图，未继承检查':'Revision drawing; checks not inherited','未关联检查结果':'No linked check result','PCB草稿':'PCB draft','候选检查中':'Candidate checks in progress',
  '原理图工作区':'Schematic workspace','设计检查':'Design checks','器件与库':'Components and libraries','版本历史':'Version history','相关文件':'Related files','原理图概览':'Schematic overview','当前版本的文件与原生网表':'Files and native netlist for the selected revision','原理图文件':'Schematic files','器件数量':'Components','连接分组':'Pin groups','图页数量':'Sheets','连接分组含独立引脚':'Pin groups include isolated pins','尚无同版原生网表':'No native netlist for this revision','网表统计读取失败，可切换页面重试':'Netlist statistics could not load. Switch views to retry.','尚无原理图':'No schematic','图纸与报告绑定所选版本':'Drawings and reports belong to the selected revision','暂无同版 ERC 结果':'No ERC result for this revision',
  '仅显示所选版本的原理图 ERC；PCB 检查见 PCB 工作区。':'Shows schematic ERC for the selected revision. PCB checks are in the PCB workspace.','查看检查详情':'View check details','下载 ERC 报告 ↗':'Download ERC report ↗','本版没有登记外部符号库。图纸内嵌符号随原生工程保存。':'No external symbol library is registered for this revision. Embedded symbols are included in the native project.','此版本尚无可下载文件。':'No downloadable files for this revision.','错误':'Errors','警告':'Warnings','展开图纸':'Expand drawing','退出大图':'Exit expanded view','上一图页':'Previous sheet','下一图页':'Next sheet','图页列表':'Sheet list','原理图图页':'Schematic sheet','PCB图层':'PCB layer','当前版本图纸':'Drawing for the selected revision',
  'PCB 概述':'PCB overview','所选版本的板级信息':'Board information for the selected revision','焊盘数量':'Pads','过孔数量':'Vias','铜层数量':'Copper layers','最小走线宽度':'Minimum track width','顶层':'Top','底层':'Bottom','全部铜层':'All copper layers','尚无 PCB':'No PCB','正在读取板级统计…':'Loading board statistics…','此版本尚无 PCB 文件':'No PCB file for this revision','此版本的 PCB 无法读取':'Unable to read the PCB for this revision',
  '界面语言':'Interface language','中文':'中文','英语':'English','English':'English',
  '等待执行':'Queued','执行中':'Running','执行完成':'Completed','未完成':'Failed','已取消':'Cancelled','已中断':'Interrupted','通过':'Passed','需处理':'Needs attention','未采纳':'Not adopted','历史版本':'History','检查中':'Checking',
  '实时已连接':'Live connection active','实时同步':'Live sync','连接已断开 · 重连中':'Disconnected · reconnecting','连接中断 · 重连中':'Connection lost · reconnecting','服务未连接':'Service unavailable','重新连接':'Reconnect','正在重新连接':'Reconnecting',
  '尚未保存':'Not saved','需求和偏好已保存。':'Requirements and preferences saved.','工程已创建。':'Project created.','正在校验并导出工程包…':'Verifying and exporting the project package…','工程包已通过校验并开始下载。':'The verified project package is downloading.','工程包已校验并导入为新工程。':'The project package was verified and imported as a new project.','导出工程包':'Export project package','导入任务已开始。':'Import task started.','检查任务已开始。':'Check task started.',
  '当前版本':'Current version','当前 PCB 草稿':'Current PCB draft','当前采购计划':'Current procurement plan','当前机械版本':'Current mechanical version','当前固件版本':'Current firmware version','PCB 草稿':'PCB draft','修改生成':'Generated by edit','从明确需求建立':'Built from explicit requirements','已有工程导入':'Imported project',
  '正在读取接入信息…':'Loading connection information…','正在统计本机存储…':'Calculating local storage…','正在读取工具状态…':'Loading tool status…','刷新信息':'Refresh information','连接 Agent，管理本机工作台':'Connect an Agent and manage the local workbench','查看接入、存储和工具，管理缓存与工程回收站。':'Review connection, storage, and tools; manage caches and the project recycle bin.',
  'Agent / MCP 接入':'Agent / MCP connection','复制地址或配置，连接同一份本地工程。':'Copy an address or configuration to connect to the same local project.','存储概览':'Storage overview','查看数据位置与已统计文件大小。':'View data locations and measured file sizes.','工具状态':'Tool status','查看当前服务使用的工具与独立环境。':'View tools and isolated environments used by the service.',
  '清理缓存':'Clean caches','工程回收站':'Project recycle bin','仅本机访问':'Local access only','MCP 服务地址':'MCP service address','令牌文件路径':'Token file path','令牌内容不在此页展示。HTTP 客户端需从上述本机文件取得令牌，再填写到 Authorization 请求头。':'The token is not displayed here. HTTP clients must read it from the local file above and place it in the Authorization header.',
  'stdio 配置':'stdio configuration','HTTP 配置':'HTTP configuration','云端 Agent 无法直接连接此电脑的 127.0.0.1。此页不修改访问权限。':'A cloud Agent cannot directly connect to 127.0.0.1 on this computer. This page does not change access permissions.',
  '工作台根目录':'Workbench root','根目录':'root folder','工程快照与产物':'Project snapshots and artifacts','工程目录':'project folder','数据库文件':'Database file','数据库路径':'database path','可在下方扫描并清理缓存。工程文件通过回收站管理；目录迁移尚未提供。':'Scan and clean caches below. Project files are managed through the recycle bin; folder migration is not available yet.',
  '可用':'Available','不可用':'Unavailable','尚未配置':'Not configured','版本未记录':'Version not recorded','正在读取本机信息与存储统计…':'Loading local information and storage statistics…','未取得工具状态，请稍后刷新。':'Tool status unavailable. Refresh later.','读取未完成，请检查本地服务后重试。':'Loading did not complete. Check the local service and retry.'
  ,'设置新工程默认偏好、制造资料选择和本机工具链；管理接入、存储与回收站。':'Set new-project defaults, manufacturing selections, and local toolchains; manage connections, storage, and the recycle bin.'
  ,'正在读取 PCB 默认偏好…':'Loading PCB defaults…','正在读取制造资料选择…':'Loading manufacturing selections…'
  ,'PCB 新工程默认偏好':'PCB defaults for new projects','只用于初始化以后创建的工程；已有工程、旧任务和硬约束不会被改写。':'Used only to initialize future projects. Existing projects, historical tasks, and hard constraints are not rewritten.'
  ,'制造账户与资料选择':'Manufacturing account and profile selection','仅保存供应商资料来源和脱敏别名；地址、电话、税号及银行资料仍留在供应商账户。':'Stores only provider-profile sources and masked aliases. Addresses, phone numbers, tax identities, and banking data remain in the supplier account.'
  ,'工具链档案':'Toolchain profiles','选择并校验不同的本机工具链；FormForge 不移动、复制或删除外部工具。':'Select and validate local toolchains. FormForge does not move, copy, or delete external tools.'
  ,'板框':'Board outline','默认圆角':'Rounded by default','默认直角（需说明原因）':'Square by default (reason required)','默认圆角半径（mm）':'Default corner radius (mm)','直角原因':'Reason for square corners'
  ,'布线风格':'Routing style','平衡':'Balanced','整齐优先':'Neatness first','短路径优先':'Shortest path first','摆放密度':'Placement density','紧凑':'Compact','装配与维修空间优先':'Assembly and service space first'
  ,'丝印风格':'Silkscreen style','功能标识优先':'Functional labeling first','精简':'Minimal','详细':'Detailed','过孔偏好':'Via preference','按项目工艺规则':'Use project process rules','保守尺寸优先':'Conservative dimensions first','保存 PCB 默认偏好':'Save PCB defaults'
  ,'这些值会复制到新工程的结构化软规则，并记录设置版本。焊盘中心出线、锐角限制、原生 DRC 等工程硬约束不能在这里关闭。':'These values are copied into new projects as structured soft rules with the settings version. Hard constraints such as centered pad exits, sharp-corner policy, and native DRC cannot be disabled here.'
  ,'收货资料来源':'Shipping-profile source','使用供应商账户默认资料':'Use supplier-account default','每次结算时选择并核对':'Select and verify at each checkout','收货资料脱敏别名':'Masked shipping-profile alias','开票资料来源':'Invoice-profile source','默认发票类型':'Default invoice type','结算时确认':'Confirm at checkout','不开票':'No invoice','个人发票':'Personal invoice','企业增值税普通发票':'Enterprise general VAT invoice','企业增值税专用发票':'Enterprise special VAT invoice','开票资料脱敏别名':'Masked invoice-profile alias','保存制造资料选择':'Save manufacturing selection'
  ,'不要填写完整地址、姓名或电话。':'Do not enter a full address, name, or phone number.','不要填写税号、银行账号或开户地址。':'Do not enter a tax ID, bank account, or registered address.','正式准备和待付款提交会绑定这份设置的版本与脱敏选择；供应商结算返回仍是实际地址、发票和运费的唯一依据。':'Formal preparation and unpaid submission bind this settings version and masked selection. The supplier checkout response remains authoritative for the actual address, invoice, and shipping charge.'
  ,'本机默认设置已保存。':'Local defaults saved.','已验证档案':'Validated profiles','添加工具链档案':'Add toolchain profile','工具类型':'Tool type','档案名称':'Profile name','主程序路径':'Main executable path','校验后设为激活档案':'Activate after validation','校验并保存档案':'Validate and save profile','激活':'Activate','当前档案':'Current profile','待重启生效':'Restart required'
  ,'只读取并校验所选工具，不移动或复制。':'Only reads and validates the selected tool; it is not moved or copied.','KiCad 会同时核对同安装目录的 Python 与 share/kicad；ARM GNU 会核对同根目录的 objcopy、readelf、size、子工具及 libgcc。':'KiCad also validates Python and share/kicad from the same installation. ARM GNU validates objcopy, readelf, size, subtools, and libgcc from the same root.'
  ,'尚未保存工具链档案；当前服务继续使用启动参数、环境变量或自动发现结果。':'No toolchain profile is saved. The current service continues to use launch arguments, environment variables, or auto-discovery.','工具链档案已校验并保存。':'Toolchain profile validated and saved.','工具链已校验并选择；重启服务后生效。':'Toolchain validated and selected; restart the service to apply it.','工具链档案已选择；按提示重启服务后生效。':'Toolchain profile selected; restart the service as indicated.'
  ,'取消 KiCad 档案选择':'Clear KiCad profile selection','取消 ARM GNU 档案选择':'Clear ARM GNU profile selection','已取消档案选择；重启后按启动参数、环境变量或自动发现选择工具。':'Profile selection cleared. After restart, the tool is selected from launch arguments, environment variables, or auto-discovery.'
  ,'DRC 原生检查':'Native DRC','ERC 原生检查':'Native ERC','修改 PCB 草稿':'Edit PCB draft','修改待处理，当前版本未变':'Changes need attention; current version unchanged','修改版本检查结束':'Revision checks finished'
  ,'名义检查通过':'Nominal checks passed','固件编译与映像回读':'Firmware build and image verification','导出板体与器件几何':'Export board and component geometry','工程检查待确认':'Project checks pending confirmation','工艺与装配检查':'Process and assembly checks'
  ,'机械实体与 STEP 回读':'Mechanical solids and STEP verification','机械检查需处理，当前版本未变':'Mechanical checks need attention; current version unchanged','机械模型已采纳；制造与实物检查另列':'Mechanical model adopted; manufacturing and physical checks are separate','机械设计 · feature_model':'Mechanical design · feature_model'
  ,'构建机械模型与验证':'Build and verify mechanical model','构建检查通过':'Build checks passed','独立固件版本；行为、时序和上板运行尚未验证。':'Independent firmware version; behavior, timing, and on-device operation remain unverified.','生成工程产物':'Generate project artifacts'
  ,'这是原生规则检查结果。功能、制造与实物验证仍需独立进行。':'These are native rule-check results. Functional, manufacturing, and physical validation remain separate.'
  ,'外部制造文件 · USB V76 流程验收':'External manufacturing files · USB V76 workflow validation'
  ,'来源与摘要':'Source and digest','项目编写':'Project-authored','下载原始文件 ↗':'Download original file ↗','保存原始文件、来源和版本，供各设计模块引用。':'Preserve original files, sources, and versions for use by each design module.'
  ,'器件与库存':'Components and inventory','查询精确器件与库存，保存可追溯的供应快照。':'Query exact components and inventory and save a traceable supply snapshot.','嘉立创 SMT · 精确 C 编号':'JLCPCB SMT · exact C number','C 编号（逗号、空格或换行分隔，最多 50 项）':'C numbers (comma, space, or newline separated; up to 50)','查询器件':'Query components','查询记录':'Query history','跟随最新查询':'Follow latest query'
  ,'按 C 编号读取型号、厂家和当前库存。每次查询保存独立快照，可查看历史，不改动原理图、PCB 或订单。':'Read models, manufacturers, and current inventory by C number. Each query saves an independent snapshot with history and does not modify the schematic, PCB, or orders.','库存为查询时刻的观察值，未预留。基础/扩展库分类、采购损耗和价格尚未核定；符号、封装与 3D 模型导入将单独检查。':'Inventory is observed at query time and is not reserved. Basic/extended classification, procurement loss, and price are not yet verified; symbol, footprint, and 3D model imports are checked separately.','此查询绑定较早的工程上下文；它仍是当时的库存观察。':'This query is bound to an earlier project context and remains an inventory observation from that time.','C 编号':'C number','厂家型号':'Manufacturer part number','厂家':'Manufacturer','库存数量':'Inventory','库分类':'Library category','未核定':'Unverified'
  ,'查看真实图页，以及与输入版本对应的电气检查。':'View real sheets and electrical checks for the selected input version.','查看同版板图、三维渲染，以及规则和连接检查。':'View the matching board, 3D rendering, rule checks, and connectivity checks.','正在跟随最新修改':'Following latest changes','候选 · 未采纳':'Candidate · not adopted','来源原理图电气审查 · 所列检查通过 · 历史版本':'Source-schematic electrical review · listed checks passed · historical version','焊盘网络与原理图一致 · 0 处待连线 · 未采纳，当前版本保持不变':'Pad nets match the schematic · 0 unrouted · not adopted; current version unchanged'
  ,'读取模型参数…':'Loading model parameters…','独立参数化模型、真实零件与同版 STEP 检查。':'Independent parametric models, real parts, and same-version STEP checks.','机械来源 · 4 个冻结文件':'Mechanical sources · 4 frozen files','读取应用源码、硬件绑定与同版真实构建结果。':'Read application source, hardware binding, and same-version real build results.','读取此版本的源码与检查…':'Loading source and checks for this version…','下载固件工程 ↗':'Download firmware project ↗'
  ,'制造审核资料':'Manufacturing review package','制造资料':'Manufacturing files','从固定设计版本出发，连接报价与制造交付。':'Start from a fixed design version and connect quoting with manufacturing delivery.','仅PCB资料':'PCB files only','仅SMD装配':'SMD assembly only','全部电气器件（包含插件）':'All electrical components (including through-hole parts)','审核装配范围':'Review assembly scope','装配面':'Assembly side','双面':'Both sides','正面':'Top','背面':'Bottom','数量（可留空；未明确时仅作审核资料）':'Quantity (may be blank; review-only when unspecified)','审核说明':'Review note','准备新的审核资料':'Prepare new review package','请选择':'Select','生成审核资料':'Generate review package'
  ,'原始BOM ↗':'Original BOM ↗','原始坐标 ↗':'Original positions ↗','原生DRC ↗':'Native DRC ↗','原生ERC ↗':'Native ERC ↗','装配审核表 ↗':'Assembly review sheet ↗','Gerber与钻孔包（审核用）↗':'Gerber and drill package (for review) ↗','下载审核资料包 ↗':'Download review package ↗','下载完整预检Report ↗':'Download full preflight report ↗'
  ,'仅供审核 · 尚未生产放行':'For review only · not released to production','制造审核资料已生成；尚未生产放行':'Manufacturing review files generated; not released to production','资料一致性通过':'File consistency passed','CAM几何/外形尺寸与实际平台预览未审核':'CAM geometry, outline dimensions, and actual platform preview are not reviewed','完整电气/参考/阻抗与制造工艺资格未形成放行':'Complete electrical, reference, impedance, and manufacturing-process qualifications are not released','采购型号/库存/替料和装配方向未审核':'Part models, inventory, substitutions, and assembly orientation are not reviewed'
  ,'本场景器件与省略原因':'Components and omissions for this scenario','正在跟随后续任务':'Following the latest task','未完成的工程与采购审核':'Incomplete engineering and procurement reviews','设计版本':'Design version','采购料号未齐':'Procurement part numbers are incomplete'
  ,'正在读取本地器件资料…':'Loading local component data…','本次快照':'this snapshot','物理叠层报告':'physical stack-up report','检查报告':'check report','装配 STEP':'assembly STEP','固件工程包':'firmware project','构建报告':'build report','完整预检报告':'full preflight report'
  ,'模型参数':'Model parameters','调整尺寸或颜色，生成新的设计版本。原版本会保留。':'Adjust dimensions or colors to generate a new design version. The original version is preserved.','应用参数并重建':'Apply parameters and rebuild','机身长度 mm':'Body length (mm)','机身宽度 mm':'Body width (mm)','机身厚度 mm':'Body thickness (mm)','壳体壁厚 mm':'Shell wall thickness (mm)','外轮廓圆角 mm':'Outline corner radius (mm)','左键旋转 · 右键平移 · 滚轮缩放':'Left-drag to rotate · right-drag to pan · wheel to zoom','颜色':'Color'
  ,'应用源码':'Application source','向量表':'Vector table','Flash 映像':'Flash image','RAM 分配（含栈预留）':'RAM allocation (including stack reserve)','设计条件与来源':'Design conditions and sources','行为需求':'Behavior requirements','正在读取…':'Loading…','构建检查不证明行为、时序或实物运行。实际选项字节、供电、外设波形及上板验证仍需完成。':'Build checks do not prove behavior, timing, or physical operation. Option bytes, power, peripheral waveforms, and on-device validation remain pending.','ELF、HEX、BIN 的完整装载字节已核对。':'The complete loaded bytes of the ELF, HEX, and BIN were verified.','原理图版本或需求已变化，继续使用前需要重新核对硬件依赖。':'The schematic version or requirements changed; recheck hardware dependencies before continuing.','正在读取此版本的源码与检查…':'Loading source and checks for this version…'
  ,'连接你的 Agent，管理本机缓存、工程回收站与工具。':'Connect your Agent and manage local caches, the project recycle bin, and tools.','Bearer 认证':'Bearer authentication','已读 0 个文件':'0 files read','部分完成':'Partially completed'
  ,'固定孔：已保存例外':'Mounting holes: exception saved','固定孔：规划匹配':'Mounting holes: plan matched','固定孔：规划被破坏':'Mounting holes: plan violated','既有走线优化':'Existing track optimization','自动位号排放':'Automatic reference placement','创建工程记录需求与偏好，或导入已有 KiCad 工程。':'Create a project to record requirements and preferences, or import an existing KiCad project.','每次检查和每份文件，都有可追溯的版本。':'Every check and file has a traceable version.','正在读取本工程的器件查询记录…':'Loading this project’s component-query history…','达到本轮统计时间或文件数上限。':'This scan reached its time or file-count limit.','达到本轮统计时间上限。':'This scan reached its time limit.','已登记接口的逐针信号、针网或原生丝印不匹配；读取interfaceLegends.findings修正，旧版本保持。':'Registered interface pin signals, pin nets, or native silkscreen do not match. Read interfaceLegends.findings to correct them; the old version is preserved.'
  ,'保留原始BOM和双面坐标，按所选范围另出审核表。工艺/料号/方向未核对，不上传平台或下单。':'Preserve the original BOM and both-side coordinates and generate a separate review sheet for the selected scope. Process, part numbers, and orientation are not verified; nothing is uploaded or ordered.','从固定原图与PCB版本生成文件。资料一致性、工程资格、采购和平台预览分别核对；尚未提供报价、提交或付款。':'Generate files from fixed schematic and PCB versions. File consistency, engineering qualification, procurement, and platform preview are checked separately; quoting, submission, and payment are not provided here.','数量/工艺等空项保持未明确；本任务只准备审核资料':'Blank quantity or process fields remain unspecified; this task only prepares review files.'
  ,'缓存与临时文件':'Caches and temporary files','扫描后查看可清理目录与预计大小。':'Scan to review cleanable folders and estimated sizes.','扫描可清理内容':'Scan cleanable content','清理所选内容':'Clean selected items','最近清理记录':'Recent cleanup history','恢复工程':'Restore project','彻底删除文件':'Permanently delete files','显示实际删除的文件大小；被占用、变化或无法删除的目录会单独列出。':'Shows the actual size of deleted files. Busy, changed, or undeletable folders are listed separately.'
  ,'删除工程先移入这里，仍占用空间。可恢复，或彻底删除本地工程文件以释放空间。任务时钟与订单元数据保留；供应商订单不会被撤销。':'Deleted projects are moved here first and still use disk space. Restore them or permanently delete local project files to reclaim space. Task timing and order metadata are retained; supplier orders are not cancelled.','只扫描超过 1 小时未变化的下载缓存、测试临时副本和原生读取缓存。工程数据、登录资料、运行环境及正式验收记录保留。':'Only download caches, temporary test copies, and native read caches unchanged for more than one hour are scanned. Project data, login data, runtime environments, and formal validation records are retained.'
  ,'后台统计进行中，页面会自动更新；每个目录最多扫描60秒。仅统计所列目录的文件逻辑大小，排除链接；环境、依赖与其他目录不计入。统计期间文件可能变化，“至少”表示尚未统计完整。':'Background measurement is running and the page updates automatically; each folder is scanned for at most 60 seconds. Only logical file sizes in listed folders are counted, excluding links; environments, dependencies, and other folders are excluded. Files may change during measurement; “at least” means the result is incomplete.'
  ,'工程数据':'Project data','工程快照、任务产物、资料与数据库':'Project snapshots, task artifacts, documents, and database','本地运行资料':'Local runtime data','日志、冻结引擎与本机配置，包含受保护的凭据文件':'Logs, frozen engines, and local configuration, including protected credential files','开发缓存':'Development cache','本项目的构建与临时缓存':'Build and temporary caches for this project','验证记录':'Validation records','测试结果与检查证据':'Test results and check evidence','交付归档':'Deliverable archives','阶段交付文件与源码快照':'Stage deliverables and source snapshots'
  ,'本地服务 Python':'Local service Python','网页、HTTP 与 MCP 服务':'Web, HTTP, and MCP service','原生电路检查与导出':'Native circuit checks and export','受约束 PCB 编辑；此项仅检查配置文件存在':'Constrained PCB editing; this item only checks that the configuration file exists','独立机械环境':'Independent mechanical environment','机械建模、STEP 与装配几何':'Mechanical modeling, STEP, and assembly geometry','独立 CAM 环境':'Independent CAM environment','Gerber、钻孔与装配文件审核':'Gerber, drill, and assembly file review','ARM GNU 工具链':'ARM GNU toolchain'
};

const patterns:Array<[RegExp,(match:RegExpMatchArray)=>string]>=[
  [/^磁盘占用 (至少 )?(.+)$/u,m=>`Stored file size ${m[1]?'at least ':''}${m[2]}`],
  [/^文件总大小 (至少 )?(.+)$/u,m=>`Total file size ${m[1]?'at least ':''}${m[2]}`],
  [/^将永久删除当前列表中 (\d+) 个工程的本地设计、资料和任务产物，无法恢复。任务时钟、失败记录和订单元数据保留。确认后在后台执行，可离开本页；新加入回收站的工程不包含在本次操作中。$/u,m=>`Permanently delete local designs, sources and task outputs for the ${m[1]} projects currently listed. This cannot be undone. Task timing, failure records and order metadata are retained. Deletion runs in the background; projects added to the recycle bin later are excluded.`],
  [/^正在清空回收站：(\d+) \/ (\d+) 个工程，已实际删除 (.+)。可离开本页，后台将继续执行。$/u,m=>`Emptying recycle bin: ${m[1]} / ${m[2]} projects, ${m[3]} actually deleted. You may leave this page; deletion continues in the background.`],
  [/^(回收站清理完成|回收站清理未开始|回收站部分清理完成)：已清除 (\d+) 个工程，实际删除 (.+)；(\d+) 项未完成。$/u,m=>`${m[1]==='回收站清理完成'?'Recycle bin emptied':m[1]==='回收站清理未开始'?'Recycle bin cleanup did not start':'Recycle bin partially emptied'}: ${m[2]} projects removed, ${m[3]} actually deleted; ${m[4]} incomplete.`],
  [/^接口插拔方向 (.+)：(.+) · 板边偏移 ([\d.]+) mm；实际插头与外壳空间另验$/,m=>`Connector access ${m[1]}: ${m[2]==='安装基准朝外且贴合板边'?'outward datum aligned with edge':m[2]==='朝向板内，须旋转并重布'?'inward; rotate and reroute':'datum not aligned with edge'} · edge offset ${m[3]} mm; verify plug and enclosure clearance separately`],
  [/^接口方向未确认：(.+)；须提供有来源的插拔基准，不按接口名称猜方向$/,m=>`Unconfirmed connector direction: ${m[1]}; provide source-backed mating datums, never infer direction from its name`],
  [/^工程版本 (\d+)$/u,m=>`Project version ${m[1]}`],
  [/^(\d+) 份修订（含候选）$/u,m=>`${m[1]} revisions (including candidates)`],
  [/^查看更早 (\d+) 份修订与候选$/u,m=>`View ${m[1]} earlier revisions and candidates`],
  [/^(\d+) 份资料$/u,m=>`${m[1]} documents`],
  [/^(\d+) 个文件$/u,m=>`${m[1]} files`],
  [/^(\d+) 个错误 · (\d+) 个警告(?: · (\d+) 处未连)?$/u,m=>`${m[1]} errors · ${m[2]} warnings${m[3]===undefined?'':` · ${m[3]} unrouted`}`],
  [/^版本 ([\w-]+) · 等待此版本的预览$/u,m=>`Version ${m[1]} · waiting for this version's preview`],
  [/^正在读取 (.+) · ([\w-]+)$/u,m=>`Loading ${m[1]} · ${m[2]}`],
  [/^查看更早任务（还有 (\d+) 条）$/u,m=>`View earlier tasks (${m[1]} remaining)`],
  [/^拔出 (\d+)%$/u,m=>`Withdraw ${m[1]}%`],
  [/^修订 ([\w-]+) · (\d+) (?:个文件|files) · 独立机械版本$/u,m=>`Revision ${m[1]} · ${m[2]} files · independent mechanical version`],
  [/^修订 ([\w-]+) · (\d+) (?:个文件|files) · 独立固件版本$/u,m=>`Revision ${m[1]} · ${m[2]} files · independent firmware version`],
  [/^修订 ([\w-]+) · (\d+) (?:个文件|files) · 独立采购版本$/u,m=>`Revision ${m[1]} · ${m[2]} files · independent procurement version`],
  [/^排队 (.+) · 运行 (.+) · 结束 (.+)$/u,m=>`Queued ${duration(m[1])} · ran ${duration(m[2])} · finished ${m[3]}`],
  [/^板体几何交接(.+)；覆盖范围与配合检查另列$/u,m=>`Board geometry handoff ${m[1]==='完成'?'completed':translateText(m[1],'en')}; coverage and fit checks are listed separately`],
  [/^紧固连接 (\d+) 处 · 限位\/防脱 (\d+) 组；按声明位移检查。$/u,m=>`${m[1]} fastening connections · ${m[2]} retention groups; checked at declared displacements.`],
  [/^(\d+) 个原生零件 · 配合、公差、载荷与制造需独立验证。$/u,m=>`${m[1]} native parts · fit, tolerance, load, and manufacturing require separate validation.`],
  [/^修订 ([\w-]+)(?: · (当前版本|当前机械版本|未采纳))?$/u,m=>`Revision ${m[1]}${m[2]?` · ${translateText(m[2],'en')}`:''}`],
  [/^版本 (.+) · ([\d,]+) 字节$/u,m=>`Version ${m[1]} · ${m[2]} bytes`],
  [/^下载(.+) ↗$/u,m=>`Download ${translateText(m[1],'en')} ↗`],
  [/^查询时间：(.+) · 库存范围：SMT 接口报告值$/u,m=>`Queried ${m[1]} · inventory scope: SMT API reported value`],
  [/^外部符号库 · (\d+) 个冻结版本$/u,m=>`External symbol libraries · ${m[1]} frozen versions`],
  [/^本次原生DRC：(\d+) 错误 · (\d+) 警告 · (\d+) 未连；参考与阻抗另验$/u,m=>`Native DRC for this run: ${m[1]} errors · ${m[2]} warnings · ${m[3]} unrouted; reference and impedance are separate`],
  [/^未采纳原因：(.+)$/u,m=>`Not adopted: ${translateText(m[1],'en')}`],
  [/^名义实体与独立 STEP 回读通过 · (\d+) 个零件 · 已采纳机械版本 · 不代表制造或实物验证$/u,m=>`Nominal solids and independent STEP verification passed · ${m[1]} parts · mechanical version adopted · does not prove manufacturing or physical validation`],
  [/^编译与映像回读通过 · 已采纳独立固件版本 · 行为与实物验证另列$/u,()=>`Build and image verification passed · independent firmware version adopted · behavior and physical validation are separate`],
  [/^器件查询(.+)；库存为本次时刻观察，未预留 · 跟随最新查询$/u,m=>`Component query ${m[1]==='完成'?'completed':translateText(m[1],'en')}; inventory observed at this time and not reserved · following latest query`],
  [/^资料任务结束 · ([\w-]+)$/u,m=>`File task finished · ${m[1]}`],
  [/^([\w-]+) · 资料任务结束 · (.+)$/u,m=>`${m[1]} · file task finished · ${translateText(m[2],'en')}`],
  [/^([\w-]+) · 历史\/候选 · (.+)$/u,m=>`${m[1]} · history/candidate · ${m[2]}`],
  [/^([\w-]+) · 当前版本 · (.+)$/u,m=>`${m[1]} · current version · ${m[2]}`],
  [/^输入版本 ([\w-]+)$/u,m=>`Input version ${m[1]}`],
  [/^本场景选择 (\d+) 个器件，省略 (\d+) 项；数量 未明确。$/u,m=>`This scenario selects ${m[1]} components and omits ${m[2]}; quantity is unspecified.`],
  [/^(\d+) 个电气器件 · (\d+) 个原生机械对象 · 原始BOM (\d+) 项 · 原始坐标 (\d+) 项$/u,m=>`${m[1]} electrical components · ${m[2]} native mechanical objects · ${m[3]} original BOM items · ${m[4]} original positions`],
  [/^原生检查：ERC (\d+) 项 · DRC (\d+) 项（警告 (\d+)） · 未连 (\d+)。$/u,m=>`Native checks: ${m[1]} ERC findings · ${m[2]} DRC findings (${m[3]} warnings) · ${m[4]} unrouted.`],
  [/^原生检查：ERC 错误 (\d+) \/ 警告 (\d+) \/ 已排除 (\d+) · DRC 错误 (\d+) \/ 警告 (\d+) \/ 已排除 (\d+) · 未连 (\d+) · 原图差异 (\d+|未检查)。$/u,m=>`Native checks: ERC ${m[1]} errors / ${m[2]} warnings / ${m[3]} exclusions · DRC ${m[4]} errors / ${m[5]} warnings / ${m[6]} exclusions · ${m[7]} unrouted · ${m[8]==='未检查'?'schematic parity not checked':m[8]+' schematic differences'}.`],
  [/^删除于 (.+)$/u,m=>`Deleted ${m[1]}`],
  [/^信息读取于 (.+?)(?: · 存储正在后台统计。)?$/u,m=>`Information read ${m[1]}${bodyHas(m[0],'存储正在后台统计')?' · storage measurement is running in the background.':''}`],
  [/^(.+) · (完成|部分完成|失败|已取消|已中断) · ([\w-]+)$/u,m=>`${m[1]} · ${{'完成':'Completed','部分完成':'Partially completed','失败':'Failed','已取消':'Cancelled','已中断':'Interrupted'}[m[2]]} · ${m[3]}`],
  [/^建模特征 · (\d+) 步$/u,m=>`Model features · ${m[1]} steps`],
  [/^统一调整 (\d+) 颗螺钉$/u,m=>`Adjust all ${m[1]} screws together`],
  [/^版本 ([\w-]+) · 检查任务产物 · 爆炸仅用于查看$/u,m=>`Version ${m[1]} · check task artifact · exploded view is for inspection only`],
  [/^(.+) · 颜色$/u,m=>`${m[1]} · Color`],
  [/^(\d+) \/ (\d+) 字节$/u,m=>`${m[1]} / ${m[2]} bytes`],
  [/^(\d+) 项 · 启动\/内存已回读$/u,m=>`${m[1]} items · startup and memory verified`],
  [/^(.+\.[ch]) · ([\d,]+) 字节$/u,m=>`${m[1]} · ${m[2]} bytes`],
  [/^硬件 ([\w-]+) · (.+)$/u,m=>`Hardware ${m[1]} · ${m[2]}`],
  [/^状态灯：物理脚 (\d+)$/u,m=>`Status LED: physical pin ${m[1]}`],
  [/^采购料号未齐：(.+)$/u,m=>`Procurement part numbers are incomplete: ${m[1]}`],
  [/^(?:扫描中|统计中) · 已读 (\d+) 个文件$/u,m=>`Scanning · ${m[1]} files read`],
  [/^(.+) · 部分完成 · (.+)$/u,m=>`${m[1]} · partially completed · ${m[2]}`],
  [/^跳过 (\d+) 个链接或重解析入口。$/u,m=>`Skipped ${m[1]} links or reparse points.`],
  [/^FormForge (.+) · 服务初始化于 (.+)。状态基于服务启动时的检测与当前工具文件是否存在；刷新不会执行 CAD、编译或安装。KiCad Python 仅核对配置与文件存在。工具可用不代表具体工程检查通过。$/u,m=>`FormForge ${m[1]} · service initialized ${m[2]}. Status is based on startup detection and current tool-file availability; refresh does not run CAD, compile, or install anything. KiCad Python only checks configuration and file presence. Tool availability does not prove a project check passed.`],
  [/^复制(.+)$/u,m=>`Copy ${translateText(m[1],'en')}`],
  [/^(.+)已复制。$/u,m=>`${translateText(m[1],'en')} copied.`],
];

function duration(value:string){return value.replace(/(\d+) 分(?:钟)?/gu,'$1m').replace(/(\d+) 秒/gu,'$1s');}
function bodyHas(value:string,part:string){return value.includes(part);}

// Full phrases run before fragments so partially translated strings cannot
// prevent a dynamic status from matching on the next observer pass.
function translateEngineeringText(value:string):string|undefined{
  const exact=(engineeringMessages as Record<string,string>)[value];
  if(exact!==undefined)return exact;
  let m:RegExpMatchArray|null;
  if((m=value.match(/^(?:扫描中|统计中|Scanning) · 已读 ([\d,]+) (?:个文件|files)$/u)))return `Scanning · ${m[1]} files read`;
  if((m=value.match(/^(Before|After) edit: (.+)$/u)))return `${m[1]} edit: ${translateText(m[2],'en')}`;
  if((m=value.match(/^Live objects · ([\w-]+) · (.+) · browser font approximation$/u)))return `Live objects · ${m[1]} · ${translateText(m[2],'en')} · browser font approximation`;
  if((m=value.match(/^正在读取新候选 · 当前显示 ([\w-]+)$/u)))return `Loading new candidate · currently displaying ${m[1]}`;
  if((m=value.match(/^本次复核绑定所选设计版本 ([\w-]+)；(.+)。$/u)))return `This review is bound to selected design revision ${m[1]}; ${m[2]}.`;
  if((m=value.match(/^普通走线提案：转弯 (\d+) → (\d+) · 短折簇 (\d+) → (\d+) · 线长 (.+) → (.+) mm；接点与过孔位置保持，保存后的原生检查另列$/u)))return `Routing proposal: bends ${m[1]} → ${m[2]} · short-bend clusters ${m[3]} → ${m[4]} · length ${m[5]} → ${m[6]} mm; contact and via positions preserved; saved native checks listed separately`;
  if((m=value.match(/^版本 ([\w-]+) · (.+) · 爆炸仅用于查看$/u)))return `Version ${m[1]} · ${translateText(m[2],'en')} · exploded view is for inspection only`;
  if((m=value.match(/^摆放复核：(\d+) 组绕行值得检查 · 已分析 (\d+) 组二端串联连接$/u)))return `Placement review: ${m[1]} detours to inspect · ${m[2]} two-terminal series connections analyzed`;
  if((m=value.match(/^这是局部摆放提示；下界未计障碍和出线直段，不代表已有可行路线或全板最优。(\d+) 组复杂连接未纳入。候选仍须检查庭院、本体、净距、连接和已有电气条件。$/u)))return `Local placement advice only. The lower bound excludes obstacles and straight pad exits and does not prove a feasible route or a board-wide optimum. ${m[1]} complex connections are not covered. Candidates still require courtyard, body, clearance, connectivity and existing electrical checks.`;
  if((m=value.match(/^本次(已启用间隙避让|仅作间隙审核)(?: · 新增铜保存复核(通过|未通过))?。仅覆盖推定的表面SMD焊盘间隙；完整本体、未识别封装与装配空间尚未验证。$/u)))return `${m[1]==='已启用间隙避让'?'Gap avoidance enabled':'Gap review only'}${m[2]?` · new copper save verification ${m[2]==='通过'?'passed':'failed'}`:''}. Covers inferred surface SMD pad gaps only; complete bodies, unidentified footprints and assembly clearance remain unverified.`;
  if((m=value.match(/^反馈输入：(.+) · PCB SHA (.+)$/u)))return `Feedback input: ${m[1]} · PCB SHA ${m[2]}`;
  if((m=value.match(/^([A-Za-z][A-Za-z0-9_]*)：(.+)$/u))){const suffix=translateText(m[2],'en');if(suffix!==m[2])return `${m[1]}: ${suffix}`;}
  if((m=value.match(/^物理叠层 · (\d+) 层 · 名义 (.+) mm$/u)))return `Physical stackup · ${m[1]} layers · nominal ${m[2]} mm`;
  if((m=value.match(/^各层模型厚度合计 (.+) mm；原生叠层(已核对|待核对)，不表示阻抗通过。$/u)))return `Total modeled layer thickness: ${translateText(m[1],'en')} mm; native stackup ${m[2]==='已核对'?'verified':'pending verification'}; impedance is not verified.`;
  if((m=value.match(/^资料：(.+)$/u)))return `Evidence: ${m[1]}`;
  if((m=value.match(/^(.+) · (\d+) 个实体 · (.+) mm³$/u)))return `${m[1]} · ${m[2]} solids · ${m[3]} mm³`;
  if((m=value.match(/^绑定 PCB ([\w-]+) · 仅记录版本依赖；板体、器件与接口实体需另行明确导入。$/u)))return `Bound PCB ${m[1]} · revision dependency recorded; board, component and interface solids require explicit frozen imports.`;
  if((m=value.match(/^(\d+) 个独立机械孔 · 原电气连接(保持|未保持) · 尺寸、来源与版本已记录$/u)))return `${m[1]} independent mechanical holes · original electrical connections ${m[2]==='保持'?'preserved':'not preserved'} · dimensions, sources and revision recorded`;
  if((m=value.match(/^整板线形：(\d+) 条待审线链 · 短折簇 (\d+) · 局部折返 (\d+)；已在焊盘、过孔、分支与层\/线宽变化处分段，必要引出与等长需另审$/u)))return `Board routing quality: ${m[1]} chains to review · ${m[2]} short-bend clusters · ${m[3]} backtracks; chains split at pads, vias, branches and layer/width changes. Necessary escapes and length matching require review.`;
  if((m=value.match(/^裸露转角：直角 (\d+) · 锐角 (\d+)；焊盘内 (\d+)、真实分支 (\d+) 另列$/u)))return `Exposed corners: ${m[1]} right angles · ${m[2]} acute angles; ${m[3]} pad-internal corners and ${m[4]} real branches listed separately`;
  if((m=value.match(/^新增转角约束：(通过|未通过) · 拦截 (\d+) 处 · 既有未改 (\d+) 处$/u)))return `New corner policy: ${m[1]==='通过'?'passed':'failed'} · ${m[2]} blocked · ${m[3]} unchanged existing findings`;
  if((m=value.match(/^新增出线约束：(通过|未通过) · 拦截 (\d+) 处 · 既有未改 (\d+) 处；直接水平\/垂直连接邻近端子且未转弯另列$/u)))return `New pad-exit policy: ${m[1]==='通过'?'passed':'failed'} · ${m[2]} blocked · ${m[3]} unchanged findings; direct straight horizontal/vertical terminal connections listed separately`;
  if((m=value.match(/^焊盘出线：斜出 (\d+) · 偏心 (\d+) · 过早转弯 (\d+)；从焊盘中心按上下左右直出，焊盘外直行 (.+) mm 再转弯 · 覆盖 (\d+) 个焊盘 · 未覆盖 (\d+) 项$/u)))return `Pad exits: ${m[1]} diagonal · ${m[2]} off-center · ${m[3]} early bends; cardinal exits from pad centers with ${m[4]} mm straight lead outside copper before turning · ${m[5]} pads covered · ${m[6]} uncovered`;
  if((m=value.match(/^电容供电节点：(\d+)\/(\d+) 项中心线拓扑已证明；声明条件(通过|未通过)，去耦回路与电源完整性另验$/u)))return `Capacitor supply nodes: ${m[1]}/${m[2]} centerline topology rules proved; declared conditions ${m[3]==='通过'?'passed':'failed'}; decoupling loops and power integrity require separate checks`;
  if((m=value.match(/^本板创建时冻结了来源原理图 ([\w-]+) 的电气审查；这是交接依据，当前 PCB 检查另列。$/u)))return `This board retains the electrical review of source schematic ${m[1]} as its handoff basis; current PCB checks are listed separately.`;
  if((m=value.match(/^配置条件及待实测项 · (\d+)$/u)))return `Conditions and required physical checks · ${m[1]}`;
  if((m=value.match(/^待实测：([\s\S]+)$/u)))return `Physical verification required: ${m[1]}`;
  if((m=value.match(/^([✓✗]) (.+)：(.+)（含工程预算或典型值）$/u)))return `${m[1]} ${m[2]}: ${m[3]} (includes engineering allowance or typical values)`;
  if((m=value.match(/^所在磁盘可用 (.+)$/u)))return `Available disk space: ${m[1]}`;
  if((m=value.match(/^磁盘总容量 (.+)，包含其他应用和文件$/u)))return `Total disk capacity: ${m[1]}, including other applications and files`;
  if((m=value.match(/^统计于 (.+)，完成结果最多复用 (\d+) 秒。文件总大小与磁盘占用分别统计，排除链接；环境、依赖与其他目录不计入。压缩后的实际占用以落盘结果为准，节省比例因文件而异。“至少”表示尚未统计完整。$/u)))return `Measured ${m[1]}; completed results may be reused for up to ${m[2]} seconds. Total file size and stored file size are measured separately. Links, environments, dependencies and unlisted folders are excluded. Compression savings vary by file and reflect flushed data. At least means the measurement is incomplete.`;
  if((m=value.match(/^统计于 (.+)，完成结果最多复用 (\d+) 秒。仅统计所列目录的文件逻辑大小，排除链接；环境、依赖与其他目录不计入。统计期间文件可能变化，“至少”表示尚未统计完整。$/u)))return `Measured ${m[1]}; completed results may be reused for up to ${m[2]} seconds. Only logical file sizes in listed folders are counted, excluding links, environments, dependencies and other folders. Files may change during measurement; “at least” means incomplete.`;
  if((m=value.match(/^从库新增 (.+) · (.+) · 来源版本 (.+)$/u)))return `Add ${m[1]} from library · ${m[2]} · source version ${m[3]}`;
  if((m=value.match(/^新增 (\d+) 段导线$/u)))return `Add ${m[1]} wire segments`;
  if((m=value.match(/^内层(\d+)$/u)))return `Inner ${m[1]}`;
  if((m=value.match(/^(\d+) 个器件 · (\d+) 段走线 · 最小线宽为本版实际走线宽度$/u)))return `${m[1]} components · ${m[2]} track segments · minimum width measured from this revision's tracks`;
  if((m=value.match(/^新增网络标签 (.+)$/u)))return `Add net label ${m[1]}`;
  if((m=value.match(/^新增说明：([\s\S]+)$/u)))return `Add note: ${m[1]}`;
  if((m=value.match(/^查看其余 (\d+) 项修改$/u)))return `View ${m[1]} remaining changes`;
  if((m=value.match(/^明确替换 (\d+) 个原分组，形成 (\d+) 个目标分组；其余连接保持不变。$/u)))return `Replace ${m[1]} original groups with ${m[2]} target groups; preserve other connections.`;
  if((m=value.match(/^创建真实 PCB · (\d+) 个器件 · (.+) mm$/u)))return `Create native PCB · ${m[1]} components · ${m[2]} mm`;
  if((m=value.match(/^新增 (\d+) 段 (.+) 铜线 · (.+) mm$/u)))return `Add ${m[1]} ${m[2]} track segments · ${m[3]} mm`;
  if((m=value.match(/^新增 (.+) 过孔 · (.+) mm$/u)))return `Add ${m[1]} via · ${m[2]} mm`;
  if((m=value.match(/^新增机械安装孔 (.+) · (.+) mm · 位置 (.+) mm$/u)))return `Add mounting hole ${m[1]} · ${m[2]} mm · position ${m[3]} mm`;
  if((m=value.match(/^新增说明丝印 (.+) · (.+) · (.+) mm$/u)))return `Add legend ${m[1]} · ${m[2]} · ${m[3]} mm`;
  if((m=value.match(/^新增 (.+) (.+) 铺铜并原生填充$/u)))return `Add ${m[1]} ${m[2]} copper zone and fill natively`;
  if((m=value.match(/^设置 (\d+) 层物理叠层 · 名义 (.+) mm$/u)))return `Set ${m[1]}-layer physical stackup · nominal ${m[2]} mm`;
  if((m=value.match(/^冻结符号库 · (.+) · (\d+) 个符号$/u)))return `Frozen symbol library · ${m[1]} · ${m[2]} symbols`;
  if((m=value.match(/^(\d+) 个符号 · 格式版本 (\w+) · (.+)$/u)))return `${m[1]} symbols · format version ${m[2]} · ${m[3]}`;
  if((m=value.match(/^Download (.+) ↗$/u)))return `Download ${translateText(m[1],'en')} ↗`;
  if((m=value.match(/^修订 ([\w-]+) · (\d+) (?:个文件|files) · (.+)$/u)))return `Revision ${m[1]} · ${m[2]} files · ${translateText(m[3],'en')}`;
  if((m=value.match(/^修订 ([\w-]+) · (.+)$/u)))return `Revision ${m[1]} · ${translateText(m[2],'en')}`;
  if((m=value.match(/^排队 (.+) · 已运行 (.+)$/u)))return `Queued ${duration(m[1])} · running ${duration(m[2])}`;
  if((m=value.match(/^机械来源 · (\d+) 个冻结文件$/u)))return `Mechanical sources · ${m[1]} frozen files`;
  if((m=value.match(/^版本 ([\w-]+) · (.+) · 字体为网页近似$/u)))return `Revision ${m[1]} · ${translateText(m[2],'en')} · browser font approximation`;
  if((m=value.match(/^实时对象 · ([\w-]+) · (.+) · 字体为网页近似$/u)))return `Live objects · ${m[1]} · ${translateText(m[2],'en')} · browser font approximation`;
  if((m=value.match(/^板体与已选器件 · 版本 ([\w-]+) · 覆盖范围见报告$/u)))return `Board and selected components · revision ${m[1]} · see report for coverage`;
  if((m=value.match(/^布局反馈：间隙覆盖 (\d+)\/(\d+) · 未覆盖 (\d+) · 既有冲突 (\d+)$/u)))return `Layout feedback: gap coverage ${m[1]}/${m[2]} · uncovered ${m[3]} · existing conflicts ${m[4]}`;
  if((m=value.match(/^焊盘网络与原理图一致 · (\d+) 处待连线 · 已保存 PCB 草稿；布局布线和制造检查继续进行(?: · 原生确认合并 (\d+) 个物理连接分量)?$/u)))return `Pad nets match the schematic · ${m[1]} unrouted · PCB draft saved; layout, routing and manufacturing checks continue${m[2]?` · native checks confirmed ${m[2]} physical component merges`:''}`;
  if((m=value.match(/^修改(前|后)：(.+)$/u)))return `${m[1]==='前'?'Before':'After'} edit: ${translateText(m[2],'en')}`;
  if((m=value.match(/^自研搜索 (\d+)\/(\d+) · (.+) · 已找到 (\d+) 处连接候选$/u)))return `Route search ${m[1]}/${m[2]} · ${m[3]} · ${m[4]} connection candidates found`;
  if((m=value.match(/^KiCad CLI 导出已冻结器件 (.+)$/u)))return `Export frozen component ${m[1]} with KiCad CLI`;
  if((m=value.match(/^(.+) · ([A-Za-z][A-Za-z0-9_-]*)$/u))){const label=(engineeringMessages as Record<string,string>)[m[1]];if(label!==undefined)return `${label} · ${m[2]}`;}
  if((m=value.match(/^(.+) 颜色$/u)))return `${m[1]} color`;
  if((m=value.match(/^(.+) 材质$/u)))return `${m[1]} material`;
  if((m=value.match(/^(来源|许可)：(.*)$/u)))return `${m[1]==='来源'?'Source':'License'}: ${m[2]}`;
  if((m=value.match(/^本版计算值 (.+)$/u)))return `Calculated value for this revision: ${m[1]}`;
  if((m=value.match(/^实际删除 (\d+) (?:个文件|files)，(\d+) 项未完成。$/u)))return `Deleted ${m[1]} files; ${m[2]} items incomplete.`;
  return undefined;
}

const fragments:Array<[string,string]>=[
  ['功能分区','Functional groups'],['跨区网络','Cross-group nets'],['未分区器件','Unassigned components'],['接口基准已对齐板边','Interface datum aligned with board edge'],['需要调整接口位置或方向','Review interface position or direction'],
  ['关联器件','Affected components'],['受影响网络','Affected nets'],['试放阻挡','Trial placement obstacles'],['插拔基准待确认','Mating datum unconfirmed'],['可比较器件','Placement candidates'],['暂无','None'],['区域外','Outside region'],
  ['封装内部间距与线宽冲突，先审查器件或规则','Internal pad spacing conflicts with trace width; review the footprint or routing rule first'],['已有合法直出，可继续实际找路','A legal straight exit exists; continue actual pathfinding'],['比较外围器件局部摆放','Compare surrounding component placement'],['先审查阻挡铜线的明确重布范围','Review the explicit scope for obstructing copper rerouting'],['进一步核对板边、铜区、孔和本体约束','Review board-edge, copper-zone, hole and body constraints'],
  ['此历史版本未执行一体零件连通性验收。','This historical revision has no one-piece connectivity acceptance result.'],
  ['一体零件存在断开实体，未通过验收：','Disconnected solids in one-piece parts; acceptance failed: '],
  ['一体零件连通性通过；来源总成独立保留。','One-piece connectivity passed; source assemblies remain intact.'],
  ['尺寸参数','Dimensions'],['外观颜色','Appearance'],
  ['所运行规则通过','Executed rules passed'],['发现待处理问题','Issues found'],['旧输入的报告','Report for older input'],['等待此版本的预览','Waiting for this version preview'],
  ['还没有可显示的机械模型','No mechanical model available yet'],['此版本还没有三维模型','No 3D model for this version'],['尚无工程产物','No project artifacts'],['尚无输入版本','No input version'],
  ['检查任务产物','Check task artifact'],['诊断产物 · 任务未完成','Diagnostic artifact · task incomplete'],['候选预览 / 检查进行中','Candidate preview / checks running'],
  ['报告 ↗','Report ↗'],['找路依据 ↗','Routing evidence ↗'],['阶段耗时 ↗','Stage timing ↗'],['读取耗时 ↗','Read timing ↗'],['查看任务详情','View task details'],
  ['停止中','Stopping'],['取消','Cancel'],['草稿已保存','Draft saved'],['参考覆盖需处理','Reference coverage needs attention'],['所查几何已核对','Selected geometry verified'],['几何需处理','Geometry needs attention'],
  ['几何已核对','Geometry verified'],['完整PCBA已核对','Complete PCBA verified'],['仅板体已核对','Board substrate only verified'],['部分器件几何已核对','Selected component geometry verified'],['爆炸方向与紧固接收件装入方向不一致，请查看工程报告。','Exploded directions differ from the receiver entry directions. See the engineering report.'],['缺少原版本PCB绑定，刷新后再重建；不会静默解除依赖。','The original PCB binding is missing. Refresh before rebuilding; the dependency will not be removed.'],['插拔空间需处理','Insertion clearance needs attention'],['声明净空通过','Declared clearance passed'],['资料一致 · 未放行','Files consistent · not released'],['资料需处理','Files need attention'],
  ['采购草案','Procurement draft'],['料号资料通过','Part data passed'],['文件检查通过 · 未放行','File checks passed · not released'],['文件需处理','Files need attention'],['STEP已核对 · 未放行','STEP verified · not released'],['STEP需处理','STEP needs attention'],
  ['正在生成','Generating'],['正在读取图页','Loading sheet'],['当前显示','currently showing'],['当前仍显示旧版','still showing older version'],['预览未加载','Preview not loaded'],['重新加载此预览','Reload this preview'],['新图读取失败','New drawing failed to load'],['可重新选择图纸重试','select the drawing again to retry'],
  ['实时对象预览','Live object preview'],['实际候选对象实时显示','Live display of actual candidate objects'],['原生导出图可从上方列表选择','select native exports from the list above'],['检查与采纳状态独立','check and adoption states are independent'],
  ['拔出位置','Withdrawal position'],['就位','Seated'],['行程末端','End of travel'],['装配姿态','Assembled'],['爆炸查看','Exploded view'],
  ['统计中','Scanning'],['未创建','Not created'],['未取得','Unavailable'],['此位置未计入','Not counted'],['至少 ','At least '],[' 个文件',' files'],[' 个链接或重解析入口',' links or reparse points']
];

export function currentLanguage():UiLanguage{
  try{return localStorage.getItem(STORAGE_KEY)==='en'?'en':'zh-CN';}catch{return 'zh-CN';}
}

export function locale(){return currentLanguage()==='en'?'en-US':'zh-CN';}

export function translateText(value:string,language:UiLanguage=currentLanguage()):string{
  if(language!=='en'||!/[一-鿿]/u.test(value))return value;
  const match=value.match(/^(\s*)([\s\S]*?)(\s*)$/u);if(!match)return value;
  const [,leading,body,trailing]=match;
  const manufacturing=manufacturingEnglish[body];if(manufacturing!==undefined)return leading+manufacturing+trailing;
  const manufacturingDynamic=translateManufacturingText(body,text=>translateText(text,'en'));if(manufacturingDynamic!==undefined)return leading+manufacturingDynamic+trailing;
  const engineering=translateEngineeringText(body);
  if(engineering!==undefined)return leading+engineering+trailing;
  const schematic=translateSchematicText(body);if(schematic!==undefined)return leading+schematic+trailing;
  const direct=english[body];if(direct!==undefined)return leading+direct+trailing;
  for(const [pattern,render] of patterns){const found=body.match(pattern);if(found)return leading+render(found)+trailing;}
  const manufacturingSegments=translateManufacturingSegments(body);if(manufacturingSegments!==undefined)return leading+manufacturingSegments+trailing;
  let translated=body;
  for(const [source,target] of fragments)translated=translated.split(source).join(target);
  return leading+translated+trailing;
}

type TextRecord={source:string;rendered:string};
const textRecords=new WeakMap<Text,TextRecord>();
const attributeRecords=new WeakMap<Element,Map<string,TextRecord>>();
let observer:MutationObserver|undefined;

function ignored(element:Element|null){
  return !!element?.closest('script,style,code,pre,[translate="no"],[data-i18n-ignore]');
}

function applyText(node:Text,language:UiLanguage){
  if(ignored(node.parentElement))return;
  const value=node.nodeValue||'',record=textRecords.get(node);
  const source=record&&value===record.rendered?record.source:value;
  const rendered=language==='en'?translateText(source,'en'):source;
  textRecords.set(node,{source,rendered});if(value!==rendered)node.nodeValue=rendered;
}

const translatedAttributes=['aria-label','placeholder','title','content','alt'] as const;
function applyAttributes(element:Element,language:UiLanguage){
  // Preformatted configuration contents stay verbatim, but their own accessible
  // labels are interface copy and still need translation.
  if(element.closest('script,style,[translate="no"],[data-i18n-ignore]')||element.parentElement?.closest('code,pre'))return;
  let records=attributeRecords.get(element);if(!records){records=new Map();attributeRecords.set(element,records);}
  for(const name of translatedAttributes){
    if(!element.hasAttribute(name))continue;
    const value=element.getAttribute(name)||'',record=records.get(name);
    const source=record&&value===record.rendered?record.source:value;
    const rendered=language==='en'?translateText(source,'en'):source;
    records.set(name,{source,rendered});if(value!==rendered)element.setAttribute(name,rendered);
  }
}

function applyTree(root:Node,language:UiLanguage){
  if(root.nodeType===Node.TEXT_NODE){applyText(root as Text,language);return;}
  if(root.nodeType!==Node.ELEMENT_NODE&&root.nodeType!==Node.DOCUMENT_NODE)return;
  if(root.nodeType===Node.ELEMENT_NODE)applyAttributes(root as Element,language);
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
  while(walker.nextNode()){
    const node=walker.currentNode;
    if(node.nodeType===Node.TEXT_NODE)applyText(node as Text,language);else applyAttributes(node as Element,language);
  }
}

export function setLanguage(language:UiLanguage){
  try{localStorage.setItem(STORAGE_KEY,language);}catch{}
  document.documentElement.lang=language;document.documentElement.dataset.language=language;
  const select=document.getElementById('language-switch') as HTMLSelectElement|null;if(select)select.value=language;
  applyTree(document,language);
  window.dispatchEvent(new CustomEvent('formforge:languagechange',{detail:{language}}));
}

export function initI18n(){
  const language=currentLanguage();document.documentElement.lang=language;document.documentElement.dataset.language=language;
  const select=document.getElementById('language-switch') as HTMLSelectElement|null;
  if(select){select.value=language;select.onchange=()=>setLanguage(select.value==='en'?'en':'zh-CN');}
  applyTree(document,language);
  observer?.disconnect();observer=new MutationObserver(mutations=>{
    const active=currentLanguage();
    for(const mutation of mutations){
      if(mutation.type==='attributes')applyAttributes(mutation.target as Element,active);
      else {applyTree(mutation.target,active);for(const node of mutation.addedNodes)applyTree(node,active);}
    }
  });
  observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:[...translatedAttributes]});
}
