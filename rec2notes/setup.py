"""setup: pick the rec2notes folder, install whisper.cpp and download the models into it.

whisper.cpp is cloned at a pinned tag and commit, and built, on Linux, and unzipped from a pinned, checksummed release on Windows.
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

from . import Abort, courses, i18n, merge, paths, stopping, ui
from .i18n import t

REPO_URL = "https://github.com/ggml-org/whisper.cpp"
WHISPER_CPP = ("b5130", "927cfce34f31707e17f2bff35c349632fb9e2c3a")  # the tag and commit Linux clones; the tag is WINDOWS_BUILDS' version
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
MODELS = ("large-v3", "large-v3-turbo", "large-v3-turbo-q5_0")  # the interactive menu; --whisper-model takes any whisper.cpp model in models.sha256
CPU_MODEL = "large-v3-turbo"  # what setup fetches on the CPU backend, where large-v3 is too slow
REQUIRED_TOOLS = {  # and the chosen agent
    "cmake": ["cmake"],
    "git": ["git"],
    "make": ["make"],
    "a C++ compiler": ["c++", "g++", "clang++"],
    "ffmpeg": ["ffmpeg"],
}
WINDOWS_TOOLS = ("ffmpeg",)  # the prebuilt whisper.cpp needs no build tools


def main(argv: list[str] | None = None) -> int:
    i18n.load()
    p = argparse.ArgumentParser(prog="rec2notes setup", description=t("setup.help.description"))
    p.add_argument("--agent", choices=tuple(merge.AGENTS), help=t("setup.help.agent"))
    p.add_argument("--backend", choices=BACKENDS, help=t("setup.help.backend"))
    p.add_argument("--whisper-model", metavar="NAME",
                   help=t("setup.help.whisper_model", cpu_model=CPU_MODEL, default_model=paths.default_whisper_model()))
    p.add_argument("--language", choices=tuple(i18n.LANGUAGES), help=t("setup.help.language"))
    args = p.parse_args(argv)
    try:
        asking = args.backend is None and args.whisper_model is None and args.agent is None and interactive()
        explicit_language = args.language or os.environ.get("REC2NOTES_LANGUAGE")
        asking_language = interactive() and not explicit_language
        if asking or asking_language:
            console = ui.Console()
        if asking and console.banner():
            print()
        if asking_language:
            language = pick(console, t("setup.title.language"), list(i18n.LANGUAGES.items()), i18n.setting_language())
        else:
            language = args.language or (explicit_language if explicit_language in i18n.LANGUAGES else i18n.DEFAULT)
        i18n.set_language(language)
        if not interactive() and not explicit_language:
            print(t("setup.language_default"))
        check_not_elevated()
        agent = args.agent or os.environ.get("REC2NOTES_AGENT") or only_agent_installed()
        check_tools(agent)
        if asking:
            if not confirm(t("setup.confirm_install")):
                print(t("setup.nothing_installed"))
                return 0
            use_folder(ask_folder(paths.pointed_folder() or paths.default_folder()))
        else:
            use_folder(paths.pointed_folder() or paths.default_folder())
        if explicit_language or asking_language:
            paths.save_setting("language", language)
        if agent is None:  # both installed
            agent = choose_agent(console if asking else None)
        elif not args.agent and not os.environ.get("REC2NOTES_AGENT"):
            print(t("setup.agent_only", label=merge.AGENTS[agent].label, settings=t("path.settings")))
        switch = True  # without questions, an unpinned clone moves to the pinned commit
        if asking and not paths.WINDOWS and (current := clone_commit()) not in (None, WHISPER_CPP[1]):
            switch = confirm(t("setup.switch_whisper", dir=paths.whisper_dir(), current=current[:9], tag=WHISPER_CPP[0]))
        installed = installed_backend()
        backend = args.backend or installed or ("cpu" if paths.WINDOWS else "auto")
        if asking:
            backend = pick(console, t("setup.title.backend"), backend_menu(installed), backend)
            model = pick(console, t("setup.title.model"), model_menu(default_model(backend)), default_model(backend))
            print()
        else:
            model = args.whisper_model or default_model(backend)
        with stopping.handling():
            backend, reason = choose_backend(backend)
            if not asking and not args.backend and backend == installed:
                reason = t("setup.reason.installed")
            print(t("setup.backend_line", backend=backend, reason=reason))
            if paths.WINDOWS:
                install_prebuilt(backend)
                download_models(model)
            else:
                clone(switch)
                download_models(model)
                build(backend)
            paths.save_setting("whisper_model", model)
            paths.save_setting("agent", agent)
            ensure_folders_file()
        print("\n" + t("setup.complete", folder=paths.folder()))
        if (env := os.environ.get("REC2NOTES_WHISPER_MODEL")) and env != model:
            print(t("setup.env_whisper", env=env, model=model))
    except Abort as e:
        print(f"setup: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print("setup: " + t("setup.failed", cmd=shlex.join(e.cmd), status=e.returncode), file=sys.stderr)
        return 1
    except stopping.Stopped as e:
        print(f"setup: {e.message}", file=sys.stderr)
        return e.status
    except (KeyboardInterrupt, EOFError) as e:  # at a question, before installing
        print("\nsetup: " + t("setup.interrupted" if isinstance(e, KeyboardInterrupt) else "setup.no_answer"), file=sys.stderr)
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
        answer = input(f"{question} {t('setup.yes_no')} ").strip().lower()
        if answer in ("", *i18n.YES):
            return True
        if answer in ("n", "no"):
            return False


def ask_folder(default: Path) -> Path:
    """The absolute folder typed (`~` expanded); Enter takes the default."""
    while True:
        answer = input(t("setup.ask_folder", default=default) + " ").strip()
        folder = Path(os.path.expanduser(answer)) if answer else default
        if not folder.is_absolute():
            print(t("absolute_windows" if paths.WINDOWS else "absolute_other"))
        elif folder.exists() and not folder.is_dir():
            print(t("setup.folder_is_file", folder=folder))
        else:
            return Path(os.path.normpath(folder))


def use_folder(folder: Path) -> None:
    """Create the rec2notes folder if needed and point to it; everything else setup does lands in it."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise Abort(t("setup.cannot_create", folder=folder, reason=e.strerror)) from None
    paths.write_pointer(folder)
    print(t("setup.folder", folder=folder))


