"""Resolve what the user pointed us at, and register the generated library in fp-lib-table."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class InputError(Exception):
    """The given path is not something we can work with."""


@dataclass
class Target:
    project_dir: Path
    root_schematic: Path
    project_file: Optional[Path]


def resolve(path: Path) -> Target:
    """Accept a ``.kicad_pro``, a ``.kicad_sch`` or a directory containing exactly one project."""
    path = Path(path)
    if not path.exists():
        raise InputError(f"{path} does not exist")
    if path.is_dir():
        pros = sorted(path.glob("*.kicad_pro"))
        if len(pros) == 1:
            return resolve(pros[0])
        if len(pros) > 1:
            raise InputError(f"{path} contains several projects: {', '.join(p.name for p in pros)}")
        schs = sorted(path.glob("*.kicad_sch"))
        if len(schs) == 1:
            return resolve(schs[0])
        raise InputError(f"no single .kicad_pro or .kicad_sch found in {path}")
    if path.suffix == ".kicad_pro":
        sch = path.with_suffix(".kicad_sch")
        if not sch.is_file():
            raise InputError(f"root schematic {sch.name} not found next to {path.name}")
        return Target(path.parent, sch, path)
    if path.suffix == ".kicad_sch":
        pro = path.with_suffix(".kicad_pro")
        return Target(path.parent, path, pro if pro.is_file() else None)
    raise InputError(f"expected a .kicad_pro, a .kicad_sch or a directory, got {path.name}")


def register_library(project_dir: Path, name: str, pretty_dir: Path, descr: str = "") -> str:
    """Add ``name`` to the project's fp-lib-table. Returns 'created', 'added' or 'present'."""
    table = Path(project_dir) / "fp-lib-table"
    pretty_dir = Path(pretty_dir).resolve()
    try:
        uri = "${KIPRJMOD}/" + pretty_dir.relative_to(Path(project_dir).resolve()).as_posix()
    except ValueError:
        uri = pretty_dir.as_posix()
    entry = f'\t(lib (name "{name}")(type "KiCad")(uri "{uri}")(options "")(descr "{descr}"))\n'
    if not table.is_file():
        table.write_text(f"(fp_lib_table\n\t(version 7)\n{entry})\n", encoding="utf-8")
        return "created"
    text = table.read_text(encoding="utf-8")
    if f'(name "{name}")' in text:
        return "present"
    body = text.rstrip()
    if not body.endswith(")"):
        raise InputError(f"{table} does not look like an fp-lib-table")
    table.write_text(body[:-1].rstrip("\n") + "\n" + entry + ")\n", encoding="utf-8")
    return "added"
