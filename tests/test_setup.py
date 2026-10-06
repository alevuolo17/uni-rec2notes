import contextlib
import functools
import hashlib
import http.server
import io
import os
import shutil
import signal
import subprocess
import threading
import unittest
import zipfile
from unittest import mock

from rec2notes import Abort, paths, setup, stopping

from .helpers import MODEL, Sandbox

EXE = ".exe" if paths.WINDOWS else ""  # the real platform's, for fake tools on PATH; tests patch paths.WINDOWS


class SetupSandbox(Sandbox):
    def only_on_path(self, *tools):
        """PATH holds just these (fake) tools, so the real machine's glslc or nvcc can't leak in."""
        (self.tmp / "bin").mkdir()
        for tool in tools:
            self.add_tool(tool)
        patcher = mock.patch.dict(os.environ, {"PATH": str(self.tmp / "bin")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_tool(self, tool):
        (self.tmp / "bin" / f"{tool}{EXE}").touch(mode=0o755)


@mock.patch.object(paths, "WINDOWS", False)
class Backend(SetupSandbox):
    def test_auto_picks_vulkan_when_glslc_is_installed(self):
        self.only_on_path("glslc")
        self.assertEqual(setup.choose_backend("auto")[0], "vulkan")

    def test_auto_falls_back_to_cpu_and_says_what_to_install(self):
        self.only_on_path()
        backend, reason = setup.choose_backend("auto")
        self.assertEqual(backend, "cpu")
        self.assertIn("glslc", reason)

    def test_explicit_vulkan_without_glslc_fails(self):
        self.only_on_path()
        with self.assertRaisesRegex(Abort, "the vulkan backend needs glslc"):
            setup.choose_backend("vulkan")

    def test_windows_has_cpu_and_vulkan_with_a_driver(self):
        with mock.patch.object(paths, "WINDOWS", True):  # inside the class's patch, which sets False
            self.assertEqual(setup.choose_backend("auto")[0], "cpu")
            with mock.patch.object(setup, "vulkan_driver", return_value=True):
                self.assertEqual(setup.choose_backend("vulkan")[0], "vulkan")
            with mock.patch.object(setup, "vulkan_driver", return_value=False), \
                    self.assertRaisesRegex(Abort, "needs a Vulkan driver"):
                setup.choose_backend("vulkan")
            with self.assertRaisesRegex(Abort, "no cuda backend"):
                setup.choose_backend("cuda")

    def test_cmake_flags_are_all_explicit(self):
        self.assertEqual(setup.cmake_flags("vulkan"), ["-DGGML_VULKAN=ON", "-DGGML_CUDA=OFF"])
        self.assertEqual(setup.cmake_flags("cpu"), ["-DGGML_VULKAN=OFF", "-DGGML_CUDA=OFF"])


@unittest.skipIf(paths.WINDOWS, "no sudo or pkexec on Windows")
class Elevated(Sandbox):
    def test_refuses_to_run_under_pkexec(self):
        os.environ["PKEXEC_UID"] = "1000"
        with mock.patch("os.geteuid", return_value=0):
            with self.assertRaisesRegex(Abort, "without sudo or pkexec"):
                setup.check_not_elevated()


class FoldersFile(Sandbox):
    def test_created_once_and_never_overwritten(self):
        paths.folders_file().unlink()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            setup.ensure_folders_file()
            self.assertIn("Add your courses: run `rec2notes` → 3 Courses.", out.getvalue())
            self.assertIn("# net = ''  # Reti di calcolatori", paths.folders_file().read_text(encoding="utf-8"))
            self.write_folders('net = "Reti"\n')
            setup.ensure_folders_file()
        self.assertEqual(paths.folders_file().read_text(encoding="utf-8"), 'net = "Reti"\n')


class Served(Sandbox):
    """A local server stands in for Hugging Face and GitHub: the URL constants point to it."""

    def setUp(self):
        super().setUp()
        self.served = self.tmp / "served"
        self.served.mkdir()
        handler = functools.partial(Quiet, directory=str(self.served))
        server = QuietServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def point(self, constant, value):
        patcher = mock.patch.object(setup, constant, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def serve(self, path, content):
        file = self.served / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)


class Downloads(Served):
    def setUp(self):
        super().setUp()
        self.point("WHISPER_MODELS_URL", self.url)
        self.point("VAD_MODELS_URL", self.url)
        self.point("MODEL_CHECKSUMS", {})
        paths.whisper_model(MODEL).unlink()
        paths.vad_model().unlink()

    def serve(self, name, content):
        super().serve(f"ggml-{name}.bin", content)
        setup.MODEL_CHECKSUMS[f"ggml-{name}.bin"] = hashlib.sha256(content).hexdigest()

    def download(self, model):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup.download_models(model)
        return out.getvalue()

    def parts(self):
        return list(paths.whisper_model("x").parent.glob("*.part"))

    def test_downloads_the_model_and_the_vad_model(self):
        self.serve("tiny", b"whisper bytes")
        self.serve(paths.VAD_MODEL, b"vad bytes")
        out = self.download("tiny")
        self.assertEqual(paths.whisper_model("tiny").read_bytes(), b"whisper bytes")
        self.assertEqual(paths.vad_model().read_bytes(), b"vad bytes")
        self.assertEqual(self.parts(), [])
        self.assertIn("Download", out)

    def test_a_downloaded_model_is_skipped(self):
        self.serve("tiny", b"whisper bytes")
        self.add_model("tiny")
        self.add_model(paths.VAD_MODEL)
        self.assertIn("Model tiny: already downloaded", self.download("tiny"))

    def test_a_model_with_no_checksum_is_refused_before_downloading(self):
        self.serve("tiny", b"whisper bytes")
        self.serve(paths.VAD_MODEL, b"vad bytes")
        (self.served / "ggml-nope.bin").write_bytes(b"unpinned")
        with self.assertRaisesRegex(Abort, "'nope' is not a Whisper model setup knows. Pick one of: tiny$"):
            self.download("nope")
        self.assertEqual(sorted(p.name for p in paths.whisper_model("x").parent.iterdir()), [])

    def test_a_name_with_a_path_is_refused(self):
        self.serve("tiny", b"whisper bytes")
        for name in ("../x", "../ggml-tiny", "x/../tiny"):
            with self.assertRaisesRegex(Abort, "is not a Whisper model setup knows"):
                self.download(name)

    def test_the_vad_model_is_not_a_whisper_model(self):
        self.serve(paths.VAD_MODEL, b"vad bytes")
        with self.assertRaisesRegex(Abort, "is not a Whisper model setup knows"):
            self.download(paths.VAD_MODEL)

    def test_a_wrong_checksum_leaves_nothing(self):
        self.serve("tiny", b"whisper bytes")
        self.serve(paths.VAD_MODEL, b"vad bytes")
        (self.served / "ggml-tiny.bin").write_bytes(b"tampered bytes")
        with self.assertRaisesRegex(Abort, "the downloaded tiny does not match its checksum"):
            self.download("tiny")
        self.assertFalse(paths.whisper_model("tiny").exists())
        self.assertEqual(self.parts(), [])

    def test_a_stop_mid_download_leaves_nothing(self):
        self.serve("tiny", b"x" * (3 * setup.CHUNK))
        with mock.patch.object(stopping, "check", side_effect=stopping.Stopped(signal.SIGINT)), \
                self.assertRaises(stopping.Stopped):
            self.download("tiny")
        self.assertFalse(paths.whisper_model("tiny").exists())
        self.assertEqual(self.parts(), [])


class ShippedChecksums(unittest.TestCase):
    def test_every_model_setup_offers_has_a_checksum(self):
        for windows in (False, True):
            with mock.patch.object(paths, "WINDOWS", windows):
                defaults = {setup.CPU_MODEL, paths.default_whisper_model()}
            for model in [*setup.MODELS, *defaults, paths.VAD_MODEL]:
                self.assertRegex(setup.MODEL_CHECKSUMS.get(f"ggml-{model}.bin", ""), "^[0-9a-f]{64}$", model)


@mock.patch.object(paths, "WINDOWS", True)
class Prebuilt(Served):
    """The Windows install: the backend's pinned zip, its files under Release/, unzipped next to whisper-cli."""

    def setUp(self):
        super().setUp()
        self.point("WINDOWS_BUILDS", {})

    def release(self, version, files, backend="cpu"):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            for name, content in files.items():
                z.writestr(f"Release/{name}", content)
        path = f"{version}/whisper-{backend}-x64.zip"
        self.serve(path, archive.getvalue())
        setup.WINDOWS_BUILDS[backend] = (version, f"{self.url}/{path}", hashlib.sha256(archive.getvalue()).hexdigest())

    def install(self, backend="cpu"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            setup.install_prebuilt(backend)
        return out.getvalue()

    def bin(self):
        return paths.whisper_dir() / "bin"

    def test_unzips_next_to_whisper_cli_and_marks_the_checksum(self):
        self.release("b1", {"whisper-cli.exe": b"exe", "ggml.dll": b"dll"})
        self.assertIn("Installed whisper.cpp b1 (cpu)", self.install())
        self.assertEqual(paths.whisper_cli_built(), self.bin() / "whisper-cli.exe")
        self.assertEqual(sorted(p.name for p in self.bin().iterdir()), ["VERSION", "ggml.dll", "whisper-cli.exe"])
        self.assertEqual((self.bin() / "VERSION").read_text(encoding="utf-8"), setup.WINDOWS_BUILDS["cpu"][2] + "\n")
        self.assertEqual(sorted(p.name for p in paths.whisper_dir().iterdir()), ["bin", "models"])  # the zip is gone

    def test_the_installed_version_is_kept(self):
        self.release("b1", {"whisper-cli.exe": b"exe"})
        self.install()
        (self.served / "b1" / "whisper-cpu-x64.zip").unlink()
        self.assertIn("b1 (cpu) already installed", self.install())

    def test_a_new_version_replaces_the_old_files(self):
        self.release("b1", {"whisper-cli.exe": b"old", "old.dll": b"dll"})
        self.install()
        self.release("b2", {"whisper-cli.exe": b"new"})
        self.install()
        self.assertEqual(sorted(p.name for p in self.bin().iterdir()), ["VERSION", "whisper-cli.exe"])
        self.assertEqual((self.bin() / "whisper-cli.exe").read_bytes(), b"new")

    def test_another_backend_replaces_the_files(self):
        self.release("b1", {"whisper-cli.exe": b"cpu", "ggml-cpu.dll": b"dll"})
        self.install()
        self.release("b1", {"whisper-cli.exe": b"gpu", "ggml-vulkan.dll": b"dll"}, backend="vulkan")
        self.assertIn("Installed whisper.cpp b1 (vulkan)", self.install("vulkan"))
        self.assertEqual(sorted(p.name for p in self.bin().iterdir()), ["VERSION", "ggml-vulkan.dll", "whisper-cli.exe"])
        self.assertIn("Installed whisper.cpp b1 (cpu)", self.install())

    def test_a_wrong_checksum_installs_nothing(self):
        self.release("b1", {"whisper-cli.exe": b"exe"})
        setup.WINDOWS_BUILDS["cpu"] = setup.WINDOWS_BUILDS["cpu"][:2] + ("0" * 64,)
        with self.assertRaisesRegex(Abort, "does not match its checksum"):
            self.install()
        self.assertFalse(self.bin().exists())
        self.assertEqual(sorted(p.name for p in paths.whisper_dir().iterdir()), ["models"])


class Clone(Sandbox):
    """The pinned clone, with real git against a local repository: offline."""

    def setUp(self):
        super().setUp()
        self.addCleanup(self.make_writable)  # runs before the Sandbox deletes tmp
        self.source = self.tmp / "upstream"
        self.source.mkdir()
        self.git(self.source, "init", "-q")
        (self.source / ".gitignore").write_text("*.bin\n")
        self.pinned = self.commit("pinned")
        self.git(self.source, "tag", "b5130")
        self.newer = self.commit("newer")
        for name, value in (("REPO_URL", self.source.as_uri()), ("WHISPER_CPP", ("b5130", self.pinned))):
            patcher = mock.patch.object(setup, name, value)  # file://, so --depth isn't ignored as for a local path
            patcher.start()
            self.addCleanup(patcher.stop)
        self.target = paths.whisper_dir()
        shutil.rmtree(self.target)  # the Sandbox's models: a first setup clones before downloading them

    def make_writable(self):
        """git's objects are read-only, which Windows refuses to delete."""
        for folder, _, files in os.walk(self.tmp):
            for name in files:
                os.chmod(os.path.join(folder, name), 0o600)

    def git(self, cwd, *args):
        return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args], cwd=cwd,
                              check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        (self.source / "file.txt").write_text(message)
        self.git(self.source, "add", "-A")
        self.git(self.source, "commit", "-q", "-m", message)
        return self.git(self.source, "rev-parse", "HEAD")

    def clone(self, switch=True):
        """setup.clone with git's own output silenced; returns what setup printed."""
        out = io.StringIO()
        with open(os.devnull, "w") as null, mock.patch.object(subprocess, "Popen",
                                                              functools.partial(subprocess.Popen, stdout=null, stderr=null)), \
                contextlib.redirect_stdout(out):
            setup.clone(switch)
        return out.getvalue()

    def test_a_fresh_clone_is_the_pinned_commit_only(self):
        self.clone()
        self.assertEqual(setup.clone_commit(), self.pinned)
        self.assertEqual(self.git(self.target, "rev-parse", "--is-shallow-repository"), "true")

    @unittest.skipIf(paths.WINDOWS, "only Linux clones, and rmtree can't delete git's read-only objects on Windows")
    def test_a_moved_tag_is_refused_and_the_clone_deleted(self):
        self.git(self.source, "tag", "-f", "b5130", self.newer)
        with self.assertRaisesRegex(Abort, f"commit {self.newer}, not the pinned {self.pinned}: the tag was moved"):
            self.clone()
        self.assertFalse(self.target.exists())

    def existing_clone_at_newer(self):
        self.git(self.tmp, "clone", "-q", self.source.as_uri(), str(self.target))
        (self.target / "models").mkdir()
        (self.target / "models" / "ggml-x.bin").write_text("model")

    def test_an_existing_clone_switches_to_the_pin_and_keeps_its_models(self):
        self.existing_clone_at_newer()
        out = self.clone()
        self.assertIn(f"switching {self.target} from {self.newer[:9]} to b5130", out)
        self.assertEqual(setup.clone_commit(), self.pinned)
        self.assertEqual((self.target / "models" / "ggml-x.bin").read_text(), "model")

    def test_a_switch_to_a_moved_tag_leaves_the_clone(self):
        self.existing_clone_at_newer()
        self.git(self.source, "tag", "-f", "b5130", self.newer)
        with self.assertRaisesRegex(Abort, "the tag was moved. .* is left as it was"):
            self.clone()
        self.assertEqual(setup.clone_commit(), self.newer)

    def test_a_clone_at_the_pin_is_kept(self):
        self.clone()
        self.assertIn("b5130 already cloned", self.clone())

    def test_an_unpinned_clone_is_kept_when_not_switching(self):
        self.existing_clone_at_newer()
        self.assertIn(f"kept at {self.newer[:9]}, not the pinned b5130; rerun setup and answer yes", self.clone(False))
        self.assertEqual(setup.clone_commit(), self.newer)


class PinnedVersions(unittest.TestCase):
    def test_linux_clones_the_version_windows_unzips(self):
        self.assertEqual(setup.WHISPER_CPP[0], setup.WINDOWS_BUILDS["cpu"][0])


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # a client that hangs up mid-download, as the stop test does


class QuestionsMixin:
    """Running setup without options in a terminal asks first; the install steps are mocked. Each platform's
    flow runs on every platform, with paths.WINDOWS set to it."""

    windows = False

    def setUp(self):
        super().setUp()
        self.only_on_path("cmake", "git", "make", "c++", "ffmpeg", "claude")
        self.steps = {name: self.patch(name) for name in ("clone", "download_models", "build", "install_prebuilt")}
        self.patch("interactive", return_value=True)
        self.patch("check_not_elevated")  # os.geteuid doesn't exist on Windows; Elevated tests it
        os.environ["REC2NOTES_LANGUAGE"] = "en"  # an explicit language asks no question; the language tests unset it
        windows = mock.patch.object(paths, "WINDOWS", self.windows)
        windows.start()
        self.addCleanup(windows.stop)

    def patch(self, name, **kwargs):
        patcher = mock.patch.object(setup, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def setup(self, *args, answers=()):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=list(answers)) as asked, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = setup.main(list(args))
        return code, out.getvalue(), [call.args[0] for call in asked.call_args_list]

    def test_the_language_is_asked_first_even_with_options(self):
        del os.environ["REC2NOTES_LANGUAGE"]
        code, out, asked = self.setup("--agent", "claude", "--backend", "cpu", "--whisper-model", "large-v3", answers=["2"])
        self.assertEqual(code, 0, out)
        self.assertEqual(asked, ["Choose 1-2 [1]: "])
        self.assertIn("Language / Lingua", out)
        self.assertIn("Setup completato.", out)
        self.assertEqual(paths.saved_settings()["language"], "it")

    def test_enter_picks_english_on_the_first_run_and_the_saved_language_on_a_rerun(self):
        del os.environ["REC2NOTES_LANGUAGE"]
        args = ("--agent", "claude", "--backend", "cpu", "--whisper-model", "large-v3")
        code, out, _ = self.setup(*args, answers=[""])
        self.assertIn("Setup complete.", out)
        self.assertEqual(paths.saved_settings()["language"], "en")
        self.setup(*args, answers=["2"])
        code, out, asked = self.setup(*args, answers=[""])
        self.assertEqual(asked, ["Scegli 1-2 [2]: "])  # the question is in the saved language, which Enter keeps
        self.assertIn("Setup completato.", out)

    def test_the_language_flag_and_variable_ask_nothing(self):
        del os.environ["REC2NOTES_LANGUAGE"]
        code, out, asked = self.setup("--language", "it", answers=["", "", "", ""])
        self.assertEqual(asked[0], "Installare rec2notes su questo computer? [S/n] ")
        self.assertNotIn("Language / Lingua", out)
        self.assertEqual(paths.saved_settings()["language"], "it")
        os.environ["REC2NOTES_LANGUAGE"] = "en"
        code, out, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual(asked[0], "Install rec2notes on this computer? [Y/n] ")
        self.assertEqual(paths.saved_settings()["language"], "en")

    def test_without_a_terminal_it_is_english_and_says_so_and_saves_nothing(self):
        del os.environ["REC2NOTES_LANGUAGE"]
        self.patch("interactive", return_value=False)
        code, out, asked = self.setup(answers=[])
        self.assertEqual(code, 0, out)
        self.assertIn("Language: English (the default); --language it changes it.", out)
        self.assertNotIn("language", paths.saved_settings())

    def test_declining_the_install_saves_no_language(self):
        del os.environ["REC2NOTES_LANGUAGE"]
        code, out, _ = self.setup(answers=["2", "n"])
        self.assertEqual(code, 0, out)
        self.assertIn("Non è stato installato nulla.", out)
        self.assertNotIn("language", paths.saved_settings())

    def test_the_chosen_agent_is_required(self):
        os.environ["REC2NOTES_AGENT"] = "antigravity"
        code, out, _ = self.setup(answers=[])
        self.assertEqual(code, 1)
        self.assertIn("missing: agy (Antigravity). Antigravity is not a distribution package: "
                      "see https://antigravity.google/docs/cli, and make sure `agy` is on PATH.", out)

    def test_the_agent_flag_is_required_too(self):
        code, out, _ = self.setup("--agent", "antigravity", answers=[])
        self.assertEqual(code, 1)
        self.assertIn("missing: agy (Antigravity).", out)

    def test_the_only_agent_installed_is_used_and_saved_without_asking(self):
        (self.tmp / "bin" / f"claude{EXE}").unlink()
        self.add_tool("agy")
        code, out, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual(code, 0, out)
        self.assertIn("Agent: Antigravity, the one installed; `rec2notes` → Settings changes it.", out)
        self.assertFalse(any("Agent, the AI" in a for a in asked))
        self.assertEqual(paths.saved_settings()["agent"], "antigravity")

    def test_with_both_agents_installed_the_user_picks_one(self):
        self.add_tool("agy")
        code, out, asked = self.setup(answers=["", "", "", "2", "", ""])
        self.assertEqual(code, 0, out)
        self.assertIn("Agent, the AI that writes the notes", out)
        self.assertEqual(asked[2:4], ["Choose 1-2: ", "Choose 1-2: "])  # nothing pre-selected: Enter asks again
        self.assertEqual(paths.saved_settings()["agent"], "antigravity")
        code, _, asked = self.setup(answers=["", "", "", "", ""])  # a rerun pre-selects the saved one
        self.assertEqual((code, asked[2]), (0, "Choose 1-2 [2]: "))

    def test_with_both_agents_and_no_terminal_the_saved_one_else_claude(self):
        self.add_tool("agy")
        self.patch("interactive", return_value=False)
        self.assertEqual(self.setup(answers=[])[0], 0)
        self.assertEqual(paths.saved_settings()["agent"], "claude")
        paths.save_setting("agent", "antigravity")
        self.setup(answers=[])
        self.assertEqual(paths.saved_settings()["agent"], "antigravity")

    def test_with_no_agent_installed_both_are_named(self):
        (self.tmp / "bin" / f"claude{EXE}").unlink()
        code, out, _ = self.setup(answers=[])
        self.assertEqual(code, 1)
        self.assertIn("claude (Claude Code) or agy (Antigravity)", out)
        self.assertIn("Claude Code is not a distribution package: see https://claude.com/claude-code", out)
        self.assertIn("Antigravity is not a distribution package: see https://antigravity.google/docs/cli", out)

    def test_no_installs_nothing(self):
        paths.pointer_file().unlink()
        code, out, asked = self.setup(answers=["n"])
        self.assertEqual((code, asked), (0, ["Install rec2notes on this computer? [Y/n] "]))
        self.assertIn("Nothing installed.", out)
        self.steps["clone"].assert_not_called()
        self.assertFalse(paths.pointer_file().exists())

    def test_the_first_setup_offers_rec2notes_in_the_home_folder(self):
        paths.pointer_file().unlink()
        code, _, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual((code, asked[1]), (0, f"Where should your rec2notes folder go? [{self.home / 'rec2notes'}] "))
        self.assertTrue((self.home / "rec2notes").is_dir())
        self.assertEqual(paths.folder(), self.home / "rec2notes")

    def test_a_typed_folder_is_created_and_pointed_to(self):
        code, out, _ = self.setup(answers=["", "~/Uni/rec2notes", "", ""])
        self.assertEqual(code, 0)
        self.assertEqual(paths.folder(), self.home / "Uni" / "rec2notes")
        self.assertIn(f"Setup complete. rec2notes folder: {self.home / 'Uni' / 'rec2notes'}", out)

    def test_without_a_terminal_the_first_setup_uses_the_default_folder(self):
        self.patch("interactive", return_value=False)
        paths.pointer_file().unlink()
        self.assertEqual(self.setup(answers=[])[0], 0)
        self.assertEqual(paths.folder(), self.home / "rec2notes")

    def test_without_a_terminal_a_rerun_keeps_the_folder(self):
        self.patch("interactive", return_value=False)
        self.setup(answers=[])
        self.assertEqual(paths.folder(), self.folder)

    def test_ctrl_d_at_a_question_installs_nothing(self):
        code, out, _ = self.setup(answers=[EOFError])
        self.assertEqual(code, 1)
        self.assertIn("nothing installed", out)
        self.steps["clone"].assert_not_called()

    def test_a_rerun_offers_the_saved_model(self):
        paths.save_setting("whisper_model", "large-v3-turbo-q5_0")
        code, _, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual((code, asked[3]), (0, "Choose 1-3 [3]: "))
        self.steps["download_models"].assert_called_once_with("large-v3-turbo-q5_0")

    def test_a_saved_model_off_the_menu_is_offered_first(self):
        paths.save_setting("whisper_model", "small")
        code, out, _ = self.setup(answers=["", "", "", ""])
        self.assertEqual(code, 0)
        self.assertIn("1) small", out)
        self.assertIn("your current model", out)
        self.steps["download_models"].assert_called_once_with("small")

    def test_an_environment_model_that_differs_is_named(self):
        with mock.patch.dict(os.environ, {"REC2NOTES_WHISPER_MODEL": "large-v3-turbo-q5_0"}):
            code, out, _ = self.setup(answers=["", "", "", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(paths.saved_settings().get("whisper_model"), "large-v3")
        self.assertIn("$REC2NOTES_WHISPER_MODEL is set to large-v3-turbo-q5_0, which wins over large-v3: "
                      "remove it to use large-v3.", out)

    def test_ctrl_c_at_a_question_installs_nothing(self):
        code, out, _ = self.setup(answers=["", KeyboardInterrupt])
        self.assertEqual(code, 130)
        self.assertIn("setup: interrupted, nothing installed", out)
        self.steps["clone"].assert_not_called()


class Questions(QuestionsMixin, SetupSandbox):
    """The Linux flow: the backend question, clone and build."""

    def test_a_relative_folder_or_a_file_is_asked_again(self):
        (self.tmp / "a-file").touch()
        code, out, asked = self.setup(answers=["", "rec2notes", str(self.tmp / "a-file"), "", "", ""])
        self.assertEqual((code, len(asked)), (0, 6))
        self.assertIn("Type an absolute path", out)
        self.assertIn("is a file, not a folder", out)
        self.assertEqual(paths.folder(), self.folder)

    def test_enter_takes_the_defaults(self):
        code, _, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual((code, asked[1:]), (0, [f"Where should your rec2notes folder go? [{self.folder}] ",
                                                 "Choose 1-4 [1]: ", "Choose 1-3 [2]: "]))
        self.steps["build"].assert_called_once_with("cpu")  # auto, without glslc
        self.steps["download_models"].assert_called_once_with("large-v3-turbo")  # the CPU's default

    def test_a_gpu_backend_defaults_to_large_v3(self):
        self.add_tool("glslc")
        code, _, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual((code, asked[3]), (0, "Choose 1-3 [1]: "))
        self.steps["download_models"].assert_called_once_with("large-v3")

    def test_the_environment_model_beats_the_cpu_default(self):
        with mock.patch.dict(os.environ, {"REC2NOTES_WHISPER_MODEL": "large-v3"}):
            self.setup(answers=["", "", "", ""])
        self.steps["download_models"].assert_called_once_with("large-v3")

    def test_without_options_or_a_terminal_the_cpu_gets_turbo(self):
        self.patch("interactive", return_value=False)
        self.setup(answers=[])
        self.steps["download_models"].assert_called_once_with("large-v3-turbo")

    def test_choices_by_number_after_a_wrong_answer(self):
        code, out, asked = self.setup(answers=["y", "", "cpu", "4", "9", "2"])
        self.assertEqual((code, len(asked)), (0, 6))
        self.steps["build"].assert_called_once_with("cpu")
        self.steps["download_models"].assert_called_once_with("large-v3-turbo")
        self.assertEqual(paths.whisper_model_choice(), "large-v3-turbo")
        self.assertNotIn("REC2NOTES_WHISPER_MODEL", out)

    def test_menu_marks_downloaded_models(self):
        self.add_model("large-v3")
        _, out, _ = self.setup(answers=["", "", "", ""])
        self.assertIn("1) large-v3             most accurate, 2.9 GB, downloaded", out)

    def test_options_skip_the_questions(self):
        code, out, asked = self.setup("--whisper-model", "large-v3-turbo", answers=[])
        self.assertEqual((code, asked), (0, []))
        self.steps["build"].assert_called_once_with("cpu")

    def test_an_unpinned_clone_asks_to_switch(self):
        self.patch("clone_commit", return_value="d09f61a708f3487afa956ff578e60eae5e7a233c")
        code, _, asked = self.setup(answers=["", "", "", "", ""])
        self.assertEqual(code, 0)
        self.assertIn(f"whisper.cpp in {paths.whisper_dir()} is at d09f61a70, not rec2notes' pinned b5130. "
                      "Switch to b5130 and rebuild? [Y/n] ", asked)
        self.steps["clone"].assert_called_once_with(True)

    def test_no_keeps_an_unpinned_clone(self):
        self.patch("clone_commit", return_value="d09f61a708f3487afa956ff578e60eae5e7a233c")
        self.setup(answers=["", "", "n", "", ""])
        self.steps["clone"].assert_called_once_with(False)

    def test_a_pinned_clone_asks_nothing_more(self):
        self.patch("clone_commit", return_value=setup.WHISPER_CPP[1])
        code, _, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual((code, len(asked)), (0, 4))
        self.steps["clone"].assert_called_once_with(True)

    def test_without_questions_an_unpinned_clone_switches(self):
        self.patch("clone_commit", return_value="d09f61a708f3487afa956ff578e60eae5e7a233c")
        self.setup("--whisper-model", "tiny", answers=[])
        self.steps["clone"].assert_called_once_with(True)

    def built_with(self, backend):
        cache = paths.whisper_dir() / "build" / "CMakeCache.txt"
        cache.parent.mkdir(parents=True)
        cache.write_text("".join(f"{flag.removeprefix('-D').replace('=', ':BOOL=')}\n" for flag in setup.cmake_flags(backend)))

    def test_a_rerun_keeps_the_installed_backend_over_auto(self):
        self.add_tool("glslc")  # auto would pick vulkan
        self.built_with("cpu")
        code, out, _ = self.setup("--whisper-model", "tiny", answers=[])
        self.assertEqual(code, 0)
        self.steps["build"].assert_called_once_with("cpu")
        self.assertIn("Backend: cpu. The one installed; --backend changes it.", out)

    def test_the_menu_offers_the_installed_backend_first(self):
        self.add_tool("nvcc")
        self.built_with("cuda")
        code, out, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual((code, asked[2]), (0, "Choose 1-4 [3]: "))
        self.assertIn("NVIDIA, with the CUDA toolkit, installed", out)
        self.steps["build"].assert_called_once_with("cuda")

    def test_an_explicit_backend_replaces_the_installed_one(self):
        self.add_tool("glslc")
        self.built_with("cpu")
        self.setup("--backend", "vulkan", answers=[])
        self.steps["build"].assert_called_once_with("vulkan")


class QuestionsOnWindows(QuestionsMixin, SetupSandbox):
    """The Windows flow: cpu or vulkan, no build tools, the prebuilt whisper.cpp, turbo."""

    windows = True

    def setUp(self):
        super().setUp()
        self.driver = self.patch("vulkan_driver", return_value=True)

    def test_enter_takes_the_defaults(self):
        code, out, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual((code, asked[1:]), (0, [f"Where should your rec2notes folder go? [{self.folder}] ",
                                                 "Choose 1-2 [1]: ", "Choose 1-3 [2]: "]))
        self.steps["install_prebuilt"].assert_called_once_with("cpu")
        self.steps["download_models"].assert_called_once_with("large-v3-turbo")
        for step in ("clone", "build"):
            self.steps[step].assert_not_called()
        self.assertNotIn("REC2NOTES_WHISPER_MODEL", out)  # turbo is already the default there

    def test_no_clone_question(self):
        self.patch("clone_commit", return_value="d09f61a708f3487afa956ff578e60eae5e7a233c")
        code, _, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual((code, len(asked)), (0, 4))

    def test_only_ffmpeg_and_claude_are_required(self):
        for ffmpeg in (self.tmp / "bin").glob("ffmpeg*"):
            ffmpeg.unlink()
        code, out, _ = self.setup(answers=[])
        self.assertEqual(code, 1)
        self.assertIn("missing: ffmpeg. Install ffmpeg with `winget install Gyan.FFmpeg`", out)

    def test_vulkan_installs_its_build_with_turbo(self):
        code, _, _ = self.setup(answers=["", "", "2", ""])
        self.assertEqual(code, 0)
        self.steps["install_prebuilt"].assert_called_once_with("vulkan")
        self.steps["download_models"].assert_called_once_with("large-v3-turbo")

    def test_the_menu_says_when_there_is_no_vulkan_driver(self):
        self.driver.return_value = False
        _, out, _ = self.setup(answers=["", "", "", ""])
        self.assertIn("5x faster (no Vulkan driver found)", out)

    def test_vulkan_without_a_driver_is_refused(self):
        self.driver.return_value = False
        code, out, _ = self.setup("--backend", "vulkan", answers=[])
        self.assertEqual(code, 1)
        self.assertIn("needs a Vulkan driver", out)
        self.steps["install_prebuilt"].assert_not_called()

    def test_cuda_is_refused(self):
        code, out, _ = self.setup("--backend", "cuda", answers=[])
        self.assertEqual(code, 1)
        self.assertIn("no cuda backend", out)

    def installed(self, backend, version="b5130"):
        bin = paths.whisper_cli_built().parent
        bin.mkdir(parents=True)
        for name in ("whisper-cli.exe", "ggml-cpu-haswell.dll", *(["ggml-vulkan.dll"] if backend == "vulkan" else [])):
            (bin / name).touch()
        (bin / "VERSION").write_text(f"{version}\n", encoding="utf-8")

    def test_a_rerun_keeps_vulkan(self):
        self.installed("vulkan")
        code, out, _ = self.setup("--whisper-model", "tiny", answers=[])
        self.assertEqual(code, 0)
        self.steps["install_prebuilt"].assert_called_once_with("vulkan")
        self.assertIn("Backend: vulkan. The one installed; --backend changes it.", out)

    def test_the_menu_offers_the_installed_vulkan_first(self):
        self.installed("vulkan")
        code, out, asked = self.setup(answers=["", "", "", ""])
        self.assertEqual((code, asked[2]), (0, "Choose 1-2 [2]: "))
        self.assertIn("about 5x faster, installed", out)
        self.steps["install_prebuilt"].assert_called_once_with("vulkan")

    def test_vulkan_from_an_older_pinned_zip_is_kept(self):
        self.installed("vulkan", version="0" * 64)  # a checksum no longer in WINDOWS_BUILDS
        self.setup("--whisper-model", "tiny", answers=[])
        self.steps["install_prebuilt"].assert_called_once_with("vulkan")

    def test_a_cpu_install_stays_cpu(self):
        self.installed("cpu")
        self.setup("--whisper-model", "tiny", answers=[])
        self.steps["install_prebuilt"].assert_called_once_with("cpu")

    def test_vulkan_kept_after_the_driver_is_gone_says_how_to_switch(self):
        self.installed("vulkan")
        self.driver.return_value = False
        code, out, _ = self.setup("--whisper-model", "tiny", answers=[])
        self.assertEqual(code, 1)
        self.assertIn("needs a Vulkan driver", out)
        self.assertIn("or use --backend cpu", out)
        self.steps["install_prebuilt"].assert_not_called()

    def test_another_model_is_saved_for_the_runs(self):
        code, out, _ = self.setup(answers=["", "", "", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(paths.whisper_model_choice(), "large-v3")
        self.assertNotIn("REC2NOTES_WHISPER_MODEL", out)

