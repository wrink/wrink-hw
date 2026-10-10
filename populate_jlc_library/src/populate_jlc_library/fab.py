"""Produce everything JLCPCB needs to fabricate and assemble a KiCad board.

Gerbers, drill files, the upload zip, a BOM and a placement file (CPL) are written to one
directory. All work happens in a temporary staging directory first; the output directory is only
touched once every step has succeeded, so a failed run never leaves half-written results behind.
"""
from __future__ import annotations

import csv
import os
import shlex
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from .project import InputError, Target

#: Environment variable that replaces the ``kicad-cli`` command (the tests use it for a stand-in).
ENV_KICAD_CLI = "POPULATE_JLC_KICAD_CLI"

GERBER_LAYERS = ("F.Cu", "B.Cu", "F.Paste", "B.Paste", "F.Silkscreen", "B.Silkscreen",
                 "F.Mask", "B.Mask", "Edge.Cuts")
#: Files that go into the zip uploaded as "PCB" (drill maps and the like stay out).
ZIP_SUFFIXES = (".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gtp", ".gbp", ".gm1", ".gbrjob", ".drl")
#: Gerber files that must exist after plotting; a missing one means a layer was silently dropped.
REQUIRED_SUFFIXES = (".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gm1")

BOM_LABELS = ["Comment", "Designator", "Footprint", "LCSC Part #"]
BOM_FIELDS = ["Value", "Reference", "Footprint", "LCSC"]
CPL_LABELS = ["Designator", "Mid X", "Mid Y", "Layer", "Rotation"]


class FabError(Exception):
    """A step failed; the message says what to do about it."""


@dataclass
class Job:
    """One board: where its files are and where the results go."""

    name: str
    project_dir: Path
    pcb: Path
    sch: Path
    out_dir: Path

    @classmethod
    def from_target(cls, target: Target, out: Optional[Path] = None) -> "Job":
        pcb = target.root_schematic.resolve().with_suffix(".kicad_pcb")
        if not pcb.is_file():
            raise InputError(f"board {pcb.name} not found next to {target.root_schematic.name}")
        project_dir = target.project_dir.resolve()
        return cls(name=pcb.stem, project_dir=project_dir, pcb=pcb, sch=target.root_schematic.resolve(),
                   out_dir=Path(out).resolve() if out else project_dir / "gerber")


@dataclass
class Summary:
    out_dir: Path
    files: List[Path] = field(default_factory=list)
    bom_lines: int = 0
    bom_parts: int = 0
    placed: int = 0
    left_out: List[str] = field(default_factory=list)   # through-hole references (hand-soldered)
    warnings: List[str] = field(default_factory=list)


Report = Callable[[str, str], None]


# ---------------------------------------------------------------- kicad-cli

def kicad_cli_command() -> List[str]:
    override = os.environ.get(ENV_KICAD_CLI)
    if override:
        return shlex.split(override)
    exe = shutil.which("kicad-cli")
    if not exe:
        raise FabError("kicad-cli not found in PATH (it ships with KiCad 7 and later)")
    return [exe]


def run_cli(*args: object) -> subprocess.CompletedProcess:
    return subprocess.run(kicad_cli_command() + [str(a) for a in args], capture_output=True, text=True)


def _fail(what: str, proc: subprocess.CompletedProcess) -> FabError:
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return FabError(f"{what} failed (exit {proc.returncode})" + (f": {detail[-1]}" if detail else ""))


# ---------------------------------------------------------------- steps

def check_not_open(pcb: Path, force: bool) -> None:
    lock = pcb.with_name(f"~{pcb.name}.lck")
    if lock.exists() and not force:
        raise FabError(f"KiCad seems to have {pcb.name} open (found {lock.name}). "
                       "Save and close the PCB editor first, or run with --force.")


