#!/usr/bin/env bash
# Regenerate everything needed to order from JLCPCB into ./gerber
#
#   gerber/<name>-*.g*, *.drl      gerbers + drill files (PTH/NPTH separate)
#   gerber/<name>_gerbers.zip      the zip to upload as "PCB"
#   gerber/<name>_bom_jlcpcb.csv   BOM in JLCPCB column names (SMD parts only)
#   gerber/<name>_cpl_jlcpcb.csv   component placement in JLCPCB column names
#
# Usage: ./generate_gerber.sh [--skip-drc] [--force]
#   --skip-drc   do not run DRC (default: refill zones, save, DRC+parity, abort on any violation)
#   --force      run even if KiCad seems to have the board open (lock file present)
#
# Requires: kicad-cli (10.x), zip, python3. Run with the KiCad PCB editor CLOSED:
# the DRC step refills zones and rewrites the .kicad_pcb file.
set -euo pipefail

NAME=rpi-nrf52840-hat
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCB="$HERE/$NAME.kicad_pcb"
SCH="$HERE/$NAME.kicad_sch"
OUT="$HERE/gerber"

# Placement file: no per-part rotation/position corrections. Every assembled part uses JLCPCB's
# own (EasyEDA) footprint from jlcpcb-lib, or a custom footprint (D1) whose pads coincide with
# JLCPCB's, so KiCad's origins and rotations already equal JLCPCB's. Keep it that way: footprints
# from populate-jlc-library, not hand-adjusted ones.

usage() { sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

SKIP_DRC=0; FORCE=0
for a in "$@"; do
  case "$a" in
    --skip-drc) SKIP_DRC=1 ;;
    --force)    FORCE=1 ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "unknown argument: $a" >&2; usage >&2; exit 2 ;;
  esac
done

for t in kicad-cli zip python3; do
  command -v "$t" >/dev/null 2>&1 || { echo "error: '$t' not found in PATH" >&2; exit 1; }
done
[ -f "$PCB" ] || { echo "error: $PCB not found" >&2; exit 1; }
[ -f "$SCH" ] || { echo "error: $SCH not found" >&2; exit 1; }

if [ -e "$HERE/~$NAME.kicad_pcb.lck" ] && [ "$FORCE" -eq 0 ]; then
  echo "error: KiCad seems to have the board open (found ~$NAME.kicad_pcb.lck)." >&2
  echo "       Save and close the PCB editor first, or re-run with --force." >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------- 1. DRC gate
if [ "$SKIP_DRC" -eq 0 ]; then
  echo "==> DRC: refill zones, save, DRC + schematic parity (any violation aborts)"
  if ! kicad-cli pcb drc --refill-zones --save-board --schematic-parity \
        --severity-error --severity-warning --exit-code-violations \
        -o "$TMP/drc.txt" "$PCB" >/dev/null; then       # exclusions you set in KiCad are respected
    echo "error: DRC reported violations (or failed to run):" >&2
    sed -n '1,60p' "$TMP/drc.txt" >&2 || true
    exit 1
  fi
else
  echo "==> DRC skipped (--skip-drc)"
fi

# ---------------------------------------------------------------- 2. clean output
[ "$(basename "$OUT")" = "gerber" ] || { echo "refusing to clean unexpected dir: $OUT" >&2; exit 1; }
mkdir -p "$OUT"
find "$OUT" -mindepth 1 -delete
echo "==> Cleaned $OUT"

