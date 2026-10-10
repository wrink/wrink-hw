"""Stand-in for kicad-cli, used by the tests (no KiCad needed).

Implements only the sub-commands jlcpcb-fab calls, writing small files in KiCad's real formats.
Behaviour switches (environment variables):
  STUB_DRC_FAIL=1     `pcb drc` reports a violation and exits 5
  STUB_OLD_SILK=1     only the old layer names (F.SilkS/B.SilkS) are accepted, like KiCad < 10
  STUB_CLI_LOG=<file> every invocation is appended to this file
"""
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if os.environ.get("STUB_CLI_LOG"):
    with open(os.environ["STUB_CLI_LOG"], "a") as f:
        f.write(" ".join(args) + "\n")


def opt(name):
    return args[args.index(name) + 1]


pcb_or_sch = Path(args[-1])
stem = pcb_or_sch.stem
cmd = " ".join(args[:2]) if args[0] != "pcb" or args[1] != "export" else " ".join(args[:3])

if cmd == "pcb drc":
    report = Path(opt("-o"))
    if os.environ.get("STUB_DRC_FAIL"):
        report.write_text("** Found 1 DRC violations **\n[silk_overlap]: Silkscreen clearance\n")
        sys.exit(5)
    report.write_text("** Found 0 DRC violations **\n")
elif cmd == "pcb export gerbers":
    out = Path(opt("-o"))
    ext = {"F.Cu": "gtl", "B.Cu": "gbl", "F.Paste": "gtp", "B.Paste": "gbp", "F.Silkscreen": "gto",
           "B.Silkscreen": "gbo", "F.SilkS": "gto", "B.SilkS": "gbo", "F.Mask": "gts", "B.Mask": "gbs",
           "Edge.Cuts": "gm1"}
    old = bool(os.environ.get("STUB_OLD_SILK"))
    for layer in opt("--layers").split(","):
        if layer not in ext or ("Silkscreen" in layer) == old and "Silk" in layer:
            print(f"Unknown layer {layer}", file=sys.stderr)
            sys.exit(1)
    for layer in opt("--layers").split(","):
        (out / f"{stem}-{layer.replace('.', '_')}.{ext[layer]}").write_text("G04*\n")
    (out / f"{stem}-job.gbrjob").write_text("{}\n")
elif cmd == "pcb export drill":
    out = Path(opt("-o"))
    for kind in ("PTH", "NPTH"):
        (out / f"{stem}-{kind}.drl").write_text("M48\n")
        (out / f"{stem}-{kind}-drl_map.pdf").write_text("%PDF\n")
elif cmd == "pcb export pos":
    rows = [("C1", "4.7uF", "C0805", "115.0", "-58.0", "-90.0", "top"),
            ("U1", "mod", "E73", "122.0", "-68.5", "0.0", "top"),
            ("J1", "hdr", "PinHeader", "100.0", "-50.0", "0.0", "top"),
            ("R9", "10k", "R0603", "101.5", "-51.25", "450.0", "bottom")]
    if "--smd-only" in args:
        rows = [r for r in rows if r[0] != "J1"]
    Path(opt("-o")).write_text("Ref,Val,Package,PosX,PosY,Rot,Side\n" + "".join(
        '"%s","%s","%s",%s,%s,%s,%s\n' % r for r in rows))
elif cmd == "sch export":
    Path(opt("-o")).write_text(
        '"Comment","Designator","Footprint","LCSC Part #"\n'
        '"4.7uF","C1","jlcpcb:C0805","C20416422"\n'
        '"mod","U1","jlcpcb:E73","C356849"\n'
        '"hdr","J1","Conn:PinHeader","C41413841"\n'
        '"10k","R9,J1","jlcpcb:R0603",""\n')
else:
    print(f"stub: unsupported command {args}", file=sys.stderr)
    sys.exit(2)
