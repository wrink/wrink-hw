import contextlib
import csv
import io
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from populate_jlc_library.fab import (ENV_KICAD_CLI, FabError, Job, convert_bom, convert_cpl, find_projects,
                                      generate)
from populate_jlc_library.fab_cli import main
from populate_jlc_library.project import resolve

STUB = Path(__file__).with_name("stub_kicad_cli.py")


class StubEnv(unittest.TestCase):
    """Runs every test with kicad-cli replaced by the stand-in."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (ENV_KICAD_CLI, "STUB_DRC_FAIL", "STUB_OLD_SILK", "STUB_CLI_LOG")}
        for k in ("STUB_DRC_FAIL", "STUB_OLD_SILK", "STUB_CLI_LOG"):
            os.environ.pop(k, None)
        os.environ[ENV_KICAD_CLI] = f"{shlex.quote(sys.executable)} {shlex.quote(str(STUB))}"
        self.root = Path(tempfile.mkdtemp()).resolve()

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def board(self, name="b", where=None):
        d = Path(where or self.root / name)
        d.mkdir(parents=True, exist_ok=True)
        for ext in ("kicad_pro", "kicad_sch", "kicad_pcb"):
            (d / f"{name}.{ext}").write_text("(x)\n")
        return d

    def job(self, name="b", **kw):
        d = self.board(name)
        return Job.from_target(resolve(d), **kw)


class ConvertTests(unittest.TestCase):
    def test_bom_drops_through_hole_and_reports_missing_lcsc(self):
        rows = [{"Comment": "a", "Designator": "C1,J1", "Footprint": "f", "LCSC Part #": "C1"},
                {"Comment": "b", "Designator": "J2", "Footprint": "f", "LCSC Part #": "C2"},
                {"Comment": "c", "Designator": "R1", "Footprint": "f", "LCSC Part #": " "}]
        kept, missing = convert_bom(rows, ["J1", "J2"])
        self.assertEqual([r["Designator"] for r in kept], ["C1", "R1"])
        self.assertEqual(missing, ["R1"])
        self.assertEqual(rows[0]["Designator"], "C1,J1")                       # input untouched

    def test_cpl_format_side_and_rotation(self):
        rows = [{"Ref": "C1", "PosX": "1.5", "PosY": "-2", "Rot": "-90.0", "Side": "top"},
                {"Ref": "R1", "PosX": "0", "PosY": "0", "Rot": "450", "Side": "bottom"}]
        self.assertEqual(convert_cpl(rows), [["C1", "1.5000mm", "-2.0000mm", "Top", "270"],
                                             ["R1", "0.0000mm", "0.0000mm", "Bottom", "90"]])


class GenerateTests(StubEnv):
    def test_full_run(self):
        job = self.job()
        steps = []
        s = generate(job, report=lambda a, b: steps.append(a))
        self.assertEqual(steps, ["DRC", "gerbers", "drill", "zip", "BOM", "placement"])
        out = job.out_dir
        self.assertEqual(out, job.project_dir / "gerber")
        for f in ("b-F_Cu.gtl", "b-B_Silkscreen.gbo", "b-Edge_Cuts.gm1", "b-job.gbrjob", "b-PTH.drl",
                  "b-NPTH.drl", "b-PTH-drl_map.pdf", "b_gerbers.zip", "b_bom_jlcpcb.csv", "b_cpl_jlcpcb.csv"):
            self.assertTrue((out / f).is_file(), f)
        self.assertFalse(list(out.glob("bom_raw*")) + list(out.glob("pos_*")))
        with zipfile.ZipFile(out / "b_gerbers.zip") as z:
            names = set(z.namelist())
        self.assertIn("b-F_Cu.gtl", names)
        self.assertIn("b-PTH.drl", names)
        self.assertIn("b-job.gbrjob", names)
        self.assertFalse([n for n in names if n.endswith(".pdf") or n.endswith(".csv")])
        self.assertEqual(s.left_out, ["J1"])
        self.assertEqual(s.placed, 3)
        self.assertEqual((s.bom_lines, s.bom_parts), (3, 3))
        self.assertTrue(any("R9" in w for w in s.warnings))
        with (out / "b_bom_jlcpcb.csv").open(newline="") as f:
            bom = list(csv.DictReader(f))
        self.assertEqual([r["Designator"] for r in bom], ["C1", "U1", "R9"])    # J1 gone, also from "R9,J1"
        cpl = (out / "b_cpl_jlcpcb.csv").read_text().splitlines()
        self.assertEqual(cpl[0], "Designator,Mid X,Mid Y,Layer,Rotation")
        self.assertIn("C1,115.0000mm,-58.0000mm,Top,270", cpl)
        self.assertIn("R9,101.5000mm,-51.2500mm,Bottom,90", cpl)
        self.assertFalse(any(line.startswith("J1") for line in cpl))

    def test_old_silkscreen_layer_names_are_a_fallback(self):
        os.environ["STUB_OLD_SILK"] = "1"
        job = self.job()
        generate(job)
        self.assertTrue((job.out_dir / "b-F_SilkS.gto").is_file())
        self.assertFalse([p for p in job.out_dir.iterdir() if p.is_dir()])

    def test_only_this_boards_previous_results_are_replaced(self):
        job = self.job()
        job.out_dir.mkdir()
        (job.out_dir / "b-stale.gtl").write_text("old")
        (job.out_dir / "other-F_Cu.gtl").write_text("other board")
        (job.out_dir / "notes.txt").write_text("mine")
        generate(job)
        self.assertFalse((job.out_dir / "b-stale.gtl").exists())
        self.assertEqual((job.out_dir / "other-F_Cu.gtl").read_text(), "other board")
        self.assertEqual((job.out_dir / "notes.txt").read_text(), "mine")

    def test_drc_failure_stops_before_touching_outputs(self):
        os.environ["STUB_DRC_FAIL"] = "1"
        job = self.job()
        job.out_dir.mkdir()
        (job.out_dir / "b-F_Cu.gtl").write_text("keep me")
        with self.assertRaises(FabError) as cm:
            generate(job)
        self.assertIn("silk_overlap", str(cm.exception))
        self.assertEqual((job.out_dir / "b-F_Cu.gtl").read_text(), "keep me")
        generate(job, skip_drc=True)                                           # --skip-drc works
        self.assertNotEqual((job.out_dir / "b-F_Cu.gtl").read_text(), "keep me")

    def test_failed_step_leaves_old_outputs(self):
        job = self.job()
        generate(job)
        before = sorted(p.name for p in job.out_dir.iterdir())
        os.environ[ENV_KICAD_CLI] = f"{shlex.quote(sys.executable)} -c 'import sys; sys.exit(3)'"
        with self.assertRaises(FabError):
            generate(job, skip_drc=True, force=True)
        self.assertEqual(sorted(p.name for p in job.out_dir.iterdir()), before)

    def test_lock_file_refuses_unless_forced(self):
        job = self.job()
        lock = job.pcb.with_name(f"~{job.pcb.name}.lck")
        lock.write_text("")
        with self.assertRaises(FabError) as cm:
            generate(job)
        self.assertIn("--force", str(cm.exception))
        generate(job, force=True)

    def test_custom_output_directory(self):
        out = self.root / "elsewhere"
        job = self.job(out=out)
        generate(job)
        self.assertTrue((out / "b_gerbers.zip").is_file())


class DiscoveryTests(StubEnv):
    def test_find_projects_skips_backups_and_hidden(self):
        (self.root / ".git").mkdir()
        self.board("one", self.root / "one")
        self.board("two", self.root / "boards" / "two")
        self.board("old", self.root / "one-backups" / "old")
        self.board("hid", self.root / ".history" / "hid")
        found = find_projects(self.root / "one")                               # found from inside the repo
        self.assertEqual([p.name for p in found], ["two.kicad_pro", "one.kicad_pro"])  # walk order


class CliTests(StubEnv):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main([str(a) for a in argv])
        return rc, out.getvalue(), err.getvalue()

    def test_single_project_output(self):
        d = self.board()
        rc, out, err = self.run_cli(d / "b.kicad_pro")
        self.assertEqual(rc, 0)
        for text in ("DRC", "gerbers", "zip", "left out", "J1", str(d / "gerber")):
            self.assertIn(text, out)
        self.assertIn("warning", err)                                           # R9 has no LCSC number

    def test_all_projects_in_repo_and_failures_dont_stop_the_rest(self):
        (self.root / ".git").mkdir()
        self.board("a", self.root / "a")
        self.board("b", self.root / "b")
        (self.root / "a" / "~a.kicad_pcb.lck").write_text("")
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            rc, out, err = self.run_cli("--skip-drc")
        finally:
            os.chdir(cwd)
        self.assertEqual(rc, 1)
        self.assertIn("seems to have a.kicad_pcb open", err)
        self.assertTrue((self.root / "b" / "gerber" / "b_gerbers.zip").is_file())

    def test_bad_input(self):
        rc, _, err = self.run_cli(self.root / "missing.kicad_pro")
        self.assertEqual(rc, 2)
        self.assertIn("does not exist", err)

    def test_out_needs_single_project(self):
        a, b = self.board("a"), self.board("b")
        rc, _, err = self.run_cli(a, b, "-o", self.root / "x")
        self.assertEqual(rc, 2)

    def test_missing_kicad_cli(self):
        os.environ.pop(ENV_KICAD_CLI)
        old = os.environ["PATH"]
        os.environ["PATH"] = ""
        try:
            rc, _, err = self.run_cli(self.board(), "--skip-drc")
        finally:
            os.environ["PATH"] = old
        self.assertEqual(rc, 1)
        self.assertIn("kicad-cli not found", err)


if __name__ == "__main__":
    unittest.main()