def pick(console: ui.Console, title: str, options: list[tuple[str, str]], default: str | None) -> str:
    """Ask for one of the (name, description) options by number; Enter takes the default, if there is one."""
    print(f"\n{console.style(title, ui.BOLD)}")
    width = max(len(name) for name, _ in options)
    for number, (name, description) in enumerate(options, 1):
        print(f"  {number}) {name:<{width}}  {console.style(description, ui.DIM)}")
    names = [name for name, _ in options]
    while True:
        answer = input(t("setup.choose", n=len(options)) + ("" if default is None else f" [{names.index(default) + 1}]") + ": ").strip()
        if not answer and default is not None:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return names[int(answer) - 1]


def backend_menu(installed: str | None) -> list[tuple[str, str]]:
    if paths.WINDOWS:
        options = [("cpu", t("setup.backend.cpu_windows")),
                   ("vulkan", t("setup.backend.vulkan_windows")
                              + ("" if vulkan_driver() else " " + t("setup.backend.no_vulkan_driver")))]
    else:
        glslc, nvcc = shutil.which("glslc"), shutil.which("nvcc")
        options = [("auto", t("setup.backend.auto", choice="vulkan" if glslc else "cpu")),
                   ("vulkan", t("setup.backend.vulkan") + ("" if glslc else " " + t("setup.backend.no_glslc"))),
                   ("cuda", t("setup.backend.cuda") + ("" if nvcc else " " + t("setup.backend.no_nvcc"))),
                   ("cpu", t("setup.backend.cpu"))]
    return [(name, description + (", " + t("setup.installed") if name == installed else "")) for name, description in options]


def installed_backend() -> str | None:
    """The backend of the whisper.cpp in the rec2notes folder, which a rerun keeps unless told otherwise: on Windows
    from its files, so an older pinned zip still counts (upstream's cpu zip has no ggml-vulkan.dll), on Linux from
    the GGML flags of the cached build."""
    if paths.WINDOWS:
        cli = paths.whisper_cli_built()
        if not cli.is_file():
            return None
        return "vulkan" if (cli.parent / "ggml-vulkan.dll").is_file() else "cpu"
    cache = paths.whisper_dir() / "build" / "CMakeCache.txt"
    if not cache.is_file():
        return None
    lines = cache.read_text(encoding="utf-8", errors="replace").splitlines()
    return next((backend for backend in ("vulkan", "cuda") if f"GGML_{backend.upper()}:BOOL=ON" in lines), "cpu")


def model_menu(default: str) -> list[tuple[str, str]]:
    models = MODELS if default in MODELS else (default, *MODELS)
    return [(name, t("setup.model.current" if name not in MODELS else f"setup.model.{name}")
             + (", " + t("setup.downloaded") if paths.whisper_model(name).exists() else ""))
            for name in models]


def model_description(name: str) -> str:
    return t(f"setup.model.{name}")


def agent_menu() -> list[tuple[str, str]]:
    return [(name, t(f"setup.agent.{name}")) for name in ("claude", "antigravity")]


