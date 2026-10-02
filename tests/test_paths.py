import os
import shutil
import unittest
from pathlib import Path
from unittest import mock

from rec2notes import Abort, paths

from .helpers import Sandbox


class PointerLocation(unittest.TestCase):
    def config_dir(self, windows, **env):
        with mock.patch.object(paths, "WINDOWS", windows), mock.patch.dict(os.environ, env), \
                mock.patch.object(Path, "home", return_value=Path("/home/u")):
            if "XDG_CONFIG_HOME" not in env:
                os.environ.pop("XDG_CONFIG_HOME", None)
            return paths.config_dir()

    def test_linux_uses_the_xdg_default(self):
        self.assertEqual(self.config_dir(False), Path("/home/u/.config/uni-rec2notes"))

    def test_windows_uses_appdata(self):
        self.assertEqual(self.config_dir(True, APPDATA="/r"), Path("/r/uni-rec2notes"))

    def test_an_absolute_xdg_variable_wins_everywhere(self):
        env = dict(XDG_CONFIG_HOME=os.path.abspath("c"), APPDATA="/r")
        self.assertEqual(self.config_dir(True, **env), Path(os.path.abspath("c")) / "uni-rec2notes")
        self.assertEqual(self.config_dir(False, **env), Path(os.path.abspath("c")) / "uni-rec2notes")

    def test_a_relative_xdg_variable_is_ignored(self):
        self.assertEqual(self.config_dir(False, XDG_CONFIG_HOME="rel"), Path("/home/u/.config/uni-rec2notes"))


class Folder(Sandbox):
    def test_everything_lives_in_the_folder(self):
        self.assertEqual(paths.courses_file(), self.folder / "courses.toml")
        self.assertEqual(paths.folders_file(), self.folder / "folders.toml")
        self.assertEqual(paths.whisper_model("large-v3"), self.folder / "whisper.cpp" / "models" / "ggml-large-v3.bin")
        self.assertEqual(paths.runs_dir(), self.folder / "cache" / "runs")
        self.assertEqual(paths.transcript_cache("m", "abc"), self.folder / "cache" / "transcripts" / "m" / "abc.txt")

    def test_the_pointer_keeps_backslashes_in_single_quotes(self):
        folder = Path(r"C:\Users\x\rec2notes" if paths.WINDOWS else r"/a/b\n")  # a basic string would read \n
        paths.write_pointer(folder)
        self.assertEqual(paths.pointer_file().read_text(encoding="utf-8"), f"folder = '{folder}'\n")
        self.assertEqual(paths.pointed_folder(), folder)

    def test_a_folder_with_a_quote_round_trips(self):
        folder = self.tmp / "l'uni"
        paths.write_pointer(folder)
        self.assertEqual(paths.pointed_folder(), folder)

    def test_no_pointer_says_to_run_setup(self):
        paths.pointer_file().unlink()
        self.assertIsNone(paths.pointed_folder())
        with self.assertRaisesRegex(Abort, "run `rec2notes setup` first"):
            paths.courses_file()

    def test_a_missing_folder_stops_and_is_not_created(self):
        shutil.rmtree(self.folder)
        with self.assertRaisesRegex(Abort, "is missing"):
            paths.runs_dir()
        self.assertFalse(self.folder.exists())

    def test_a_relative_folder_in_the_pointer_is_refused(self):
        paths.pointer_file().write_text("folder = 'rec2notes'\n", encoding="utf-8")
        with self.assertRaisesRegex(Abort, "must be the absolute path"):
            paths.folder()


class WhisperModelChoice(Sandbox):
    def test_without_a_saved_model_it_is_the_platform_default(self):
        self.assertIsNone(paths.saved_settings().get("whisper_model"))
        self.assertEqual(paths.whisper_model_choice(), paths.default_whisper_model())

    def test_the_saved_model_is_used(self):
        paths.save_setting("whisper_model", "large-v3-turbo-q5_0")
        self.assertEqual(paths.settings_file().read_text(encoding="utf-8"), 'whisper_model = "large-v3-turbo-q5_0"\n')
        self.assertEqual(paths.whisper_model_choice(), "large-v3-turbo-q5_0")

    def test_the_environment_beats_the_saved_model(self):
        paths.save_setting("whisper_model", "large-v3-turbo-q5_0")
        os.environ["REC2NOTES_WHISPER_MODEL"] = "tiny"
        self.assertEqual(paths.whisper_model_choice(), "tiny")

    def test_saving_one_setting_keeps_the_others_and_none_removes_it(self):
        paths.save_setting("whisper_model", "large-v3-turbo")
        paths.save_setting("effort", "max")
        paths.save_setting("claude_model", "opus")
        paths.save_setting("claude_model", None)
        self.assertEqual(paths.settings_file().read_text(encoding="utf-8"),
                         'whisper_model = "large-v3-turbo"\neffort = "max"\n')
        self.assertEqual(paths.saved_settings(), {"whisper_model": "large-v3-turbo", "effort": "max"})

    def test_no_pointer_or_folder_means_no_saved_model(self):
        shutil.rmtree(self.folder)
        self.assertIsNone(paths.saved_settings().get("whisper_model"))
        paths.pointer_file().unlink()
        self.assertIsNone(paths.saved_settings().get("whisper_model"))

    def test_a_broken_settings_file_is_named(self):
        for text, error in (("whisper_model = \n", "settings.toml: "), ("whisper_model = 3\n", "`whisper_model` must be text")):
            (self.folder / "settings.toml").write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(Abort, error):
                paths.whisper_model_choice()
