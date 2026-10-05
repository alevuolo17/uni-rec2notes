"""The interactive menu: numbered prompts that end in the same arguments the command line takes.

Standard library only, no full-screen UI. `hub` returns the parsed arguments of a run to start, or
None if the user quit or declined; running it is the caller's job, exactly as with flags.
"""

import argparse
import os
from pathlib import Path
from typing import Callable

from . import Abort, courses, doctor, merge, paths, setup, transcribe, ui


class Quit(Exception):
    """The user left the menu: `q`, Ctrl-D or Ctrl-C at a prompt."""


Parse = Callable[[list[str]], argparse.Namespace]
CLAUDE_MODELS = (None, "opus", "sonnet", "haiku")  # None: Claude Code's default; the aliases follow its latest models


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
    console.line(f"  {console.style('4', ui.CYAN)}  Settings: your rec2notes folder, default model, effort and Whisper")
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
    return _confirm(console, ask, args, [
        ("Course", [course.name], ()),
        ("Note", [note.name], ()),
        *([("Clean", ["yes, then merge the cleaned copy",
                      f"an extra agent call on the note's {len(note.read_text(encoding='utf-8').split()):,} words"],
            ())] if clean else []),
        ("Recording", [a.name for a in audio], ()),
    ])


def _guided_create(console, ask, parse) -> argparse.Namespace | None:
    note = _ask_new_note(console, ask)
    audio = _ask_recordings(console, ask)
    course = _pick_course(console, ask, note)
    args = parse([str(note), *map(str, audio), "--course", course.slug])
    return _confirm(console, ask, args, [
        ("Course", [course.name], ()),
        ("New note", [note.name], ()),
        ("Recording", [a.name for a in audio], ()),
        ("Length", [f"about {args.length}% of the transcript"], ()),
    ])


def _confirm(console, ask, args: argparse.Namespace, rows: list[tuple[str, list[str], tuple]]) -> argparse.Namespace | None:
    """Show what will run, with the settings and anything that stops it; Enter starts it, `c` changes the
    settings for this run only."""
    sums = [transcribe.sha256_file(a) for a in args.audio]
    seconds = [transcribe.audio_seconds(a) for a in args.audio]
    while True:
        caches = [paths.transcript_cache(args.whisper_model, s) for s in sums]
        console.line()
        console.summary([*rows, *_settings_rows(args), *_transcript_rows(seconds, caches)])
        console.line()
        problems = doctor.start_problems(args.whisper_model, caches)
        if problems:
            console.line("Can't start yet:")
            for problem in problems:
                console.line(f"  - {problem}")
            console.line()
        prompt, answers = (("c to change settings, n to cancel: ", ("c", "n", "no")) if problems else
                           ("Start? [Y/n, c to change settings] ", ("", "y", "yes", "c", "n", "no")))
        while (answer := _ask(ask, prompt, allow_empty=True).lower()) not in answers:
            console.line("Type c or n." if problems else "Type y, n or c.")
        if answer in ("n", "no"):
            return None
        if answer != "c":
            return args
        args.claude_model = _ask_claude_model(console, ask, args.claude_model)
        args.effort = _ask_effort(console, ask, args.effort)
        args.whisper_model = _ask_whisper_model(console, ask, args.whisper_model) or args.whisper_model


def _settings_rows(args: argparse.Namespace) -> list[tuple[str, list[str], tuple]]:
    return [
        ("Agent", ["claude"], ()),
        ("Model", [_model_name(args.claude_model)], ()),
        ("Effort", [args.effort], ()),
        ("Whisper", [args.whisper_model], ()),
    ]


def _transcript_rows(seconds: list[float | None], caches: list[Path]) -> list[tuple[str, list[str], tuple]]:
    """The transcript's size, which is most of what the merge costs: exact for the cached parts, else guessed from
    the recordings' length."""
    words, guessed = 0, False
    for part_seconds, cache in zip(seconds, caches):
        if cache.exists():
            words += len(cache.read_text(encoding="utf-8").split())
        elif part_seconds is None:
            return [("Transcript", ["size unknown: ffmpeg can't read the recording's length"], ())]
        else:
            words += round(part_seconds / 60 * transcribe.WORDS_PER_MINUTE)
            guessed = True
    audio = ui.rough(sum(s for s in seconds if s))
    size = f"about {max(100, round(words, -2)):,} words, from {audio} of audio" if guessed else f"{words:,} words, cached"
    if words <= transcribe.LONG_TRANSCRIPT_WORDS:
        return [("Transcript", [size], ())]
    return [("Transcript", [size, "unusually long: the merge will be slow and use a lot of your agent's quota"],
             (ui.YELLOW,))]


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
        typed = _clean(_ask(ask, "New note (.md), its full path; ~ is your home folder: "))
        note = Path(os.path.abspath(os.path.expanduser(typed)))
        if note.suffix.lower() != ".md":
            note = note.with_name(note.name + ".md")
        if not note.parent.is_dir():
            hint = " Variables like $HOME aren't expanded here: use ~ for your home folder." if "$" in typed or "%" in typed else ""
            console.line(f"No such folder: {note.parent}.{hint}")
        elif note.exists():
            console.line(f"{note} already exists: pick another name.")
        else:
            return note


