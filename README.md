# FormForge

**让 AI Agent 与人一起，把电子产品的想法变成可编辑、可检查的工程。**

中文 · [English](README.en.md)

FormForge 是一个运行在本机的产品工程工作台，把**需求、原理图、PCB、机械结构、固件和制造资料**放进同一套工程流程。你可以在网页中查看设计、调整已开放的参数、检查版本和结果；外部 AI Agent 则通过 MCP 调用工程工具，执行明确的设计与检查任务。

例如，开发一个游戏手柄，既要设计电路和按键位置，也要考虑 PCB 与外壳的配合、固件引脚及后续制造文件。FormForge 将这些工作关联起来，让每一步都有对应的工程文件、输入版本和检查报告。

| 游戏手柄装配模型 | 游戏手柄爆炸视图 |
| --- | --- |
| ![游戏手柄的外壳、摇杆与按键装配模型](docs/images/gamepad-assembled.png) | ![游戏手柄外壳、PCBA、按键和紧固件的爆炸视图](docs/images/gamepad-exploded.png) |

*图中为设计过程中的 CAD 模型与软件界面示例。示例工程文件未随源码分发；图片不代表产品已经完成实物或制造验收。*

## 可以用它做什么

| 工作 | FormForge 提供的能力 |
| --- | --- |
| 整理需求 | 保存产品要求、用户偏好、已确认决定、待解决问题和使用检查 |
| 设计电路 | 创建或导入 KiCad 原理图，编辑器件与连接，读取原生电气检查和连接关系 |
| 设计 PCB | 规划布局、编辑器件与铜线、自研布线与局部优化，检查板框、接口标注及规则 |
| 设计结构 | 参数化建模、完整 PCBA 装配、部件显隐和爆炸视图，导出 STEP / GLB / STL |
| 开发固件 | 绑定硬件版本与引脚，生成受支持的 STM32 模板、构建并读取 ELF / HEX / BIN |
| 准备制造 | 整理 Gerber、BOM、贴装坐标及结构件文件，解析外部制造资料，使用受控的供应商流程 |
| 管理工程 | 保留修订、候选、任务、失败记录、报告和资料来源，导入或导出工程包 |

当前首个 EDA 后端为 **KiCad 10**。布局布线保留 FormForge 自研实现；机械建模使用 CadQuery / OpenCascade，固件构建使用 ARM GNU Toolchain。

## 从电路到结构与固件

### 原理图

原理图页面集中显示图纸、版本、器件、连接分组和检查结果。Agent 可以通过工具执行编辑，再用原生 ERC 与连接目标核对结果；网页用于查看和复核同一版本。

![USB 2.4 GHz 接收器的原理图工作区，包含图纸、版本与器件信息](docs/images/schematic-workspace.png)

### PCB

从带有明确针序标注的 USB-RS422 接口板，到包含摇杆、按键和控制电路的游戏手柄 PCB，都可以在工作台中查看铜层与器件，并通过工具执行布局、布线和检查。接口可达性、安装孔、供电与返回路径等仍需要结合具体设计审查。

| USB-RS422 接口板 | 游戏手柄 PCB |
| --- | --- |
| ![具有四角安装孔和接口针序丝印的 USB-RS422 PCB 示例](docs/images/usb-rs422-pcb.png) | ![游戏手柄 PCB 的器件、摇杆位置与顶层走线示例](docs/images/gamepad-pcb.png) |

### 机械结构与装配

将电路板和元器件作为完整 PCBA，与外壳、电池、按键和紧固件一起考虑。通过参数、部件显隐、多视角和爆炸视图检查内部布局；导出的工程实体可以继续编辑和复核。

| 风扇结构与内部布局 | 复古掌机结构 |
| --- | --- |
| ![风扇机械工作区中的线框检查、内部电路与部件列表](docs/images/fan-assembly.png) | ![复古掌机机械工作区中的外壳、屏幕和按键模型](docs/images/handheld-assembly.png) |

### 固件

固件与硬件依据和引脚绑定关联，页面可查看应用源码、Flash / RAM 使用量、向量表和构建产物。当前有 8 种 STM32 目标的 blink、UART echo 模板支持；其他模板以能力接口返回的范围为准。外部固件也可进入导入、格式检查和离线烧录计划流程。

![固件工作区展示源码、Flash 与 RAM 使用情况及向量表信息](docs/images/firmware-workspace.png)

## 怎样与 Agent 配合

**需求与使用场景 → 电路 → PCB → 结构装配 → 固件 → 制造资料与复核**

