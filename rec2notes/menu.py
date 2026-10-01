"""The interactive menu: numbered prompts that end in the same arguments the command line takes.

Standard library only, no full-screen UI. `hub` returns the parsed arguments of a run to start, or
None if the user quit or declined; running it is the caller's job, exactly as with flags.
"""

import argparse
import os
from pathlib import Path
from typing import Callable

from . import Abort, courses, doctor, paths, ui


class Quit(Exception):
    """The user left the menu: `q`, Ctrl-D or Ctrl-C at a prompt."""


Parse = Callable[[list[str]], argparse.Namespace]


def hub(console: ui.Console, ask: Callable[[str], str], parse: Parse, parse_create: Parse) -> argparse.Namespace | None:
    """`parse` and `parse_create` turn the answers into the arguments of a run and of `rec2notes create`."""
    try:
        console.banner()
        while True:
            _options(console)
            choice = _ask(ask, "> ").lower()
            try:  # a screen that can't go on says why and comes back here, where Settings may fix it
                if choice == "1":
                    what = _run_choice(console, ask)
                    if what == "1":
                        return _guided_run(console, ask, parse)
                    if what == "2":
                        return _guided_create(console, ask, parse_create)
                elif choice == "2":
                    doctor.run(console)
                elif choice == "3":
                    _courses(console, ask)
                elif choice == "4":
                    _settings(console, ask)
                elif choice == "q":
                    return None
                elif choice not in ("h", "?", "help"):
                    console.line("Not an option.")
            except Abort as e:
                console.line(str(e))
    except Quit:
        console.line()
        return None


def _options(console: ui.Console) -> None:
    """The hub's options, shown before every prompt so the user always sees what can be typed."""
    console.line()
    console.line(f"  {console.style('1', ui.CYAN)}  Run: complete a note, or create one, from a recording")
    console.line(f"  {console.style('2', ui.CYAN)}  Doctor: check that everything is set up")
    console.line(f"  {console.style('3', ui.CYAN)}  Courses: list them, add your own")
    console.line(f"  {console.style('4', ui.CYAN)}  Settings: where your rec2notes folder is")
    console.line(f"  {console.style('q', ui.CYAN)}  Quit")
    console.line()


def _run_choice(console, ask) -> str:
    """"1" to complete a note, "2" to create one, "b" to go back."""
    console.line()
    console.line(f"  {console.style('1', ui.CYAN)}  Complete a note from its recording")
    console.line(f"  {console.style('2', ui.CYAN)}  Create a note from a recording, for a lecture with no notes")
    console.line(f"  {console.style('b', ui.CYAN)}  Back")
    console.line()
    while (choice := _ask(ask, "> ").lower()) not in ("1", "2", "b"):
        console.line("Type 1, 2 or b.")
    return choice


def _guided_run(console, ask, parse) -> argparse.Namespace | None:
    note = _ask_file(console, ask, "Note (.md): ")
    clean = _yes(console, ask, "Clean the note first? It is copied to '(pulito)' and the copy is merged. [y/N] ", default=False)
    audio = _ask_recordings(console, ask)
    course = _pick_course(console, ask, note)
    args = parse([str(note), *map(str, audio), "--course", course.slug, *(["--clean"] if clean else [])])

    console.line()
    console.summary([
        ("Course", [course.name], ()),
        ("Note", [note.name], ()),
        *([("Clean", ["yes, then merge the cleaned copy"], ())] if clean else []),
        ("Recording", [a.name for a in audio], ()),
        *_settings_rows(args),
    ])
    console.line()
    return args if _yes(console, ask, "Start? [Y/n] ") else None


def _guided_create(console, ask, parse) -> argparse.Namespace | None:
    note = _ask_new_note(console, ask)
    audio = _ask_recordings(console, ask)
    course = _pick_course(console, ask, note)
    args = parse([str(note), *map(str, audio), "--course", course.slug])

    console.line()
    console.summary([
        ("Course", [course.name], ()),
        ("New note", [note.name], ()),
        ("Recording", [a.name for a in audio], ()),
        ("Length", [f"about {args.length}% of the transcript"], ()),
        *_settings_rows(args),
    ])
    console.line()
    return args if _yes(console, ask, "Start? [Y/n] ") else None


def _settings_rows(args: argparse.Namespace) -> list[tuple[str, list[str], tuple]]:
    return [
        ("Agent", ["claude"], ()),
        ("Model", [args.claude_model or "Claude Code's default"], ()),
        ("Effort", [args.effort], ()),
        ("Whisper", [args.whisper_model], ()),
    ]


