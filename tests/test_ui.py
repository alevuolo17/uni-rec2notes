import io
import os
import re
import unittest
from unittest import mock

from rec2notes import cli, i18n, ui

from .helpers import Sandbox


class Terminal(io.StringIO):
    def isatty(self):
        return True


class Formatting(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(ui.duration(42), "42s")
        self.assertEqual(ui.duration(7 * 60 + 5), "7m 05s")
        self.assertEqual(ui.duration(2 * 3600 + 3 * 60 + 59), "2h 03m")

    def test_estimates_are_in_whole_minutes(self):
        self.assertEqual(ui.rough(20), "<1m")
        self.assertEqual(ui.rough(44 * 60 + 10), "44m")
        self.assertEqual(ui.rough(63 * 60), "1h 03m")


class LabelWidth(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.set_language, "en")

    def test_english_keeps_its_column_and_italian_widens_it_to_fit_every_step_label(self):
        self.assertEqual(ui.label_width(), ui.LABEL_WIDTH)
        i18n.set_language("it")
        self.assertGreater(ui.label_width(), ui.LABEL_WIDTH)
        self.assertGreaterEqual(ui.label_width(), max(map(len, i18n.labels("step."))))

    def test_the_summary_column_fits_its_longest_label(self):
        out = io.StringIO()
        ui.Console(out, io.StringIO()).summary([("Run", ["a"], ()), ("A label of eighteen", ["b"], ())])
        self.assertEqual(out.getvalue().splitlines(), ["  Run                 a", "  A label of eighteen b"])


@mock.patch.dict(os.environ, {"NO_COLOR": "1", "TERM": "xterm"})
class OnATerminal(unittest.TestCase):
    def test_step_is_animated_then_marked(self):
        out = Terminal()
        with ui.Console(out, out).step("Merge", "claude · effort high") as step:
            step.end("ok", "claude · effort high", "6m 30s")
        self.assertIn("\r⠋ Merge       claude · effort high            0s\x1b[K", out.getvalue())
        self.assertTrue(out.getvalue().endswith("\r\x1b[K✓ Merge       claude · effort high            6m 30s\n"))

    def test_progress_draws_a_bar_with_the_time_left(self):
        step = ui.Console(Terminal()).step("Transcribe", "lezione.m4a · large-v3")
        step.progress(0)
        step.progress(35)
        text = "".join(text for text, _ in step._live_segments("⠋"))
        self.assertRegex(text, r"^⠋ Transcribe  ━{7}╸─{12}  35% · ~<1m left \(\d\d:\d\d\)$")

    def test_an_exception_marks_the_step_failed(self):
        out = Terminal()
        with self.assertRaises(RuntimeError), ui.Console(out, out).step("Merge", "claude · effort high"):
            raise RuntimeError
        self.assertIn("✗ Merge       claude · effort high", out.getvalue())

    def test_live_line_is_cut_to_the_terminal_width(self):
        console = ui.Console(Terminal())
        self.assertEqual(console._fit([("abcdef", ()), ("ghij", ())], 8), "abcdefg…")

    def test_color_unless_no_color(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": ""}):
            self.assertEqual(ui.Console(Terminal()).style("✓", ui.GREEN), "\x1b[32m✓\x1b[0m")
        self.assertEqual(ui.Console(Terminal()).style("✓", ui.GREEN), "✓")


@mock.patch.dict(os.environ, {"TERM": "xterm", "COLORTERM": "truecolor", "NO_COLOR": ""})
class Banner(unittest.TestCase):
    def header(self, columns):
        out = Terminal()
        with mock.patch("shutil.get_terminal_size", return_value=os.terminal_size((columns, 24))):
            ui.Console(out).header("Reti di calcolatori", "Lezione 1.md")
        return out.getvalue()

    def test_wide_terminal_gets_the_banner_in_the_sunset_gradient(self):
        out = self.header(120)
        self.assertIn("\x1b[38;2;115;97;43m░\x1b[0m\x1b[38;2;255;215;95m██████", out)  # top row: yellow, darker shadow
        self.assertIn("\x1b[38;2;215;95;215m", out)  # bottom row: magenta
        self.assertIn("Reti di calcolatori", out)
        self.assertNotIn("rec2notes ·", out)

    def test_narrow_terminal_gets_the_plain_header(self):
        out = self.header(80)
        self.assertNotIn("█", out)
        self.assertIn("rec2notes", out)

    def test_nearest_of_256_colors_without_truecolor(self):
        with mock.patch.dict(os.environ, {"COLORTERM": ""}):
            self.assertIn("\x1b[38;5;221m██████", self.header(120))

    def run_cli(self, *args):
        out = Terminal()
        # stdin is not a terminal, or a bare `rec2notes` opens the menu and waits on the real one
        with mock.patch("shutil.get_terminal_size", return_value=os.terminal_size((120, 24))), \
             mock.patch("sys.stdin", io.StringIO()), mock.patch("sys.stdout", out):
            try:
                code = cli.main(list(args))
            except SystemExit as exit:  # argparse exits after -h
                code = exit.code
        return code, out.getvalue()

    def test_bare_rec2notes_shows_the_banner_and_the_help(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("\x1b[38;2;255;215;95m██████", out)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", out)  # argparse colors its help on a terminal too
        self.assertLess(plain.index("██████"), plain.index("usage: rec2notes"))

    def test_help_shows_the_banner(self):
        code, out = self.run_cli("-h")
        self.assertEqual(code, 0)
        plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
        self.assertLess(plain.index("██████"), plain.index("usage: rec2notes"))

    def test_no_color_prints_it_plain(self):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            out = self.header(120)
        self.assertIn("░██████", out)
        self.assertNotIn("\x1b[", out)


class NotOnATerminal(unittest.TestCase):
    def test_plain_lines_with_progress_every_ten_percent(self):
        out = io.StringIO()
        console = ui.Console(out, out)
        self.assertEqual(console.style("✓", ui.GREEN), "✓")
        with console.step("Transcribe", "lezione.m4a · large-v3") as step:
            for percent in range(0, 101, 5):
                step.progress(percent)
            step.end("ok", "9s of audio · large-v3", "1m 02s")
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "  Transcribe  lezione.m4a · large-v3 …")
        self.assertEqual([line.split(" · ")[0] for line in lines[1:-1]], [f"  Transcribe  {p}%" for p in range(10, 100, 10)])
        self.assertEqual(lines[-1], "✓ Transcribe  9s of audio · large-v3          1m 02s")


class Transcription(Sandbox):
    def test_whisper_progress_reaches_the_output_and_the_log(self):
        code, out, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        self.assertIn("-pp", self.calls("whisper")[0])
        self.assertIn("\n  Transcribe  50% · ~", out)
        log = (self.run_dirs()[0] / "whisper.log").read_text(encoding="utf-8")
        self.assertIn("progress =  50%", log)
        self.assertIn("fake whisper-cli: done", log)
