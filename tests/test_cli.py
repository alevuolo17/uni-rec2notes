import contextlib
import io
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from rec2notes import cli, courses, paths, setup, transcribe

from .helpers import FIXTURES, MODEL, TESTS, Sandbox


class Output(Sandbox):
    def test_writes_the_sibling_and_leaves_the_note_untouched(self):
        original = self.note.read_bytes()
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.note.read_bytes(), original)
        self.assertIn("Aggiunta dal transcript.[^conflitto-1]", self.output.read_text(encoding="utf-8"))
        self.assertEqual(out.splitlines()[:3], ["rec2notes · Reti di calcolatori", "Lezione 1.md", ""])
        self.assertIn(f"✓ Transcribe  9s of audio · {MODEL}", out)
        self.assertIn("✓ Merge       claude · effort high", out)
        self.assertIn("✓ Check       only additions\n", out)
        self.assertIn("✓ Write       Lezione 1 (completo).md\n", out)
        self.assertIn("\n  Conflicts      1\n  Missed topics  yes\n  Run            ", out)
        self.assertNotIn("⚠", out)
        self.assertEqual(sorted(p.name for p in self.run_dirs()[0].iterdir()),
                         ["check.txt", "claude-stderr.txt", "input.txt", "p1.json", "reply.md", "whisper-args.txt", "whisper.log"])
        self.assertEqual(len(list(self.output.parent.iterdir())), 2, "nothing else may appear in the vault")

    def test_bare_command_prints_the_help_without_banner_off_a_terminal(self):
        code, out, err = self.rec2notes()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("usage: rec2notes"))
        self.assertNotIn("█", out)

    def test_the_installed_command_runs_the_cli(self):
        result = subprocess.run([sys.executable, "-m", "rec2notes"], cwd=TESTS.parent, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("usage: rec2notes"))

    def test_setup_is_a_subcommand(self):
        with mock.patch.object(setup, "main", return_value=0) as main:
            self.assertEqual(self.rec2notes("setup", "--backend", "cpu")[0], 0)
        main.assert_called_once_with(["--backend", "cpu"])

    def test_claude_command_line(self):
        self.assertEqual(self.rec2notes(self.note, self.audio)[0], 0)
        self.assertEqual(self.calls("claude"), [["-p", "--effort", "high", "--tools", "", "--no-session-persistence",
                                                 "--system-prompt-file", str(paths.MERGE_PROMPT)]])

    def test_claude_model_and_effort_can_be_overridden(self):
        os.environ["REC2NOTES_CLAUDE_MODEL"] = "opus"
        self.assertEqual(self.rec2notes(self.note, self.audio, "--effort", "max")[0], 0)
        (args,) = self.calls("claude")
        self.assertEqual(args[args.index("--effort") + 1], "max")
        self.assertEqual(args[args.index("--model") + 1], "opus")

    def test_unquoted_path_with_spaces_gets_a_hint(self):
        head, tail = str(self.note).split(" ", 1)  # ".../Reti/Lezione", "1.md"
        code, _, err = self.rec2notes(head, tail, self.audio)
        self.assertEqual(code, 1)
        self.assertIn(f'put it in quotes: "{self.note}"', err)

    def test_without_a_rec2notes_folder_says_to_run_setup(self):
        paths.pointer_file().unlink()
        code, _, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 1)
        self.assertIn("run `rec2notes setup` first", err)
        self.assertFalse(self.output.exists())

    def test_a_missing_rec2notes_folder_stops_and_is_not_recreated(self):
        shutil.rmtree(self.folder)
        code, _, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 1)
        self.assertIn(f"your rec2notes folder {self.folder} is missing", err)
        self.assertFalse(self.folder.exists())
        self.assertFalse(self.output.exists())

    def test_refuses_when_the_sibling_exists(self):
        self.output.write_text("versione precedente\n", encoding="utf-8")
        code, _, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "versione precedente\n")
        self.assertEqual(self.calls("claude") + self.calls("whisper"), [])

    def test_force_replaces_the_sibling_and_keeps_the_old_one(self):
        self.output.write_text("versione precedente\n", encoding="utf-8")
        code, _, err = self.rec2notes(self.note, self.audio, "--force")
        self.assertEqual(code, 0, err)
        self.assertIn("Aggiunta dal transcript.", self.output.read_text(encoding="utf-8"))
        self.assertEqual((self.run_dirs()[0] / self.output.name).read_text(encoding="utf-8"), "versione precedente\n")

    def test_refuses_an_already_completed_note(self):
        self.note.write_text("Testo.[^conflitto-1]\n\n[^conflitto-1]: **Appunti:** «a» — **Audio [00:00:01]:** «b»\n", encoding="utf-8")
        code, _, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 1)
        self.assertIn("completed note", err)
        self.assertEqual(self.calls("claude") + self.calls("whisper"), [])

    def test_refuses_a_note_with_a_missed_topics_section(self):
        self.note.write_text("Testo.\n\n## Argomenti non presenti negli appunti\n\n- **X** [00:00:01], dopo «Testo»: y.\n", encoding="utf-8")
        code, _, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 1)
        self.assertIn("completed note", err)


