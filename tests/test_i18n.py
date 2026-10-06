import ast
import os
import string
import unittest
from pathlib import Path
from unittest import mock

from rec2notes import i18n, paths
from rec2notes.messages import en, it

from .helpers import Sandbox


def fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


class Lookup(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.set_language, "en")
        catalogs = {"en": {"a": "Hello {name}", "b": "Only English"}, "it": {"a": "Ciao {name}"}}
        patch = mock.patch.dict(i18n._CATALOGS, catalogs)
        patch.start()
        self.addCleanup(patch.stop)

    def test_english_is_the_default(self):
        self.assertEqual(i18n.t("a", name="Ada"), "Hello Ada")

    def test_the_language_picks_the_catalog(self):
        i18n.set_language("it")
        self.assertEqual(i18n.t("a", name="Ada"), "Ciao Ada")

    def test_a_missing_translation_falls_back_to_english_then_to_the_key(self):
        i18n.set_language("it")
        self.assertEqual(i18n.t("b"), "Only English")
        self.assertEqual(i18n.t("nothing.here"), "nothing.here")

    def test_an_unknown_language_is_refused(self):
        with self.assertRaises(ValueError):
            i18n.set_language("fr")


class Setting(Sandbox):
    def setUp(self):
        super().setUp()
        self.addCleanup(i18n.set_language, "en")

    def test_english_without_a_setting(self):
        i18n.load()
        self.assertEqual(i18n.current(), "en")

    def test_the_saved_setting_is_used(self):
        paths.save_setting("language", "it")
        i18n.load()
        self.assertEqual(i18n.current(), "it")

    def test_the_environment_wins_over_the_saved_setting(self):
        paths.save_setting("language", "it")
        with mock.patch.dict(os.environ, {"REC2NOTES_LANGUAGE": "en"}):
            i18n.load()
        self.assertEqual(i18n.current(), "en")

    def test_a_broken_settings_file_means_english(self):
        paths.settings_file().write_text("language = [\n", encoding="utf-8")
        i18n.load()
        self.assertEqual(i18n.current(), "en")

    def test_every_command_loads_the_saved_language(self):
        paths.save_setting("language", "it")
        self.rec2notes("doctor")
        self.assertEqual(i18n.current(), "it")

    def test_a_language_we_do_not_have_means_english(self):
        with mock.patch.dict(os.environ, {"REC2NOTES_LANGUAGE": "fr"}):
            i18n.load()
        self.assertEqual(i18n.current(), "en")


class Catalogs(unittest.TestCase):
    def test_both_languages_have_the_same_keys_and_fields(self):
        self.assertEqual(en.MESSAGES.keys(), it.MESSAGES.keys())
        for key, text in en.MESSAGES.items():
            self.assertEqual(fields(text), fields(it.MESSAGES[key]), key)

    def test_every_key_the_code_asks_for_exists(self):
        """The first argument of t(...), or each branch of a conditional one; dynamic keys, built with an f-string, can't be checked."""
        def keys(node):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield node.value
            elif isinstance(node, ast.IfExp):
                yield from keys(node.body)
                yield from keys(node.orelse)

        used = set()
        for file in Path(i18n.__file__).parent.glob("*.py"):
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "t" and node.args:
                    used.update(keys(node.args[0]))
        self.assertTrue(used)
        self.assertEqual(used - en.MESSAGES.keys(), set())

    def test_a_path_hint_names_the_menu_entry_the_hub_shows(self):
        """`rec2notes` → 3 Courses points at the hub line "Courses: ...": renaming one must rename the other."""
        for language, catalog in (("en", en.MESSAGES), ("it", it.MESSAGES)):
            for key, hub in (("path.settings", "menu.hub.settings"), ("path.courses", "menu.hub.courses")):
                name = catalog[key].split("→ ")[1].removeprefix("3 ")
                self.assertTrue(catalog[hub].startswith(name + ":"), (language, key))


class Numbers(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.set_language, "en")

    def test_the_thousands_separator_follows_the_language(self):
        self.assertEqual(i18n.number(9807), "9,807")
        i18n.set_language("it")
        self.assertEqual(i18n.number(9807), "9.807")
        self.assertEqual(i18n.number(980), "980")
