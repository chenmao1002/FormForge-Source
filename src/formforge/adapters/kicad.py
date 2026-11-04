from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from ..store import DomainError
from ..jobs import Cancelled
from .importer import digest, verify_snapshot, within
from .board_stackup import read_native as read_stackup
from .schematic import DEFAULT_WIRE_WIDTH_MILS


def schematic_plot_source(workspace: Path, schematic: str, output: Path):
    """Repair a missing native plotting default only in a dedicated export copy.

    KiCad 10.0.6 treats an explicitly stored Default net class without wire_width
    as zero and exports default-width wires as stroke:none. The installed KiCad
    project templates use 6 mil. ERC/netlists continue to use the original copy.
    """
    original = within(workspace, schematic)
    project = original.with_suffix('.kicad_pro')
    if not project.is_file():
        return original, None
    settings = json.loads(project.read_text(encoding='utf-8-sig'))
    default = next((c for c in settings.get('net_settings', {}).get('classes', []) if c.get('name') == 'Default'), None)
    if default is None or default.get('wire_width', 0) > 0:
        return original, None
    previous = default.get('wire_width')
    default['wire_width'] = DEFAULT_WIRE_WIDTH_MILS
    drawing = output / 'drawing-workspace'
    shutil.copytree(workspace, drawing)
    drawing_project = within(drawing, project.relative_to(workspace).as_posix())
    drawing_project.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf8')
    report = dict(purpose='Native SVG/PDF export only; no wire paths, schematic files, ERC inputs or netlist inputs changed.',
                  setting='net_settings.classes.Default.wire_width', units='mil', before=previous, after=DEFAULT_WIRE_WIDTH_MILS,
                  defaultSource='Installed KiCad 10 project templates: Default wire_width=6 mil (0.1524 mm).',
                  project=project.relative_to(workspace).as_posix(), sourceProjectSha256=digest(project),
                  drawingProjectSha256=digest(drawing_project), schematicSha256=digest(original))
    return within(drawing, schematic), report


