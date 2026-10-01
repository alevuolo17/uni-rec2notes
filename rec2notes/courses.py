"""Courses (the repo's defaults plus the user's own) and this computer's vault folder for each one."""

import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import Abort, paths


@dataclass(frozen=True)
class Course:
    slug: str
    name: str
    vocab: str


def load_courses(path: Path | None = None) -> dict[str, Course]:
    """The user's courses, by slug (none if they have not added any)."""
    path = path or paths.courses_file()
    return _read_courses(path) if path.exists() else {}


def _read_courses(path: Path) -> dict[str, Course]:
    courses = {}
    for slug, table in _load_toml(path).items():
        fields = table if isinstance(table, dict) else {}
        if not all(isinstance(fields.get(key), str) and fields[key].strip() for key in ("name", "vocab")):
            raise Abort(f"{path}: course [{slug}] needs a non-empty `name` and `vocab`")
        courses[slug] = Course(slug, fields["name"].strip(), fields["vocab"].strip())
    return courses


def check_new_slug(slug: str) -> None:
    """Refuse a slug that can't name a new course."""
    if not re.fullmatch(r"[a-z0-9_-]+", slug):
        raise Abort(f"course slug {slug!r} must be lowercase letters, digits, - or _")
    path = paths.courses_file()
    if slug in load_courses(path):
        raise Abort(f"course {slug!r} already exists (edit {path} to change it)")


def add_course(slug: str, name: str, vocab: str) -> None:
    """Add a course to the user's courses file."""
    path = paths.courses_file()
    name, vocab = " ".join(name.split()), " ".join(vocab.split())
    check_new_slug(slug)
    if not name or not vocab:
        raise Abort("a course needs a non-empty name and vocab")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    entry = f"[{slug}]\nname = {_quote(name)}\nvocab = {_quote(vocab)}\n"
    _write(path, f"{text.rstrip()}\n\n{entry}" if text.strip() else entry)


def update_course(slug: str, name: str | None = None, vocab: str | None = None) -> None:
    """Change the name and/or vocab of one of the user's courses, keeping the rest of the file."""
    path = paths.courses_file()
    if slug not in load_courses(path):
        raise Abort(f"unknown course {slug!r} (courses: {', '.join(load_courses(path))})")
    new = {"name": name and " ".join(name.split()), "vocab": vocab and " ".join(vocab.split())}
    if (name is not None and not new["name"]) or (vocab is not None and not new["vocab"]):
        raise Abort("a course needs a non-empty name and vocab")
    lines = path.read_text(encoding="utf-8").splitlines()
    inside = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("["):
            inside = line.strip() == f"[{slug}]"
        for key, value in new.items():
            if inside and value and re.match(rf"\s*{key}\s*=", line):
                lines[i] = f"{key} = {_quote(value)}"
    text = "\n".join(lines) + "\n"
    with tempfile.TemporaryDirectory() as tmp:  # check the edit took before replacing the file
        check = Path(tmp) / path.name
        check.write_text(text, encoding="utf-8")
        edited = load_courses(check)[slug]
    if any(value and getattr(edited, key) != value for key, value in new.items()):
        raise Abort(f"could not change {slug} in {path}: edit that file by hand")
    _write(path, text)


def rename_course(old: str, new: str) -> None:
    """Rename a course's slug in courses.toml and, if it has one, in folders.toml; the folder itself is untouched."""
    path, folders_path = paths.courses_file(), paths.folders_file()
    if old not in load_courses(path):
        raise Abort(f"unknown course {old!r} (courses: {', '.join(load_courses(path))})")
    check_new_slug(new)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [f"[{new}]" if line.strip() == f"[{old}]" else line for line in lines]
    text = "\n".join(lines) + "\n"
    with tempfile.TemporaryDirectory() as tmp:  # check the edit took before replacing anything
        check = Path(tmp) / path.name
        check.write_text(text, encoding="utf-8")
        renamed = load_courses(check)
    if new not in renamed or old in renamed:
        raise Abort(f"could not rename {old} in {path}: edit that file by hand")
    folders = folders_path.read_text(encoding="utf-8") if folders_path.exists() else ""
    folders = re.sub(rf"(?m)^(\s*){re.escape(old)}(\s*=)", rf"\g<1>{new}\g<2>", folders)
    _write(path, text)
    if folders:
        _write(folders_path, folders)


def absolute_folder(text: str) -> Path:
    """The absolute path typed for a course's folder (`~` expanded). It may not exist yet, but it must be a
    folder you can write in, or one that can be created inside a folder you can write in."""
    typed = text.strip()
    folder = Path(os.path.expanduser(typed))
    if not typed or not folder.is_absolute():
        start = "a drive, like C:\\," if paths.WINDOWS else "/"
        raise Abort(f"{typed!r} is not an absolute path: it must start with {start} or ~")
    folder = Path(os.path.normpath(folder))
    existing = next(p for p in (folder, *folder.parents) if p.exists())
    if not existing.is_dir():
        raise Abort(f"{existing} is not a folder")
    if not os.access(existing, os.W_OK | os.X_OK):
        raise Abort(f"you have no write access to {existing}" if existing == folder
                    else f"{folder} can't be created: you have no write access to {existing}")
    return folder


