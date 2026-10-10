"""Thin wrapper around the ``easyeda2kicad`` command line tool."""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

ENV_COMMAND = "POPULATE_JLC_EASYEDA2KICAD"      # override the tool (used by the tests)
EXISTS = re.compile(r"exist|overwrite", re.IGNORECASE)


@dataclass
class Result:
    lcsc: str
    status: str                                  # 'ok', 'exists' or 'failed'
    new_files: List[str] = field(default_factory=list)
    output: str = ""


def default_command() -> List[str]:
    env = os.environ.get(ENV_COMMAND)
    return shlex.split(env) if env else [sys.executable, "-m", "easyeda2kicad"]


class Downloader:
    def __init__(self, out_dir: Path, libname: str = "jlcpcb", *, full: bool = False,
                 overwrite: bool = False, command: Optional[Sequence[str]] = None,
                 timeout: float = 90.0, project_dir: Optional[Path] = None) -> None:
        self.out_dir = Path(out_dir).resolve()
        self.project_dir = Path(project_dir).resolve() if project_dir else None
        self.libname = libname
        self.full = full
        self.overwrite = overwrite
        self.command = list(command) if command else default_command()
        self.timeout = timeout

    @property
    def pretty_dir(self) -> Path:
        return self.out_dir / f"{self.libname}.pretty"

    def _footprints(self) -> set:
        return {p.name for p in self.pretty_dir.glob("*.kicad_mod")} if self.pretty_dir.is_dir() else set()

    @property
    def project_relative(self) -> bool:
        """3D model paths can be written as ${KIPRJMOD}/... only if the library is inside the project."""
        if not self.project_dir:
            return False
        try:
            self.out_dir.relative_to(self.project_dir)
            return True
        except ValueError:
            return False

    def args(self, lcsc: str) -> List[str]:
        flags = ["--full"] if self.full else ["--footprint", "--3d"]
        if self.project_relative:                    # easyeda2kicad computes the path relative to its cwd
            flags.append("--project-relative")
        if self.overwrite:
            flags.append("--overwrite")
        return self.command + [f"--lcsc_id={lcsc}", *flags, "--output", str(self.out_dir / self.libname)]

    def fetch(self, lcsc: str) -> Result:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        before = self._footprints()
        try:
            proc = subprocess.run(self.args(lcsc), capture_output=True, text=True, timeout=self.timeout,
                                  cwd=self.project_dir if self.project_relative else None)
            rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            return Result(lcsc, "failed", [], f"timed out after {self.timeout:g} s")
        except OSError as exc:                       # tool not runnable
            return Result(lcsc, "failed", [], str(exc))
        new = sorted(self._footprints() - before)
        if rc == 0:
            return Result(lcsc, "ok", new, out)
        if not new and EXISTS.search(out):           # footprint already in the library
            return Result(lcsc, "exists", [], out)
        return Result(lcsc, "failed", [], out)
