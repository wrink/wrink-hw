# JLCPCB tools for KiCad

Two commands, installed together:

* [`populate-jlc-library`](#populate-jlc-library): download JLCPCB's own footprints for the parts in a schematic.
* [`jlcpcb-fab`](#jlcpcb-fab): generate gerbers, drill files, BOM and placement file for a JLCPCB order.

They work on any KiCad project; nothing is specific to one board. Typical flow: populate the library,
use its footprints on the PCB, then run `jlcpcb-fab` when the board is done.

## Install (in a virtual environment)

From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e populate_jlc_library      # also installs easyeda2kicad
```

With uv, `uv run --with-editable ./populate_jlc_library jlcpcb-fab` runs the current source
without a rebuild (plain `--with ./populate_jlc_library` caches a built copy and can run stale code).

# populate-jlc-library

Download the JLCPCB/EasyEDA footprint (and 3D model) of every LCSC part used in a KiCad
schematic into a **local footprint library** you can check in and use when designing the PCB.

JLCPCB assembles from the EasyEDA footprint of each LCSC part. Using that footprint in KiCad
means pads, origin and 3D model match what JLCPCB will place, so KiCad's position file needs no
per-part corrections (this is what causes "the module is offset in the placement preview").

```
schematic (.kicad_sch / .kicad_pro) -> LCSC numbers -> easyeda2kicad -> jlcpcb-lib/jlcpcb.pretty
```

## Use

Give it a project file, a schematic, or a directory containing one project:

```sh
populate-jlc-library rpi-nrf52840-hat/rpi-nrf52840-hat.kicad_pro --dry-run   # list the parts, download nothing
populate-jlc-library rpi-nrf52840-hat/rpi-nrf52840-hat.kicad_pro --only C356849   # try one part first
populate-jlc-library rpi-nrf52840-hat/rpi-nrf52840-hat.kicad_pro --register       # all parts + fp-lib-table entry
```

A `.kicad_sch` works the same way (hierarchical sheets are followed); with a bare schematic the
library goes next to it. The default output is `<project dir>/jlcpcb-lib/`:

| File | Content |
|---|---|
| `jlcpcb.pretty/` | footprints, one `.kicad_mod` per EasyEDA footprint (shared by parts with the same package) |
| `jlcpcb.3dshapes/` | 3D models (`.step`, `.wrl`); paths inside the footprints are `${KIPRJMOD}/...` |
| `lcsc_footprints.csv` | LCSC number -> references, value, footprint file(s); merged across runs |
| `download.log` | full output of every download |

Options: `--out DIR`, `--lib-name NAME`, `--field NAME` (extra symbol field holding the LCSC
number; defaults: `LCSC`, `LCSC Part #`, `LCSC Part`, `LCSC#`, `JLCPCB Part #`, `JLCPCB`, `JLC`),
`--only C1,C2`, `--skip-dnp`, `--full` (also symbols), `--overwrite`, `--register`, `--dry-run`,
`--delay`, `--timeout`. Exit code: 0 ok, 1 some downloads failed, 2 bad input/environment.

`--register` adds the library to the project's `fp-lib-table`; restart KiCad afterwards. Then set
each symbol's *Footprint* field to `jlcpcb:<name>` (names are in `lcsc_footprints.csv`) and run
*Update PCB from Schematic*. The tool never edits schematics or boards.

Notes:
* The library folder must be inside the project directory for project-relative 3D paths; otherwise
  absolute paths are written.
* Power symbols and `#` references are ignored. Parts without an LCSC number are reported.
* A footprint that already exists is left alone unless `--overwrite` is given. When several LCSC
  numbers map to the same EasyEDA footprint (all 0603 resistors, say) only the first creates a file.
* Network access to `easyeda.com` is needed; the tool is a thin wrapper around
  [easyeda2kicad](https://github.com/uPesy/easyeda2kicad).

# jlcpcb-fab

Everything needed to order a board: run it when the PCB is finished.

```sh
jlcpcb-fab                                         # every project in the git repository you are in
jlcpcb-fab rpi-nrf52840-hat                        # a project directory, .kicad_pro or .kicad_sch
jlcpcb-fab board/board.kicad_pro -o /tmp/order     # other output directory (single project only)
jlcpcb-fab --skip-drc                              # do not run DRC
```

Output goes to `<project dir>/gerber/` (git-ignore it, or `git add -f` it to record an order):

| File | Content |
|---|---|
| `<name>-<layer>.g*` | gerbers: copper, mask, paste, silkscreen, edge cuts |
| `<name>-PTH.drl`, `-NPTH.drl` | drill files (plated and non-plated separate), plus PDF maps |
| `<name>_gerbers.zip` | gerbers and drill files: upload as "PCB" |
| `<name>_bom_jlcpcb.csv` | BOM with JLCPCB's column names |
| `<name>_cpl_jlcpcb.csv` | placement file with JLCPCB's column names |

What it does, in order: refuses if KiCad has the board open (`--force` overrides); runs DRC
(refills zones, **saves the board**, checks schematic parity; any error or warning stops the run,
violations you excluded in KiCad are respected); plots gerbers and drill files; zips them; builds
BOM and CPL. All of it happens in a temporary directory and the files of this board in the output
directory are only replaced when every step worked, so a failed run leaves the previous order files
alone. Other files in the directory are never touched.

* **Through-hole parts** (found as "in the position export but not SMD") are left out of the BOM and
  CPL: it assumes you order and solder them yourself. They are listed in the output.
* **No placement corrections.** KiCad's position and rotation are written as they are, which matches
  JLCPCB when the footprints are JLCPCB's own (use `populate-jlc-library` for that). If a part is
  rotated or offset in JLCPCB's placement preview, the footprint differs from JLCPCB's.
* BOM lines without an LCSC number are reported as warnings.
* Needs `kicad-cli` (KiCad 7 or later; developed against 10.0) in `PATH`. Exit code: 0 ok, 1 a step
  failed, 2 bad input.

## Develop

```sh
python -m unittest discover -s populate_jlc_library/tests -v
```
The tests use stand-ins for `easyeda2kicad` (`tests/stub_easyeda2kicad.py`, selected through
`POPULATE_JLC_EASYEDA2KICAD`) and for `kicad-cli` (`tests/stub_kicad_cli.py`, selected through
`POPULATE_JLC_KICAD_CLI`), so they need neither network nor KiCad.
