# FormForge

**Describe what you want. Let an agent help turn it into a real product.**

[中文](README.md) · English · [GPL-3.0](LICENSE)

## Featured project: BRICK-90 retro handheld

**Designing a retro handheld with an agent: circuits, PCB, enclosure, and game firmware.**

I use BRICK-90 to show a real design workflow in FormForge. Starting from a portrait-format monochrome falling-block game handheld, I worked with an external agent calling engineering tools to develop the circuit, two-layer PCB, mechanical assembly, and STM32 game firmware through several rounds of changes and review.

[![BRICK-90 design walkthrough: click the animation for the full video](docs/images/brick90-demo.gif)](docs/videos/brick90-design-demo.mp4)

**[▶ Watch / download the full design walkthrough (1 min 37 sec)](docs/videos/brick90-design-demo.mp4)**

1080p · Chinese captions · no narration. The video reviews the existing project's requirements, schematics, PCB, mechanical assembly, firmware, and manufacturing files. The animation above is an excerpt of the exploded assembly view.

| BRICK-90 assembly | Internal layout and exploded view |
| --- | --- |
| ![BRICK-90 CAD assembly showing the enclosure, display, and controls](docs/images/brick90-assembled.png) | ![BRICK-90 exploded view showing the enclosure, display, buttons, PCBA, and battery compartment](docs/images/brick90-exploded.png) |

*These are CAD previews. The screen artwork is a visual reference.*

| Stage | What has been generated or checked in this case |
| --- | --- |
| Circuits and PCB | KiCad schematics, a two-layer PCB, and native ERC / DRC checks tied to the corresponding revisions |
| Mechanical assembly | Enclosure, buttons, display retention, and battery compartment; STEP / GLB assemblies and individual printable STL parts |
| Game firmware | STM32 game application, ARM GCC build outputs in ELF / HEX / BIN, and game-logic tests on the host computer |
| Manufacturing preparation | Gerber, drill, BOM, placement, and mechanical-part files |

This case includes human decisions and engineering review. The work so far covers the design files and checks above. Physical assembly, display and button operation, feel, and battery life still need prototype testing. Manufacturing and delivery have not been completed. I want to show what each step has actually achieved as I work toward a complete physical product.

## Why I am building FormForge

I want making an electronic product to begin with a simple request:

> “I need a game controller.”

From there, an agent would understand the requirements, propose a design, and automatically develop the **schematics, PCB, mechanical structure, and firmware**. It would check how those pieces fit together, then **place orders for PCB fabrication, assembly, and mechanical parts**, taking the design through to manufacturing and delivery.

The experience I am aiming for is simple: **say what you want, and a week later it arrives at your doorstep.**

That is my goal for FormForge: shorten the distance between describing a product and receiving it. This is the complete experience I want to build toward. Today, key design choices, engineering review, and manufacturing still require human involvement, and delivery time depends on the process and supplier.

To work toward that goal, I am bringing **requirements, schematics, PCB design, mechanical assemblies, firmware, and manufacturing files** into one engineering workbench. People can review and adjust designs in the web interface, while an external AI agent calls tools through MCP. Each step produces actual engineering files, revisions, and check results.

I want FormForge to help people turn ideas into physical products and give hardware-agent developers a practical set of engineering tools.

## What you can do

| Work | Available capabilities |
| --- | --- |
| Define requirements | Keep product requirements, preferences, confirmed decisions, open issues, and checks of how a product will be used |
| Design circuits | Create or import KiCad schematics, edit components and connections, and read native electrical checks and connectivity |
| Design PCBs | Plan placement, edit components and copper, use FormForge's own routing and local optimization tools, and review outlines, interface legends, and rules |
| Design mechanical parts | Build parametric geometry, work with complete PCBAs, inspect parts and exploded views, and export STEP / GLB / STL |
| Develop firmware | Bind firmware to hardware revisions and pins, generate supported STM32 templates, and build and inspect ELF / HEX / BIN files |
| Prepare manufacturing files | Organize Gerber, BOM, placement, and mechanical files; parse external manufacturing inputs; use supported supplier workflows |
| Manage projects | Preserve revisions, candidates, tasks, failures, reports, and source provenance; import and export project packages |

I chose **KiCad 10** as the first EDA backend and kept FormForge's own placement and routing implementation. Mechanical modeling uses CadQuery / OpenCascade, and firmware builds use the ARM GNU Toolchain.

## More projects and workspaces

The views below also show a USB receiver, a USB-RS422 interface board, a game controller, and a fan, along with BRICK-90 in the mechanical workspace.

### Schematics

The schematic workspace brings drawings, revisions, components, connectivity groups, and check results together. Agents can make changes through the tools, then check them against native ERC and declared connectivity targets. The web interface displays the same revision for review.

![USB 2.4 GHz receiver schematic workspace with the drawing, revision, and component information](docs/images/schematic-workspace.png)

### PCBs

The examples range from a USB-RS422 interface board with explicit pin legends to a game controller PCB with sticks, buttons, and control circuitry. Inspect copper layers and components, then use the tools for placement, routing, and checks. Connector access, mounting, power delivery, and return paths still require review for each design.

| USB-RS422 interface board | Game controller PCB |
| --- | --- |
| ![USB-RS422 PCB example with four corner mounting holes and interface pin legends](docs/images/usb-rs422-pcb.png) | ![Game controller PCB showing component placement, stick locations, and top-layer routing](docs/images/gamepad-pcb.png) |

