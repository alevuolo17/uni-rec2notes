import contextlib
import importlib.metadata
import io
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import rec2notes
from rec2notes import cli, courses, merge, paths, setup, transcribe, verify

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
        self.assertNotIn(b"\r\n", self.output.read_bytes())  # Windows' text mode would write CRLF

    def test_a_run_in_italian_keeps_the_columns_aligned(self):
        paths.save_setting("language", "it")
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        self.assertIn(f"✓ {'Trascrizione':<12} 9s di audio · {MODEL}", out)
        self.assertIn(f"✓ {'Unione':<12} claude · effort high", out)
        self.assertIn(f"✓ {'Controllo':<12} solo aggiunte\n", out)
        self.assertIn(f"✓ {'Scrittura':<12} Lezione 1 (completo).md\n", out)
        self.assertIn(f"\n  {'Conflitti':<15} 1\n  {'Argomenti persi':<15} sì\n  {'Esecuzione':<15} ", out)
        self.assertIn("[^conflitto-1]", self.output.read_text(encoding="utf-8"))  # the note keeps its Italian markers

    def test_the_help_is_in_the_saved_language(self):
        paths.save_setting("language", "it")
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(["--help"])
        self.assertIn("Completa degli appunti di lezione formattati", out.getvalue())
        self.assertIn("`rec2notes` → Impostazioni", out.getvalue().replace("\n", " "))

    def test_runs_use_the_model_setup_saved(self):
        paths.save_setting("whisper_model", "tiny")
        self.add_model("tiny")
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        self.assertIn("✓ Transcribe  9s of audio · tiny", out)

    def test_a_broken_settings_file_is_an_error_not_a_crash(self):
        paths.settings_file().write_text("whisper_model = \n", encoding="utf-8")
        code, _, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 1)
        self.assertIn(f"rec2notes: {paths.settings_file()}: ", err)

    def test_bare_command_prints_the_help_without_banner_off_a_terminal(self):
        code, out, err = self.rec2notes()
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("usage: rec2notes"))
        self.assertNotIn("█", out)

    def test_the_installed_command_runs_the_cli(self):
        result = subprocess.run([sys.executable, "-m", "rec2notes"], cwd=TESTS.parent, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("usage: rec2notes"))

    def test_version_prints_the_installed_version(self):
        out = io.StringIO()
        with mock.patch("importlib.metadata.version", return_value="9.9.9"), \
                self.assertRaises(SystemExit) as exit, contextlib.redirect_stdout(out):
            cli.main(["--version"])
        self.assertEqual((exit.exception.code, out.getvalue()), (0, "rec2notes 9.9.9\n"))

    def test_version_without_package_metadata_says_it_is_not_installed(self):
        with mock.patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
            self.assertEqual(rec2notes.version(), "unknown (not installed)")

    def test_setup_is_a_subcommand(self):
        with mock.patch.object(setup, "main", return_value=0) as main:
            self.assertEqual(self.rec2notes("setup", "--backend", "cpu")[0], 0)
        main.assert_called_once_with(["--backend", "cpu"])

    def test_claude_command_line(self):
        self.assertEqual(self.rec2notes(self.note, self.audio)[0], 0)
        self.assertEqual(self.calls("claude"), [["-p", "--effort", "high", "--tools", "", "--strict-mcp-config",
                                                 "--no-session-persistence",
                                                 "--system-prompt-file", str(paths.MERGE_PROMPT)]])

    def test_claude_model_and_effort_can_be_overridden(self):
        os.environ["REC2NOTES_CLAUDE_MODEL"] = "opus"
        self.assertEqual(self.rec2notes(self.note, self.audio, "--effort", "max")[0], 0)
        (args,) = self.calls("claude")
        self.assertEqual(args[args.index("--effort") + 1], "max")
        self.assertEqual(args[args.index("--model") + 1], "opus")

    def test_the_saved_claude_model_and_effort_are_used_unless_overridden(self):
        paths.save_setting("effort", "low")
        paths.save_setting("claude_model", "haiku")
        args = cli.parse_args([str(self.note), str(self.audio)])
        self.assertEqual((args.effort, args.model), ("low", "haiku"))
        os.environ["REC2NOTES_EFFORT"] = "medium"
        self.assertEqual(cli.parse_args([str(self.note), str(self.audio)]).effort, "medium")
        self.assertEqual(cli.parse_args([str(self.note), str(self.audio), "--effort", "max"]).effort, "max")

    def test_without_saved_settings_the_effort_is_high_and_the_model_claude_codes(self):
        args = cli.parse_args([str(self.note), str(self.audio)])
        self.assertEqual((args.effort, args.model), ("high", None))

    def test_a_bad_saved_effort_is_refused(self):
        paths.save_setting("effort", "huge")
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            cli.parse_args([str(self.note), str(self.audio)])
        self.assertIn("the effort must be one of low, medium, high, xhigh, max, not 'huge'", err.getvalue())

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
        self.assertEqual(self.calls("claude"), [["-p", "--effort", "low", "--tools", "", "--strict-mcp-config",
                                                 "--no-session-persistence",
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
        self.assertEqual(self.calls("claude"), [["-p", "--effort", "max", "--tools", "", "--strict-mcp-config",
                                                 "--no-session-persistence",
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
        cache = self.transcript_cache()
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


class Antigravity(Sandbox):
    AGY_ARGS = ["-p=", "--input-format", "stream-json", "--output-format", "stream-json", "--disable-slash-commands",
                "--print-timeout", "12h", "--model"]

    def setUp(self):
        super().setUp()
        paths.save_setting("antigravity_consent", "yes")

    def test_merges_in_a_throwaway_home_holding_only_its_settings_and_deletes_it(self):
        code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity")
        self.assertEqual(code, 0, err)
        self.assertIn("Aggiunta dal transcript.[^conflitto-1]", self.output.read_text(encoding="utf-8"))
        self.assertIn(f"✓ Merge       antigravity {paths.ANTIGRAVITY_MODEL}", out)
        self.assertNotIn("⚠", out)
        run_dir = self.run_dirs()[0]
        self.assertEqual(sorted(p.name for p in run_dir.iterdir()),
                         ["agy-events.jsonl", "agy-stderr.txt", "check.txt", "input.txt", "p1.json", "reply.md",
                          "whisper-args.txt", "whisper.log"])
        (call,) = self.calls("agy")
        self.assertEqual(call["args"], [*self.AGY_ARGS, paths.ANTIGRAVITY_MODEL])
        self.assertEqual((Path(call["home"]).name, Path(call["home"]).parent.name), ("agy-home", run_dir.name))
        self.assertEqual((Path(call["cwd"]).name, call["cwd_files"]), ("agy-work", []))
        self.assertEqual(call["settings"], merge.AGY_SETTINGS)
        self.assertEqual(call["xdg"], [], "no XDG folder may lead agy to the real ones")
        self.assertFalse((self.home / ".gemini").exists())

    def test_a_used_tool_is_warned_about_and_the_note_still_written(self):
        os.environ["FAKE_AGY_MODE"] = "search"
        code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.output.exists())
        self.assertIn("⚠ Merge       the agent used search_web: check the note", out)
        self.assertIn("  Tools          the agent used search_web\n", out)

    def test_a_denied_tool_twice_stops_the_run_and_names_the_tool(self):
        os.environ["FAKE_AGY_MODE"] = "denied"
        code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity")
        self.assertEqual(code, 1)
        self.assertIn("agy returned an empty reply twice", err)
        self.assertIn("It tried to use read_file, which rec2notes blocks", err)
        self.assertFalse(self.output.exists())
        self.assertEqual(len(self.calls("agy")), 2)
        self.assertFalse((self.run_dirs()[0] / "agy-home").exists())

    def test_a_failed_call_deletes_the_home_too(self):
        os.environ["FAKE_AGY_MODE"] = "fail"
        code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity")
        self.assertEqual(code, 1)
        self.assertIn("fake agy: something went wrong", err)
        self.assertEqual([p.name for p in self.run_dirs()[0].iterdir() if p.name.startswith("agy-")],
                         ["agy-events.jsonl", "agy-stderr.txt"])

    def test_deleting_the_home_tries_again_while_a_file_in_it_is_locked(self):
        rmtree = shutil.rmtree
        locked = iter([True, True])  # Windows: agy's background updater holds a lock for a moment after agy exits

        def flaky(path, *args, **kwargs):
            if next(locked, False):
                raise PermissionError(13, "in use")
            rmtree(path, *args, **kwargs)
        with mock.patch.object(merge.shutil, "rmtree", flaky), mock.patch.object(merge.time, "sleep") as slept:
            code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity")
        self.assertEqual(code, 0, err)
        self.assertEqual(slept.call_count, 2)
        self.assertFalse((self.run_dirs()[0] / "agy-home").exists())
        self.assertNotIn("could not delete", out + err)

    def test_a_home_that_stays_locked_is_named_and_listing_models_still_works(self):
        with (mock.patch.object(merge.shutil, "rmtree", side_effect=PermissionError(13, "in use")),
              mock.patch.object(merge.time, "sleep"),
              mock.patch.object(merge.tempfile, "tempdir", str(self.tmp))):  # the Sandbox deletes what stays
            self.assertIn(paths.ANTIGRAVITY_MODEL, merge.antigravity_models())
            code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity")
        self.assertEqual(code, 0, err)
        self.assertIn(f"could not delete {self.run_dirs()[0] / 'agy-home'}, which may hold a copy of the conversation", out + err)

    def test_a_signed_out_agy_stops_the_run_before_transcribing(self):
        os.environ["FAKE_AGY_MODE"] = "hang"
        with mock.patch.object(merge, "AGY_TIMEOUT", 1):
            code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity")
        self.assertEqual(code, 1)
        self.assertIn("agy did not answer in 1 s: not signed in, or no network; run `agy` once and sign in", err)
        self.assertEqual(self.calls("agy") + self.calls("whisper"), [])

    def test_a_model_the_account_lacks_stops_the_run(self):
        code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity", "--model", "nope")
        self.assertEqual(code, 1)
        self.assertIn("Antigravity has no model 'nope'; yours: gemini-3.8-flash-high, gemini-3.8-flash-low", err)
        self.assertEqual(self.calls("whisper"), [])

    def test_the_effort_is_ignored_with_a_note(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            args = cli.parse_args([str(self.note), str(self.audio), "--agent", "antigravity", "--effort", "max"])
        self.assertIsNone(args.effort)
        self.assertIn("--effort is for claude; Antigravity's model names carry the effort", err.getvalue())

    def test_agent_and_model_precedence(self):
        parse = lambda *extra: cli.parse_args([str(self.note), str(self.audio), *extra])
        paths.save_setting("antigravity_model", "gemini-3.8-flash-low")
        os.environ["REC2NOTES_AGENT"] = "antigravity"
        self.assertEqual((parse().agent, parse().model), ("antigravity", "gemini-3.8-flash-low"))
        os.environ["REC2NOTES_MODEL"] = "from-env"
        self.assertEqual(parse().model, "from-env")
        self.assertEqual(parse("--model", "flag").model, "flag")
        os.environ["REC2NOTES_CLAUDE_MODEL"] = "sonnet"
        self.assertEqual((parse("--agent", "claude").agent, parse("--agent", "claude").model), ("claude", "from-env"))
        del os.environ["REC2NOTES_MODEL"]
        self.assertEqual(parse("--agent", "claude").model, "sonnet")
        self.assertEqual(parse("--agent", "claude", "--claude-model", "opus").model, "opus")

    def test_an_unknown_agent_is_refused(self):
        os.environ["REC2NOTES_AGENT"] = "gpt"
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):
            cli.parse_args([str(self.note), str(self.audio)])
        self.assertIn("the agent must be one of claude, antigravity, not 'gpt'", err.getvalue())

    def test_clean_and_create(self):
        code, out, err = self.rec2notes("clean", self.note, "--agent", "antigravity")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.note.with_name("Lezione 1 (pulito).md").read_text(encoding="utf-8").startswith("Pulito."))
        self.assertEqual(sorted(p.name for p in self.run_dirs()[0].iterdir()),
                         ["clean-agy-events.jsonl", "clean-agy-stderr.txt", "clean-reply.md"])
        created = self.note.with_name("Lezione 2.md")
        code, out, err = self.rec2notes("create", created, self.audio, "--agent", "antigravity")
        self.assertEqual(code, 0, err)
        self.assertEqual(created.read_text(encoding="utf-8"), "## Appunti di Reti di calcolatori\n\nNota creata dal transcript.\n")
        self.assertEqual([c["args"][-1] for c in self.calls("agy")], [paths.ANTIGRAVITY_MODEL] * 2)

    def test_dry_run_shows_the_command_and_runs_nothing(self):
        code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn(f"would pipe this to: agy -p= {shlex.join(self.AGY_ARGS[1:])} {paths.ANTIGRAVITY_MODEL}", err)
        self.assertIn("with the prompt merge.md at its top", err)
        self.assertEqual(self.calls("agy"), [])


class AntigravityConsent(Sandbox):
    def run_on_terminal(self, answer):
        with (mock.patch.object(cli, "on_terminal", return_value=True),
              mock.patch("builtins.input", **{"side_effect" if isinstance(answer, BaseException) else "return_value": answer}) as asked):
            code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity")
        return code, out, err, asked

    def test_off_a_terminal_the_first_run_stops_before_sending_anything(self):
        code, out, err = self.rec2notes(self.note, self.audio, "--agent", "antigravity")
        self.assertEqual(code, 1)
        self.assertIn("Antigravity sends your note and transcript to Google: run rec2notes with --agent antigravity once in a terminal", err)
        self.assertEqual(self.calls("agy") + self.calls("whisper"), [])

    def test_on_a_terminal_it_explains_and_a_yes_is_saved(self):
        code, out, err, asked = self.run_on_terminal("y")
        self.assertEqual(code, 0, err)
        self.assertIn("On personal accounts, those terms let Google use what you send to improve its models", out)
        self.assertEqual(paths.saved_settings()["antigravity_consent"], "yes")
        self.output.unlink()
        code, out, err, asked = self.run_on_terminal("n")  # saved: not asked again
        self.assertEqual(code, 0, err)
        asked.assert_not_called()

    def test_anything_but_yes_stops_and_saves_nothing(self):
        code, out, err, asked = self.run_on_terminal("")
        self.assertEqual(code, 1)
        self.assertIn("nothing was sent. To use Claude Code instead, pass --agent claude", err)
        self.assertNotIn("antigravity_consent", paths.saved_settings())
        self.assertEqual(self.calls("agy") + self.calls("whisper"), [])

    def test_ctrl_d_at_the_question_is_a_no(self):
        code, out, err, asked = self.run_on_terminal(EOFError())
        self.assertEqual(code, 1)
        self.assertIn("nothing was sent", err)
        self.assertNotIn("antigravity_consent", paths.saved_settings())

    def test_claude_and_dry_runs_never_ask(self):
        self.assertEqual(self.rec2notes(self.note, self.audio, "--agent", "antigravity", "--dry-run")[0], 0)
        self.assertEqual(self.rec2notes(self.note, self.audio)[0], 0)


class DryRun(Sandbox):
    def test_prints_the_input_and_runs_nothing(self):
        cache = self.transcript_cache()
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


class RunFolders(Sandbox):
    def test_a_run_deletes_the_run_folders_older_than_thirty_days_and_nothing_else(self):
        runs = paths.runs_dir()
        old = runs / f"{datetime.now() - timedelta(days=31):{cli.RUN_STAMP}}_Lezione 0"
        recent = runs / f"{datetime.now() - timedelta(days=29):{cli.RUN_STAMP}}_Lezione 0"
        stray = runs / "2000-01-01 not a run"
        for folder in (old, recent, stray):
            (folder / "sub").mkdir(parents=True)
            (folder / "sub" / "input.txt").write_text("appunti\n", encoding="utf-8")
        old_file = runs / f"{datetime.now() - timedelta(days=31):{cli.RUN_STAMP}}_file"
        old_file.write_text("x\n", encoding="utf-8")
        self.assertEqual(self.rec2notes(self.note, self.audio)[0], 0)
        self.assertFalse(old.exists())
        kept = set(self.run_dirs())
        self.assertLessEqual({recent, stray, old_file}, kept)
        self.assertEqual(len(kept), 4)  # and this run's

    def test_a_dry_run_deletes_nothing(self):
        old = paths.runs_dir() / f"{datetime.now() - timedelta(days=31):{cli.RUN_STAMP}}_Lezione 0"
        old.mkdir(parents=True)
        self.transcript_cache().parent.mkdir(parents=True)
        self.transcript_cache().write_text("[00:00:00] Lezione.\n", encoding="utf-8")
        self.assertEqual(self.rec2notes(self.note, self.audio, "--dry-run")[0], 0)
        self.assertTrue(old.exists())


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
        self.enterContext(mock.patch.object(transcribe, "LONG_TRANSCRIPT_WORDS", 1))
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"⚠ Transcript  \d+ words, unusually long")
        self.assertIn("✓ Merge", out)


class Verify(Sandbox):
    def setUp(self):
        super().setUp()
        self.slides = self.tmp / "slides" / "Lezione 1.pdf"
        self.slides.parent.mkdir()
        self.slides.write_bytes(b"%PDF-1.4\nfake slides\n%%EOF\n")

    def test_without_a_recording_it_checks_the_slides_only(self):
        original = self.note.read_text(encoding="utf-8")
        code, out, err = self.rec2notes("verify", self.note, self.slides)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.note.read_text(encoding="utf-8"),
                         f"{original.rstrip()}\n\n{verify.SECTION_HEADING}\n\n"
                         "- **Appunti:** «x» — **Correzione:** y — **Fonte:** slide 1: «z»\n")
        self.assertEqual(out.splitlines()[:2], ["rec2notes · Check against slides", "Lezione 1.md"])
        self.assertIn("✓ Slides      1 PDF · 3 pages · ≈ 5k tokens", out)
        self.assertEqual(sorted(p.name for p in self.run_dirs()[0].glob("*.png")),
                         ["slides-1-1.png", "slides-1-2.png", "slides-1-3.png"])
        self.assertIn("✓ Verify      claude · effort high", out)
        self.assertIn("✓ Write       Lezione 1.md\n", out)
        self.assertRegex(out, r"\n  Findings +1\n  Run +")
        self.assertNotIn("Transcribe", out)
        self.assertEqual(self.calls("whisper"), [])
        self.assertNotIn("<transcript>", (self.run_dirs()[0] / "verify-input.txt").read_text(encoding="utf-8"))
        self.assertEqual(sorted(p.name for p in self.note.parent.iterdir()), ["Lezione 1.md"])

    def test_with_a_recording_the_transcript_goes_too(self):
        code, out, err = self.rec2notes("verify", self.note, self.slides, self.audio)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines()[:2], ["rec2notes · Reti di calcolatori", "Lezione 1.md"])
        self.assertIn("✓ Transcribe", out)
        self.assertTrue(self.transcript_cache().exists())
        self.assertIn("</notes>\n\n<transcript>\n[", (self.run_dirs()[0] / "verify-input.txt").read_text(encoding="utf-8"))

    def test_it_runs_claude_whatever_agent_is_saved(self):
        os.environ["REC2NOTES_AGENT"] = "antigravity"
        code, _, err = self.rec2notes("verify", self.note, self.slides, "--model", "opus")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.calls("claude"), [merge.claude_command("high", "opus", paths.VERIFY_PROMPT)[1:]
                                                + merge.STREAM_JSON])
        self.assertEqual(self.calls("agy"), [])

    def test_a_page_range_after_the_pdf(self):
        code, out, err = self.rec2notes("verify", self.note, f"{self.slides}:2-3")
        self.assertEqual(code, 0, err)
        self.assertIn("✓ Slides      1 PDF · 2 pages", out)
        self.assertEqual(self.poppler_calls()[-1][:6], ["-f", "2", "-l", "3", "-scale-to", "960"])

    def test_ranges_are_parsed_only_after_a_pdf(self):
        self.assertEqual(verify.parse_slides("x.pdf:3"), verify.Slides(Path("x.pdf"), 3, 3))
        self.assertEqual(verify.parse_slides("C:\\Slides\\x.PDF:12-30"), verify.Slides(Path("C:\\Slides\\x.PDF"), 12, 30))
        self.assertEqual(verify.parse_slides("C:\\Slides\\x.pdf"), verify.Slides(Path("C:\\Slides\\x.pdf")))
        self.assertEqual(verify.parse_slides("lezione.m4a"), verify.Slides(Path("lezione.m4a")))

    def test_a_range_outside_the_pdf_stops_before_any_call(self):
        code, _, err = self.rec2notes("verify", self.note, f"{self.slides}:2-9")
        self.assertEqual(code, 1)
        self.assertIn("has 3 pages: pages 2-9 are not all in it", err)
        self.assertEqual(self.calls("claude"), [])
        self.assertEqual(self.run_dirs(), [])

    def test_over_100_pages_stops_before_any_call(self):
        self.slides.write_bytes(b"%PDF-1.4\npages 120\n")
        code, _, err = self.rec2notes("verify", self.note, self.slides)
        self.assertEqual(code, 1)
        self.assertIn("120 pages in all, over the 100", err)
        self.assertEqual(self.rec2notes("verify", self.note, f"{self.slides}:21-120")[0], 0)

    def test_without_poppler_it_says_how_to_install_it(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.tmp / "bin")}):
            code, _, err = self.rec2notes("verify", self.note, self.slides)
        self.assertEqual(code, 1)
        self.assertIn("needs poppler (pdfinfo and pdftoppm)", err)

    def poppler_calls(self):
        log = self.tmp / "poppler.log"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def test_slides_in_another_format_are_refused(self):
        pptx = self.tmp / "slides" / "Lezione 1.pptx"
        pptx.write_bytes(b"PK")
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            cli.parse_verify_args([str(self.note), str(pptx)])
        self.assertIn("export the slides to PDF first", err.getvalue())

    def test_at_least_one_pdf_is_needed(self):
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            cli.parse_verify_args([str(self.note), str(self.audio)])
        self.assertIn("at least one .pdf", err.getvalue())

    def test_a_missing_file_stops_before_any_call(self):
        code, _, err = self.rec2notes("verify", self.note, self.tmp / "nope.pdf")
        self.assertEqual(code, 1)
        self.assertIn("file not found", err)
        self.assertEqual(self.calls("claude"), [])

    def test_a_note_changed_during_the_run_is_left_alone(self):
        os.environ["FAKE_CLAUDE_TOUCH"] = str(self.note)
        code, out, err = self.rec2notes("verify", self.note, self.slides)
        self.assertEqual(code, 0, err)
        self.assertNotIn(verify.SECTION_HEADING, self.note.read_text(encoding="utf-8"))
        self.assertIn("⚠ Write       Lezione 1.md changed during the run, so it was left alone", out)
        self.assertIn(verify.SECTION_HEADING, (self.run_dirs()[0] / "Lezione 1.md").read_text(encoding="utf-8"))
