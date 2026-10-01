import unittest
from pathlib import Path

from rec2notes import Abort, courses, paths

from .helpers import Sandbox


class CourseResolution(Sandbox):
    def resolve(self, note, slug=None):
        all_courses = courses.load_courses()
        folders = {} if slug else courses.load_folders(all_courses)
        return courses.resolve_course(note, all_courses, folders, slug)

    def test_folder_in_the_note_path_picks_the_course(self):
        self.assertEqual(self.resolve(self.note).slug, "net")

    def test_folder_can_be_a_relative_path(self):
        self.write_folders('analisi = "Uni/Analisi"\n')
        note = self.tmp / "vault" / "Uni" / "Analisi" / "Lezioni" / "Lezione 2.md"
        self.assertEqual(self.resolve(note).slug, "analisi")

    def test_folder_can_be_an_absolute_path(self):
        self.write_folders(f"net = '{self.note.parent}'\n")
        subfolder_note = self.note.parent / "Lezioni" / "Lezione 3.md"
        self.assertEqual(self.resolve(self.note).slug, "net")
        self.assertEqual(self.resolve(subfolder_note).slug, "net")

    @unittest.skipUnless(paths.WINDOWS, "only Windows paths ignore case")
    def test_a_folder_in_another_case_matches_on_windows(self):
        self.write_folders(f"net = '{str(self.note.parent).lower()}'\n")
        self.assertEqual(self.resolve(self.note).slug, "net")

    def test_folder_that_is_a_symlink(self):
        link = self.tmp / "Le mie reti"
        try:
            link.symlink_to(self.note.parent)
        except OSError as error:  # Windows allows symlinks only to admins or in Developer Mode
            self.skipTest(f"can't create a symlink here: {error}")
        self.write_folders('net = "Le mie reti"\n')
        self.assertEqual(self.resolve(link / self.note.name).slug, "net")

    def test_course_flag_works_without_the_folders_file(self):
        paths.folders_file().unlink()
        self.assertEqual(self.resolve(self.note, slug="basi").name, "Basi di dati")

    def test_no_match_fails_and_lists_the_folders(self):
        note = self.tmp / "vault" / "Altro" / "Lezione.md"
        with self.assertRaises(Abort) as raised:
            self.resolve(note)
        message = str(raised.exception)
        self.assertIn("net: Reti", message)
        self.assertIn(str(paths.folders_file()), message)
        self.assertIn("--course", message)

    def test_unknown_slug_in_the_folders_file_is_an_error(self):
        self.write_folders('reti = "Reti"\n')
        with self.assertRaisesRegex(Abort, "unknown course 'reti'"):
            self.resolve(self.note)

    def test_commented_template_configures_nothing(self):
        self.write_folders(courses.folders_template(courses.load_courses()))
        self.assertEqual(courses.load_folders(courses.load_courses()), {})


class UserCourses(Sandbox):
    def test_a_course_added_by_the_user_is_loaded_beside_the_repo_ones(self):
        courses.add_course("fisica", "Fisica 1", 'Lezione di "fisica": è spin,\nmomento.')
        loaded = courses.load_courses()
        self.assertEqual(loaded["fisica"].vocab, 'Lezione di "fisica": è spin, momento.')
        self.assertIn("net", loaded)

    def test_no_courses_file_means_no_courses(self):
        paths.courses_file().unlink()
        self.assertEqual(courses.load_courses(), {})

    def test_a_broken_user_file_names_itself(self):
        paths.courses_file().write_text('[x]\nname = "X"\n', encoding="utf-8")
        with self.assertRaisesRegex(Abort, "courses.toml: course \\[x\\] needs"):
            courses.load_courses()

    def test_update_changes_the_given_fields_and_keeps_the_rest_of_the_file(self):
        text = paths.courses_file().read_text(encoding="utf-8")
        paths.courses_file().write_text("# mine\n" + text, encoding="utf-8")
        courses.update_course("analisi", vocab='Nuovo "vocab":\nè qui.')
        loaded = courses.load_courses()
        self.assertEqual((loaded["analisi"].name, loaded["analisi"].vocab),
                         ("Analisi matematica", 'Nuovo "vocab": è qui.'))
        self.assertEqual(loaded["net"], courses.Course("net", "Reti di calcolatori", "Lezione di reti."))
        self.assertTrue(paths.courses_file().read_text(encoding="utf-8").startswith("# mine\n[net]"))
        courses.update_course("basi", name="SS")
        self.assertEqual(courses.load_courses()["basi"].name, "SS")

    def test_update_refuses_an_unknown_course_or_an_empty_value(self):
        before = paths.courses_file().read_text(encoding="utf-8")
        for slug, kwargs in (("nope", {"name": "N"}), ("net", {"name": "  "}), ("net", {"vocab": ""})):
            with self.assertRaises(Abort, msg=(slug, kwargs)):
                courses.update_course(slug, **kwargs)
        self.assertEqual(paths.courses_file().read_text(encoding="utf-8"), before)

    def test_rename_moves_the_course_and_its_folder_line_and_keeps_the_rest(self):
        self.write_folders('# mine\nnet = "Reti"  # here\nanalisi = "Uni/Analisi"\n')
        courses.rename_course("net", "reti")
        loaded = courses.load_courses()
        self.assertEqual((list(loaded), loaded["reti"].name), (["reti", "analisi", "basi"], "Reti di calcolatori"))
        self.assertEqual(paths.folders_file().read_text(encoding="utf-8"), '# mine\nreti = "Reti"  # here\nanalisi = "Uni/Analisi"\n')

    def test_rename_works_for_a_course_without_a_folder_or_a_folders_file(self):
        paths.folders_file().unlink()
        courses.rename_course("analisi", "arch")
        self.assertIn("arch", courses.load_courses())
        self.assertFalse(paths.folders_file().exists())

    def test_rename_refuses_an_unknown_course_and_a_taken_or_bad_slug_and_changes_nothing(self):
        before = paths.courses_file().read_text(encoding="utf-8"), paths.folders_file().read_text(encoding="utf-8")
        for old, new in (("nope", "x"), ("net", "analisi"), ("net", "Bad Slug"), ("net", "")):
            with self.assertRaises(Abort, msg=(old, new)):
                courses.rename_course(old, new)
        self.assertEqual((paths.courses_file().read_text(encoding="utf-8"), paths.folders_file().read_text(encoding="utf-8")), before)

    def test_add_refuses_a_bad_or_existing_slug(self):
        for slug in ("Bad Slug", "", "net"):
            with self.assertRaises(Abort, msg=slug):
                courses.add_course(slug, "N", "V")
        courses.add_course("fisica", "Fisica", "V")
        with self.assertRaisesRegex(Abort, "already exists"):
            courses.check_new_slug("fisica")
        with self.assertRaisesRegex(Abort, "already exists"):
            courses.add_course("fisica", "Fisica", "V")

    def test_set_folder_creates_appends_and_replaces(self):
        paths.folders_file().unlink()
        courses.set_folder("analisi", "Uni/Analisi")
        courses.set_folder("net", "Reti")
        courses.set_folder("analisi", "/home/x/Vault/Analisi  2")
        self.assertEqual(paths.folders_file().read_text(encoding="utf-8"), 'analisi = "/home/x/Vault/Analisi  2"\nnet = "Reti"\n')
        self.assertEqual(courses.load_folders(courses.load_courses()), {"analisi": "home/x/Vault/Analisi  2", "net": "Reti"})

    def test_set_folder_keeps_comments_and_an_absolute_folder_matches_subfolders(self):
        self.write_folders('# mine\n# analisi = ""\nnet = "Reti"\n')
        courses.set_folder("analisi", str(self.tmp / "vault" / "Altro"))
        self.assertTrue(paths.folders_file().read_text(encoding="utf-8").startswith("# mine\n# analisi"))
        note = self.tmp / "vault" / "Altro" / "Sotto" / "L.md"
        all_courses = courses.load_courses()
        self.assertEqual(courses.resolve_course(note, all_courses, courses.load_folders(all_courses)).slug, "analisi")