def check_not_elevated() -> None:
    # Under sudo or pkexec, HOME and PATH belong to root: everything would land in root's home.
    if not paths.WINDOWS and os.geteuid() == 0 and (os.environ.get("SUDO_USER") or os.environ.get("PKEXEC_UID")):
        raise Abort(t("setup.not_elevated"))


def only_agent_installed() -> str | None:
    """The agent installed, when there is one; None for both or none."""
    installed = [name for name, agent in merge.AGENTS.items() if shutil.which(agent.program)]
    return installed[0] if len(installed) == 1 else None


def choose_agent(console: ui.Console | None) -> str:
    """With both agents installed: the user's pick in a terminal (`console`), the saved one pre-selected; off a
    terminal the saved one, else claude."""
    saved = paths.saved_settings().get("agent")
    saved = saved if saved in merge.AGENTS else None
    if console is None:
        return saved or "claude"
    return pick(console, t("setup.title.agent"), agent_menu(), saved)


def check_tools(agent: str | None) -> None:
    """The build tools, ffmpeg and `agent`; None means any agent, as with both installed."""
    if agent is not None and agent not in merge.AGENTS:
        raise Abort(t("setup.unknown_agent", agent=agent, names=", ".join(merge.AGENTS)))
    required = {what: REQUIRED_TOOLS[what] for what in WINDOWS_TOOLS} if paths.WINDOWS else REQUIRED_TOOLS
    missing = [what for what, names in required.items() if not any(shutil.which(n) for n in names)]
    wanted = [merge.AGENTS[agent]] if agent else list(merge.AGENTS.values())
    agent_missing = not any(shutil.which(a.program) for a in wanted)
    if agent_missing:
        missing.append(f" {t('setup.or')} ".join(f"{a.program} ({a.label})" for a in wanted))
    if not missing:
        return
    message = t("setup.missing", items=", ".join(missing))
    if paths.WINDOWS and "ffmpeg" in missing:
        message += " " + t("setup.install_ffmpeg")
    elif any(what in REQUIRED_TOOLS for what in missing):
        message += " " + t("setup.install_packages")
    if agent_missing:
        message += "".join(" " + t("setup.agent_not_package", label=a.label, hint=a.install_hint()) for a in wanted)
    raise Abort(message)


def choose_backend(requested: str) -> tuple[str, str]:
    """(backend, why); an explicit GPU backend whose build tools are missing fails instead of falling back."""
    if paths.WINDOWS:
        if requested in ("auto", "cpu"):
            return "cpu", t("setup.reason.windows_cpu")
        if requested == "cuda":
            raise Abort(t("setup.windows_no_cuda"))
        if not vulkan_driver():
            raise Abort(t("setup.windows_no_vulkan"))
        return "vulkan", t("setup.reason.windows_vulkan")
    if requested == "auto":
        if shutil.which("glslc"):
            return "vulkan", t("setup.reason.auto_vulkan")
        return "cpu", t("setup.reason.auto_cpu", packages=t("setup.vulkan_packages"))
    if requested == "vulkan" and not shutil.which("glslc"):
        raise Abort(t("setup.no_glslc", packages=t("setup.vulkan_packages")))
    if requested == "cuda" and not shutil.which("nvcc"):
        raise Abort(t("setup.no_nvcc"))
    return requested, t("setup.reason.explicit")


def vulkan_driver() -> bool:
    """Every Windows GPU driver with Vulkan installs its loader in System32."""
    return (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "vulkan-1.dll").is_file()


def cmake_flags(backend: str) -> list[str]:
    # Every flag explicit, so a rerun with another backend reconfigures the cached build.
    return [f"-DGGML_VULKAN={'ON' if backend == 'vulkan' else 'OFF'}",
            f"-DGGML_CUDA={'ON' if backend == 'cuda' else 'OFF'}"]


def clone(switch: bool = True) -> None:
    """Clone whisper.cpp's pinned tag, or move an existing clone to it when `switch`; either way the commit must be
    the pinned one, as a tag can be moved. An existing clone is switched in place: its ignored models and build stay."""
    tag, commit = WHISPER_CPP
    target = paths.whisper_dir()
    current = clone_commit()
    if current == commit:
        print(t("setup.clone.already", tag=tag, target=target))
        return
    if current and not switch:
        print(t("setup.clone.kept", current=current[:9], tag=tag))
        return
    if current:
        print(t("setup.clone.switching", target=target, current=current[:9], tag=tag))
        _run(["git", "fetch", "--depth", "1", REPO_URL, f"refs/tags/{tag}"], cwd=target)  # not origin, which may be changed
        _check_commit(_git("rev-parse", "FETCH_HEAD^{commit}"), target, remove=False)
        _run(["git", "-c", "advice.detachedHead=false", "checkout", "--detach", commit], cwd=target)
        return
    if target.exists() and any(target.iterdir()):
        raise Abort(t("setup.clone.not_a_clone", target=target))
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "-c", "advice.detachedHead=false", "clone", "--depth", "1", "--branch", tag, REPO_URL, str(target)])
    _check_commit(clone_commit(), target, remove=True)


