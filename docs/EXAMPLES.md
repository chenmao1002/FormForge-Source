# FormForge 首次体验 · BRICK-90 与 SERVO8

中文 · [English](EXAMPLES.en.md)

这是 FormForge 0.37.1 的首次预发布体验包，包含应用源码、两个可导入的示例、原生设计文件及启动脚本。

| 示例 | 已有内容 | 尚未证明的范围 |
| --- | --- | --- |
| BRICK-90 复古掌机 | 原理图、PCB、39零件装配模型、游戏源码及制造资料 | 实物装配、显示/按键、手感和续航；固件依赖重审提示保留 |
| SERVO8 八路舵机控制板 | USB控制与预留3.3V串口的电路、PCB、STM32硬件PWM固件、电脑调试工具、Gerber/BOM/CPL | 没有完整PCBA/外壳总成交付；实物波形、负载、温升与USB通信未验，未生产放行 |

## 开始体验

1. 准备 Windows x64、Python 3.12、Node.js 24 和 Microsoft Edge，确保 Python/Node 可以从命令行启动。首次安装依赖需要联网。
2. 解压整个 ZIP，保留 `app`、两个 `.formforge.zip` 与启动脚本的相对位置。
3. 双击 **Start-Examples.cmd**。它会安装主服务与网页依赖、启动工作台，再分别导入两个示例。
4. 打开 **http://127.0.0.1:17990**，从左侧工程列表选择 BRICK-90 或 SERVO8。再次运行脚本会复用相同示例。

端口被占用时，在本目录运行：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-Examples.ps1 -Port 17991
~~~

停止服务：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\app\scripts\Stop.ps1
~~~

## 按页面体验

- **原理图**：点击“展开图纸”，缩放查看器件和连接。
- **PCB**：切换顶层、底层或全部铜层，查看板框、安装孔、走线及逐针标注。
- **BRICK-90 机械设计**：拖动旋转；调节“爆炸查看”后点击“适应”，恢复为0后再适应视图。
- **固件**：BRICK-90 选择 `game.c`；SERVO8 选择 `servo_core.c`，阅读应用源码。已有 HEX / BIN 可查看下载。
- **制造**：查看已有 Gerber、BOM 和坐标文件。这些是历史审核资料，不是生产放行。

查看已有结果无需先安装 CadQuery、ARM 工具链或登录供应商。重新执行 EDA 检查需要 KiCad 10，机械重建与固件编译需要对应的可选环境；方法见 `app/README.md`。

## SERVO8 电脑工具与供电范围

`native/SERVO8/servo-control.html` 是串口调试页面。可以先打开查看界面；本次预发布没有连接或验证真实板卡，不要把界面可打开当成通信或舵机动作已验收。

当前设计使用 STM32G030K8T6 输出八路硬件 PWM，CP2102N 作为 USB-UART 桥，AHCT244 缓冲控制信号。它针对已记录的 SG90/5V 原型方案，不能据此宣称适配任意舵机。

舵机使用独立外接稳压5V供电，USB不能给八路舵机供电；预留串口为3.3V逻辑。默认1000–2000µs是脉宽范围，不是已标定角度。OFF只停止控制脉冲，5V仍存在，不能替代机械急停。8A是设计预算，不是本板实测额定电流。进一步接线或运行前应完成供电、波形、负载和机械检查。

## 包含哪些文件

- `BRICK-90.formforge.zip`、`SERVO8.formforge.zip`：也可在现有 FormForge 中使用“导入工程包”；每次新导入使用新的本地身份。
- `native/BRICK-90/`、`native/SERVO8/`：方便直接查看的原生EDA、固件、模型或制造文件。SERVO8不包含机械总成。
- `app/`：应用源码，不附带预装虚拟环境、Node依赖、运行数据或登录态。
- `SHA256SUMS.json`：交付文件摘要；许可证和第三方声明在包根目录。

示例只保留必要版本与结果。历史检查不能代替本机重新验收、样机验证或供应商批准。厂家参考PDF未分发，来源链接保留在相关来源记录中；继续重建时需取得并重新登记所需资料。

首次报错请保留终端第一条错误和必要的本地日志片段。欢迎在仓库 Issues 反馈安装、导入或查看时的具体问题。
