import os
from unittest import mock

from rec2notes import doctor, paths

from .helpers import MODEL, Sandbox
from .test_menu import Hub


class Doctor(Sandbox):
    def setUp(self):
        super().setUp()
        built = paths.whisper_cli_built()
        built.parent.mkdir(parents=True)
        built.touch(mode=0o755)

    def test_all_in_place(self):
        code, out, _ = self.rec2notes("doctor")
        self.assertEqual(code, 0)
        self.assertNotIn("✗", out)
        self.assertNotIn("again", out)
        self.assertIn("logged in", out)

    def test_the_first_row_is_the_version(self):
        with mock.patch("importlib.metadata.version", return_value="9.9.9"):
            _, out, _ = self.rec2notes("doctor")
        self.assertRegex(out.splitlines()[1], r"^✓ rec2notes +9\.9\.9$")

    def test_details_start_in_one_column(self):
        _, out, _ = self.rec2notes("doctor")
        columns = {line.index(detail) for line, detail in
                   ((l, d) for l in out.splitlines() for d in (MODEL, "folder set", "silero-v5.1.2", "logged in")
                    if l.startswith("✓") and l.endswith(d))}
        self.assertEqual(len(columns), 1, out)

    def test_not_logged_in_fails_with_the_fix(self):
        os.environ["FAKE_CLAUDE_MODE"] = "logged-out"
        code, out, _ = self.rec2notes("doctor")
        self.assertEqual(code, 1)
        self.assertIn("not logged in", out)
        self.assertIn("then run `rec2notes doctor` again", out)
        self.assertIn("claude auth login", out)
        self.assertEqual(self.calls("claude"), [])  # `auth status` is not a merge call

    def test_missing_model_names_the_setup_command(self):
        paths.whisper_model(MODEL).unlink()
        code, out, _ = self.rec2notes("doctor")
        self.assertEqual(code, 1)
        self.assertIn(f"{paths.SETUP} --whisper-model {MODEL}", out)

    def test_the_saved_model_is_the_one_checked(self):
        paths.save_setting("whisper_model", "tiny")
        code, out, _ = self.rec2notes("doctor")
        self.assertEqual(code, 1)
        self.assertIn("tiny is missing", out)
        self.assertIn(f"{paths.SETUP} --whisper-model tiny", out)

    def test_no_rec2notes_folder_is_one_failure_with_the_fix(self):
        paths.pointer_file().unlink()
        code, out, err = self.rec2notes("doctor")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("✗ rec2notes folder", out)
        self.assertIn("run `rec2notes setup` first", out)
        self.assertNotIn("whisper-cli", out)  # it lives in the folder
        self.assertIn("1 problem to fix", out)

    def test_missing_folders_file(self):
        paths.folders_file().unlink()
        self.assertEqual(doctor.folders()[0][:3], ("fail", "Folders", f"{paths.folders_file()} does not exist"))

    def test_course_without_folder_is_a_warning(self):
        marks = {label: mark for mark, label, *_ in doctor.folders()}
        self.assertEqual(marks["Reti di calcolatori"], "ok")
        self.assertEqual(set(marks.values()), {"ok", "warn"})  # the other courses have no folder
        self.assertEqual(doctor.folders()[1][3], "set it in `rec2notes` → 3 Courses → e, or pass --course analisi")
        self.write_folders("")
        self.assertEqual(doctor.folders()[0], ("fail", "Folders", "no course has a folder",
                                               "add your courses, or set their folders, in `rec2notes` → 3 Courses"))

    def folder_rows(self, **folders):
        self.write_folders("".join(f"{slug} = '{folder}'\n" for slug, folder in folders.items()))
        return {label: (mark, detail, fix) for mark, label, detail, fix in doctor.folders()}

    def test_an_existing_writable_absolute_folder_is_ok(self):
        rows = self.folder_rows(net=self.note.parent)
        self.assertEqual(rows["Reti di calcolatori"], ("ok", str(self.note.parent), None))

    def test_a_relative_name_is_ok_but_says_it_is_not_looked_up(self):
        mark, detail, _ = self.folder_rows(net="Reti")["Reti di calcolatori"]
        self.assertEqual(mark, "ok")
        self.assertIn("not looked up", detail)

    def test_a_missing_folder_warns_with_its_path(self):
        mark, detail, fix = self.folder_rows(net=self.tmp / "nope")["Reti di calcolatori"]
        self.assertEqual(mark, "warn")
        self.assertIn(f"{self.tmp / 'nope'} does not exist", detail)
        self.assertIn("net", fix)

    def test_a_file_instead_of_a_folder_warns(self):
        self.assertEqual(self.folder_rows(net=self.note)["Reti di calcolatori"][0], "warn")

    def test_a_folder_you_cannot_write_in_fails(self):
        folder = self.tmp / "readonly"
        self.read_only(folder)
        mark, detail, fix = self.folder_rows(net=folder)["Reti di calcolatori"]
        self.assertEqual((mark, detail), ("fail", f"you have no write access to {folder}"))
        self.assertIn("permissions", fix)

    def test_only_the_outer_folder_fails_and_names_the_courses_inside_it(self):
        outer = self.tmp / "Docs"
        (outer / "Analisi").mkdir(parents=True)
        (outer / "Reti").mkdir()
        rows = self.folder_rows(basi=outer, analisi=outer / "Analisi", net=outer / "Reti")
        mark, detail, fix = rows["Basi di dati"]
        self.assertEqual(mark, "fail")
        self.assertIn(f"{outer} contains the folder of Analisi matematica, Reti di calcolatori", detail)
        self.assertIn("smaller folder for `basi`", fix)
        self.assertEqual(rows["Analisi matematica"][0], "ok")
        self.assertEqual(rows["Reti di calcolatori"][0], "ok")

    def test_two_courses_with_the_same_folder_both_fail(self):
        folder = self.tmp / "Docs"
        folder.mkdir()
        rows = self.folder_rows(net=folder, analisi=folder)
        self.assertIn("is also the folder of Analisi", rows["Reti di calcolatori"][1])
        self.assertIn("is also the folder of Reti di calcolatori", rows["Analisi matematica"][1])
        self.assertEqual(rows["Basi di dati"][0], "warn")  # no folder is still only a warning

    def test_doctor_exits_1_for_an_overlap(self):
        self.write_folders(f"net = '{self.tmp}/A'\nanalisi = '{self.tmp}/A/B'\n")
        self.assertEqual(self.rec2notes("doctor")[0], 1)

    def test_without_claude_or_ffmpeg(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "bin")}):
            code, out, _ = self.rec2notes("doctor")
        self.assertEqual(code, 1)
        self.assertIn("Claude Code", out)
        self.assertIn("not checked", out)


class HubDoctor(Hub):
    def test_doctor_from_the_menu_returns_to_it(self):
        args, out, prompts = self.hub("2", "q")
        self.assertIsNone(args)
        self.assertIn("Claude Code", out)
        self.assertEqual(len(prompts), 2)
        self.assertGreater(out.rindex("Doctor: check"), out.index("Claude Code"))  # the options return after the checklist
