"""Where things live: files in this package, the user's rec2notes folder and its pointer, and programs."""

import json
import os
import shutil
import sys
import tomllib
from pathlib import Path

from . import Abort

WINDOWS = sys.platform == "win32"
TOOL = "uni-rec2notes"
PROMPTS = Path(__file__).resolve().parent / "prompts"
MERGE_PROMPT = PROMPTS / "merge.md"
CLEAN_PROMPT = PROMPTS / "clean.md"
CREATE_PROMPT = PROMPTS / "create.md"
SETUP = "rec2notes setup"  # the command, for the fix hints

VAD_MODEL = "silero-v5.1.2"


def default_whisper_model() -> str:
    """Before setup has saved a model: large-v3, but turbo on Windows, where whisper runs on the CPU by default."""
    return "large-v3-turbo" if WINDOWS else "large-v3"


def config_dir() -> Path:
    """Where the pointer lives: $XDG_CONFIG_HOME when it is absolute (the XDG spec says a relative one is invalid
    and must be ignored), else ~/.config, or %APPDATA% on Windows."""
    value = os.environ.get("XDG_CONFIG_HOME", "")
    if os.path.isabs(value):
        return Path(value) / TOOL
    if WINDOWS:
        return Path(os.environ["APPDATA"]) / TOOL
    return Path.home() / ".config" / TOOL


def pointer_file() -> Path:
    return config_dir() / "rec2notes.toml"


def default_folder() -> Path:
    return Path.home() / "rec2notes"  # not Documents: OneDrive backs that up on Windows


def pointed_folder() -> Path | None:
    """The rec2notes folder the pointer names, or None when there is no pointer yet."""
    pointer = pointer_file()
    try:
        with open(pointer, "rb") as f:
            value = tomllib.load(f).get("folder")
    except FileNotFoundError:
        return None
    except tomllib.TOMLDecodeError as e:
        raise Abort(f"{pointer}: {e}") from None
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise Abort(f"{pointer}: `folder` must be the absolute path of your rec2notes folder")
    return Path(value)


def folder() -> Path:
    """The rec2notes folder; it must exist, as only setup creates it."""
    pointed = pointed_folder()
    if pointed is None:
        raise Abort(f"no rec2notes folder yet: run `{SETUP}` first")
    if not pointed.is_dir():
        raise Abort(f"your rec2notes folder {pointed} is missing (moved or deleted?). "
                    f"If you moved it, point to it in `rec2notes` → Settings; or run `{SETUP}` to make a new one")
    return pointed


def write_pointer(folder: Path) -> None:
    # A literal string keeps Windows' backslashes readable; one holding ' needs a basic string (JSON's is one).
    value = f"'{folder}'" if "'" not in str(folder) else json.dumps(str(folder), ensure_ascii=False)
    pointer = pointer_file()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(f"folder = {value}\n", encoding="utf-8")


def settings_file() -> Path:
    return folder() / "settings.toml"


def saved_settings() -> dict[str, str]:
    """The defaults saved in the rec2notes folder's settings.toml: the Whisper model setup downloaded, and what
    `rec2notes` → Settings chose. Empty before anything is saved."""
    pointed = pointed_folder()
    if pointed is None:
        return {}
    file = pointed / "settings.toml"
    try:
        with open(file, "rb") as f:
            settings = tomllib.load(f)
    except (FileNotFoundError, NotADirectoryError):
        return {}
    except tomllib.TOMLDecodeError as e:
        raise Abort(f"{file}: {e}") from None
    for key, value in settings.items():
        if not isinstance(value, str):
            raise Abort(f"{file}: `{key}` must be text in quotes, such as {key} = \"high\"")
    return {key: value for key, value in settings.items() if value}


def save_setting(key: str, value: str | None) -> None:
    """Save a default in settings.toml, keeping the others; None removes it."""
    settings = saved_settings() | {key: value}
    settings_file().write_text("".join(f"{k} = {json.dumps(v)}\n" for k, v in settings.items() if v), encoding="utf-8")


SETTING_ENV = {  # each setting's environment variable, which wins over the saved default
    "claude_model": "REC2NOTES_CLAUDE_MODEL",
    "effort": "REC2NOTES_EFFORT",
    "whisper_model": "REC2NOTES_WHISPER_MODEL",
}


def setting_choice(key: str) -> str | None:
    """What runs use: the setting's environment variable, else the saved default, else the built-in one (None for
    the Claude model: Claude Code's default)."""
    built_in = {"claude_model": None, "effort": "high", "whisper_model": default_whisper_model()}[key]
    return os.environ.get(SETTING_ENV[key]) or saved_settings().get(key) or built_in


def whisper_model_choice() -> str:
    return setting_choice("whisper_model")


def courses_file() -> Path:
    return folder() / "courses.toml"


def folders_file() -> Path:
    return folder() / "folders.toml"


def whisper_dir() -> Path:
    return folder() / "whisper.cpp"


def whisper_cli_built() -> Path:
    """Our whisper-cli: built from source on Linux, the prebuilt release unzipped into bin on Windows."""
    return whisper_dir() / ("bin/whisper-cli.exe" if WINDOWS else "build/bin/whisper-cli")


def whisper_model(name: str) -> Path:
    return whisper_dir() / "models" / f"ggml-{name}.bin"


def vad_model() -> Path:
    return whisper_model(VAD_MODEL)


def downloaded_whisper_models() -> list[str]:
    """The Whisper models in the rec2notes folder, without the VAD model."""
    found = (p.name.removeprefix("ggml-").removesuffix(".bin") for p in (whisper_dir() / "models").glob("ggml-*.bin"))
    return sorted(name for name in found if name != VAD_MODEL)


def cache_dir() -> Path:
    return folder() / "cache"


def transcript_cache(model: str, sha256: str) -> Path:
    return cache_dir() / "transcripts" / model / f"{sha256}.txt"


def runs_dir() -> Path:
    return cache_dir() / "runs"


def program(name: str) -> str:
    """The full path of a program on PATH, to run it by: Windows' CreateProcess finds only .exe files, not the
    .cmd wrappers npm (and the tests) install. The bare name if it isn't found, so running it fails as usual."""
    return shutil.which(name) or name
