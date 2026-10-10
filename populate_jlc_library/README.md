# populate-jlc-library

Download the JLCPCB/EasyEDA footprint (and 3D model) of every LCSC part used in a KiCad
schematic into a **local footprint library** you can check in and use when designing the PCB.

JLCPCB assembles from the EasyEDA footprint of each LCSC part. Using that footprint in KiCad
means pads, origin and 3D model match what JLCPCB will place, so KiCad's position file needs no
per-part corrections (this is what causes "the module is offset in the placement preview").

```
schematic (.kicad_sch / .kicad_pro) -> LCSC numbers -> easyeda2kicad -> jlcpcb-lib/jlcpcb.pretty
```

## Install (in a virtual environment)

From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e populate_jlc_library      # also installs easyeda2kicad
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

## Develop

```sh
python -m unittest discover -s populate_jlc_library/tests -v
```
The tests use a stand-in for `easyeda2kicad` (`tests/stub_easyeda2kicad.py`), selected through the
`POPULATE_JLC_EASYEDA2KICAD` environment variable, so they need no network.