# ---------------------------------------------------------------- 3. gerbers
plot() {
  kicad-cli pcb export gerbers -o "$OUT/" --check-zones --subtract-soldermask \
    --layers "$1" "$PCB" >/dev/null
}
LAYERS_A="F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"
LAYERS_B="F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts"
echo "==> Gerbers"
shopt -s nullglob
plot "$LAYERS_A" || true      # a bad layer name makes this fail; the retry below handles it
silk=( "$OUT"/*.gto )
if [ "${#silk[@]}" -eq 0 ]; then
  echo "    silkscreen missing with layer names F.SilkS/B.SilkS, retrying with F.Silkscreen/B.Silkscreen"
  find "$OUT" -mindepth 1 -delete
  plot "$LAYERS_B"
fi
for ext in gtl gbl gts gbs gto gbo gm1; do
  f=( "$OUT"/*."$ext" )
  [ "${#f[@]}" -ge 1 ] || { echo "error: expected a *.$ext file but none was generated" >&2; exit 1; }
done

# ---------------------------------------------------------------- 4. drill
echo "==> Drill files"
kicad-cli pcb export drill -o "$OUT/" --format excellon --excellon-units mm \
  --excellon-zeros-format decimal --excellon-oval-format route \
  --excellon-separate-th --drill-origin absolute \
  --generate-map --map-format pdf "$PCB" >/dev/null
drl=( "$OUT"/*.drl )
[ "${#drl[@]}" -ge 1 ] || { echo "error: no drill files generated" >&2; exit 1; }

# ---------------------------------------------------------------- 5. zip for upload
echo "==> Zip"
ZIP="$OUT/${NAME}_gerbers.zip"
files=()
for f in "$OUT"/*; do
  case "${f##*.}" in
    gtl|gbl|gts|gbs|gto|gbo|gtp|gbp|gm1|gbrjob|drl) files+=( "$(basename "$f")" ) ;;
  esac
done
( cd "$OUT" && zip -q "$ZIP" "${files[@]}" )

# ---------------------------------------------------------------- 6. BOM + CPL
echo "==> BOM / placement"
kicad-cli sch export bom --exclude-dnp \
  --fields 'Value,Reference,Footprint,LCSC' \
  --labels 'Comment,Designator,Footprint,LCSC Part #' \
  --group-by 'Value,Footprint,LCSC' \
  --ref-delimiter ',' --ref-range-delimiter '' \
  -o "$TMP/bom_raw.csv" "$SCH" >/dev/null
kicad-cli pcb export pos --format csv --units mm --side both --exclude-dnp \
  -o "$TMP/pos_all.csv" "$PCB" >/dev/null
kicad-cli pcb export pos --format csv --units mm --side both --smd-only --exclude-dnp \
  -o "$TMP/pos_smd.csv" "$PCB" >/dev/null

TMP="$TMP" OUT="$OUT" NAME="$NAME" python3 - <<'PY'
import csv, os, sys
tmp, out, name = os.environ["TMP"], os.environ["OUT"], os.environ["NAME"]

def rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))

pos_all, pos_smd = rows(f"{tmp}/pos_all.csv"), rows(f"{tmp}/pos_smd.csv")
refs_all = {r["Ref"] for r in pos_all}
refs_smd = {r["Ref"] for r in pos_smd}
tht = refs_all - refs_smd                     # through-hole parts: hand-solder, not in the order

# --- BOM: drop through-hole designators, keep JLCPCB column names
bom = rows(f"{tmp}/bom_raw.csv")
kept, missing = [], []
for r in bom:
    des = [d for d in r["Designator"].split(",") if d and d not in tht]
    if not des:
        continue
    r["Designator"] = ",".join(des)
    if not r["LCSC Part #"].strip():
        missing.append(r["Designator"])
    kept.append(r)
with open(f"{out}/{name}_bom_jlcpcb.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["Comment", "Designator", "Footprint", "LCSC Part #"],
                       quoting=csv.QUOTE_ALL, extrasaction="ignore")
    w.writeheader(); w.writerows(kept)

# --- CPL: JLCPCB column names, KiCad's position and rotation unchanged
with open(f"{out}/{name}_cpl_jlcpcb.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
    for r in pos_smd:
        layer = "Top" if r["Side"].strip().lower() == "top" else "Bottom"
        w.writerow([r["Ref"], f"{float(r['PosX']):.4f}mm", f"{float(r['PosY']):.4f}mm", layer,
                    f"{float(r['Rot']) % 360:g}"])

print(f"    BOM rows: {len(kept)}   CPL parts: {len(pos_smd)}   through-hole (hand-solder, left out): {sorted(tht) or 'none'}")
if missing:
    print("    WARNING: BOM rows without an LCSC part number:", "; ".join(missing), file=sys.stderr)
print("    NOTE: verify polarized/rotated parts (modules, ICs, LEDs) in JLCPCB's placement preview.")
PY

echo "==> Done. Files in $OUT:"
ls -1 "$OUT"
