"""Run the downloads and keep a merged LCSC -> footprint map next to the library."""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .downloader import Downloader, Result
from .schematic import Part

MAP_NAME = "lcsc_footprints.csv"
LOG_NAME = "download.log"
HEADER = ["lcsc", "refs", "value", "status", "footprint_files"]
SHARED = "(existing or shared footprint)"


def _read_map(path: Path) -> Dict[str, Dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {r["lcsc"]: r for r in csv.DictReader(f)}


def write_map(out_dir: Path, rows: Iterable[Tuple[Part, Result]]) -> Path:
    """Merge ``rows`` into the map: earlier rows stay, and a known footprint name is not
    replaced by 'existing/shared' when a later run only found the footprint already there."""
    path = Path(out_dir) / MAP_NAME
    merged = _read_map(path)
    for part, res in rows:
        files = "|".join(res.new_files)
        status = "FAILED" if res.status == "failed" else "ok"
        prev = merged.get(part.lcsc)
        if status == "ok" and not files:
            files = prev["footprint_files"] if prev and prev["footprint_files"] and prev["status"] == "ok" else SHARED
        merged[part.lcsc] = {
            "lcsc": part.lcsc,
            "refs": ",".join(part.sorted_refs()),
            "value": "; ".join(sorted(part.values)),
            "status": status,
            "footprint_files": files if status == "ok" else "",
        }
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for key in sorted(merged, key=lambda k: int(k[1:])):
            w.writerow(merged[key])
    return path


def fix_attributes(pretty_dir: Path, names: Iterable[str]) -> None:
    """easyeda2kicad marks some all-SMD footprints `(attr through_hole)`; KiCad's position export
    (--smd-only) would then drop them from the placement file. Correct it when no pad is drilled."""
    for name in names:
        path = pretty_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "(attr through_hole)" in text and "(drill" not in text and " thru_hole " not in text:
            path.write_text(text.replace("(attr through_hole)", "(attr smd)", 1), encoding="utf-8")


def populate(parts: List[Part], downloader: Downloader, *, progress: Optional[Callable[[str], None]] = print,
             delay: float = 0.5) -> List[Tuple[Part, Result]]:
    """Download every part (one at a time), log the tool output, update the map."""
    out_dir = downloader.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    results: List[Tuple[Part, Result]] = []
    with (out_dir / LOG_NAME).open("a", encoding="utf-8") as log:
        for i, part in enumerate(parts):
            res = downloader.fetch(part.lcsc)
            fix_attributes(downloader.pretty_dir, res.new_files)
            log.write(f"---- {part.lcsc} ({','.join(part.sorted_refs())}) -> {res.status}\n{res.output}\n")
            results.append((part, res))
            if progress:
                names = "|".join(res.new_files) or ("already in library" if res.status == "exists" else "")
                progress(f"  {part.lcsc:<10} {res.status.upper():<7} {names}")
            if delay and i + 1 < len(parts):
                time.sleep(delay)                    # be polite to the service
    write_map(out_dir, results)
    return results