def contains(outer: str, inner: str) -> bool:
    """Whether a note in the `inner` course folder is also in the `outer` one (they may be the same folder)."""
    return _contains(Path(inner).parts, Path(outer).parts)


def overlaps(a: str, b: str) -> bool:
    """Whether two course folders (as `load_folders` gives them) are the same or one contains the other."""
    return contains(a, b) or contains(b, a)


def check_folder_free(folder: Path, slug: str) -> None:
    """Refuse a folder that is, contains or is inside another course's folder: a note there would match both."""
    for other, other_folder in load_folders(load_courses()).items():
        if other != slug and overlaps(str(folder).strip("/"), other_folder):
            raise Abort(f"{folder} overlaps the folder of {other} ({other_folder}): notes there would match both "
                        "courses. Pick a folder that isn't inside, and doesn't contain, another course's folder.")


def set_folder(slug: str, folder: str) -> None:
    """Set this computer's vault folder for a course in folders.toml, keeping the rest of the file."""
    path = paths.folders_file()
    line = f"{slug} = {_quote(folder)}"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for i, existing in enumerate(lines):
        if re.match(rf"\s*{re.escape(slug)}\s*=", existing):
            lines[i] = line
            break
    else:
        lines.append(line)
    _write(path, "\n".join(lines) + "\n")


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # a JSON string is a TOML basic string


def _write(path: Path, text: str) -> None:
    """Write a whole file in one step, so a crash never leaves half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_folders(courses: dict[str, Course], path: Path | None = None) -> dict[str, str]:
    """The folder of each course slug as written in the folders file (empty if there is no file or no folder)."""
    path = path or paths.folders_file()
    if not path.exists():
        return {}
    folders = {}
    for slug, folder in _load_toml(path).items():
        if slug not in courses:
            raise Abort(f"{path}: unknown course {slug!r} (courses: {', '.join(courses)})")
        if not isinstance(folder, str):
            raise Abort(f"{path}: the folder for {slug!r} must be a string")
        if folder.strip().strip("/"):
            folders[slug] = folder.strip()
    return folders


def load_folders(courses: dict[str, Course], path: Path | None = None) -> dict[str, str]:
    """This computer's folder for each course slug, without the leading and trailing slashes (how they are matched)."""
    return {slug: folder.strip("/") for slug, folder in read_folders(courses, path).items()}


def resolve_course(note: Path, courses: dict[str, Course], folders: dict[str, str], slug: str | None = None) -> Course:
    """The --course slug if given, otherwise the one course whose folder is in the note's path."""
    if slug:
        if slug not in courses:
            raise Abort(f"unknown course {slug!r} (courses: {', '.join(courses)})")
        return courses[slug]
    # Both the path as given and with symlinks resolved: vaults are often reached through a link.
    candidates = (Path(os.path.abspath(note)).parent.parts, note.resolve().parent.parts)
    matches = [s for s, folder in folders.items() if any(_contains(dirs, Path(folder).parts) for dirs in candidates)]
    if len(matches) == 1:
        return courses[matches[0]]
    if matches:
        raise Abort(f"the note's path matches several courses ({', '.join(matches)}); pick one with --course")
    raise Abort(_no_match_message(courses, folders))


def folders_template(courses: dict[str, Course]) -> str:
    lines = [
        "# This computer's vault folder for each course: `rec2notes` → Courses fills it in.",
        "# By hand: the absolute path of the folder that holds a course's notes, in single quotes,",
        "# such as reti = 'C:\\Users\\me\\Appunti\\Reti' or reti = '/home/me/Appunti/Reti'.",
        "",
    ]
    lines += [f"# {slug} = ''  # {course.name}" for slug, course in courses.items()]
    return "\n".join(lines) + "\n"


def _contains(dirs: tuple[str, ...], folder: tuple[str, ...]) -> bool:
    dirs, folder = [os.path.normcase(d) for d in dirs], [os.path.normcase(f) for f in folder]  # Windows ignores case
    n = len(folder)
    return any(dirs[i:i + n] == folder for i in range(len(dirs) - n + 1))


def _no_match_message(courses: dict[str, Course], folders: dict[str, str]) -> str:
    path = paths.folders_file()
    lines = ["could not tell the course from the note's path."]
    if not path.exists():
        lines.append(f"No folders are configured on this computer: {path} does not exist (run {paths.SETUP} to create it).")
    elif not folders:
        lines.append(f"No folders are set in {path}.")
    else:
        lines.append(f"Folders configured in {path}:")
        lines += [f"  {slug}: {folder}" for slug, folder in folders.items()]
    if courses:
        lines.append(f"Set this course's folder there, or pass --course SLUG (one of: {', '.join(courses)}).")
    else:
        lines.append("No courses are defined yet: add one with `rec2notes course add`.")
    return "\n".join(lines)


def _load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise Abort(f"{path} not found") from None
    except tomllib.TOMLDecodeError as e:
        raise Abort(f"{path}: {e}") from None
