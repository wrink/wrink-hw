import contextlib
import csv
import io
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

from populate_jlc_library import sexpr
from populate_jlc_library.cli import main
from populate_jlc_library.downloader import ENV_COMMAND, Downloader
from populate_jlc_library.library import MAP_NAME, populate
from populate_jlc_library.project import InputError, register_library, resolve
from populate_jlc_library.schematic import collect_parts

STUB = Path(__file__).with_name("stub_easyeda2kicad.py")


def symbol(ref, lcsc, value="v", footprint="Lib:FP", dnp="no", field="LCSC", extra_refs=()):
    refs = "".join(f'(path "/{r}" (reference "{r}") (unit 1))' for r in (ref, *extra_refs))
    lcsc_prop = f'(property "{field}" "{lcsc}")' if lcsc else ""
    return (f'(symbol (lib_id "Device:X") (at 0 0 0) (dnp {dnp}) (property "Reference" "{ref}") '
            f'(property "Value" "{value}") (property "Footprint" "{footprint}") {lcsc_prop} '
            f'(instances (project "p" {refs})))')


def sheet_node(filename):
    return f'(sheet (at 0 0) (property "Sheetname" "S") (property "Sheetfile" "{filename}"))'


def schematic(*nodes):
    # lib_symbols definitions carry an LCSC property too and must never be counted
    libs = '(lib_symbols (symbol "Device:X" (property "Reference" "X") (property "LCSC" "C999999")))'
    return "(kicad_sch (version 20250114) " + libs + " " + " ".join(nodes) + ")"


class SexprTests(unittest.TestCase):
    def test_parse_nested_and_escapes(self):
        tree = sexpr.parse('(a (b "x \\"q\\" y") c 1.5)')
        self.assertEqual(sexpr.head(tree), "a")
        self.assertEqual(sexpr.first(tree, "b")[1], 'x "q" y')
        self.assertIsInstance(tree[2], sexpr.Sym)

    def test_properties(self):
        tree = sexpr.parse('(symbol (property "A" "1") (property "B" "2" (at 0 0 0)))')
        self.assertEqual(sexpr.properties(tree), {"A": "1", "B": "2"})

    def test_unbalanced(self):
        for bad in ("(a (b)", "(a))", ""):
            with self.assertRaises(ValueError):
                sexpr.parse(bad)


class SchematicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def write(self, name, text):
        p = self.tmp / name
        p.write_text(text)
        return p

    def test_collects_merges_and_ignores(self):
        root = self.write("root.kicad_sch", schematic(
            symbol("R1", "C23138", "330"),
            symbol("R2", "C23138", "330"),
            symbol("R3", "c25804", "4.7k", dnp="yes", field="LCSC Part #"),     # case-insensitive field/value
            symbol("#PWR01", "C1234567"),                                       # power: ignored
            symbol("U1", ""),                                                   # no LCSC: reported
            sheet_node("sub.kicad_sch"),
        ))
        self.write("sub.kicad_sch", schematic(
            symbol("C1", "C1647", "18pF", extra_refs=("C11",)),                  # repeated sheet instance
            symbol("R9", "C23138", "330"),
            sheet_node("root.kicad_sch"),                                       # cycle must not loop
        ))
        got = collect_parts(root)
        self.assertEqual(sorted(got.parts), ["C1647", "C23138", "C25804"])
        self.assertEqual(got.parts["C23138"].sorted_refs(), ["R1", "R2", "R9"])
        self.assertEqual(got.parts["C1647"].sorted_refs(), ["C1", "C11"])
        self.assertTrue(got.parts["C25804"].dnp_everywhere)
        self.assertFalse(got.parts["C23138"].dnp_everywhere)
        self.assertEqual(got.without_lcsc, ["U1"])
        self.assertEqual(len(got.sheets), 2)
        self.assertNotIn("C999999", got.parts)

    def test_custom_field(self):
        root = self.write("root.kicad_sch", schematic(symbol("R1", "C42", field="Supplier")))
        self.assertEqual(collect_parts(root).parts, {})
        self.assertIn("C42", collect_parts(root, ["Supplier"]).parts)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "b.kicad_pro").write_text("{}")
        (self.d / "b.kicad_sch").write_text(schematic())

    def test_resolve_variants(self):
        for given in (self.d / "b.kicad_pro", self.d / "b.kicad_sch", self.d):
            t = resolve(given)
            self.assertEqual(t.root_schematic.name, "b.kicad_sch")
            self.assertEqual(t.project_dir, self.d)
            self.assertEqual(t.project_file.name, "b.kicad_pro")

    def test_resolve_errors(self):
        with self.assertRaises(InputError):
            resolve(self.d / "nope.kicad_pro")
        with self.assertRaises(InputError):
            resolve(self.d / "readme.txt" if (self.d / "readme.txt").write_text("x") else None)
        (self.d / "c.kicad_pro").write_text("{}")
        with self.assertRaises(InputError):
            resolve(self.d)                                                     # two projects

    def test_register_library(self):
        pretty = self.d / "lib" / "jlcpcb.pretty"
        pretty.mkdir(parents=True)
        self.assertEqual(register_library(self.d, "jlcpcb", pretty), "created")
        self.assertEqual(register_library(self.d, "jlcpcb", pretty), "present")
        table = (self.d / "fp-lib-table").read_text()
        self.assertEqual(table.count('(name "jlcpcb")'), 1)
        self.assertIn('${KIPRJMOD}/lib/jlcpcb.pretty', table)
        (self.d / "fp-lib-table").write_text('(fp_lib_table\n\t(version 7)\n\t(lib (name "other")(type "KiCad")(uri "x")(options "")(descr ""))\n)\n')
        self.assertEqual(register_library(self.d, "jlcpcb", pretty), "added")
        text = (self.d / "fp-lib-table").read_text()
        self.assertTrue(text.rstrip().endswith(")"))
        self.assertIn('\t(lib (name "jlcpcb")', text)                          # tab-indented like KiCad


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp()) / "jlcpcb-lib"
        self._old = os.environ.get(ENV_COMMAND)
        os.environ[ENV_COMMAND] = f"{shlex.quote(sys.executable)} {shlex.quote(str(STUB))}"

    def tearDown(self):
        if self._old is None:
            os.environ.pop(ENV_COMMAND, None)
        else:
            os.environ[ENV_COMMAND] = self._old

    def parts(self, *lcsc):
        from populate_jlc_library.schematic import Part
        return [Part(c, refs={f"X{i}"}, values={"v"}) for i, c in enumerate(lcsc)]

    def read_map(self):
        with (self.out / MAP_NAME).open(newline="") as f:
            return {r["lcsc"]: r for r in csv.DictReader(f)}

    def test_download_shared_failure_and_map_merge(self):
        dl = Downloader(self.out)
        res = populate(self.parts("C356849", "C23138", "C23162", "C9999999"), dl, progress=None, delay=0)
        status = {p.lcsc: r.status for p, r in res}
        self.assertEqual(status, {"C356849": "ok", "C23138": "ok", "C23162": "exists", "C9999999": "failed"})
        self.assertTrue((self.out / "jlcpcb.pretty" / "FP_C356849.kicad_mod").is_file())
        m = self.read_map()
        self.assertEqual(m["C356849"]["footprint_files"], "FP_C356849.kicad_mod")
        self.assertEqual(m["C23138"]["footprint_files"], "R0603.kicad_mod")
        self.assertIn("shared", m["C23162"]["footprint_files"])
        self.assertEqual(m["C9999999"]["status"], "FAILED")
        # a partial second run keeps all other rows and keeps the known footprint name
        populate(self.parts("C23138"), dl, progress=None, delay=0)
        m2 = self.read_map()
        self.assertEqual(sorted(m2), sorted(m))
        self.assertEqual(m2["C23138"]["footprint_files"], "R0603.kicad_mod")
        # --overwrite regenerates instead of reporting 'exists'
        res3 = populate(self.parts("C23138"), Downloader(self.out, overwrite=True), progress=None, delay=0)
        self.assertEqual(res3[0][1].status, "ok")

    def test_project_relative_runs_in_project_dir(self):
        proj = self.out.parent
        dl = Downloader(self.out, project_dir=proj)
        populate(self.parts("C356849"), dl, progress=None, delay=0)
        cwd, call = (self.out / "jlcpcb.last_call").read_text().splitlines()[:2]
        self.assertEqual(Path(cwd).resolve(), proj.resolve())
        self.assertIn("--project-relative", call)
        # library outside the project: no --project-relative (the tool would crash on relative_to(cwd))
        other = Path(tempfile.mkdtemp()) / "lib"
        populate(self.parts("C356849"), Downloader(other, project_dir=proj), progress=None, delay=0)
        self.assertNotIn("--project-relative", (other / "jlcpcb.last_call").read_text())

    def test_full_creates_symbols(self):
        populate(self.parts("C356849"), Downloader(self.out, full=True), progress=None, delay=0)
        self.assertTrue((self.out / "jlcpcb.kicad_sym").is_file())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "b.kicad_pro").write_text("{}")
        (self.d / "b.kicad_sch").write_text(schematic(
            symbol("R1", "C23138", "330"), symbol("U1", "C356849", "mod"),
            symbol("R2", "C9999999", "bad"), symbol("J1", "")))
        self._old = os.environ.get(ENV_COMMAND)
        os.environ[ENV_COMMAND] = f"{shlex.quote(sys.executable)} {shlex.quote(str(STUB))}"

    def tearDown(self):
        if self._old is None:
            os.environ.pop(ENV_COMMAND, None)
        else:
            os.environ[ENV_COMMAND] = self._old

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main([str(a) for a in argv])
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_writes_nothing(self):
        rc, out, _ = self.run_cli(self.d / "b.kicad_pro", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn("3 distinct LCSC part(s)", out)
        self.assertIn("no LCSC number: J1", out)
        self.assertFalse((self.d / "jlcpcb-lib").exists())

    def test_populate_register_and_exit_code(self):
        rc, out, err = self.run_cli(self.d / "b.kicad_sch", "--register", "--delay", "0")
        self.assertEqual(rc, 1)                                                 # C9999999 failed
        self.assertIn("C9999999", err)
        self.assertTrue((self.d / "jlcpcb-lib" / "jlcpcb.pretty" / "FP_C356849.kicad_mod").is_file())
        self.assertIn("jlcpcb created", out)
        rc, _, _ = self.run_cli(self.d, "--only", "C356849,C23138", "--delay", "0", "--register")
        self.assertEqual(rc, 0)
        self.assertEqual((self.d / "fp-lib-table").read_text().count('(name "jlcpcb")'), 1)

    def test_bad_lib_name(self):
        rc, _, err = self.run_cli(self.d / "b.kicad_pro", "--lib-name", "my.lib", "--dry-run")
        self.assertEqual(rc, 2)

    def test_bad_input(self):
        rc, _, err = self.run_cli(self.d / "missing.kicad_pro")
        self.assertEqual(rc, 2)
        self.assertIn("does not exist", err)


if __name__ == "__main__":
    unittest.main()
