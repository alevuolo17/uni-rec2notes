"""setup: pick the rec2notes folder, install whisper.cpp and download the models into it.

whisper.cpp is cloned and built on Linux, and unzipped from a pinned, checksummed release on Windows.
Run without options in a terminal, it shows the banner and asks before installing, then asks
for the folder, the backend and the model; with options, or outside a terminal, it asks nothing.
"""

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import Abort, courses, paths, stopping, ui

REPO_URL = "https://github.com/ggml-org/whisper.cpp"
# Hugging Face repos at pinned commits; models.sha256 lists every ggml-*.bin they hold with its SHA-256, as
# https://huggingface.co/api/models/<repo>/tree/<commit> reports it (an LFS file's oid is its SHA-256).
WHISPER_MODELS_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1"
VAD_MODELS_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b"
MODEL_CHECKSUMS = {name: sha256 for sha256, name in (
    line.split() for line in Path(__file__).with_name("models.sha256").read_text(encoding="utf-8").splitlines())}
CHUNK = 1 << 20
WINDOWS_BUILDS = {  # backend: (version, url, sha256) of the whisper.cpp zip setup unzips on Windows
    "cpu": ("b5130", "https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-bin-x64.zip",
            "f9ec6c52a2e949b62ab51fa21d0d497958f9e41c3010c157c4e42932d5316f3c"),
    # Built by docs/whisper_vulkan.py, as upstream ships no Windows Vulkan zip; rebuild it when cpu's version changes.
    "vulkan": ("b5130", "https://github.com/alevuolo17/uni-rec2notes/releases/download/whisper-b5130/whisper-vulkan-x64.zip",
               "2721d142b6676389da01408332275dc0e73d82e2f2492d56e56a83798ec03047"),
}
BACKENDS = ("auto", "vulkan", "cuda", "cpu")
MODELS = {  # the interactive menu; --whisper-model takes any whisper.cpp model in models.sha256
    "large-v3": "most accurate, 2.9 GB",
    "large-v3-turbo": "much faster, a small loss in accuracy, 1.5 GB",
    "large-v3-turbo-q5_0": "turbo compressed, the lightest, for the CPU backend, 0.5 GB",
}
CPU_MODEL = "large-v3-turbo"  # what setup fetches on the CPU backend, where large-v3 is too slow
VULKAN_PACKAGES = "the Vulkan headers and loader, glslc, and the SPIR-V tools and headers (README, Setup)"
CLAUDE = "claude (Claude Code)"
REQUIRED_TOOLS = {
    "cmake": ["cmake"],
    "git": ["git"],
    "make": ["make"],
    "a C++ compiler": ["c++", "g++", "clang++"],
    "ffmpeg": ["ffmpeg"],
    CLAUDE: ["claude"],
}
WINDOWS_TOOLS = ("ffmpeg", CLAUDE)  # the prebuilt whisper.cpp needs no build tools


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rec2notes setup", description="Install whisper.cpp and download the models into your rec2notes folder. "
                                                          "Safe to rerun. Without options, in a terminal, it asks for the folder, "
                                                          "the backend and the model.")
    p.add_argument("--backend", choices=BACKENDS,
                   help="vulkan: any GPU with a Vulkan driver; cuda: NVIDIA with the CUDA toolkit; cpu: no GPU. "
                        "auto (default): vulkan if glslc is installed, else cpu. On Windows: cpu (default) or vulkan")
    p.add_argument("--whisper-model", metavar="NAME",
                   help=f"Whisper model to download and use, any of whisper.cpp's (default: $REC2NOTES_WHISPER_MODEL, else the "
                        f"saved one, else {CPU_MODEL} on the cpu backend, else {paths.default_whisper_model()})")
    args = p.parse_args(argv)
    backend = args.backend or "auto"
    try:
        asking = args.backend is None and args.whisper_model is None and interactive()
        if asking:
            console = ui.Console()
            if console.banner():
                print()
        check_not_elevated()
        check_tools()
        if asking:
            if not confirm("Install rec2notes on this computer?"):
                print("Nothing installed.")
                return 0
            use_folder(ask_folder(paths.pointed_folder() or paths.default_folder()))
            backend = pick(console, "Backend", backend_menu(), "cpu" if paths.WINDOWS else backend)
            model = pick(console, "Whisper model", model_menu(default_model(backend)), default_model(backend))
            print()
        else:
            use_folder(paths.pointed_folder() or paths.default_folder())
            model = args.whisper_model or default_model(backend)
        with stopping.handling():
            backend, reason = choose_backend(backend)
            print(f"Backend: {backend}. {reason}")
            if paths.WINDOWS:
                install_prebuilt(backend)
                download_models(model)
            else:
                clone()
                download_models(model)
                build(backend)
            paths.save_setting("whisper_model", model)
            ensure_folders_file()
        print(f"\nSetup complete. rec2notes folder: {paths.folder()}")
        if (env := os.environ.get("REC2NOTES_WHISPER_MODEL")) and env != model:
            print(f"$REC2NOTES_WHISPER_MODEL is set to {env}, which wins over {model}: remove it to use {model}.")
    except Abort as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"setup: `{shlex.join(e.cmd)}` failed with exit status {e.returncode}", file=sys.stderr)
        return 1
    except stopping.Stopped as e:
        print(f"setup: {e.message}", file=sys.stderr)
        return e.status
    except (KeyboardInterrupt, EOFError) as e:  # at a question, before installing
        print(f"\nsetup: {'interrupted' if isinstance(e, KeyboardInterrupt) else 'no answer'}, nothing installed", file=sys.stderr)
        return 130 if isinstance(e, KeyboardInterrupt) else 1
    return 0


