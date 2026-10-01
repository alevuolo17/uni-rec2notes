import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rec2notes import cli, paths

TESTS = Path(__file__).resolve().parent
FAKES = TESTS / "fakes"
FIXTURES = TESTS / "fixtures"
MODEL = paths.default_whisper_model()  # large-v3, turbo on Windows

COURSES = """\
[net]
name = "Reti di calcolatori"
vocab = "Lezione di reti."

[analisi]
name = "Analisi matematica"
vocab = "Lezione di analisi."

[basi]
name = "Basi di dati"
vocab = "Lezione di basi di dati."
"""


class Sandbox(unittest.TestCase):
    """A temporary home and rec2notes folder, a vault with one Reti di calcolatori note, and the fakes first on PATH."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rec2notes-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        env = mock.patch.dict(os.environ, {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),  # Windows' home
            "XDG_CONFIG_HOME": str(self.tmp / "config"),  # the pointer's; it wins over %APPDATA% too
            "PATH": f"{FAKES}{os.pathsep}{os.environ.get('PATH', '')}",
            "FAKE_CLAUDE_LOG": str(self.tmp / "claude.log"),
            "FAKE_WHISPER_LOG": str(self.tmp / "whisper.log"),
            "FAKE_PYTHON": sys.executable,  # for the fakes' .cmd wrappers on Windows
            "PYTHONUTF8": "1",  # the fakes read and write UTF-8, as the real programs do
        })
        env.start()
        self.addCleanup(env.stop)
        for var in ("REC2NOTES_EFFORT", "REC2NOTES_CLAUDE_MODEL", "REC2NOTES_WHISPER_MODEL", "FAKE_CLAUDE_MODE",
                    "FORCE_COLOR"):  # FORCE_COLOR makes argparse colour the usage on Python 3.14
            os.environ.pop(var, None)
        fake = FAKES / ("claude.cmd" if paths.WINDOWS else "claude")
        self.assertEqual(os.path.normcase(str(shutil.which("claude"))), os.path.normcase(fake),
                         "the tests must never reach the real claude")

        self.folder = self.tmp / "rec2notes"
        self.folder.mkdir()
        paths.write_pointer(self.folder)
        self.add_model(MODEL)
        self.add_model(paths.VAD_MODEL)
        self.write_courses(COURSES)
        self.write_folders('net = "Reti"\n')
        self.note = self.tmp / "vault" / "Reti" / "Lezione 1.md"
        self.note.parent.mkdir(parents=True)
        shutil.copy(FIXTURES / "note.md", self.note)
        self.output = self.note.with_name("Lezione 1 (completo).md")
        self.audio = self.make_audio("lezione.m4a", b"audio one")

    def add_model(self, name):
        path = paths.whisper_model(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    def read_only(self, folder):
        """Make `folder` (created if missing) read-only for this test, or skip the test where that can't be done."""
        if paths.WINDOWS:
            self.skipTest("chmod can't make a folder read-only on Windows")
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o500)
        self.addCleanup(folder.chmod, 0o700)
        if os.access(folder, os.W_OK):
            self.skipTest("permissions aren't enforced here (running as root?)")

    def write_courses(self, text):
        path = paths.courses_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_folders(self, text):
        path = paths.folders_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def make_audio(self, name, content):
        path = self.tmp / "recordings" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def rec2notes(self, *args):
        """Run the CLI in-process; returns (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([str(a) for a in args])
        return code, out.getvalue(), err.getvalue()

    def calls(self, fake):
        """The argument lists the fake `claude` or `whisper-cli` was called with."""
        log = self.tmp / f"{'claude' if fake == 'claude' else 'whisper'}.log"
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []

    def run_dirs(self):
        runs = paths.runs_dir()
        return sorted(runs.iterdir()) if runs.exists() else []