class Clean(Sandbox):
    def setUp(self):
        super().setUp()
        self.cleaned = self.note.with_name("Lezione 1 (pulito).md")

    def test_clean_command_writes_the_sibling_and_leaves_the_note_untouched(self):
        original = self.note.read_bytes()
        code, out, err = self.rec2notes("clean", self.note)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.note.read_bytes(), original)
        self.assertEqual(self.cleaned.read_text(encoding="utf-8"), "Pulito.\n\n" + original.decode())
        self.assertIn("✓ Clean       claude · effort high", out)
        self.assertIn("✓ Write       Lezione 1 (pulito).md\n", out)
        self.assertEqual(self.calls("whisper"), [])
        self.assertEqual(sorted(p.name for p in self.run_dirs()[0].iterdir()), ["clean-claude-stderr.txt", "clean-reply.md"])
        self.assertEqual(len(list(self.note.parent.iterdir())), 2, "nothing else may appear in the vault")

    def test_clean_command_line(self):
        self.assertEqual(self.rec2notes("clean", self.note, "--effort", "low", "--claude-model", "opus")[0], 0)
        self.assertEqual(self.calls("claude"), [["-p", "--effort", "low", "--tools", "", "--no-session-persistence",
                                                 "--system-prompt-file", str(paths.CLEAN_PROMPT), "--model", "opus"]])

    def test_clean_command_refuses_when_the_sibling_exists_unless_forced(self):
        self.cleaned.write_text("versione precedente\n", encoding="utf-8")
        code, _, err = self.rec2notes("clean", self.note)
        self.assertEqual((code, self.calls("claude")), (1, []))
        self.assertIn("--force", err)
        self.assertEqual(self.cleaned.read_text(encoding="utf-8"), "versione precedente\n")
        self.assertEqual(self.rec2notes("clean", self.note, "--force")[0], 0)
        self.assertTrue(self.cleaned.read_text(encoding="utf-8").startswith("Pulito."))
        self.assertEqual((self.run_dirs()[0] / self.cleaned.name).read_text(encoding="utf-8"), "versione precedente\n")

    def test_clean_command_needs_an_existing_note(self):
        code, _, err = self.rec2notes("clean", self.tmp / "nope.md")
        self.assertEqual(code, 1)
        self.assertIn("note not found", err)

    def test_a_failed_clean_writes_nothing_to_the_vault(self):
        os.environ["FAKE_CLAUDE_MODE"] = "fail"
        code, _, err = self.rec2notes("clean", self.note)
        self.assertEqual(code, 1)
        self.assertIn("claude exited with status 1 twice", err)
        self.assertFalse(self.cleaned.exists())

    def test_clean_flag_merges_the_cleaned_copy(self):
        original = self.note.read_bytes()
        code, out, err = self.rec2notes(self.note, self.audio, "--clean")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.note.read_bytes(), original)
        self.assertTrue(self.cleaned.read_text(encoding="utf-8").startswith("Pulito."))
        merged = self.note.with_name("Lezione 1 (pulito) (completo).md")
        self.assertIn("Aggiunta dal transcript.", merged.read_text(encoding="utf-8"))
        self.assertFalse(self.output.exists())
        self.assertLess(out.index("✓ Clean"), out.index("✓ Transcribe"))
        self.assertIn("✓ Write       Lezione 1 (pulito) (completo).md\n", out)
        clean_call, merge_call = self.calls("claude")
        self.assertEqual(clean_call[clean_call.index("--system-prompt-file") + 1], str(paths.CLEAN_PROMPT))
        self.assertEqual(merge_call[merge_call.index("--system-prompt-file") + 1], str(paths.MERGE_PROMPT))
        self.assertIn("Pulito.", (self.run_dirs()[0] / "input.txt").read_text(encoding="utf-8"))  # the merge got the cleaned text
        self.assertIn("reply.md", [p.name for p in self.run_dirs()[0].iterdir()])

    def test_clean_flag_refuses_before_any_call_when_a_sibling_exists(self):
        self.cleaned.write_text("versione precedente\n", encoding="utf-8")
        code, _, err = self.rec2notes(self.note, self.audio, "--clean")
        self.assertEqual((code, self.calls("claude") + self.calls("whisper")), (1, []))
        self.assertIn("(pulito).md already exists", err)

    def test_clean_flag_dry_run_says_it_does_not_clean(self):
        code, out, err = self.rec2notes(self.note, self.audio, "--clean", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("would first clean the note", err)
        self.assertNotIn("Pulito.", out)
        self.assertFalse(self.cleaned.exists())
        self.assertEqual(self.calls("claude"), [])


class ReplyChecks(Sandbox):
    def test_retries_once_on_an_empty_reply(self):
        os.environ["FAKE_CLAUDE_MODE"] = "empty-then-good"
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        self.assertIn("⚠ Merge       claude returned an empty reply; retrying once", out)
        self.assertEqual(len(self.calls("claude")), 2)
        self.assertTrue(self.output.exists())

    def test_fails_after_a_second_bad_reply_and_keeps_it(self):
        os.environ["FAKE_CLAUDE_MODE"] = "fail"
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 1)
        self.assertIn("exited with status 1 twice", err)
        self.assertIn("✗ Merge       claude · effort high", out)
        self.assertIn("fake claude: something went wrong", err)
        self.assertEqual(len(self.calls("claude")), 2)
        self.assertFalse(self.output.exists())
        self.assertTrue((self.run_dirs()[0] / "reply.md").exists())

    def test_dropped_words_warn_but_the_output_is_still_written(self):
        os.environ["FAKE_CLAUDE_MODE"] = "drop"
        os.environ["FAKE_CLAUDE_DROP"] = "in modo isolato"
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        self.assertTrue(self.output.exists())
        self.assertIn("⚠ Check       1 passage missing or changed", out)
        self.assertIn("  Changed        «in modo isolato» is missing", out)
        self.assertIn("note:  «in modo isolato»", (self.run_dirs()[0] / "check.txt").read_text(encoding="utf-8"))