class KiCadAdapter:
    id = "kicad10"
    version = "0.6.4"

    def __init__(self, config):
        self.config = config
        self.cli = config.kicad_cli
        self.detected_version = None
        self.issue = None
        if self.cli:
            try:
                result = subprocess.run([self.cli, "version"], capture_output=True, text=True, timeout=12,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                self.detected_version = result.stdout.strip()
                if result.returncode or not self.detected_version.startswith("10."):
                    self.issue = "需要 KiCad 10；当前检测到的版本不匹配。"
            except Exception as exc:
                self.issue = f"无法启动 KiCad：{exc}"
        else:
            self.issue = "未找到 KiCad 10。可通过 FORMFORGE_KICAD_CLI 指定安装路径。"

    def describe(self):
        return dict(id=self.id, name="KiCad 10", version=self.detected_version,
                    available=self.issue is None, reason=self.issue,
                    capabilities=["import", "erc", "drc", "netlist", "svg_preview", "board_glb", "schematic_read", "schematic_edit", "schematic_initialize", "schematic_pdf", "bom",
                                  "symbol_library_probe", "symbol_library_import", "symbol_library_read", "schematic_review", "pcb_handoff"],
                    unavailable=["autonomous_circuit_synthesis", "placement", "routing", "resume", "incremental_geometry"],
                    progressMode="phase", verification="native-rule-checks-only")

    def inspect(self, revision, output: Path, params, phase, check_cancel, publish):
        if self.issue:
            raise DomainError("dependency_missing", self.issue)
        source = within(self.config.data, revision["relativePath"])
        verify_snapshot(source, revision["manifest"])
        workspace = output / "workspace"
        shutil.copytree(source, workspace)
        verify_snapshot(workspace, revision["manifest"])
        scratch = output / "scratch"
        scratch.mkdir()
        env = os.environ.copy()
        env.update(KICAD_CONFIG_HOME=str(scratch / "kicad-config"), TEMP=str(scratch), TMP=str(scratch))
        # Environment-dependent KiCad system libraries are explicitly pinned to this installation.
        share = Path(self.cli).resolve().parent.parent / "share" / "kicad"
        # KiCad appends its major.minor directory to KICAD_CONFIG_HOME. Seed only
        # vendor defaults, never the user's global configuration or credentials.
        private_config = scratch / "kicad-config" / "10.0"
        private_config.mkdir(parents=True)
        # CLI violation item descriptions use the private KiCad language setting
        # on Windows, even when the outer report headings are already English.
        # Keep native evidence deterministic without changing the user's settings.
        (private_config / 'kicad_common.json').write_text(
            json.dumps({'system': {'language': 'English'}}), encoding='utf8')
        for name in ("fp-lib-table", "sym-lib-table"):
            template = share / "template" / name
            if template.is_file():
                shutil.copyfile(template, private_config / name)
        for var, folder in (("KICAD10_SYMBOL_DIR", "symbols"), ("KICAD10_FOOTPRINT_DIR", "footprints"),
                            ("KICAD10_3DMODEL_DIR", "3dmodels")):
            if (share / folder).is_dir():
                env[var] = str(share / folder)
        deadline = time.monotonic() + params["timeoutSeconds"]
        checks = []
        commands = []

        def run(name, args, allow_violations=False):
            check_cancel()
            if time.monotonic() >= deadline:
                raise DomainError("tool_failed", "任务已达到时间预算。请查看已有报告后创建新任务。")
            phase(name)
            logfile = output / (f"command-{len(commands) + 1}.log")
            began = time.monotonic()
            with logfile.open("wb") as log:
                process = subprocess.Popen([self.cli, *map(str, args)], cwd=workspace, env=env,
                                           stdout=log, stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    while process.poll() is None:
                        check_cancel()
                        if time.monotonic() >= deadline:
                            raise DomainError("tool_failed", "KiCad 执行超时，当前输出仅供诊断。")
                        time.sleep(0.12)
                    code = process.returncode
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
            commands.append(dict(name=name, args=[str(a) for a in args], exitCode=code,
                                 elapsedSeconds=round(time.monotonic() - began, 3)))
            publish(logfile, "log", "text/plain")
            native_log=logfile.read_text(encoding='utf8',errors='replace')
            if 'Library table' in native_log and 'skipping' in native_log:
                raise DomainError('tool_failed','KiCad跳过了工程库表；不能把回退到全局库的结果当作冻结库检查。请修正库表格式后创建新任务。')
            if code not in ({0, 5} if allow_violations else {0}):
                raise DomainError("tool_failed", f"{name}失败（退出码 {code}），详细原因见任务日志。")
            return code

        def read_report(path, kind, exit_code):
            if not path.is_file():
                raise DomainError("tool_failed", "KiCad 没有生成预期检查报告。")
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            items = []
            if kind == "erc":
                if "sheets" not in raw:
                    raise DomainError("tool_failed", "无法识别 KiCad ERC 报告结构。")
                for sheet in raw["sheets"]:
                    items.extend(sheet.get("violations", []))
            else:
                if "violations" not in raw:
                    raise DomainError("tool_failed", "无法识别 KiCad DRC 报告结构。")
                for field in ("violations", "unconnected_items", "schematic_parity"):
                    items.extend(raw.get(field, []))
            active = [i for i in items if not i.get("excluded", False)]
            report = dict(kind=kind, status="passed" if not active and exit_code == 0 else "failed",
                          findings=len(active), errors=sum(i.get("severity") == "error" for i in active),
                          warnings=sum(i.get("severity") == "warning" for i in active),
                          unconnected=len(raw.get("unconnected_items", [])),
                          items=[dict(type=i.get("type", "unknown"), severity=i.get("severity", "unknown"),
                                      description=i.get("description", "")) for i in active[:80]])
            checks.append(report)
            publish(path, "report", "application/json")

        schematic = revision["entrypoints"].get("schematic")
        rendering = None
        baseline_only = bool(params.get("_baselineNetlistOnly"))
        if baseline_only and params.get("scope") != "schematic":
            raise DomainError("invalid_input", "基线网表导出仅限原理图。")
        board = revision["entrypoints"].get("board") if params.get('scope') != 'schematic' else None
        if schematic:
            sch = within(workspace, schematic)
            drawing_sch = sch
            if not baseline_only:
                report = output / "erc.json"
                code = run("检查原理图电气规则", ["sch", "erc", "--format", "json", "--severity-all",
                            "--exit-code-violations", "-o", report, sch], True)
                read_report(report, "erc", code)
                drawing_sch, rendering = schematic_plot_source(workspace, schematic, output)
                if rendering:
                    rendering['inputRevision'] = revision['id']
                    rendering_report = output / 'schematic-render-settings.json'
                    rendering_report.write_text(json.dumps(rendering, ensure_ascii=False, indent=2), encoding='utf8')
                    publish(rendering_report, 'schematic_render_report', 'application/json')
                svgs = output / "schematic"
                svgs.mkdir()
                run("导出原理图预览", ["sch", "export", "svg", "-o", str(svgs) + os.sep, drawing_sch])
                previews = sorted(svgs.glob("*.svg"))
                if not previews:
                    raise DomainError("tool_failed", "原理图预览为空。")
                for svg in previews:
                    publish(svg, "schematic_preview", "image/svg+xml")
            netlist = output / "netlist.xml"
            run("导出原生连接关系", ["sch", "export", "netlist", "--format", "kicadxml", "-o", netlist, sch])
            publish(netlist, "netlist", "application/xml")
            if params.get('includeBoardNetlist'):
                native_netlist = output / 'netlist.net'
                run('导出保留原生 PCB 网络名的网表', ['sch', 'export', 'netlist', '--format', 'kicadsexpr', '-o', native_netlist, sch])
                publish(native_netlist, 'pcb_native_netlist', 'text/plain')
            if params.get('includeDocumentation') and not baseline_only:
                pdf = output / 'schematic.pdf'
                run('导出原理图 PDF',['sch','export','pdf','-o',pdf,drawing_sch])
                publish(pdf,'schematic_pdf','application/pdf')
                bom = output / 'bom.csv'
                run('导出器件清单',['sch','export','bom','-o',bom,sch])
                publish(bom,'bom','text/csv')
        if board:
            pcb = within(workspace, board)
            report = output / "drc.json"
            parity = bool(schematic and pcb.with_suffix(".kicad_sch") == within(workspace, schematic))
            args = ["pcb", "drc", "--format", "json", "--severity-all", "--exit-code-violations", "-o", report]
            if parity:
                args.append("--schematic-parity")
            code = run("检查 PCB 规则与连接", [*args, pcb], True)
            read_report(report, "drc", code)
            checks[-1]["schematicParityChecked"] = parity
            previews=[("front", "F.Cu,F.Silkscreen,Edge.Cuts"),("back", "B.Cu,B.Silkscreen,Edge.Cuts")]
            previews.extend((layer.split('.')[0].lower(),layer+',Edge.Cuts') for layer in read_stackup(pcb)['copperLayers'][1:-1])
            for name, layers in previews:
                svg = output / f"pcb-{name}.svg"
                run("导出 PCB " + ("正面" if name == "front" else "背面" if name=='back' else name),
                    ["pcb", "export", "svg", "--layers", layers, "--mode-single", "--fit-page-to-board",
                     "--exclude-drawing-sheet", *(["--mirror"] if name=='back' else []), "-o", svg, pcb])
                publish(svg, "pcb_preview", "image/svg+xml")
            if params.get("include3d"):
                glb = output / "board.glb"
                run("生成板体 3D 预览", ["pcb", "export", "glb", "--board-only", "--include-tracks", "-o", glb, pcb])
                publish(glb, "board_model", "model/gltf-binary")
        check_cancel()
        verify_snapshot(source, revision["manifest"])
        summary = dict(backend="kicad10", toolVersion=self.detected_version, inputRevision=revision["id"],
                       checks=checks, checksPassed=bool(checks) and all(c["status"] == "passed" for c in checks),
                       manufacturingReady=False, hardwareVerified=False, commands=commands,
                       scope="导入工程的原生规则检查与预览；不是自动设计或制造放行。",
                       modelScope="board-only" if board and params.get("include3d") else None)
        summary['baselineNetlistOnly'] = baseline_only
        if baseline_only:summary['scope'] = 'Fresh native baseline netlist only; ERC and preview intentionally not repeated. Not an acceptance verdict.'
        summary['schematicRendering'] = rendering
        summary_path = output / "inspection.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        publish(summary_path, "summary", "application/json")
        return summary
