import contextlib
import io
import shutil
import unittest
from unittest import mock

from rec2notes import i18n, paths, uninstall
from tests.helpers import MODEL, Sandbox


class UninstallTest(Sandbox):
    def uninstall(self, *answers):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=list(answers)) as asked, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = uninstall.main([])
        return code, out.getvalue(), [call.args[0] for call in asked.call_args_list]

    def test_yes_deletes_the_folder_and_the_pointer_and_leaves_the_notes(self):
        cached = paths.transcript_cache(MODEL, "abc")
        cached.parent.mkdir(parents=True)
        cached.write_text("trascrizione\n", encoding="utf-8")
        paths.save_setting("whisper_model", MODEL)
        code, out, asked = self.uninstall("y")
        self.assertEqual((code, asked), (0, ["Delete them? [y/N] "]))
        for name in ("courses.toml", "folders.toml", "settings.toml", "whisper.cpp", "cache"):
            self.assertIn(str(self.folder / name), out)
        self.assertFalse(self.folder.exists())
        self.assertFalse(paths.pointer_file().exists())
        self.assertFalse(paths.pointer_file().parent.exists())
        self.assertTrue(self.note.is_file())
        self.assertIn("pipx uninstall uni-rec2notes", out)

    def test_in_italian_s_deletes_and_the_entries_are_described_in_italian(self):
        i18n.set_language("it")
        code, out, asked = self.uninstall("s")
        self.assertEqual((code, asked), (0, ["Eliminarli? [s/N] "]))
        self.assertIn("i tuoi corsi", out)
        self.assertIn("Eliminato.", out)
        self.assertFalse(self.folder.exists())

    def test_enter_or_no_deletes_nothing(self):
        for answer in ("", "n"):
            code, out, _ = self.uninstall(answer)
            self.assertEqual(code, 0)
            self.assertIn("Nothing deleted.", out)
            self.assertTrue(paths.courses_file().is_file())
            self.assertTrue(paths.whisper_model(MODEL).is_file())

    def test_ctrl_d_deletes_nothing(self):
        code, out, _ = self.uninstall(EOFError)
        self.assertEqual(code, 1)
        self.assertIn("no answer, nothing deleted", out)
        self.assertTrue(paths.courses_file().is_file())

    def test_other_files_in_the_folder_are_kept(self):
        own = self.folder / "my notes.txt"
        own.write_text("mine\n", encoding="utf-8")
        code, out, _ = self.uninstall("y")
        self.assertEqual(code, 0)
        self.assertEqual(list(self.folder.iterdir()), [own])
        self.assertNotIn(str(own), out.split("Delete them?")[0])
        self.assertIn(f"Kept {self.folder}", out)
        self.assertFalse(paths.pointer_file().exists())

    def test_a_missing_folder_still_deletes_the_pointer(self):
        shutil.rmtree(self.folder)
        code, out, _ = self.uninstall("y")
        self.assertEqual(code, 0)
        self.assertIn("already gone", out)
        self.assertFalse(paths.pointer_file().exists())
        self.assertFalse(self.folder.exists())

    def test_nothing_installed_asks_nothing(self):
        paths.pointer_file().unlink()
        code, out, asked = self.uninstall()
        self.assertEqual((code, asked), (0, []))
        self.assertIn("pipx uninstall uni-rec2notes", out)
        self.assertTrue(self.folder.is_dir())  # without the pointer rec2notes doesn't know it is its folder

    def test_the_command_line_runs_it(self):
        with mock.patch("builtins.input", return_value="n"):
            code, out, _ = self.rec2notes("uninstall")
        self.assertEqual(code, 0)
        self.assertIn("Nothing deleted.", out)


if __name__ == "__main__":
    unittest.main()
