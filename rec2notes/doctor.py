"""doctor: check that everything a run needs is in place, and say how to fix what isn't.

Each check gives (mark, label, detail, fix): mark is ok, warn or fail, and fix is what to do about a
warn or fail. Nothing is installed or changed here; `setup` does that.
"""

import os
import shutil
import subprocess
from pathlib import Path

from . import Abort, version, courses, paths, transcribe, ui

PACKAGES_HINT = "install it with your package manager (the README lists the packages)"
COURSES = "`rec2notes` → 3 Courses"  # where courses and their folders are set
AUTH_TIMEOUT = 20  # seconds; `claude auth status` may reach the network


def run(console: ui.Console) -> int:
    """Print the checklist; 1 if anything failed (warnings don't count), else 0."""
    results = [("ok", "rec2notes", version(), None), claude_installed(), claude_logged_in(), ffmpeg(), rec2notes_folder()]
    if results[-1][0] == "ok":  # the rest lives in the folder
        results += [whisper_cli(), *models(paths.whisper_model_choice()), *folders()]
    width = max(ui.LABEL_WIDTH, *(len(label) for _, label, _, _ in results))
    console.line()
    for mark, label, detail, fix in results:
        console.mark(mark, label, detail, width=width)
        if fix:
            console.line(f"  {'':<{width}} {console.style('→ ' + fix, ui.DIM)}")
    failed = sum(r[0] == "fail" for r in results)
    warned = sum(r[0] == "warn" for r in results)
    console.line()
    if failed:
        console.line(f"{failed} problem{'s' if failed > 1 else ''} to fix" + (f", {warned} to look at" if warned else "") + ".")
        console.line("Fix the items above, then run `rec2notes doctor` again.")
    else:
        console.line("Everything needed is in place." if not warned else f"Ready to run; {warned} to look at.")
    return 1 if failed else 0


def start_problems(model: str, caches: list[Path]) -> list[str]:
    """What stops a run from starting, checked before a long transcription: Whisper's needs only matter while a
    recording has no cached transcript (`caches`, one per recording, for `model`)."""
    problems = []
    if not shutil.which("claude"):
        problems.append("claude (Claude Code) is not on PATH")
    if not all(c.exists() for c in caches):
        if not shutil.which("ffmpeg"):
            problems.append("ffmpeg is not installed")
        if not transcribe.whisper_cli():
            problems.append(f"whisper-cli is not built; run {paths.SETUP}")
        for model_file in (paths.whisper_model(model), paths.vad_model()):
            if not model_file.exists():
                problems.append(f"{model_file} is missing; run {paths.SETUP} --whisper-model {model}")
    return problems


def claude_installed() -> tuple:
    found = shutil.which("claude")
    if found:
        return "ok", "Claude Code", found, None
    return "fail", "Claude Code", "not on PATH", "see https://claude.com/claude-code, and make sure `claude` is on PATH"


def claude_logged_in() -> tuple:
    if not shutil.which("claude"):
        return "warn", "Claude login", "not checked: Claude Code is missing", None
    try:
        status = subprocess.run([paths.program("claude"), "auth", "status"], capture_output=True, timeout=AUTH_TIMEOUT).returncode
    except (OSError, subprocess.TimeoutExpired):
        return "warn", "Claude login", "could not check", "run `claude auth status` yourself"
    if status == 0:
        return "ok", "Claude login", "logged in", None
    return "fail", "Claude login", "not logged in", "run `claude auth login`"


def ffmpeg() -> tuple:
    if shutil.which("ffmpeg"):
        return "ok", "ffmpeg", shutil.which("ffmpeg"), None
    return "fail", "ffmpeg", "not installed", PACKAGES_HINT


def rec2notes_folder() -> tuple:
    try:
        return "ok", "rec2notes folder", str(paths.folder()), None
    except Abort as e:
        return "fail", "rec2notes folder", str(e), None  # the message says what to do


def whisper_cli() -> tuple:
    found = transcribe.whisper_cli()
    if found:
        return "ok", "whisper-cli", found, None
    return "fail", "whisper-cli", "not built", f"run {paths.SETUP}"


def models(model: str) -> list[tuple]:
    results = []
    for label, name, file in (("Whisper model", model, paths.whisper_model(model)),
                              ("VAD model", paths.VAD_MODEL, paths.vad_model())):
        if file.exists():
            results.append(("ok", label, name, None))
        else:
            results.append(("fail", label, f"{name} is missing", f"run {paths.SETUP} --whisper-model {model}"))
    return results


def folders() -> list[tuple]:
    path = paths.folders_file()
    if not path.exists():
        return [("fail", "Folders", f"{path} does not exist", f"run {paths.SETUP} to create it")]
    try:
        all_courses = courses.load_courses()
        configured = courses.read_folders(all_courses)
    except Abort as e:
        return [("fail", "Folders", str(e), f"fix {path}")]
    if not configured:
        return [("fail", "Folders", "no course has a folder", f"add your courses, or set their folders, in {COURSES}")]
    results = []
    for slug, course in all_courses.items():
        folder = configured.get(slug)
        if folder is None:
            results.append(("warn", course.name, "no folder set", f"set it in {COURSES} → e, or pass --course {slug}"))
        else:
            results.append(folder_row(slug, course.name, folder, configured, all_courses, path))
    return results


def folder_row(slug: str, name: str, folder: str, configured: dict[str, str], all_courses: dict, path: Path) -> tuple:
    """One course's folder: it mustn't overlap another course's, and, if absolute, must exist and be writable
    (the finished note is written next to the source note). A relative name can't be looked up: there is no vault root."""
    mine = folder.strip("/")
    others = {o: f.strip("/") for o, f in configured.items() if o != slug}
    same = [o for o, f in others.items() if courses.contains(mine, f) and courses.contains(f, mine)]
    inside = [o for o, f in others.items() if courses.contains(mine, f) and o not in same]
    if same:  # only the outer folder is blamed: the courses inside it are fine
        return ("fail", name, f"{folder} is also the folder of {_names(same, all_courses)}",
                f"give each course its own folder in {path}")
    if inside:
        return ("fail", name, f"{folder} contains the folder of {_names(inside, all_courses)}",
                f"pick a smaller folder for `{slug}`, or remove its line, in {path}")
    if not Path(folder).is_absolute():
        return "ok", name, f"{folder} (a folder name: not looked up)", None
    where = Path(folder)
    if not where.exists():
        return "warn", name, f"{folder} does not exist", f"create it, or fix the line for `{slug}` in {path}"
    if not where.is_dir():
        return "warn", name, f"{folder} is not a folder", f"fix the line for `{slug}` in {path}"
    if not os.access(where, os.W_OK | os.X_OK):
        return ("fail", name, f"you have no write access to {folder}",
                f"runs write the finished note next to the note: fix the permissions, or pick another folder in {path}")
    return "ok", name, folder, None


def _names(slugs: list[str], all_courses: dict) -> str:
    return ", ".join(all_courses[slug].name for slug in slugs)