class Create(Sandbox):
    def setUp(self):
        super().setUp()
        self.created = self.note.with_name("Lezione 2.md")

    def test_writes_the_new_note_and_nothing_else(self):
        code, out, err = self.rec2notes("create", self.created, self.audio)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.created.read_text(encoding="utf-8"), "## Appunti di Reti di calcolatori\n\nNota creata dal transcript.\n")
        self.assertEqual(out.splitlines()[:2], ["rec2notes · Reti di calcolatori", "Lezione 2.md"])
        self.assertIn("✓ Create      claude · effort high · length 20%", out)
        self.assertIn("✓ Write       Lezione 2.md\n", out)
        self.assertRegex(out, r"\n  Length +10 words, 167% of the transcript \(aimed at 20%\)\n  Run +")
        self.assertEqual(sorted(p.name for p in self.note.parent.iterdir()), ["Lezione 1.md", "Lezione 2.md"])
        self.assertEqual(sorted(p.name for p in self.run_dirs()[0].iterdir()),
                         ["claude-stderr.txt", "input.txt", "p1.json", "reply.md", "whisper-args.txt", "whisper.log"])

    def test_claude_command_line_and_input(self):
        self.assertEqual(self.rec2notes("create", self.created, self.audio, "--length", "10", "--effort", "max")[0], 0)
        self.assertEqual(self.calls("claude"), [["-p", "--effort", "max", "--tools", "", "--no-session-persistence",
                                                 "--system-prompt-file", str(paths.CREATE_PROMPT)]])
        sent = (self.run_dirs()[0] / "input.txt").read_text(encoding="utf-8")
        self.assertRegex(sent, r"^<course>\nname: Reti di calcolatori\nvocab: .*\n</course>\n\n"
                               r"<length>\ntranscript: [\d,]+ words\ntarget: about [\d,]+ words\n</length>\n\n<transcript>\n\[")
        self.assertNotIn("<notes>", sent)

    def test_the_target_is_a_share_of_the_spoken_words(self):
        self.assertEqual(cli.spoken_words("[00:00:01] uno due\n[p2 00:00:05] tre\n"), 3)
        self.assertEqual(cli.target_words(13_214, 20), 2_600)
        self.assertEqual(cli.target_words(80, 10), 100)

    def test_md_is_added_to_a_name_without_it(self):
        self.assertEqual(self.rec2notes("create", self.note.with_name("Lezione 2"), self.audio)[0], 0)
        self.assertTrue(self.created.exists())

    def test_refuses_an_existing_note_unless_forced(self):
        self.created.write_text("versione precedente\n", encoding="utf-8")
        code, _, err = self.rec2notes("create", self.created, self.audio)
        self.assertEqual(code, 1)
        self.assertIn("--force", err)
        self.assertEqual(self.calls("claude") + self.calls("whisper"), [])
        self.assertEqual(self.rec2notes("create", self.created, self.audio, "--force")[0], 0)
        self.assertIn("Nota creata", self.created.read_text(encoding="utf-8"))
        self.assertEqual((self.run_dirs()[0] / self.created.name).read_text(encoding="utf-8"), "versione precedente\n")

    def test_the_course_comes_from_the_folder_or_the_flag(self):
        elsewhere = self.tmp / "vault" / "Altro"
        elsewhere.mkdir()
        code, _, err = self.rec2notes("create", elsewhere / "Lezione.md", self.audio)
        self.assertEqual(code, 1)
        self.assertIn("could not tell the course", err)
        self.assertEqual(self.rec2notes("create", elsewhere / "Lezione.md", self.audio, "--course", "analisi")[0], 0)
        self.assertIn("Analisi", (elsewhere / "Lezione.md").read_text(encoding="utf-8"))

    def test_the_folder_must_exist(self):
        code, _, err = self.rec2notes("create", self.tmp / "nope" / "Lezione.md", self.audio)
        self.assertEqual(code, 1)
        self.assertIn("the note's folder does not exist", err)
        self.assertFalse((self.tmp / "nope").exists())

    def test_length_must_be_a_sensible_percentage(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["create", str(self.created), str(self.audio), "--length", "80"])

    def test_a_failed_call_writes_nothing_to_the_vault(self):
        os.environ["FAKE_CLAUDE_MODE"] = "fail"
        code, _, err = self.rec2notes("create", self.created, self.audio)
        self.assertEqual(code, 1)
        self.assertIn("claude exited with status 1 twice", err)
        self.assertFalse(self.created.exists())

    def test_dry_run_prints_the_input_and_runs_nothing(self):
        cache = paths.transcript_cache(MODEL, transcribe.sha256_file(self.audio))
        cache.parent.mkdir(parents=True)
        cache.write_text((FIXTURES / "transcript.txt").read_text(encoding="utf-8"), encoding="utf-8")
        code, out, err = self.rec2notes("create", self.created, self.audio, "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("<length>\ntranscript: ", out)
        self.assertIn("<transcript>\n[00:00:05] buongiorno", out)
        self.assertIn(f"--system-prompt-file {shlex.quote(str(paths.CREATE_PROMPT))}", err)
        self.assertEqual(self.calls("claude") + self.calls("whisper"), [])
        self.assertFalse(self.created.exists())
        self.assertEqual(self.run_dirs(), [])


class DryRun(Sandbox):
    def test_prints_the_input_and_runs_nothing(self):
        cache = paths.transcript_cache(MODEL, transcribe.sha256_file(self.audio))
        cache.parent.mkdir(parents=True)
        cache.write_text((FIXTURES / "transcript.txt").read_text(encoding="utf-8"), encoding="utf-8")
        code, out, err = self.rec2notes(self.note, self.audio, "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith("<course>\nname: Reti di calcolatori\nvocab: Lezione di reti."))
        self.assertIn(f"<notes>\n{self.note.read_text(encoding="utf-8")}</notes>\n\n<transcript>\n[00:00:05] buongiorno", out)
        self.assertIn("claude -p --effort high", err)
        self.assertEqual(self.calls("claude") + self.calls("whisper"), [])
        self.assertFalse(self.output.exists())
        self.assertEqual(self.run_dirs(), [])

    def test_uncached_audio_is_a_placeholder(self):
        code, out, err = self.rec2notes(self.note, self.audio, "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("(not transcribed yet: lezione.m4a)", out)
        self.assertEqual(self.calls("whisper"), [])


@unittest.skipIf(paths.WINDOWS, "the fakes can't send SIGTERM on Windows: os.kill ends the process there")
class Stopping(Sandbox):
    def test_sigterm_stops_whisper_deletes_the_audio_and_marks_the_step_failed(self):
        os.environ["FAKE_WHISPER_STOP_PARENT"] = "1"
        handler_before = signal.getsignal(signal.SIGTERM)
        start = time.monotonic()
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertLess(time.monotonic() - start, 10, "whisper must be stopped, not waited for")
        with self.assertRaises(ProcessLookupError, msg="whisper is still running"):
            os.kill(int((self.tmp / "whisper.log.pid").read_text(encoding="utf-8")), 0)
        self.assertEqual(code, 128 + signal.SIGTERM)
        self.assertIn("rec2notes: stopped by SIGTERM", err)
        self.assertIn(f"✗ Transcribe  lezione.m4a · {MODEL}", out)
        self.assertEqual([p.name for p in self.run_dirs()[0].iterdir() if p.suffix == ".wav"], [])
        self.assertEqual(self.calls("claude"), [])
        self.assertFalse(self.output.exists())
        self.assertIs(signal.getsignal(signal.SIGTERM), handler_before)

    def test_sigterm_during_the_merge_stops_claude_without_a_retry(self):
        os.environ["FAKE_CLAUDE_MODE"] = "stop-parent"
        start = time.monotonic()
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertLess(time.monotonic() - start, 10, "claude must be stopped, not waited for")
        self.assertEqual(code, 128 + signal.SIGTERM)
        self.assertIn("✗ Merge       claude · effort high", out)
        self.assertNotIn("retrying", out)
        self.assertEqual(len(self.calls("claude")), 1)
        self.assertFalse(self.output.exists())


class CourseCommand(Sandbox):
    def add(self, *extra, slug="fisica"):
        return self.rec2notes("course", "add", slug, "Fisica 1", "Lezione di fisica.", *extra)

    def listed(self):
        return self.rec2notes("course", "list")[1]

    def test_add_then_list(self):
        folder = self.tmp / "vault" / "Fisica"
        folder.mkdir()
        code, _, _ = self.add("--folder", folder)
        self.assertEqual(code, 0)
        out = self.listed()
        self.assertIn(f"Fisica 1  [{str(folder).lstrip('/')}]", out)
        self.assertIn("net", out)

    def edit(self, *args, slug="net"):
        return self.rec2notes("course", "edit", slug, *args)

    def test_edit_changes_name_vocab_and_folder(self):
        folder = self.tmp / "vault" / "Laboratorio"
        folder.mkdir()
        code, out, _ = self.edit("--name", "Reti", "--vocab", "Nuovo.", "--folder", folder)
        self.assertEqual(code, 0)
        self.assertIn("Updated course net", out)
        loaded = courses.load_courses()["net"]
        self.assertEqual((loaded.name, loaded.vocab), ("Reti", "Nuovo."))
        self.assertIn(f"Reti  [{str(folder).lstrip('/')}]", self.listed())

    def test_edit_renames_the_slug_and_keeps_its_folder(self):
        code, out, _ = self.edit("--slug", "reti", "--name", "Reti")
        self.assertEqual((code, "Updated course reti" in out), (0, True))
        listed = self.listed()
        self.assertIn("reti", listed)
        self.assertNotIn("net", listed)
        self.assertIn("Reti  [Reti]", listed)

    def test_a_taken_slug_edits_nothing(self):
        before = paths.courses_file().read_text(encoding="utf-8")
        code, _, err = self.edit("--slug", "analisi", "--name", "Reti")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)
        self.assertEqual(paths.courses_file().read_text(encoding="utf-8"), before)

    def test_edit_with_nothing_to_change_or_an_unknown_course_fails(self):
        self.assertIn("nothing to change", self.edit()[2])
        code, _, err = self.edit("--name", "X", slug="nope")
        self.assertEqual(code, 1)
        self.assertIn("unknown course 'nope'", err)

    def test_a_bad_folder_edits_nothing(self):
        before = paths.courses_file().read_text(encoding="utf-8")
        code, _, err = self.edit("--name", "Reti", "--folder", "7")
        self.assertEqual(code, 1)
        self.assertIn("not an absolute path", err)
        self.assertEqual(paths.courses_file().read_text(encoding="utf-8"), before)

    def test_adding_an_existing_course_fails(self):
        code, _, err = self.add(slug="net")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_a_folder_that_is_not_an_absolute_path_adds_nothing(self):
        code, _, err = self.add("--folder", "7")
        self.assertEqual(code, 1)
        self.assertIn("not an absolute path", err)
        self.assertNotIn("fisica", self.listed())

    def test_a_missing_folder_fails_unless_create_is_given(self):
        folder = self.tmp / "vault" / "Nuova" / "Fisica"
        code, _, err = self.add("--folder", folder)
        self.assertEqual(code, 1)
        self.assertIn("--create", err)
        self.assertFalse(folder.exists())
        self.assertNotIn("fisica", self.listed())
        self.assertEqual(self.add("--folder", folder, "--create")[0], 0)
        self.assertTrue(folder.is_dir())
        self.assertIn("fisica", self.listed())

    def test_a_folder_overlapping_another_courses_folder_adds_nothing(self):
        outer = self.tmp / "Docs"
        inner = outer / "Reti"
        inner.mkdir(parents=True)
        self.write_folders(f"net = '{inner}'\n")
        code, _, err = self.add("--folder", outer)
        self.assertEqual(code, 1)
        self.assertIn("overlaps the folder of net", err)
        self.assertNotIn("fisica", self.listed())


class TranscriptSize(Sandbox):
    def test_the_size_is_shown_before_the_merge(self):
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"✓ Transcript  \d+ words\n")
        self.assertLess(out.index("Transcript "), out.index("Merge "))

    def test_an_unusually_long_transcript_warns_and_goes_on_off_a_terminal(self):
        from rec2notes import cli
        cli.LONG_TRANSCRIPT_WORDS = 1
        self.addCleanup(setattr, cli, "LONG_TRANSCRIPT_WORDS", 30_000)
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"⚠ Transcript  \d+ words, unusually long")
        self.assertIn("✓ Merge", out)