def _pick_course(console, ask, note: Path) -> courses.Course:
    """The course the user picks, or adds; the one whose folder holds the note, if any, is picked by Enter."""
    all_courses = courses.load_courses()
    slugs = list(all_courses)
    try:
        default = slugs.index(courses.resolve_course(note, all_courses, courses.load_folders(all_courses)).slug)
    except Abort:
        default = None
    labels = [all_courses[s].name + ("  (from the note's folder)" if i == default else "") for i, s in enumerate(slugs)]
    i = _pick(console, ask, "Which course is this?", labels, default, new="A new course")
    return _new_course(console, ask) if i is None else all_courses[slugs[i]]


def _pick(console, ask, question: str, labels: list[str], default: int | None = None,
          new: str | None = None) -> int | None:
    """The index of the label the user picks, `default` on Enter; None for `n`, offered when `new` names it."""
    console.line(question)
    for i, label in enumerate(labels, 1):
        console.line(f"  {console.style(str(i), ui.CYAN)}  {label}")
    if new:
        console.line(f"  {console.style('n', ui.CYAN)}  {new}")
    prompt = "> " if default is None else f"[{default + 1}] > "
    while True:
        choice = _ask(ask, prompt, allow_empty=default is not None).lower()
        if not choice:
            return default
        if new and choice == "n":
            return None
        if choice.isascii() and choice.isdecimal() and 1 <= int(choice) <= len(labels):
            return int(choice) - 1
        console.line(f"Type a number from 1 to {len(labels)}{', or n' if new else ''}.")


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
        console.line(f"Added {name}. No folder set, so a run won't pre-select it for a note; "
                     f"to set one, edit the course here or put {slug} = \"/absolute/path\" in {paths.folders_file()}.")
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
    """Show the rec2notes folder and the defaults runs use; point to the folder where the user moved it, change
    a default. The folder comes from the pointer, not `paths.folder()`, so this works while the folder is missing:
    that is when it is needed."""
    while True:
        folder = paths.pointed_folder()
        if folder is None:
            shown = f"none yet: run `{paths.SETUP}`"
        else:
            shown = str(folder) if folder.is_dir() else f"{folder} (missing: moved or deleted?)"
        console.line()
        console.line(f"  rec2notes folder  {shown}")
        console.line(f"  Claude model      {_setting('claude_model', _model_name)}")
        console.line(f"  Effort            {_setting('effort')}")
        console.line(f"  Whisper model     {_setting('whisper_model')}")
        console.line()
        console.line(f"  {console.style('c', ui.CYAN)}  Change the folder: point to where you moved it")
        console.line(f"  {console.style('m', ui.CYAN)}  Claude model    {console.style('e', ui.CYAN)}  Effort    "
                     f"{console.style('w', ui.CYAN)}  Whisper model    {console.style('b', ui.CYAN)}  Back")
        choice = _ask(ask, "> ").lower()
        if choice == "b":
            return
        if choice == "c":
            _repoint(console, ask)
        elif choice == "m":
            paths.save_setting("claude_model", _ask_claude_model(console, ask, paths.setting_choice("claude_model")))
        elif choice == "e":
            paths.save_setting("effort", _ask_effort(console, ask, paths.setting_choice("effort")))
        elif choice == "w" and (model := _ask_whisper_model(console, ask, paths.whisper_model_choice())):
            paths.save_setting("whisper_model", model)
        else:
            console.line("Type c, m, e, w or b.")


def _setting(key: str, show: Callable[[str | None], str] = str) -> str:
    """A default as runs use it, saying when an environment variable sets it and the saved one is ignored."""
    env = paths.SETTING_ENV[key]
    return show(paths.setting_choice(key)) + (f"  (from ${env}, which wins over this screen)" if os.environ.get(env) else "")


def _model_name(model: str | None) -> str:
    return model or "Claude Code's default"


def _ask_claude_model(console, ask, current: str | None) -> str | None:
    default = CLAUDE_MODELS.index(current) if current in CLAUDE_MODELS else None
    return CLAUDE_MODELS[_pick(console, ask, "Claude model:", [_model_name(m) for m in CLAUDE_MODELS], default)]


def _ask_effort(console, ask, current: str) -> str:
    default = merge.EFFORTS.index(current) if current in merge.EFFORTS else None
    return merge.EFFORTS[_pick(console, ask, "Effort, how hard Claude thinks (higher is slower):", list(merge.EFFORTS),
                               default)]


def _ask_whisper_model(console, ask, current: str) -> str | None:
    """One of the downloaded Whisper models, or None if there is none."""
    models = paths.downloaded_whisper_models()
    if not models:
        console.line(f"No Whisper model is downloaded: run `{paths.SETUP}`.")
        return None
    labels = [f"{m}  ({setup.MODELS[m]})" if m in setup.MODELS else m for m in models]
    return models[_pick(console, ask, "Whisper model (more with `rec2notes setup`):", labels,
                        models.index(current) if current in models else None)]


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
    """A path as typed, pasted or dragged into the terminal: without quotes, or backslash-escaped spaces off Windows (there a backslash separates folders)."""
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    return text if paths.WINDOWS else text.replace("\\ ", " ")


def _ask(ask, prompt: str, allow_empty: bool = False) -> str:
    while True:
        try:
            answer = ask(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise Quit from None
        if answer or allow_empty:
            return answer