def _ask_recordings(console, ask) -> list[Path]:
    """The recording, and any further parts of the same lecture, in order."""
    audio = [_ask_file(console, ask, "Recording: ")]
    while part := _ask(ask, "Another part of the same lecture? Path, or Enter if none: ", allow_empty=True):
        if path := _file_or_none(console, part):
            audio.append(path)
    return audio


def _ask_new_note(console, ask) -> Path:
    """The path of a note to create: in a folder that exists, not taken; ".md" is added when missing."""
    while True:
        note = Path(os.path.abspath(os.path.expanduser(_clean(_ask(ask, "New note, full path (.md): ")))))
        if note.suffix.lower() != ".md":
            note = note.with_name(note.name + ".md")
        if not note.parent.is_dir():
            console.line(f"No such folder: {note.parent}")
        elif note.exists():
            console.line(f"{note} already exists: pick another name.")
        else:
            return note


def _pick_course(console, ask, note: Path) -> courses.Course:
    """The course from the note's folder; if that tells nothing, the one the user picks (or adds), and remembers."""
    all_courses = courses.load_courses()
    try:
        return courses.resolve_course(note, all_courses, courses.load_folders(all_courses))
    except Abort:
        pass
    console.line("Which course is this?")
    slugs = list(all_courses)
    for i, slug in enumerate(slugs, 1):
        console.line(f"  {console.style(str(i), ui.CYAN)}  {all_courses[slug].name}")
    console.line(f"  {console.style('n', ui.CYAN)}  A new course")
    while True:
        choice = _ask(ask, "> ").lower()
        if choice == "n":
            course = _new_course(console, ask)
            break
        if choice.isascii() and choice.isdecimal() and 1 <= int(choice) <= len(slugs):
            course = all_courses[slugs[int(choice) - 1]]
            break
        console.line(f"Type a number from 1 to {len(slugs)}, or n.")
    try:
        courses.check_folder_free(note.parent, course.slug)
    except Abort as e:
        console.line(f"Not remembering the folder: {e}")
    else:
        if _yes(console, ask, f"Remember {note.parent} as the folder of {course.name}? [Y/n] "):
            courses.set_folder(course.slug, str(note.parent))
    return course


def _courses(console, ask) -> None:
    """List the courses and their folders; add one, or change one."""
    while True:
        all_courses = courses.load_courses()
        folders = courses.load_folders(all_courses)
        console.line()
        for slug, course in all_courses.items():
            console.line(f"  {console.style(slug, ui.CYAN)}  {course.name}  [{folders.get(slug, 'no folder')}]")
        console.line()
        console.line(f"  {console.style('a', ui.CYAN)}  Add a course    {console.style('e', ui.CYAN)}  Edit a course    "
                     f"{console.style('b', ui.CYAN)}  Back")
        choice = _ask(ask, "> ").lower()
        if choice == "b":
            return
        if choice == "a":
            _new_course(console, ask, with_folder=True)
        elif choice == "e" and all_courses:
            _edit_course(console, ask, all_courses)
        else:
            console.line("Type a, e or b.")


def _edit_course(console, ask, all_courses: dict[str, courses.Course]) -> None:
    """Change a course's name, vocab and folder; Enter keeps what it has. Nothing is written until all is valid."""
    while (slug := _ask(ask, "Which course (short name)? ")) not in all_courses:
        console.line(f"Not a course: {slug}")
    course = all_courses[slug]
    new_slug = _ask_new_slug(console, ask, slug)
    name = _ask(ask, f"Course name [{course.name}]: ", allow_empty=True)
    vocab = _ask(ask, f"Whisper vocabulary, Enter keeps this one:\n{course.vocab}\n", allow_empty=True)
    folder = _ask_folder(console, ask, slug, keep=True)
    if name or vocab:
        courses.update_course(slug, name or None, vocab or None)
    if folder:
        courses.set_folder(slug, folder)
    if new_slug:  # last, so the other changes find the course under its old slug
        courses.rename_course(slug, new_slug)
    console.line(f"Updated {name or course.name}.")


def _ask_new_slug(console, ask, slug: str) -> str | None:
    """A new short name for the course (its folder is kept), or None to keep [slug]."""
    while new := _ask(ask, f"Short name [{slug}]: ", allow_empty=True):
        if new == slug:
            return None
        try:
            courses.check_new_slug(new)
            return new
        except Abort as e:
            console.line(str(e))
    return None