### Mechanical design and assembly

Treat the board and its components as a complete PCBA alongside the enclosure, battery, controls, and fasteners. Use parameters, part visibility, multiple views, and exploded assemblies to inspect the internal arrangement. Export engineering geometry for further editing and review.

| Fan assembly and internal layout | BRICK-90 handheld workspace |
| --- | --- |
| ![Fan mechanical workspace with wireframe inspection, internal electronics, and the parts list](docs/images/fan-assembly.png) | ![Retro handheld mechanical workspace showing the enclosure, display, and controls](docs/images/handheld-assembly.png) |

| Game controller assembly | Exploded assembly |
| --- | --- |
| ![Game controller CAD assembly with enclosure, sticks, and buttons](docs/images/gamepad-assembled.png) | ![Exploded controller assembly showing the enclosure, PCBA, controls, and fasteners](docs/images/gamepad-exploded.png) |

### Firmware

Firmware is linked to hardware evidence and pin assignments. The workspace shows application source, Flash and RAM use, vector tables, and build artifacts. Eight STM32 targets currently support blink and UART echo templates; consult the capability interface for other template support. External firmware can also go through import, format validation, and offline flashing-plan preparation.

![Firmware workspace with application source, Flash and RAM use, and vector-table information](docs/images/firmware-workspace.png)

## Working with an agent

**Requirements and use cases → circuits → PCB → mechanical assembly → firmware → manufacturing files and review**

1. Create a project in the web interface and describe its purpose, interfaces, dimensions, and constraints.
2. Connect an external agent that can access the local MCP service.
3. Let the agent analyze requirements, propose choices, and call tools. Engineering engines produce or inspect actual project files.
4. Review candidates, drawings, models, and reports in the web interface, then continue iterating.
5. Adopt results when the required checks pass and the input version still matches. Reassess affected downstream work when an upstream design changes.

**The agent handles reasoning and decisions; FormForge executes explicit tasks and keeps the engineering evidence.** The service is not tied to a particular model and does not independently invoke paid models. Users remain involved in product tradeoffs, design review, and necessary physical testing.

The web interface, HTTP API, and MCP tools share the same business operations and project state.

## Local installation

**Windows x64** is the main validated platform. Install Python 3.12 and Node.js first; EDA functions also require KiCad 10. The installation check used Python 3.12.14, Node.js 24.14, and KiCad 10.0.6.

Run PowerShell from the repository root:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Start.ps1
~~~

Open the [local workbench](http://127.0.0.1:17840), or double-click “启动FormForge.cmd”.

Install optional modules as needed:

~~~powershell
# Mechanical modeling
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Mechanical.ps1

# External manufacturing-file parsing
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Cam.ps1 -PythonExe .venv\Scripts\python.exe

# Offline flashing tools and device packs
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Flash.ps1

# Procurement browser and desktop interface
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-ProcurementBrowser.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Setup-Desktop.ps1
~~~

Firmware compilation also requires the ARM GNU Toolchain. Select it in settings or set FORMFORGE_ARM_GCC. For a non-default KiCad installation, set FORMFORGE_KICAD_CLI and FORMFORGE_KICAD_PYTHON. The desktop interface downloads an Electron runtime; the setup script provides a DownloadProxy option for restricted networks.

MCP uses the service's /mcp endpoint with Bearer authentication. The local token is stored in .local/api-token.txt. A stdio bridge is available at scripts/stdio-proxy.py.

## Validation status

On November 4, 2026, I checked out the repository again and installed it into four fresh Python environments on the same Windows host:

- **62 Python and 41 Node targeted checks passed**, covering selected project, native-tool, mechanical, manufacturing-file, offline-flashing, and browser-policy flows.
- **Eight STM32 targets × two templates: 16 real builds passed**, with ELF / HEX / BIN and vector-table checks.
- Real web UI checks covered project creation and renaming, eight module pages, and narrow-screen settings. MCP native tasks and HTTP / web reads referred to the same revision.
- Native Electron startup and workbench loading passed. Supplier screenshot operations in a hidden window were not counted as passing.

These results use configured local tools and test fixtures. Draft PCB routing checks do not establish acceptance of a complete physical board. CAD and build checks do not establish physical performance or manufacturing approval. A clean Windows / VM installation, real supplier login and live transactions, device flashing, and physical behavior require separate validation.

Reproduce the basic checks included in this repository:

~~~powershell
npm ci --no-audit --no-fund
npm run check
npm run build
.venv\Scripts\python.exe -m pytest tests/test_source_smoke.py
npm --prefix procurement-browser ci --ignore-scripts --no-audit --no-fund
node --test desktop/tests/*.test.cjs procurement-browser/tests/*.test.cjs
~~~

The bundled basic checks cover a subset of the full local acceptance work; the repository does not include all 103 external acceptance cases.

## License

I am releasing FormForge under the **GNU General Public License v3.0 (GPL-3.0-only)**. You can use, study, modify, and redistribute it under that license. If you distribute a modified version, you must also meet GPL-3.0's source-availability and licensing requirements.

See [LICENSE](LICENSE) for the full terms. Third-party code, fonts, and resources retain their applicable licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).

Issues and pull requests are welcome, whether you are sharing your experience, reporting a problem, or helping improve the project.