def default_model(backend: str) -> str:
    """The model setup fetches unless told otherwise: the one runs use, else turbo when the backend is the CPU."""
    if chosen := os.environ.get("REC2NOTES_WHISPER_MODEL") or paths.saved_settings().get("whisper_model"):
        return chosen
    if choose_backend(backend)[0] == "cpu":
        return CPU_MODEL
    return paths.default_whisper_model()


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def confirm(question: str) -> bool:
    while True:
        answer = input(f"{question} [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False


def ask_folder(default: Path) -> Path:
    """The absolute folder typed (`~` expanded); Enter takes the default."""
    while True:
        answer = input(f"Where should your rec2notes folder go? [{default}] ").strip()
        folder = Path(os.path.expanduser(answer)) if answer else default
        if not folder.is_absolute():
            start = "a drive, like C:\\," if paths.WINDOWS else "/"
            print(f"Type an absolute path: it starts with {start} or ~")
        elif folder.exists() and not folder.is_dir():
            print(f"{folder} is a file, not a folder")
        else:
            return Path(os.path.normpath(folder))


def use_folder(folder: Path) -> None:
    """Create the rec2notes folder if needed and point to it; everything else setup does lands in it."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise Abort(f"could not create {folder}: {e.strerror}") from None
    paths.write_pointer(folder)
    print(f"rec2notes folder: {folder}")


def pick(console: ui.Console, title: str, options: list[tuple[str, str]], default: str) -> str:
    """Ask for one of the (name, description) options by number; Enter takes the default."""
    print(f"\n{console.style(title, ui.BOLD)}")
    width = max(len(name) for name, _ in options)
    for number, (name, description) in enumerate(options, 1):
        print(f"  {number}) {name:<{width}}  {console.style(description, ui.DIM)}")
    names = [name for name, _ in options]
    while True:
        answer = input(f"Choose 1-{len(options)} [{names.index(default) + 1}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return names[int(answer) - 1]


def backend_menu() -> list[tuple[str, str]]:
    if paths.WINDOWS:
        return [("cpu", "works on any PC, slower"),
                ("vulkan", "a GPU with a Vulkan driver: AMD, NVIDIA, Intel; about 5x faster"
                           + ("" if vulkan_driver() else " (no Vulkan driver found)"))]
    glslc, nvcc = shutil.which("glslc"), shutil.which("nvcc")
    return [("auto", f"vulkan if glslc is installed, else cpu: here, {'vulkan' if glslc else 'cpu'}"),
            ("vulkan", "any GPU with a Vulkan driver: Intel, AMD, NVIDIA" + ("" if glslc else " (glslc is not installed)")),
            ("cuda", "NVIDIA, with the CUDA toolkit" + ("" if nvcc else " (nvcc is not on PATH)")),
            ("cpu", "no GPU, much slower")]


def model_menu(default: str) -> list[tuple[str, str]]:
    models = MODELS if default in MODELS else {default: "your current model", **MODELS}
    return [(name, description + (", downloaded" if paths.whisper_model(name).exists() else ""))
            for name, description in models.items()]


def check_not_elevated() -> None:
    # Under sudo or pkexec, HOME and PATH belong to root: everything would land in root's home.
    if not paths.WINDOWS and os.geteuid() == 0 and (os.environ.get("SUDO_USER") or os.environ.get("PKEXEC_UID")):
        raise Abort("run setup as your own user, without sudo or pkexec. It installs into your home directory "
                    "and needs no root; only the OS packages in the README need sudo.")


def check_tools() -> None:
    required = {what: REQUIRED_TOOLS[what] for what in WINDOWS_TOOLS} if paths.WINDOWS else REQUIRED_TOOLS
    missing = [what for what, names in required.items() if not any(shutil.which(n) for n in names)]
    if not missing:
        return
    message = f"missing: {', '.join(missing)}."
    if paths.WINDOWS and "ffmpeg" in missing:
        message += " Install ffmpeg with `winget install Gyan.FFmpeg`, then open a new terminal."
    elif any(what != CLAUDE for what in missing):
        message += (" Install them with your distribution's package manager (the README lists the packages);"
                    " setup does not install packages.")
    if CLAUDE in missing:
        message += " Claude Code is not a distribution package: see https://claude.com/claude-code, and make sure `claude` is on PATH."
    raise Abort(message)


def choose_backend(requested: str) -> tuple[str, str]:
    """(backend, why); an explicit GPU backend whose build tools are missing fails instead of falling back."""
    if paths.WINDOWS:
        if requested in ("auto", "cpu"):
            return "cpu", "whisper.cpp's prebuilt CPU build."
        if requested == "cuda":
            raise Abort("on Windows, setup installs whisper.cpp for the CPU or Vulkan; there is no cuda backend.")
        if not vulkan_driver():
            raise Abort("the vulkan backend needs a Vulkan driver (vulkan-1.dll), which was not found: "
                        "update your GPU driver, or use cpu.")
        return "vulkan", "rec2notes' prebuilt Vulkan build of whisper.cpp, for any GPU with a Vulkan driver."
    if requested == "auto":
        if shutil.which("glslc"):
            return "vulkan", "Chosen automatically: glslc is installed, so whisper.cpp runs on any GPU with a Vulkan driver."
        return "cpu", ("Chosen automatically: glslc is not installed, so whisper.cpp runs on the CPU only (much slower). "
                       f"For GPU acceleration, install {VULKAN_PACKAGES}, then rerun setup.")
    if requested == "vulkan" and not shutil.which("glslc"):
        raise Abort(f"the vulkan backend needs glslc, which is not on PATH. Install {VULKAN_PACKAGES}.")
    if requested == "cuda" and not shutil.which("nvcc"):
        raise Abort("the cuda backend needs nvcc from the CUDA toolkit, which is not on PATH (it is often in /usr/local/cuda/bin).")
    return requested, "Chosen explicitly."


def vulkan_driver() -> bool:
    """Every Windows GPU driver with Vulkan installs its loader in System32."""
    return (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "vulkan-1.dll").is_file()


def cmake_flags(backend: str) -> list[str]:
    # Every flag explicit, so a rerun with another backend reconfigures the cached build.
    return [f"-DGGML_VULKAN={'ON' if backend == 'vulkan' else 'OFF'}",
            f"-DGGML_CUDA={'ON' if backend == 'cuda' else 'OFF'}"]


def clone() -> None:
    target = paths.whisper_dir()
    if (target / ".git").is_dir():
        print(f"whisper.cpp: already cloned in {target}")
        return
    if target.exists() and any(target.iterdir()):
        raise Abort(f"{target} exists but is not a git clone; move it away and rerun setup")
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", REPO_URL, str(target)])


def install_prebuilt(backend: str) -> None:
    """Unzip the backend's pinned whisper.cpp zip into whisper-cli's folder; VERSION, written last with the
    zip's checksum, marks it complete, so another backend or a rebuilt zip installs again."""
    version, url, sha256 = WINDOWS_BUILDS[backend]
    name = url.rsplit("/", 1)[1]
    target = paths.whisper_cli_built().parent
    marker = target / "VERSION"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == sha256:
        print(f"whisper.cpp: {version} ({backend}) already installed in {target}")
        return
    archive = paths.whisper_dir() / name
    download(f"whisper.cpp {version}", url, archive, sha256)
    try:
        if target.exists():
            shutil.rmtree(target)  # an older version's files would otherwise stay behind
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if info.filename.startswith("Release/") and not info.is_dir():  # every file is under Release/
                    file = target / info.filename.removeprefix("Release/")
                    file.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, open(file, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        marker.write_text(sha256 + "\n", encoding="utf-8")
    except OSError as e:
        raise Abort(f"could not install whisper.cpp in {target}: {e}") from None
    finally:
        archive.unlink(missing_ok=True)
    print(f"Installed whisper.cpp {version} ({backend}) in {target}")


def download_models(model: str) -> None:
    if f"ggml-{model}.bin" not in MODEL_CHECKSUMS or model.startswith("silero"):  # a known name holds no path
        known = (n.removeprefix("ggml-").removesuffix(".bin") for n in MODEL_CHECKSUMS)
        raise Abort(f"{model!r} is not a Whisper model setup knows. Pick one of: "
                    + ", ".join(n for n in known if not n.startswith("silero")))
    for name, base, file in ((model, WHISPER_MODELS_URL, paths.whisper_model(model)),
                             (paths.VAD_MODEL, VAD_MODELS_URL, paths.vad_model())):
        if file.exists():
            print(f"Model {name}: already downloaded")
            continue
        download(name, f"{base}/{file.name}", file, MODEL_CHECKSUMS[file.name])


def download(name: str, url: str, file: Path, sha256: str) -> None:
    """Download url into file through a .part file, kept only when complete and its SHA-256 matches, so an
    interrupted or tampered download leaves nothing behind."""
    file.parent.mkdir(parents=True, exist_ok=True)
    part = file.with_name(file.name + ".part")
    with ui.Console().step("Download", name) as step:
        try:
            with urllib.request.urlopen(url, timeout=60) as response, open(part, "wb") as out:
                size, done, digest = int(response.headers.get("Content-Length") or 0), 0, hashlib.sha256()
                while chunk := response.read(CHUNK):
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if size:
                        step.progress(min(done * 100 // size, 100))
                    stopping.check()
            if digest.hexdigest() != sha256:
                raise Abort(f"the downloaded {name} does not match its checksum: deleted it, rerun setup")
            os.replace(part, file)
        except BaseException as e:
            part.unlink(missing_ok=True)
            if isinstance(e, urllib.error.HTTPError):
                e.close()
                if e.code == 404:
                    raise Abort(f"could not download {name}: nothing at {url}") from None
            if isinstance(e, urllib.error.URLError):
                raise Abort(f"could not download {name}: {e.reason}") from None
            if isinstance(e, OSError):
                raise Abort(f"could not download {name}: {e}") from None
            raise


def build(backend: str) -> None:
    cwd = paths.whisper_dir()
    _run(["cmake", "-B", "build", *cmake_flags(backend), "-DCMAKE_BUILD_TYPE=Release"], cwd=cwd)
    _run(["cmake", "--build", "build", "-j", str(os.cpu_count() or 1), "--config", "Release"], cwd=cwd)
    cli = paths.whisper_cli_built()
    if not os.access(cli, os.X_OK):
        raise Abort(f"the build finished but {cli} is missing")
    print(f"Built {cli} ({backend})")


def ensure_folders_file() -> None:
    path = paths.folders_file()
    if path.exists():
        print(f"Folders: {path} exists, left as it is")
        return
    template = courses.folders_template(courses.load_courses())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:
        f.write(template)
    print(f"Created {path}. Add your courses: run `rec2notes`, then 3 Courses.")


def _run(cmd: list[str], cwd=None) -> None:
    print(f"$ {shlex.join(cmd)}", flush=True)
    stopping.check()
    with subprocess.Popen(cmd, cwd=cwd) as proc, stopping.child(proc):
        proc.wait()
    stopping.check()
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