class AbsoluteFolder(Sandbox):
    def test_only_an_absolute_path_is_accepted(self):
        for typed in ("7", "Reti", "Uni/Reti", "", "  "):
            with self.assertRaisesRegex(Abort, "not an absolute path", msg=typed):
                courses.absolute_folder(typed)

    def test_a_file_is_not_a_folder(self):
        with self.assertRaisesRegex(Abort, "is not a folder"):
            courses.absolute_folder(str(self.note))

    def test_a_folder_that_does_not_exist_yet_is_accepted(self):
        self.assertEqual(courses.absolute_folder(f" {self.tmp}/nuova/../altra "), self.tmp / "altra")

    def test_home_is_expanded(self):
        self.assertEqual(courses.absolute_folder("~/vault"), self.home / "vault")

    def test_a_folder_you_cannot_write_in_is_refused(self):
        folder = self.tmp / "readonly"
        self.read_only(folder)
        with self.assertRaisesRegex(Abort, "no write access to .*readonly"):
            courses.absolute_folder(str(folder))

    def test_a_missing_folder_is_refused_when_its_parent_is_not_writable(self):
        parent = self.tmp / "readonly"
        self.read_only(parent)
        with self.assertRaisesRegex(Abort, "can't be created: you have no write access to .*readonly"):
            courses.absolute_folder(str(parent / "a" / "b"))

    def test_a_missing_folder_under_a_file_is_refused(self):
        with self.assertRaisesRegex(Abort, "is not a folder"):
            courses.absolute_folder(str(self.note / "sub"))


class FolderOverlap(Sandbox):
    def setUp(self):
        super().setUp()
        self.write_folders(f"net = '{self.tmp}/Docs/Reti'\nanalisi = 'Uni/Analisi'\n")

    def refused(self, folder, slug="fisica"):
        with self.assertRaisesRegex(Abort, "overlaps the folder of"):
            courses.check_folder_free(Path(folder), slug)

    def test_the_same_folder_a_folder_inside_and_a_folder_containing_are_refused(self):
        self.refused(self.tmp / "Docs" / "Reti")
        self.refused(self.tmp / "Docs" / "Reti" / "Lezioni")
        self.refused(self.tmp / "Docs")

    def test_a_relative_entry_overlaps_the_paths_that_have_its_components(self):
        self.refused("/home/x/Uni/Analisi/Sub")

    def test_the_message_names_the_other_course(self):
        with self.assertRaisesRegex(Abort, r"folder of net \(.*Docs/Reti\)"):
            courses.check_folder_free(self.tmp / "Docs", "fisica")

    def test_unrelated_folders_and_the_courses_own_folder_are_accepted(self):
        courses.check_folder_free(self.tmp / "Altro", "fisica")
        courses.check_folder_free(self.tmp / "Docs" / "Reti", "net")
        courses.check_folder_free(self.tmp / "Docs" / "Rete", "fisica")  # a similar name is not a component match

    def test_no_folders_configured_accepts_anything(self):
        paths.folders_file().unlink()
        courses.check_folder_free(Path("/home"), "fisica")
