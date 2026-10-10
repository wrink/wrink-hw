"""Command line interface: ``jlcpcb-fab [project|dir ...] [options]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .fab import FabError, Job, find_projects, generate
from .project import InputError, resolve


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jlcpcb-fab",
        description="Generate everything needed to order a KiCad board from JLCPCB: gerbers, drill files, "
                    "the upload zip, a BOM and a placement (CPL) file. Through-hole parts are left out of "
                    "the BOM and CPL (they are hand-soldered).",
    )
    p.add_argument("projects", nargs="*", type=Path, metavar="PROJECT",
                   help="a .kicad_pro, a .kicad_sch, or a directory containing one project "
                        "(default: every project in the git repository you are in)")
    p.add_argument("-o", "--out", type=Path,
                   help="output directory (default: <project dir>/gerber); only with a single project")
    p.add_argument("--skip-drc", action="store_true",
                   help="do not run DRC (default: refill zones, save the board, DRC + schematic parity; "
                        "any error or warning stops the run)")
    p.add_argument("--force", action="store_true", help="run even if KiCad seems to have the board open")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _short(path: Path) -> str:
    """The path relative to the current directory when it is below it, else absolute."""
    try:
        return str(path.relative_to(Path.cwd().resolve())) or "."
    except ValueError:
        return str(path)


def _run_one(path: Path, args: argparse.Namespace) -> None:
    job = Job.from_target(resolve(path), args.out)
    print(f"{job.name}  ({_short(job.pcb)})")
    summary = generate(job, skip_drc=args.skip_drc, force=args.force,
                       report=lambda label, detail: print(f"  {label:<10} {detail}"))
    if summary.left_out:
        print(f"  {'left out':<10} {', '.join(summary.left_out)} (through-hole: order and solder yourself)")
    for w in summary.warnings:
        print(f"  warning    {w}", file=sys.stderr)
    print(f"  -> {_short(summary.out_dir)}/  ({len(summary.files)} files)")
    print("  Check polarized and rotated parts (modules, ICs, LEDs) in JLCPCB's placement preview.")


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    paths = args.projects
    if not paths:
        paths = find_projects(Path.cwd())
        if not paths:
            print("error: no .kicad_pro found in this repository; give a project path", file=sys.stderr)
            return 2
    if args.out and len(paths) > 1:
        print(f"error: --out needs a single project, but {len(paths)} were found", file=sys.stderr)
        return 2

    code = 0
    for i, path in enumerate(paths):
        if i:
            print()
        try:
            _run_one(path, args)
        except InputError as exc:
            print(f"error: {exc}", file=sys.stderr)
            code = max(code, 2)
        except FabError as exc:
            print(f"error: {exc}", file=sys.stderr)
            code = max(code, 1)
    return code


if __name__ == "__main__":
    sys.exit(main())
