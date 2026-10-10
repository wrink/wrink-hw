"""Stand-in for the easyeda2kicad CLI, used by the tests (no network).

Behaves like the strict real tool as far as the tests need:
  * writes <output>.pretty/<name>.kicad_mod and <output>.3dshapes/<name>.step
  * C23138 and C23162 share the footprint name "R0603"; a second request exits 1 with
    "already exists" unless --overwrite is given
  * C9999999 fails like a network error
"""
import os
import sys
from pathlib import Path

args = sys.argv[1:]
lcsc = next(a.split("=", 1)[1] for a in args if a.startswith("--lcsc_id="))
out = Path(args[args.index("--output") + 1])
Path(f"{out}.last_call").write_text(os.getcwd() + "\n" + " ".join(args) + "\n")
if lcsc == "C9999999":
    print("ERROR: simulated network failure", file=sys.stderr)
    sys.exit(1)
name = "R0603" if lcsc in ("C23138", "C23162") else f"FP_{lcsc}"
pretty = Path(f"{out}.pretty")
pretty.mkdir(parents=True, exist_ok=True)
Path(f"{out}.3dshapes").mkdir(parents=True, exist_ok=True)
fp = pretty / f"{name}.kicad_mod"
if fp.exists() and "--overwrite" not in args:
    print(f"[ERROR] footprint {name} already exists, use --overwrite", file=sys.stderr)
    sys.exit(1)
fp.write_text(f'(footprint "{name}")\n')
if "--full" in args:
    Path(f"{out}.kicad_sym").write_text("(kicad_symbol_lib)\n")
print(f"created {name}")
