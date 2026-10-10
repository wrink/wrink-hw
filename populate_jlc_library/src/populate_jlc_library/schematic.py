"""Collect LCSC part numbers from a KiCad schematic, following hierarchical sheets."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Set

from . import sexpr

log = logging.getLogger(__name__)

#: Symbol fields that may hold an LCSC part number (compared case-insensitively).
DEFAULT_FIELDS = ("LCSC", "LCSC Part #", "LCSC Part", "LCSC#", "JLCPCB Part #", "JLCPCB", "JLC")
_LCSC = re.compile(r"\bC\d{2,}\b", re.IGNORECASE)


@dataclass
class Part:
    """One distinct LCSC part and where it is used."""

    lcsc: str
    refs: Set[str] = field(default_factory=set)
    values: Set[str] = field(default_factory=set)
    footprints: Set[str] = field(default_factory=set)
    dnp_everywhere: bool = True

    def sorted_refs(self) -> List[str]:
        def key(r: str):
            m = re.fullmatch(r"([A-Za-z#]+)(\d*)(.*)", r)
            return (m.group(1), int(m.group(2) or 0), m.group(3)) if m else (r, 0, "")

        return sorted(self.refs, key=key)


@dataclass
class Collection:
    parts: Dict[str, Part] = field(default_factory=dict)
    without_lcsc: List[str] = field(default_factory=list)  # references of real parts lacking a number
    sheets: List[Path] = field(default_factory=list)


def _instance_refs(symbol: sexpr.Node) -> Set[str]:
    refs: Set[str] = set()
    for inst in sexpr.children(symbol, "instances"):
        for proj in sexpr.children(inst, "project"):
            for path in sexpr.children(proj, "path"):
                for r in sexpr.children(path, "reference"):
                    if len(r) > 1 and isinstance(r[1], str):
                        refs.add(r[1])
    return refs


def _find_lcsc(props: Dict[str, str], wanted: Set[str]) -> str:
    for name, value in props.items():
        if name.lower() in wanted:
            m = _LCSC.search(value)
            if m:
                return m.group(0).upper()
    return ""


def collect_parts(root: Path, fields: Iterable[str] = DEFAULT_FIELDS) -> Collection:
    """Walk ``root`` and every sub-sheet; return the distinct LCSC parts found.

    Power symbols and ``#``-references are ignored. A part appears once, with all its
    references merged, however many times (or on however many sheets) it is used.
    """
    wanted = {f.lower() for f in fields}
    result = Collection()
    seen: Set[Path] = set()

    def walk(path: Path) -> None:
        path = path.resolve()
        if path in seen:
            return
        seen.add(path)
        result.sheets.append(path)
        tree = sexpr.parse(path.read_text(encoding="utf-8"))
        for node in tree:
            kind = sexpr.head(node)
            if kind == "symbol" and sexpr.first(node, "lib_id") is not None:
                _add_symbol(node)
            elif kind == "sheet":
                name = sexpr.properties(node).get("Sheetfile") or sexpr.properties(node).get("Sheet file")
                if not name:
                    continue
                sub = path.parent / name
                if sub.is_file():
                    walk(sub)
                else:
                    log.warning("sub-sheet %s referenced from %s not found", name, path.name)

    def _add_symbol(node: sexpr.Node) -> None:
        lib_id = str(sexpr.first(node, "lib_id")[1])
        props = sexpr.properties(node)
        ref = props.get("Reference", "")
        if lib_id.startswith("power:") or ref.startswith("#"):
            return
        refs = _instance_refs(node) or {ref or "?"}
        lcsc = _find_lcsc(props, wanted)
        if not lcsc:
            result.without_lcsc.extend(sorted(refs))
            return
        part = result.parts.setdefault(lcsc, Part(lcsc))
        part.refs |= refs
        if props.get("Value"):
            part.values.add(props["Value"])
        if props.get("Footprint"):
            part.footprints.add(props["Footprint"])
        dnp = sexpr.first(node, "dnp")
        if not (dnp is not None and len(dnp) > 1 and str(dnp[1]) == "yes"):
            part.dnp_everywhere = False

    walk(Path(root))
    result.without_lcsc = sorted(set(result.without_lcsc))
    return result
