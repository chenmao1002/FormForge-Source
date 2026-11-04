from __future__ import annotations

import os
import json
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from .project_storage import prepare_project_storage


@dataclass
class Config:
    root: Path
    port: int = 17840
    kicad_cli: str | None = None
    kicad_python: str | None = None
    mechanical_python: str | None = None
    arm_gcc: str | None = None
    cam_python: str | None = None

    def __post_init__(self):
        self.root = self.root.resolve()
        self.data = self.root / "data"
        self.local = self.root / ".local"
        self.data.mkdir(parents=True, exist_ok=True)
        self.local.mkdir(parents=True, exist_ok=True)
        self.project_storage = prepare_project_storage(self.root)
        self.tool_origins = {}
        saved = self._saved_toolchains()
        if not self.cam_python:
            environment = os.getenv('FORMFORGE_CAM_PYTHON')
            candidate=environment or str(self.root/'.venv-cam/Scripts/python.exe')
            self.cam_python=str(Path(candidate).resolve()) if Path(candidate).is_file() else None
            self.tool_origins['cam'] = 'environment' if environment and self.cam_python else 'managed-environment'
        else:self.tool_origins['cam'] = 'explicit'
        if not self.mechanical_python:
            candidate=self.root/'.venv-mechanical/Scripts/python.exe'
            self.mechanical_python=str(candidate) if candidate.is_file() else None
            self.tool_origins['mechanical'] = 'managed-environment'
        else:self.tool_origins['mechanical'] = 'explicit'
        token_path = self.local / "api-token.txt"
        if not token_path.exists():
            token_path.write_text(secrets.token_urlsafe(36), encoding="utf-8")
        self.token = token_path.read_text(encoding="utf-8").strip()
        if not self.kicad_cli:
            environment = os.getenv("FORMFORGE_KICAD_CLI")
            selected = saved.get('kicad')
            candidates = [environment, selected and selected.get('executable'),
                          r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
                          shutil.which("kicad-cli")]
            self.kicad_cli = next((str(p) for p in candidates if p and Path(p).is_file()), None)
            if self.kicad_cli:
                self.tool_origins['kicad'] = ('environment' if environment and Path(self.kicad_cli)==Path(environment)
                                               else 'saved-profile' if selected and Path(self.kicad_cli)==Path(selected.get('executable',''))
                                               else 'automatic')
            else:self.tool_origins['kicad'] = 'automatic'
        else:self.tool_origins['kicad'] = 'explicit'
        if not self.kicad_python:
            environment = os.getenv('FORMFORGE_KICAD_PYTHON')
            selected = saved.get('kicad')
            choices = [environment, selected and selected.get('companions',{}).get('python')]
            if self.kicad_cli: choices.append(str(Path(self.kicad_cli).parent/'python.exe'))
            self.kicad_python = next((str(p) for p in choices if p and Path(p).is_file()), None)
            self.tool_origins['kicad-python'] = ('environment' if environment and self.kicad_python and Path(self.kicad_python)==Path(environment)
                                                  else 'saved-profile' if selected and self.kicad_python and Path(self.kicad_python)==Path(selected.get('companions',{}).get('python',''))
                                                  else 'automatic')
        else:self.tool_origins['kicad-python'] = 'explicit'
        if not self.arm_gcc:
            environment = os.getenv('FORMFORGE_ARM_GCC')
            selected = saved.get('arm-gcc')
            self.arm_gcc = environment or (selected and selected.get('executable'))
            self.tool_origins['arm-gcc'] = 'environment' if environment else 'saved-profile' if self.arm_gcc else 'automatic'
        else:self.tool_origins['arm-gcc'] = 'explicit'

    def _saved_toolchains(self):
        path = self.local / 'workspace-settings.json'
        try:
            value = json.loads(path.read_text(encoding='utf8'))
            if value.get('schemaVersion') != 'formforge.workspace-settings/1':return {}
            toolchains=value.get('toolchains',{});profiles={row['id']:row for row in toolchains.get('profiles',[]) if isinstance(row,dict) and isinstance(row.get('id'),str)}
            return {kind:profiles.get(toolchains.get('active',{}).get(kind)) for kind in ('kicad','arm-gcc')}
        except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError):
            return {}

    @classmethod
    def default(cls):
        return cls(Path(os.getenv("FORMFORGE_ROOT", str(Path(__file__).resolve().parents[2]))),
                   int(os.getenv("FORMFORGE_PORT", "17840")))
