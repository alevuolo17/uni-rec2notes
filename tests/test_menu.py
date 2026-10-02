import io
import os
import shutil
from unittest import mock

from rec2notes import cli, courses, menu, paths, transcribe, ui

from .helpers import MODEL, Sandbox


class Hub(Sandbox):
    def hub(self, *answers):
        """Run the menu with scripted answers; returns (parsed arguments or None, what it printed, prompts asked)."""
        out, prompts, script = io.StringIO(), [], iter(answers)

        def ask(prompt):
            prompts.append(prompt)
            try:
                return next(script)
            except StopIteration:
                raise EOFError from None

        args = menu.hub(ui.Console(out, io.StringIO()), ask, cli.parse_args, cli.parse_create_args)
        return args, out.getvalue(), prompts

    def test_quit(self):
        args, out, _ = self.hub("q")
        self.assertIsNone(args)
        self.assertIn("Run: complete a note, or create one", out)

    def test_end_of_input_quits(self):
        self.assertIsNone(self.hub()[0])

    def test_invalid_choice_asks_again(self):
        args, out, prompts = self.hub("x", "q")
        self.assertIsNone(args)
        self.assertIn("Not an option.", out)
        self.assertEqual(len(prompts), 2)
        self.assertEqual(out.count("Doctor: check"), 2)  # the options come back after the mistake

    def test_help_shows_the_options_again_without_an_error(self):
        _, out, _ = self.hub("help", "?", "H", "q")
        self.assertEqual(out.count("Doctor: check"), 4)
        self.assertNotIn("Not an option.", out)

    def test_run_asks_whether_to_complete_or_create_and_can_go_back(self):
        args, out, _ = self.hub("1", "x", "b", "q")
        self.assertIsNone(args)
        self.assertIn("Create a note from a recording", out)
        self.assertIn("Type 1, 2 or b.", out)
        self.assertEqual(out.count("Doctor: check"), 2)  # back at the hub

    def test_create_asks_the_new_note_and_recording_then_confirms(self):
        new = self.note.with_name("Lezione 2.md")
        args, out, _ = self.hub("1", "2", str(new), str(self.audio), "", "", "")
        self.assertTrue(args.create)
        self.assertEqual((args.note, args.audio, args.course, args.length), (new, [self.audio], "net", 20))
        self.assertIn("New note       Lezione 2.md", out)
        self.assertIn("Length         about 20% of the transcript", out)
        self.assertIn("Effort         high", out)
        code = cli.run_create(args, ui.Console(io.StringIO(), io.StringIO()))
        self.assertEqual(code, 0)
        self.assertTrue(new.exists())

    def test_create_refuses_a_taken_name_or_a_missing_folder_and_adds_md(self):
        args, out, _ = self.hub("1", "2", str(self.tmp / "nope" / "x.md"), str(self.note), str(self.note.with_name("Lezione 2")),
                                str(self.audio), "", "", "")
        self.assertIn(f"No such folder: {self.tmp / 'nope'}.\n", out)
        self.assertIn(f"{self.note} already exists: pick another name.", out)
        self.assertEqual(args.note, self.note.with_name("Lezione 2.md"))

    def test_create_says_variables_are_not_expanded(self):
        self.home.mkdir()
        args, out, _ = self.hub("1", "2", "$HOME/Lezione 2.md", "%USERPROFILE%/Lezione 2.md", "~/Lezione 2.md",
                                str(self.audio), "", "1", "")
        self.assertEqual(out.count("Variables like $HOME aren't expanded here: use ~ for your home folder."), 2)
        self.assertEqual(args.note, self.home / "Lezione 2.md")

    def test_create_in_an_unknown_folder_asks_for_the_course(self):
        elsewhere = self.tmp / "vault" / "Altro"
        elsewhere.mkdir()
        args, _, _ = self.hub("1", "2", str(elsewhere / "Lezione.md"), str(self.audio), "", "2", "")
        self.assertEqual(args.course, "analisi")

    def test_run_asks_note_audio_then_confirms_with_the_settings(self):
        args, out, _ = self.hub("1", "1", str(self.note), "", str(self.audio), "", "", "")
        self.assertEqual((args.note, args.audio, args.course), (self.note, [self.audio], "net"))
        self.assertIn("Reti di calcolatori", out)
        self.assertIn("Agent          claude", out)
        self.assertIn("Effort         high", out)
        self.assertIn("Model          Claude Code's default", out)

    def test_paths_are_cleaned_and_bad_ones_asked_again(self):
        args, out, _ = self.hub("1", "1", str(self.tmp / "nope.md"), f"'{self.note}'", "", f"{self.audio}".replace(" ", "\\ "), "", "", "y")
        self.assertEqual(args.note, self.note)
        self.assertIn(f"Not a file: {self.tmp / 'nope.md'}", out)

    def test_several_parts(self):
        second = self.make_audio("parte2.m4a", b"audio two")
        args, _, _ = self.hub("1", "1", str(self.note), "", str(self.audio), str(self.tmp / "nope.m4a"), str(second), "", "", "")
        self.assertEqual(args.audio, [self.audio, second])

    def test_declining_returns_nothing(self):
        self.assertIsNone(self.hub("1", "1", str(self.note), "", str(self.audio), "", "", "n")[0])

    def test_unknown_folder_asks_for_the_course(self):
        elsewhere = self.tmp / "vault" / "Altro" / "Lezione 2.md"
        elsewhere.parent.mkdir()
        elsewhere.write_text("# Lezione\n", encoding="utf-8")
        args, out, _ = self.hub("1", "1", str(elsewhere), "", str(self.audio), "", "9", "2", "")
        self.assertIn("Type a number from 1 to 3, or n.", out)
        self.assertEqual(args.course, "analisi")
        self.assertEqual(paths.folders_file().read_text(encoding="utf-8"), 'net = "Reti"\n')  # a run never writes it

    def test_the_course_of_the_notes_folder_is_asked_with_enter_picking_it(self):
        args, out, prompts = self.hub("1", "1", str(self.note), "", str(self.audio), "", "", "")
        self.assertIn("Reti di calcolatori  (from the note's folder)", out)
        self.assertIn("[1] > ", prompts)
        self.assertEqual(args.course, "net")
        self.assertFalse(any("Remember" in p for p in prompts))

    def test_another_course_can_be_picked_for_a_note_in_a_course_folder(self):
        args, _, _ = self.hub("1", "1", str(self.note), "", str(self.audio), "", "2", "")
        self.assertEqual(args.course, "analisi")
        self.assertEqual(paths.folders_file().read_text(encoding="utf-8"), 'net = "Reti"\n')

    def test_a_new_course_can_be_added_during_a_run(self):
        elsewhere = self.tmp / "vault" / "Altro" / "Lezione 2.md"
        elsewhere.parent.mkdir()
        elsewhere.write_text("# Lezione\n", encoding="utf-8")
        args, out, _ = self.hub("1", "1", str(elsewhere), "", str(self.audio), "", "n",
                                "Bad Slug", "net", "fisica", "Fisica 1", "Lezione di fisica. Termini: spin.", "")
        self.assertIn("must be lowercase", out)
        self.assertIn("already exists", out)  # a bad slug is refused before the name, vocabulary and folder are asked
        self.assertEqual(args.course, "fisica")
        self.assertIn("Fisica 1", out)

    def add_course(self, *folder_answers):
        return self.hub("3", "a", "fisica", "Fisica 1", "Lezione di fisica.", *folder_answers, "b", "q")

    def test_courses_screen_lists_and_adds(self):
        folder = self.tmp / "vault" / "Fisica"
        folder.mkdir()
        _, out, _ = self.hub("3", "x", "a", "fisica", "Fisica 1", "Lezione di fisica.", str(folder), "b", "q")
        self.assertIn("net", out)
        self.assertIn("[Reti]", out)
        self.assertIn("Type a, e or b.", out)
        self.assertIn(f"Fisica 1  [{str(folder).lstrip('/')}]", out)
        self.assertIn(f"Added Fisica 1, with the folder {folder}.", out)

    def test_editing_a_course_changes_only_what_is_typed(self):
        folder = self.tmp / "vault" / "Laboratorio"
        folder.mkdir()
        _, out, _ = self.hub("3", "e", "nope", "net", "", "Reti di calcolatori", "", str(folder), "b", "q")
        self.assertIn("Not a course: nope", out)
        self.assertIn("Updated Reti di calcolatori.", out)
        loaded = courses.load_courses()["net"]
        self.assertEqual((loaded.name, loaded.vocab), ("Reti di calcolatori", "Lezione di reti."))
        self.assertEqual(courses.load_folders(courses.load_courses())["net"], str(folder).strip("/"))

    def test_editing_with_only_enter_changes_nothing(self):
        before = paths.courses_file().read_text(encoding="utf-8"), paths.folders_file().read_text(encoding="utf-8")
        self.hub("3", "e", "net", "", "", "", "", "b", "q")
        self.assertEqual((paths.courses_file().read_text(encoding="utf-8"), paths.folders_file().read_text(encoding="utf-8")), before)

    def test_editing_the_short_name_keeps_the_folder_and_refuses_a_taken_one(self):
        _, out, _ = self.hub("3", "e", "net", "analisi", "Bad Slug", "reti", "", "", "", "b", "q")
        self.assertEqual(out.count("already exists") + out.count("must be lowercase"), 2)
        self.assertEqual(courses.load_folders(courses.load_courses()), {"reti": "Reti"})
        self.assertIn("reti", courses.load_courses())

    def test_a_folder_that_is_not_an_absolute_path_is_asked_again(self):
        folder = self.tmp / "vault"
        _, out, _ = self.add_course("7", "Reti", str(folder))
        self.assertEqual(out.count("not an absolute path"), 2)
        self.assertIn(f"Fisica 1  [{str(folder).lstrip('/')}]", out)

    def test_a_missing_folder_is_created_only_when_the_user_says_so(self):
        folder = self.tmp / "vault" / "Nuova"
        _, out, prompts = self.add_course(str(folder), "n", "")  # asked again after no; Enter skips it
        self.assertTrue(any("does not exist. Create it?" in p for p in prompts))
        self.assertFalse(folder.exists())
        self.assertIn("Fisica 1  [no folder]", out)
        _, out, _ = self.hub("3", "a", "chimica", "Chimica", "Lezione.", str(folder), "y", "b", "q")
        self.assertTrue(folder.is_dir())
        self.assertIn(f"Chimica  [{str(folder).lstrip('/')}]", out)

    def test_a_taken_slug_is_refused_before_the_rest_is_asked(self):
        _, _, prompts = self.hub("3", "a", "net", "fisica", "Fisica", "Lezione.", "", "b", "q")
        self.assertEqual(sum(p.startswith("Course name") for p in prompts), 1)

    def test_a_folder_you_cannot_write_in_is_refused_without_offering_to_create_it(self):
        parent = self.tmp / "readonly"
        self.read_only(parent)
        _, out, prompts = self.add_course(str(parent), str(parent / "new"), "")
        self.assertEqual(out.count("no write access"), 2)
        self.assertFalse(any("Create it?" in p for p in prompts))
        self.assertIn("Fisica 1  [no folder]", out)

    def test_a_folder_overlapping_another_course_is_asked_again(self):
        vault, other = self.tmp / "vault", self.tmp / "other"
        other.mkdir()
        self.write_folders(f"net = '{vault / 'Reti'}'\n")
        _, out, _ = self.add_course(str(vault), str(other))
        self.assertIn("overlaps the folder of net", out)
        self.assertIn(f"Fisica 1  [{str(other).lstrip('/')}]", out)

    def test_a_folder_is_optional(self):
        _, out, _ = self.add_course("")
        self.assertIn("Added Fisica 1. No folder set, so a run won't pre-select it", out)
        self.assertIn(str(paths.folders_file()), out)
        self.assertIn("Fisica 1  [no folder]", out)

    def test_course_picker_rejects_anything_but_a_listed_number(self):
        elsewhere = self.tmp / "vault" / "Altro" / "Lezione 2.md"
        elsewhere.parent.mkdir()
        elsewhere.write_text("# Lezione\n", encoding="utf-8")
        junk = ["abc", "0", "-1", "4", "1.5", "²", "٣"]  # the last two are digits that int() can't read
        args, out, _ = self.hub("1", "1", str(elsewhere), "", str(self.audio), "", *junk, "3", "")
        self.assertEqual(out.count("Type a number from 1 to 3, or n."), len(junk))
        self.assertEqual(args.course, "basi")

    def test_confirmation_asks_again_on_anything_but_yes_or_no(self):
        args, out, _ = self.hub("1", "1", str(self.note), "", str(self.audio), "", "", "yy", "maybe", "N")
        self.assertIsNone(args)
        self.assertEqual(out.count("Type y, n or c."), 2)
        self.assertEqual(out.count("Effort         high"), 1)  # asked again, without the summary again

    def test_c_at_start_changes_the_settings_for_this_run_only(self):
        self.add_model("large-v3-turbo-q5_0")  # second after MODEL
        args, out, _ = self.hub("1", "1", str(self.note), "", str(self.audio), "", "", "c", "4", "1", "2", "")
        self.assertEqual((args.claude_model, args.effort, args.whisper_model), ("haiku", "low", "large-v3-turbo-q5_0"))
        self.assertIn("Model          haiku", out)
        self.assertIn("Effort         low", out)
        self.assertFalse(paths.settings_file().exists())

    def test_enter_keeps_each_setting_at_c(self):
        args, _, _ = self.hub("1", "2", str(self.note.with_name("Lezione 2.md")), str(self.audio), "", "", "c", "", "", "", "")
        self.assertEqual((args.claude_model, args.effort, args.whisper_model), (None, "high", MODEL))

    def test_a_missing_whisper_model_is_shown_before_start_and_c_can_pick_a_downloaded_one(self):
        paths.whisper_model(MODEL).unlink()
        self.add_model("large-v3-turbo-q5_0")
        args, out, prompts = self.hub("1", "1", str(self.note), "", str(self.audio), "", "", "", "c", "", "", "1", "")
        self.assertIn(f"Can't start yet:\n  - {paths.whisper_model(MODEL)} is missing", out)
        self.assertIn("Type c or n.", out)  # Enter doesn't start it
        self.assertIn("c to change settings, n to cancel: ", prompts)
        self.assertEqual(args.whisper_model, "large-v3-turbo-q5_0")

    def test_a_cached_transcript_needs_no_whisper_model(self):
        paths.whisper_model(MODEL).unlink()
        cache = paths.transcript_cache(MODEL, transcribe.sha256_file(self.audio))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text("[00:00:00] Lezione.\n", encoding="utf-8")
        args, out, _ = self.hub("1", "1", str(self.note), "", str(self.audio), "", "", "")
        self.assertNotIn("Can't start yet", out)
        self.assertEqual(args.whisper_model, MODEL)

    def test_the_menu_arguments_run_like_the_flags(self):
        args, _, _ = self.hub("1", "1", str(self.note), "", str(self.audio), "", "", "")
        code = cli.run(args, ui.Console(io.StringIO(), io.StringIO()))
        self.assertEqual(code, 0)
        self.assertTrue(self.output.exists())

    def test_cleaning_is_asked_and_off_by_default(self):
        args, out, prompts = self.hub("1", "1", str(self.note), "", str(self.audio), "", "", "")
        self.assertFalse(args.clean)
        self.assertTrue(any(p.startswith("Clean the note first?") for p in prompts))
        self.assertNotIn("Clean  ", out)

    def test_yes_to_cleaning_sets_the_flag_and_shows_it_in_the_summary(self):
        args, out, _ = self.hub("1", "1", str(self.note), "y", str(self.audio), "", "", "")
        self.assertTrue(args.clean)
        self.assertIn("Clean          yes, then merge the cleaned copy", out)

    def test_the_cleaning_answer_must_be_yes_or_no(self):
        args, out, _ = self.hub("1", "1", str(self.note), "maybe", "y", str(self.audio), "", "", "")
        self.assertTrue(args.clean)
        self.assertEqual(out.count("Type y or n."), 1)

    def test_settings_shows_the_folder(self):
        _, out, _ = self.hub("4", "x", "b", "q")
        self.assertIn(f"rec2notes folder  {self.folder}", out)
        self.assertIn("Type c, m, e, w or b.", out)
        self.assertIn("Claude model      Claude Code's default", out)
        self.assertIn("Effort            high", out)
        self.assertIn(f"Whisper model     {MODEL}", out)

    def test_settings_saves_the_defaults_runs_use(self):
        self.add_model("large-v3-turbo-q5_0")
        _, out, _ = self.hub("4", "m", "2", "e", "5", "w", "2", "b", "q")
        self.assertEqual(paths.saved_settings(),
                         {"claude_model": "opus", "effort": "max", "whisper_model": "large-v3-turbo-q5_0"})
        self.assertIn("Effort            max", out)
        args = cli.parse_args([str(self.note), str(self.audio)])
        self.assertEqual((args.claude_model, args.effort, args.whisper_model), ("opus", "max", "large-v3-turbo-q5_0"))

    def test_settings_pickers_keep_the_current_value_on_enter(self):
        paths.save_setting("claude_model", "sonnet")
        _, _, prompts = self.hub("4", "m", "", "e", "", "b", "q")
        self.assertEqual(prompts.count("[3] > "), 2)  # sonnet, then high
        self.assertEqual(paths.saved_settings()["claude_model"], "sonnet")
        self.assertEqual(paths.saved_settings()["effort"], "high")

    def test_settings_offers_only_the_downloaded_whisper_models(self):
        self.add_model("large-v3-turbo")
        _, out, _ = self.hub("4", "w", "", "b", "q")
        self.assertIn("large-v3-turbo  (much faster", out)
        self.assertNotIn("large-v3-turbo-q5_0", out)
        self.assertNotIn(paths.VAD_MODEL, out)

    def test_settings_without_whisper_models_says_to_run_setup(self):
        shutil.rmtree(paths.whisper_dir() / "models")
        _, out, _ = self.hub("4", "w", "b", "q")
        self.assertIn("No Whisper model is downloaded: run `rec2notes setup`.", out)
        self.assertNotIn("whisper_model", paths.saved_settings())

    def test_settings_says_when_an_environment_variable_wins(self):
        os.environ["REC2NOTES_EFFORT"] = "low"
        _, out, _ = self.hub("4", "b", "q")
        self.assertIn("Effort            low  (from $REC2NOTES_EFFORT, which wins over this screen)", out)

    def test_settings_points_to_a_moved_folder(self):
        moved = self.tmp / "moved" / "rec2notes"
        moved.parent.mkdir()
        shutil.move(self.folder, moved)
        _, out, _ = self.hub("3", "4", "c", str(moved), "b", "3", "b", "q")
        self.assertIn("is missing (moved or deleted?). If you moved it, point to it", out)  # and the menu goes on
        self.assertIn(f"{self.folder} (missing: moved or deleted?)", out)
        self.assertIn(f"Now using {moved}.", out)
        self.assertEqual(paths.folder(), moved)
        self.assertIn("Reti di calcolatori", out)  # the Courses screen reads the moved folder
        self.assertFalse(self.folder.exists())  # nothing is created where it was

    def test_a_folder_with_only_the_models_is_accepted(self):
        moved = self.tmp / "moved"
        (moved / "whisper.cpp" / "models").mkdir(parents=True)
        self.hub("4", "c", str(moved), "b", "q")
        self.assertEqual(paths.pointed_folder(), moved)

    def test_a_folder_that_is_not_a_rec2notes_folder_is_asked_again(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        _, out, _ = self.hub("4", "c", str(empty), "rec2notes", "", "b", "q")
        self.assertIn(f"{empty} doesn't look like a rec2notes folder", out)
        self.assertIn("Type an absolute path", out)
        self.assertEqual(paths.pointed_folder(), self.folder)  # Enter kept it

    def test_settings_without_a_pointer_says_to_run_setup_and_can_still_point(self):
        paths.pointer_file().unlink()
        _, out, _ = self.hub("4", "c", str(self.folder), "b", "q")
        self.assertIn("none yet: run `rec2notes setup`", out)
        self.assertEqual(paths.pointed_folder(), self.folder)


class Main(Sandbox):
    def test_bare_command_off_a_terminal_prints_the_usage_and_no_menu(self):
        with mock.patch.object(menu, "hub") as hub:
            code, out, _ = self.rec2notes()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("usage: rec2notes"))
        hub.assert_not_called()

    def test_bare_command_on_a_terminal_opens_the_menu(self):
        with mock.patch("sys.stdin.isatty", return_value=True), mock.patch("sys.stdout.isatty", return_value=True), \
                mock.patch.object(menu, "hub", return_value=None) as hub:
            self.assertEqual(cli.main([]), 0)
        hub.assert_called_once()