def run_drc(pcb: Path, report_file: Path) -> None:
    """Refill zones, save the board, run DRC with schematic parity. Any error or warning fails.

    Violations you have excluded in KiCad are respected. This rewrites the .kicad_pcb file.
    """
    proc = run_cli("pcb", "drc", "--refill-zones", "--save-board", "--schematic-parity",
                   "--severity-error", "--severity-warning", "--exit-code-violations",
                   "-o", report_file, pcb)
    if proc.returncode == 0:
        return
    text = report_file.read_text(encoding="utf-8") if report_file.is_file() else (proc.stderr or "")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    shown = "\n".join(lines[:40]) + (f"\n... ({len(lines) - 40} more lines)" if len(lines) > 40 else "")
    raise FabError("DRC found violations (fix or exclude them in KiCad, or use --skip-drc):\n" + shown)


def export_gerbers(pcb: Path, stage: Path) -> int:
    """Plot all layers. KiCad 10 calls the silkscreen layers F.Silkscreen, older versions F.SilkS."""
    last: Optional[subprocess.CompletedProcess] = None
    for silk in ("Silkscreen", "SilkS"):
        layers = ",".join(name.replace("Silkscreen", silk) for name in GERBER_LAYERS)
        attempt = stage / f"plot-{silk}"
        attempt.mkdir()
        last = run_cli("pcb", "export", "gerbers", "-o", f"{attempt}{os.sep}", "--check-zones",
                       "--subtract-soldermask", "--layers", layers, pcb)
        files = list(attempt.iterdir())
        if last.returncode == 0 and all(any(f.suffix == s for f in files) for s in REQUIRED_SUFFIXES):
            for f in files:
                f.rename(stage / f.name)
            for leftover in stage.glob("plot-*"):
                shutil.rmtree(leftover)
            return sum(1 for f in files if f.suffix != ".gbrjob")
    assert last is not None
    raise _fail("gerber export", last)


def export_drill(pcb: Path, stage: Path) -> List[str]:
    proc = run_cli("pcb", "export", "drill", "-o", f"{stage}{os.sep}", "--format", "excellon",
                   "--excellon-units", "mm", "--excellon-zeros-format", "decimal",
                   "--excellon-oval-format", "route", "--excellon-separate-th",
                   "--drill-origin", "absolute", "--generate-map", "--map-format", "pdf", pcb)
    names = sorted((p.stem.rsplit("-", 1)[-1] for p in stage.glob("*.drl")), reverse=True)   # PTH, NPTH
    if proc.returncode != 0 or not names:
        raise _fail("drill export", proc)
    return names


def make_zip(stage: Path, name: str) -> Path:
    path = stage / f"{name}_gerbers.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(stage.iterdir()):
            if f.suffix in ZIP_SUFFIXES:
                z.write(f, f.name)
    return path


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def convert_bom(rows: Iterable[Dict[str, str]], through_hole: Iterable[str]):
    """Drop hand-soldered (through-hole) designators; return (kept rows, designators lacking LCSC)."""
    tht = set(through_hole)
    kept: List[Dict[str, str]] = []
    missing: List[str] = []
    for row in rows:
        designators = [d for d in row["Designator"].split(",") if d and d not in tht]
        if not designators:
            continue
        row = dict(row, Designator=",".join(designators))
        if not row["LCSC Part #"].strip():
            missing.append(row["Designator"])
        kept.append(row)
    return kept, missing


def convert_cpl(pos_rows: Iterable[Dict[str, str]]) -> List[List[str]]:
    """KiCad position rows -> JLCPCB placement rows. KiCad's origin and rotation are used as they
    are, which is right as long as the footprints are JLCPCB's own (see populate-jlc-library)."""
    out = []
    for r in pos_rows:
        layer = "Top" if r["Side"].strip().lower() == "top" else "Bottom"
        out.append([r["Ref"], f"{float(r['PosX']):.4f}mm", f"{float(r['PosY']):.4f}mm", layer,
                    f"{float(r['Rot']) % 360:g}"])
    return out