def clone_commit() -> str | None:
    """The commit the whisper.cpp clone is at, or None without a clone."""
    return _git("rev-parse", "HEAD") if (paths.whisper_dir() / ".git").is_dir() else None


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=paths.whisper_dir(), stdout=subprocess.PIPE, text=True, check=True).stdout.strip()


def _check_commit(got: str | None, target: Path, remove: bool) -> None:
    """Stop unless `got` is the pinned commit; a fresh clone, which holds no models yet, is deleted."""
    tag, commit = WHISPER_CPP
    if got == commit:
        return
    if remove:
        shutil.rmtree(target)
    raise Abort(t("setup.clone.moved", tag=tag, got=got, commit=commit) + " "
                + (t("setup.clone.deleted") if remove else t("setup.clone.left", target=target)))


def install_prebuilt(backend: str) -> None:
    """Unzip the backend's pinned whisper.cpp zip into whisper-cli's folder; VERSION, written last with the
    zip's checksum, marks it complete, so another backend or a rebuilt zip installs again."""
    version, url, sha256 = WINDOWS_BUILDS[backend]
    name = url.rsplit("/", 1)[1]
    target = paths.whisper_cli_built().parent
    marker = target / "VERSION"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == sha256:
        print(t("setup.prebuilt.already", version=version, backend=backend, target=target))
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
        raise Abort(t("setup.prebuilt.failed", target=target, error=e)) from None
    finally:
        archive.unlink(missing_ok=True)
    print(t("setup.prebuilt.installed", version=version, backend=backend, target=target))


def download_models(model: str) -> None:
    if f"ggml-{model}.bin" not in MODEL_CHECKSUMS or model.startswith("silero"):  # a known name holds no path
        known = (n.removeprefix("ggml-").removesuffix(".bin") for n in MODEL_CHECKSUMS)
        raise Abort(t("setup.unknown_model", model=model, names=", ".join(n for n in known if not n.startswith("silero"))))
    for name, base, file in ((model, WHISPER_MODELS_URL, paths.whisper_model(model)),
                             (paths.VAD_MODEL, VAD_MODELS_URL, paths.vad_model())):
        if file.exists():
            print(t("setup.model_downloaded", name=name))
            continue
        download(name, f"{base}/{file.name}", file, MODEL_CHECKSUMS[file.name])


def download(name: str, url: str, file: Path, sha256: str) -> None:
    """Download url into file through a .part file, kept only when complete and its SHA-256 matches, so an
    interrupted or tampered download leaves nothing behind."""
    file.parent.mkdir(parents=True, exist_ok=True)
    part = file.with_name(file.name + ".part")
    with ui.Console().step(t("setup.download"), name) as step:
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
                raise Abort(t("setup.download_checksum", name=name))
            os.replace(part, file)
        except BaseException as e:
            part.unlink(missing_ok=True)
            if isinstance(e, urllib.error.HTTPError):
                e.close()
                if e.code == 404:
                    raise Abort(t("setup.download_404", name=name, url=url)) from None
            if isinstance(e, urllib.error.URLError):
                raise Abort(t("setup.download_failed", name=name, error=e.reason)) from None
            if isinstance(e, OSError):
                raise Abort(t("setup.download_failed", name=name, error=e)) from None
            raise


def build(backend: str) -> None:
    cwd = paths.whisper_dir()
    _run(["cmake", "-B", "build", *cmake_flags(backend), "-DCMAKE_BUILD_TYPE=Release"], cwd=cwd)
    _run(["cmake", "--build", "build", "-j", str(os.cpu_count() or 1), "--config", "Release"], cwd=cwd)
    cli = paths.whisper_cli_built()
    if not os.access(cli, os.X_OK):
        raise Abort(t("setup.build_missing", cli=cli))
    print(t("setup.built", cli=cli, backend=backend))


def ensure_folders_file() -> None:
    path = paths.folders_file()
    if path.exists():
        print(t("setup.folders_exists", path=path))
        return
    template = courses.folders_template(courses.load_courses())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:
        f.write(template)
    print(t("setup.folders_created", path=path, courses=t("path.courses")))


def _run(cmd: list[str], cwd=None) -> None:
    print(f"$ {shlex.join(cmd)}", flush=True)
    stopping.check()
    with subprocess.Popen(cmd, cwd=cwd) as proc, stopping.child(proc):
        proc.wait()
    stopping.check()
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd)