1. 在网页中创建工程，写明用途、接口、尺寸和约束。
2. 连接你选择的、能够访问本机 MCP 服务的外部 Agent。
3. 由 Agent 分析需求、提出方案并调用工具，专业引擎生成或检查实际工程文件。
4. 在网页查看候选、图纸、模型和报告，根据结果继续修改。
5. 检查通过且输入版本匹配后采纳相应结果；上游设计变化时，重新审查受影响的下游工作。

**Agent 负责推理和决策，FormForge 负责执行明确任务并保存工程证据。** 服务不内置绑定某家模型，也不自行调用收费模型。使用者仍需参与产品取舍、设计复核及必要的实物验证。

网页、HTTP 和 MCP 使用同一套业务入口，读取同一份工程与任务结果。

## 本地安装

目前主要验证 **Windows x64**。先准备 Python 3.12 和 Node.js；EDA 功能另需 KiCad 10。本轮安装测试使用 Python 3.12.14、Node.js 24.14 和 KiCad 10.0.6。

在仓库根目录运行 PowerShell：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Start.ps1
~~~

默认访问 [本机工作台](http://127.0.0.1:17840)，也可双击“启动FormForge.cmd”。首次运行会创建空白工程存储和新的本机 API 令牌。

按需要安装可选模块：

~~~powershell
# 机械建模
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Mechanical.ps1

# 外部制造文件解析
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Cam.ps1 -PythonExe .venv\Scripts\python.exe

# 离线烧录工具与芯片包
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Flash.ps1

# 采购浏览器及桌面界面
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-ProcurementBrowser.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Desktop.ps1
~~~

固件构建还需安装 ARM GNU Toolchain，并在设置中选择工具链，或设置 FORMFORGE_ARM_GCC。KiCad 不在默认路径时，可设置 FORMFORGE_KICAD_CLI 和 FORMFORGE_KICAD_PYTHON。桌面版需要下载 Electron 运行时，网络受限时可使用安装脚本提供的 DownloadProxy 参数。

MCP 使用服务的 /mcp 入口及 Bearer 认证，令牌见本机 .local/api-token.txt；也可通过 scripts/stdio-proxy.py 接入。令牌只供本机客户端配置使用，不要提交到仓库。

## 当前验证范围

2026-11-04，从此私人仓库重新检出源码，在同一台 Windows 上新建 4 个独立 Python 环境安装测试：

- **62 项 Python 与 41 项 Node 定向检查通过**，覆盖工程、原生工具、机械、制造文件、离线烧录与浏览器策略等选定流程。
- **8 种 STM32 × 2 种模板，共 16 次真实构建通过**，检查 ELF / HEX / BIN 与向量表。
- 实际网页完成工程创建、改名、8 个模块切换和窄屏设置检查；MCP 原生任务与 HTTP / 网页读取的修订一致。
- Electron 原生启动与工作台加载通过；隐藏窗口的供应商截图操作未计为通过。

这些结果建立在已配置的本机工具和测试样例上。PCB 草稿布线检查不等于完整实板验收，CAD 与构建通过也不等于实物性能或制造批准。干净 Windows / VM、真实供应商登录与实时交易、设备烧录及实物性能仍需分别验证。

可复现仓库内的基础检查：

~~~powershell
npm ci --no-audit --no-fund
npm run check
npm run build
.venv\Scripts\python.exe -m pytest tests/test_source_smoke.py
npm --prefix procurement-browser ci --ignore-scripts --no-audit --no-fund
node --test desktop/tests/*.test.cjs procurement-browser/tests/*.test.cjs
~~~

上述仓库自带基础检查是完整本机验收的一部分，不包含全部 103 项外部验收用例。

## 数据与发布状态

应用工程与运行数据默认保存在本机。此仓库包含源码、依赖声明、必要运行资源、第三方许可和经过筛选的介绍图片；不附带日常工程包、订单、嘉立创账号、浏览器登录态或缓存。供应商功能需使用者自行配置和登录，报价、提交、付款和生产放行分别管理，付款与生产放行不自动执行。

运行后生成的 data/、.local/、.cache/ 和浏览器配置目录应保持私有，不要强制添加到 Git。

**仓库当前保持私有。未经所有者新的明确授权，不得修改为公开、发布公开版本或建立公开镜像。**

项目自有代码尚未选定开源许可证；第三方代码、字体和资源保留各自的许可。公开前仍需完成代码权属与第三方授权复核。