def export_bom_and_cpl(job: Job, stage: Path, summary: Summary) -> None:
    raw_bom, pos_all, pos_smd = stage / "bom_raw.csv", stage / "pos_all.csv", stage / "pos_smd.csv"
    proc = run_cli("sch", "export", "bom", "--exclude-dnp", "--fields", ",".join(BOM_FIELDS),
                   "--labels", ",".join(BOM_LABELS), "--group-by", "Value,Footprint,LCSC",
                   "--ref-delimiter", ",", "--ref-range-delimiter", "", "-o", raw_bom, job.sch)
    if proc.returncode != 0:
        raise _fail("BOM export", proc)
    for target, extra in ((pos_all, []), (pos_smd, ["--smd-only"])):
        proc = run_cli("pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
                       *extra, "--exclude-dnp", "-o", target, job.pcb)
        if proc.returncode != 0:
            raise _fail("placement export", proc)

    smd = _read_csv(pos_smd)
    summary.left_out = sorted({r["Ref"] for r in _read_csv(pos_all)} - {r["Ref"] for r in smd})

    bom, missing = convert_bom(_read_csv(raw_bom), summary.left_out)
    with (stage / f"{job.name}_bom_jlcpcb.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BOM_LABELS, quoting=csv.QUOTE_ALL, extrasaction="ignore")
        w.writeheader()
        w.writerows(bom)
    with (stage / f"{job.name}_cpl_jlcpcb.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CPL_LABELS)
        w.writerows(convert_cpl(smd))
    raw_bom.unlink(); pos_all.unlink(); pos_smd.unlink()

    summary.bom_lines = len(bom)
    summary.bom_parts = sum(len(r["Designator"].split(",")) for r in bom)
    summary.placed = len(smd)
    if missing:
        summary.warnings.append("BOM lines without an LCSC part number: " + "; ".join(missing))


def publish(stage: Path, job: Job) -> List[Path]:
    """Replace this board's previous results (files named <board>*) with the staged ones."""
    job.out_dir.mkdir(parents=True, exist_ok=True)
    for old in job.out_dir.iterdir():
        if old.is_file() and old.name.startswith(job.name):
            old.unlink()
    moved = []
    for f in sorted(stage.iterdir()):
        if f.is_file():
            target = job.out_dir / f.name
            shutil.move(str(f), target)
            moved.append(target)
    return moved


# ---------------------------------------------------------------- orchestration

def generate(job: Job, *, skip_drc: bool = False, force: bool = False,
             report: Report = lambda label, detail: None) -> Summary:
    """Run every step for one board. Raises FabError; ``report(step, detail)`` is called as it goes."""
    check_not_open(job.pcb, force)
    summary = Summary(out_dir=job.out_dir)
    with tempfile.TemporaryDirectory(prefix="jlcpcb-fab-") as tmp:
        stage = Path(tmp) / "stage"
        stage.mkdir()
        if skip_drc:
            report("DRC", "skipped")
        else:
            run_drc(job.pcb, Path(tmp) / "drc.txt")
            report("DRC", "passed, no errors or warnings")
        n = export_gerbers(job.pcb, stage)
        report("gerbers", f"{n} layers")
        report("drill", ", ".join(export_drill(job.pcb, stage)))
        zip_path = make_zip(stage, job.name)
        report("zip", f"{zip_path.name} ({zip_path.stat().st_size / 1024:.0f} KB)")
        export_bom_and_cpl(job, stage, summary)
        report("BOM", f"{summary.bom_lines} lines, {summary.bom_parts} parts")
        report("placement", f"{summary.placed} parts")
        summary.files = publish(stage, job)
    return summary


def find_projects(start: Path) -> List[Path]:
    """All ``.kicad_pro`` files under the repository containing ``start`` (or under ``start``).

    Hidden directories and ``*-backups`` directories (KiCad's own backups) are skipped.
    """
    start = Path(start).resolve()
    root = next((p for p in (start, *start.parents) if (p / ".git").exists()), start)
    found: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not d.endswith("-backups"))
        found += [Path(dirpath) / f for f in sorted(filenames) if f.endswith(".kicad_pro")]
    return found