def _new_course(console, ask, with_folder: bool = False) -> courses.Course:
    """Ask for a new course and its folder, then save them; nothing is written until all of it is valid."""
    while True:
        slug = _ask(ask, "Short name, lowercase (e.g. reti): ")
        try:
            courses.check_new_slug(slug)
            break
        except Abort as e:
            console.line(str(e))
    name = _ask(ask, "Course name: ")
    vocab = _ask(ask, "Whisper vocabulary: a sentence with 15-30 key terms, acronyms and English technical words:\n")
    folder = _ask_folder(console, ask, slug) if with_folder else None
    courses.add_course(slug, name, vocab)
    if folder:
        courses.set_folder(slug, folder)
    if folder:
        console.line(f"Added {name}, with the folder {folder}.")
    elif with_folder:
        console.line(f"Added {name}. No folder set: a run will ask which course a note belongs to, "
                     f"or put {slug} = \"/absolute/path\" in {paths.folders_file()}.")
    else:
        console.line(f"Added {name}.")
    return courses.load_courses()[slug]


def _ask_folder(console, ask, slug: str, keep: bool = False) -> str | None:
    """The folder of a course's notes as an absolute path that exists (offering to create it), or None to skip."""
    while True:
        typed = _ask(ask, "Folder that holds this course's notes, as an absolute path "
                          f"(used as it is, no subfolder is made; Enter to {'keep it' if keep else 'skip'}): ", allow_empty=True)
        if not typed:
            return None
        try:
            folder = courses.absolute_folder(_clean(typed))
            courses.check_folder_free(folder, slug)
        except Abort as e:
            console.line(str(e))
            continue
        if folder.is_dir():
            return str(folder)
        if _yes(console, ask, f"{folder} does not exist. Create it? [y/N] ", default=False):
            try:
                folder.mkdir(parents=True)
            except OSError as e:
                console.line(f"Could not create {folder}: {e.strerror}")
                continue
            return str(folder)


def _settings(console, ask) -> None:
    """Show the rec2notes folder, and point to it where the user moved it. It reads the pointer, not
    `paths.folder()`, so it works while the folder is missing: that is when it is needed."""
    while True:
        folder = paths.pointed_folder()
        if folder is None:
            shown = f"none yet: run `{paths.SETUP}`"
        else:
            shown = str(folder) if folder.is_dir() else f"{folder} (missing: moved or deleted?)"
        console.line()
        console.line(f"  rec2notes folder  {shown}")
        console.line()
        console.line(f"  {console.style('c', ui.CYAN)}  Change: point to where you moved it    "
                     f"{console.style('b', ui.CYAN)}  Back")
        choice = _ask(ask, "> ").lower()
        if choice == "b":
            return
        if choice == "c":
            _repoint(console, ask)
        else:
            console.line("Type c or b.")


def _repoint(console, ask) -> None:
    """Point to a rec2notes folder the user moved; it moves and creates nothing."""
    while typed := _ask(ask, "Where is your rec2notes folder now? Absolute path, Enter to keep the current one: ",
                        allow_empty=True):
        folder = Path(os.path.expanduser(_clean(typed)))
        if not folder.is_absolute():
            start = "a drive, like C:\\," if paths.WINDOWS else "/"
            console.line(f"Type an absolute path: it starts with {start} or ~")
            continue
        folder = Path(os.path.normpath(folder))
        if (folder / "courses.toml").is_file() or (folder / "whisper.cpp" / "models").is_dir():
            paths.write_pointer(folder)
            console.line(f"Now using {folder}.")
            return
        console.line(f"{folder} doesn't look like a rec2notes folder: it has no courses.toml and no "
                     f"whisper.cpp/models. To make a new one, run `{paths.SETUP}`.")


def _yes(console, ask, prompt: str, default: bool = True) -> bool:
    while True:
        answer = _ask(ask, prompt, allow_empty=True).lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        console.line("Type y or n.")


def _ask_file(console, ask, prompt: str) -> Path:
    while True:
        path = _file_or_none(console, _ask(ask, prompt))
        if path:
            return path


def _file_or_none(console, text: str) -> Path | None:
    path = Path(os.path.abspath(os.path.expanduser(_clean(text))))
    if path.is_file():
        return path
    console.line(f"Not a file: {path}")
    return None


def _clean(text: str) -> str:
    """A path as typed, pasted or dragged into the terminal: without quotes or backslash-escaped spaces."""
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return text.replace("\\ ", " ")


def _ask(ask, prompt: str, allow_empty: bool = False) -> str:
    while True:
        try:
            answer = ask(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise Quit from None
        if answer or allow_empty:
            return answer
