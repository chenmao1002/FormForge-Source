# FormForge

FormForge 是面向外部 Agent 的本地产品工程工作台，采用 HTML 网页与 Python 本地服务，共享 HTTP / MCP 业务入口。当前源码版本为 **0.37.1**。

包含工程版本管理、需求与偏好、KiCad 原理图和 PCB 工具、自研布局布线、机械建模、固件构建与烧录流程、制造资料及供应商浏览器集成。第三方软件、供应商服务和硬件仍需各自安装、配置或登录；本仓库不附带任何现成登录状态。

## 安装和运行

建议 Windows x64、Python 3.12+、Node.js（含 npm），EDA 功能使用 KiCad 10。其他系统上的本机接口尚未完整验证。

在仓库根目录运行 PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Start.ps1
```

默认访问 `http://127.0.0.1:17840`。也可以双击 `启动FormForge.cmd`。首次启动会创建空白数据目录和新的本机 API 令牌。

按需要安装可选模块：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Mechanical.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Cam.ps1 -PythonExe .venv\Scripts\python.exe
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Flash.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-ProcurementBrowser.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Desktop.ps1
```

固件构建还需安装 ARM GNU Toolchain，可在设置中选择工具链，或设置 `FORMFORGE_ARM_GCC`。KiCad 不在默认路径时设置 `FORMFORGE_KICAD_CLI` 和 `FORMFORGE_KICAD_PYTHON`。供应商网页集成需本机浏览器和用户自行登录。烧录需要相应硬件、驱动及独立授权；安装依赖不等于操作实物。

MCP 可使用服务的 `/mcp` 入口（Bearer 令牌见本机 `.local/api-token.txt`），或通过 `scripts/stdio-proxy.py` 接入。本地令牌不能提交到仓库。

## 验证

```powershell
npm ci --no-audit --no-fund
npm run check
npm run build
.venv\Scripts\python.exe -m pytest tests/test_source_smoke.py
npm --prefix procurement-browser ci --ignore-scripts --no-audit --no-fund
node --test desktop/tests/*.test.cjs procurement-browser/tests/*.test.cjs
```

这些检查验证源码基础运行和浏览器策略，不代替完整的工程、制造、供应商登录或实物验收。

## 数据与隐私

此仓库采用独立的全新 Git 历史，只包含应用源码、依赖锁定文件、必要的运行资源和来源/许可证说明。日常工程、订单、历史文档、运行数据库、日志、截图、凭据、浏览器缓存及旧 Git 历史均不在此仓库。

`data/`、`.local/`、`.cache/`、浏览器配置目录及环境文件保持本机私有。采购浏览器和桌面供应商会话会在运行后自行创建；请勿强制添加这些目录或上传运行后的整个安装目录。

仓库当前保持私有。**只有所有者针对公开发布作出新的明确授权后，才可修改仓库可见性、公开发布或创建公开镜像。**

## 许可

本仓库尚未为项目自有代码选定开源许可证；保留所有权利。计划公开前需由所有者选择许可证并复核第三方代码的发布条件。第三方代码、字体和库的原有许可证保存在对应目录，来源记录位于 `docs/migrations/`，这些记录用于运行依赖校验与归属说明，不包含原机器路径。
