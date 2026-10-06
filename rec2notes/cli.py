"""rec2notes: complete a lecture note from its recording, into a sibling file."""

import argparse
import os
import re
import shlex
import shutil
import sys
from datetime import datetime
from pathlib import Path

from . import Abort, version, check, courses, doctor, menu, merge, paths, setup, stopping, transcribe, ui, uninstall

COMPLETED_MARKERS = ("[^conflitto-", check.MISSED_TOPICS_HEADING, "[^non-annotati]")  # the last: the old missed-topics footnote
CREATE_LENGTH = 20  # the created note's length, in % of the transcript's words; to settle from real runs
WARNING_PASSAGES = 5  # how many changed passages to show in the terminal; check.txt has all


class Parser(argparse.ArgumentParser):
    def print_help(self, file=None):
        """The help, under the banner when it goes to a terminal wide enough for it."""
        console = ui.Console(file or sys.stdout)
        if console.banner():
            console.line()
        super().print_help(file)


def build_parser() -> Parser:
    p = Parser(
        prog="rec2notes",
        description="Complete a formatted lecture note from the lecture recording. "
                    "Writes '<note> (completo).md' next to the note and never modifies the note.",
    )
    p.epilog = ("`rec2notes create NOTE AUDIO` writes a new note from a recording alone. "
                "`rec2notes clean NOTE` cleans a raw note first. "
                "`rec2notes doctor` checks that everything a run needs is in place; `rec2notes setup` installs it, `rec2notes uninstall` removes it.")
    p.add_argument("note", type=Path, help="the formatted note (.md)")
    p.add_argument("audio", type=Path, nargs="+", help="the recording; several files are parts of one lecture, in order")
    p.add_argument("--course", metavar="SLUG", help="course slug, see `rec2notes course list` (default: from the note's folder)")
    add_whisper_option(p)
    add_agent_options(p)
    p.add_argument("--clean", action="store_true",
                   help="first clean the note into '<note> (pulito).md' (see `rec2notes clean`) and merge that copy")
    p.add_argument("--force", action="store_true", help="replace an existing '(completo)' or '(pulito)' file, keeping a copy in the run directory")
    p.add_argument("--dry-run", action="store_true", help="print the assembled input (cached transcripts only) and run nothing")
    p.add_argument("--version", action="version", version=f"rec2notes {version()}")
    p.set_defaults(create=False)
    return p


def add_whisper_option(p: argparse.ArgumentParser) -> None:
    p.add_argument("--whisper-model", metavar="NAME",
                   help="Whisper model, e.g. large-v3-turbo (default: $REC2NOTES_WHISPER_MODEL, else the one saved by setup or in `rec2notes` → Settings)")


def add_agent_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--agent", choices=tuple(merge.AGENTS),
                   help="the agent that writes the note: claude (Claude Code) or antigravity (Antigravity's agy) "
                        "(default: $REC2NOTES_AGENT, else the one saved in `rec2notes` → Settings, else claude)")
    p.add_argument("--model", metavar="MODEL",
                   help="the agent's model: for claude e.g. opus or sonnet (default: Claude Code's), for antigravity one "
                        f"that `agy models` lists (default: {paths.ANTIGRAVITY_MODEL}). Default: $REC2NOTES_MODEL, "
                        "else the one saved in `rec2notes` → Settings")
    p.add_argument("--claude-model", dest="model", help=argparse.SUPPRESS)  # the old name of --model
    p.add_argument("--effort", metavar="LEVEL",
                   help=f"claude's effort: {', '.join(merge.EFFORTS)} (default: $REC2NOTES_EFFORT, else the one saved in "
                        "`rec2notes` → Settings, else high). Antigravity's model names carry theirs")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    return _checked(build_parser(), argv)


def parse_clean_args(argv: list[str]) -> argparse.Namespace:
    p = Parser(prog="rec2notes clean",
               description="Clean a raw lecture note with the clean prompt. "
                           "Writes '<note> (pulito).md' next to the note and never modifies the note.")
    p.add_argument("note", type=Path, help="the raw note (.md)")
    add_agent_options(p)
    p.add_argument("--force", action="store_true", help="replace an existing '(pulito)' file, keeping a copy in the run directory")
    return _checked(p, argv)


def parse_create_args(argv: list[str]) -> argparse.Namespace:
    p = Parser(prog="rec2notes create",
               description="Write a new study note from a lecture recording alone, for a lecture with no notes. "
                           "Writes NOTE, which must not exist yet.")
    p.add_argument("note", type=Path, help="the note to create (.md); its folder tells the course")
    p.add_argument("audio", type=Path, nargs="+", help="the recording; several files are parts of one lecture, in order")
    p.add_argument("--course", metavar="SLUG", help="course slug, see `rec2notes course list` (default: from the note's folder)")
    p.add_argument("--length", metavar="PCT", type=int, default=CREATE_LENGTH,
                   help=f"the note's length to aim for, in %% of the transcript's words (default: {CREATE_LENGTH})")
    add_whisper_option(p)
    add_agent_options(p)
    p.add_argument("--force", action="store_true", help="replace an existing NOTE, keeping a copy in the run directory")
    p.add_argument("--dry-run", action="store_true", help="print the assembled input (cached transcripts only) and run nothing")
    p.set_defaults(create=True)
    args = _checked(p, argv)
    if not 5 <= args.length <= 60:
        p.error(f"--length must be between 5 and 60, not {args.length}")
    return args


def _checked(p: argparse.ArgumentParser, argv: list[str] | None) -> argparse.Namespace:
    args = p.parse_args(argv)
    args.agent = args.agent or paths.setting_choice("agent")
    if args.agent not in merge.AGENTS:
        p.error(f"the agent must be one of {', '.join(merge.AGENTS)}, not {args.agent!r}")
    args.model = args.model or paths.model_choice(args.agent)
    if args.agent == "claude":
        args.effort = args.effort or paths.setting_choice("effort")
        if args.effort not in merge.EFFORTS:
            p.error(f"the effort must be one of {', '.join(merge.EFFORTS)}, not {args.effort!r}")
    else:
        if args.effort:
            print(f"rec2notes: --effort is for claude; {merge.AGENTS[args.agent].label}'s model names carry the effort "
                  "(e.g. -high), so it is ignored", file=sys.stderr)
        args.effort = None
    if "whisper_model" in args and not args.whisper_model:
        args.whisper_model = paths.whisper_model_choice()
    return args


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["doctor"]:
        return doctor.run(ui.Console())
    if argv[:1] == ["setup"]:
        return setup.main(argv[1:])
    if argv[:1] == ["uninstall"]:
        return uninstall.main(argv[1:])
    if argv[:1] == ["course"]:
        return course_command(argv[1:])
    clean_only, create = argv[:1] == ["clean"], argv[:1] == ["create"]
    menu_wanted = not argv and on_terminal()
    if not argv and not menu_wanted:  # bare `rec2notes` in a pipe: show what it is and how to use it
        build_parser().print_help()
        return 0
    console = ui.Console()
    prefix = console.style("rec2notes:", ui.BOLD, ui.RED) if console.err.isatty() else "rec2notes:"
    try:
        args = (None if menu_wanted else parse_clean_args(argv[1:]) if clean_only
                else parse_create_args(argv[1:]) if create else parse_args(argv))
        if menu_wanted:  # bare `rec2notes` in a terminal: the menu picks the run
            args = menu.hub(console, input, parse_args, parse_create_args)
            if args is None:
                return 0
        with stopping.handling():
            if clean_only:
                return run_clean(args, console)
            return run_create(args, console) if args.create else run(args, console)
    except Abort as e:
        console.line(f"{prefix} {e}", err=True)
        return 1
    except stopping.Stopped as e:
        console.line(f"{prefix} {e.message}", err=True)
        return e.status


def course_command(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="rec2notes course", description="List the courses, add one, or change one of your own.")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help="list the courses and this computer's folder for each")
    add = sub.add_parser("add", help="add a course to courses.toml in your rec2notes folder")
    add.add_argument("slug", help="short name used with --course, e.g. reti")
    add.add_argument("name", help="the course name, shown to the merge step")
    add.add_argument("vocab", help="Whisper's initial prompt: a sentence with 15-30 key terms")
    add.add_argument("--folder", metavar="PATH", help="the folder of this course's notes in your vault, as an absolute path")
    add.add_argument("--create", action="store_true", help="create the --folder if it doesn't exist")
    edit = sub.add_parser("edit", help="change a course's name, vocab or folder")
    edit.add_argument("slug", help="the course to change")
    edit.add_argument("--slug", dest="new_slug", help="the new short name (its folder is kept)")
    edit.add_argument("--name", help="the new course name")
    edit.add_argument("--vocab", help="the new Whisper initial prompt")
    edit.add_argument("--folder", metavar="PATH", help="the new folder of this course's notes, as an absolute path")
    edit.add_argument("--create", action="store_true", help="create the --folder if it doesn't exist")
    args = p.parse_args(argv)
    try:
        if args.action == "edit":
            if args.slug not in courses.load_courses():
                raise Abort(f"unknown course {args.slug!r} (courses: {', '.join(courses.load_courses())})")
            if args.name is None and args.vocab is None and not args.folder and not args.new_slug:
                raise Abort("nothing to change: pass --slug, --name, --vocab or --folder")
            if args.new_slug:
                courses.check_new_slug(args.new_slug)
            folder = _folder_to_use(args)  # before anything is written
            if args.name is not None or args.vocab is not None:
                courses.update_course(args.slug, args.name, args.vocab)
            if folder:
                courses.set_folder(args.slug, str(folder))
            if args.new_slug:  # last, so the other changes find the course under its old slug
                courses.rename_course(args.slug, args.new_slug)
            print(f"Updated course {args.new_slug or args.slug}")
        elif args.action == "add":
            folder = _folder_to_use(args)  # before anything is written
            courses.add_course(args.slug, args.name, args.vocab)
            print(f"Added course {args.slug} to {paths.courses_file()}")
            if folder:
                courses.set_folder(args.slug, str(folder))
                print(f"Folder of {args.slug}: {folder}")
        else:
            all_courses = courses.load_courses()
            folders = courses.load_folders(all_courses)
            for slug, course in all_courses.items():
                print(f"{slug:<10} {course.name}  [{folders.get(slug, 'no folder')}]")
    except Abort as e:
        print(f"rec2notes: {e}", file=sys.stderr)
        return 1
    return 0


def _folder_to_use(args: argparse.Namespace) -> Path | None:
    """The checked --folder of `course add|edit`, created when --create says so."""
    folder = courses.absolute_folder(args.folder) if args.folder else None
    if folder:
        courses.check_folder_free(folder, args.slug)
    if folder and not folder.is_dir():
        if not args.create:
            raise Abort(f"{folder} does not exist (pass --create to make it)")
        try:
            folder.mkdir(parents=True)
        except OSError as e:
            raise Abort(f"could not create {folder}: {e.strerror}") from None
    return folder


def run(args: argparse.Namespace, console: ui.Console) -> int:
    given = [args.note, *args.audio]
    note = Path(os.path.abspath(args.note))
    if not note.is_file():
        raise Abort(f"note not found: {args.note}{_unquoted_hint(given, 0)}")
    for i, audio in enumerate(args.audio, 1):
        if not audio.is_file():
            raise Abort(f"audio file not found: {audio}{_unquoted_hint(given, i)}")
    note_text = note.read_text(encoding="utf-8")
    if any(marker in note_text for marker in COMPLETED_MARKERS):
        raise Abort(f"{note.name} already has conflict footnotes or missed topics, "
                    "so it looks like a completed note. Pass the original note instead.")
    cleaned = clean_path(note)
    if args.clean:
        check_free(cleaned, args.force)
    merged = cleaned if args.clean else note  # the note the merge completes
    output = merged.with_name(f"{merged.stem} (completo).md")
    check_free(output, args.force)

    all_courses = courses.load_courses()
    folders = {} if args.course else courses.load_folders(all_courses)
    course = courses.resolve_course(note, all_courses, folders, args.course)
    caches = [paths.transcript_cache(args.whisper_model, transcribe.sha256_file(a)) for a in args.audio]

    if args.dry_run:
        return dry_run(args, course, note_text, caches)

    preflight(args, args.whisper_model, caches)
    run_dir = make_run_dir(note.stem)
    console.header(course.name, note.name)
    tools = []
    if args.clean:
        cleaned_reply = clean_note(note_text, cleaned, run_dir, args, console)
        note_text, tools = cleaned_reply.text, cleaned_reply.tools

    transcript = transcribe_all(args, course, caches, run_dir, console)
    confirm_size(transcript, console)
    input_text = merge.build_input(course, note_text, transcript)
    (run_dir / "input.txt").write_text(input_text, encoding="utf-8")
    merged_reply = call_agent(console, "Merge", agent_detail(args), args, input_text, run_dir)
    reply, tools = merged_reply.text, [*tools, *(t for t in merged_reply.tools if t not in tools)]
    if not reply.endswith("\n"):
        reply += "\n"

    changes = check.missing_passages(note_text, reply)
    conflicts, missed = check.summary(reply)
    (run_dir / "check.txt").write_text(check.report(changes, conflicts, missed), encoding="utf-8")
    if changes:
        console.mark("warn", "Check", f"{len(changes)} passage{'s' if len(changes) > 1 else ''} missing or changed")
    else:
        console.mark("ok", "Check", "only additions")
    if check.TRANSCRIPT_MISMATCH in reply:
        console.mark("warn", "Check", "the agent says the transcript doesn't match this note")
    stopping.check()
    write_output(output, reply, run_dir, args.force)
    console.mark("ok", "Write", output.name)

    rows = [("Conflicts", [str(conflicts)], (ui.DIM,)), ("Missed topics", ["yes" if missed else "no"], (ui.DIM,))]
    if changes:
        shown = [check.describe(c, width=60) for c in changes[:WARNING_PASSAGES]]
        if len(changes) > WARNING_PASSAGES:
            shown.append(f"…and {len(changes) - WARNING_PASSAGES} more, see check.txt")
        rows.append(("Changed", shown, (ui.YELLOW,)))
    rows += tools_rows(tools)
    rows.append(("Run", [_home(run_dir)], (ui.DIM,)))
    console.line()
    console.summary(rows)
    return 0


def transcribe_all(args: argparse.Namespace, course: courses.Course, caches: list[Path], run_dir: Path,
                   console: ui.Console) -> str:
    """Transcribe each part not cached yet; returns the whole lecture's transcript."""
    for part, (audio, cache) in enumerate(zip(args.audio, caches), 1):
        which = f"part {part}/{len(args.audio)} · " if len(args.audio) > 1 else ""
        if cache.exists():
            console.mark("ok", "Transcribe", f"{which}cached · {args.whisper_model}")
            continue
        with console.step("Transcribe", f"{which}{audio.name} · {args.whisper_model}") as step:
            seconds = transcribe.transcribe(audio, cache, args.whisper_model, course.vocab, run_dir, part, step.progress)
            length = f"{ui.duration(seconds)} of audio · " if seconds else ""
            step.end("ok", f"{which}{length}{args.whisper_model}", ui.duration(step.elapsed))
    transcript = transcribe.label_parts([c.read_text(encoding="utf-8") for c in caches])
    if not transcript.strip():
        raise Abort("the transcript is empty: Whisper found no speech in the audio")
    return transcript


def run_create(args: argparse.Namespace, console: ui.Console) -> int:
    output = Path(os.path.abspath(args.note))
    if output.suffix.lower() != ".md":
        output = output.with_name(output.name + ".md")
    given = [args.note, *args.audio]
    for i, audio in enumerate(args.audio, 1):
        if not audio.is_file():
            raise Abort(f"audio file not found: {audio}{_unquoted_hint(given, i)}")
    if not output.parent.is_dir():
        raise Abort(f"the note's folder does not exist: {output.parent}")
    check_free(output, args.force)

    all_courses = courses.load_courses()
    folders = {} if args.course else courses.load_folders(all_courses)
    course = courses.resolve_course(output, all_courses, folders, args.course)
    caches = [paths.transcript_cache(args.whisper_model, transcribe.sha256_file(a)) for a in args.audio]

    if args.dry_run:
        transcript = cached_transcript(args, caches)
        words = spoken_words(transcript)
        sys.stdout.write(merge.build_create_input(course, transcript, words, target_words(words, args.length)))
        print_command(args, paths.CREATE_PROMPT)
        return 0

    preflight(args, args.whisper_model, caches)
    run_dir = make_run_dir(output.stem)
    console.header(course.name, output.name)
    transcript = transcribe_all(args, course, caches, run_dir, console)
    confirm_size(transcript, console)
    words = spoken_words(transcript)
    input_text = merge.build_create_input(course, transcript, words, target_words(words, args.length))
    (run_dir / "input.txt").write_text(input_text, encoding="utf-8")
    created = call_agent(console, "Create", f"{agent_detail(args)} · length {args.length}%", args, input_text, run_dir,
                         prompt=paths.CREATE_PROMPT)
    reply = created.text
    if not reply.endswith("\n"):
        reply += "\n"
    stopping.check()
    write_output(output, reply, run_dir, args.force)
    console.mark("ok", "Write", output.name)

    note_words = len(reply.split())
    length = f"{note_words:,} words, {round(100 * note_words / max(words, 1))}% of the transcript (aimed at {args.length}%)"
    console.line()
    console.summary([("Length", [length], (ui.DIM,)), *tools_rows(created.tools), ("Run", [_home(run_dir)], (ui.DIM,))])
    return 0


def spoken_words(transcript: str) -> int:
    """The transcript's words, without its timestamps."""
    return len(re.sub(r"^\[[^\]]*\]", "", transcript, flags=re.M).split())


def target_words(words: int, percent: int) -> int:
    return max(100, round(words * percent / 100, -2))


def confirm_size(transcript: str, console: ui.Console) -> None:
    """Show the transcript's size before the merge; when it is unusually long, ask on a terminal, else warn."""
    words = len(transcript.split())
    if words <= transcribe.LONG_TRANSCRIPT_WORDS:
        console.mark("ok", "Transcript", f"{words:,} words")
        return
    console.mark("warn", "Transcript", f"{words:,} words, unusually long")
    if on_terminal():
        try:
            answer = input("The merge will be slow and use a lot of your agent's quota. Continue? [Y/n] ")
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer.strip().lower() in ("n", "no"):
            raise Abort("stopped before the merge; the transcript is cached, so a rerun skips transcribing")


def run_clean(args: argparse.Namespace, console: ui.Console) -> int:
    note = Path(os.path.abspath(args.note))
    if not note.is_file():
        raise Abort(f"note not found: {args.note}")
    note_text = note.read_text(encoding="utf-8")
    output = clean_path(note)
    check_free(output, args.force)
    preflight(args, paths.default_whisper_model(), [])  # no recording: nothing to transcribe
    run_dir = make_run_dir(note.stem)
    cleaned = clean_note(note_text, output, run_dir, args, console)
    console.line()
    console.summary([*tools_rows(cleaned.tools), ("Run", [_home(run_dir)], (ui.DIM,))])
    return 0


def clean_path(note: Path) -> Path:
    return note.with_name(f"{note.stem} (pulito).md")


def clean_note(note_text: str, output: Path, run_dir: Path, args: argparse.Namespace, console: ui.Console) -> merge.Reply:
    """Clean the note with the agent and write the result to `output`; returns the agent's reply."""
    cleaned = call_agent(console, "Clean", agent_detail(args), args, note_text, run_dir,
                         prompt=paths.CLEAN_PROMPT, file_prefix="clean-")
    reply = cleaned.text if cleaned.text.endswith("\n") else cleaned.text + "\n"
    stopping.check()
    write_output(output, reply, run_dir, args.force, "clean-reply.md")
    console.mark("ok", "Write", output.name)
    return merge.Reply(reply, cleaned.tools)


def call_agent(console: ui.Console, label: str, detail: str, args: argparse.Namespace, input_text: str, run_dir: Path,
               **options) -> merge.Reply:
    """One agent call as a checklist step, warning when the agent used a tool."""
    with console.step(label, detail) as step:
        reply = merge.run_agent(args.agent, input_text, run_dir, args.effort, args.model,
                                notify=lambda message: step.note("warn", message), **options)
        if reply.tools:
            step.note("warn", f"the agent used {', '.join(reply.tools)}: check the note")
        step.end("ok", step.detail, ui.duration(step.elapsed))
    return reply


def tools_rows(tools: list[str]) -> list[tuple[str, list[str], tuple]]:
    """The summary's warning about tools the agent used: what they brought in isn't from the lecture, and a
    transcript may hold instructions that made the agent use them."""
    if not tools:
        return []
    return [("Tools", [f"the agent used {', '.join(tools)}", "check the note for text that isn't from the lecture"],
             (ui.YELLOW,))]


def agent_detail(args: argparse.Namespace) -> str:
    if args.agent != "claude":
        return f"{args.agent} {args.model}"
    claude = f"claude {args.model}" if args.model else "claude"
    return f"{claude} · effort {args.effort}"


def check_free(output: Path, force: bool) -> None:
    if output.exists() and not force:
        raise Abort(f"{output} already exists. Pass --force to replace it (the old file is kept in the run directory).")


def dry_run(args: argparse.Namespace, course: courses.Course, note_text: str, caches: list[Path]) -> int:
    if args.clean:
        print("(dry run) would first clean the note; the input below is the note as it is", file=sys.stderr)
    sys.stdout.write(merge.build_input(course, note_text, cached_transcript(args, caches)))
    print_command(args, paths.MERGE_PROMPT)
    return 0


def print_command(args: argparse.Namespace, prompt: Path) -> None:
    print(f"(dry run) would pipe this to: {shlex.join(merge.command(args.agent, args.effort, args.model, prompt))}",
          file=sys.stderr)
    if args.agent == "antigravity":
        print(f"(dry run) with the prompt {prompt.name} at its top, as one stream-json line, and HOME at a throwaway "
              "folder holding rec2notes' settings (no tools), deleted afterwards", file=sys.stderr)


def cached_transcript(args: argparse.Namespace, caches: list[Path]) -> str:
    """The transcript a dry run shows: the cached parts, and a placeholder for each part not transcribed yet."""
    parts = []
    for audio, cache in zip(args.audio, caches):
        if cache.exists():
            parts.append(cache.read_text(encoding="utf-8"))
        else:
            print(f"(dry run) no cached {args.whisper_model} transcript of {audio.name}; a real run would transcribe it",
                  file=sys.stderr)
            parts.append(f"(not transcribed yet: {audio.name})\n")
    return transcribe.label_parts(parts)


def on_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def confirm_agent(args: argparse.Namespace) -> None:
    """Antigravity sends the note and transcript to Google: say so and ask once, on a terminal, before anything is
    sent. The answer is saved in settings.toml."""
    if args.agent != "antigravity" or menu.antigravity_agreed():
        return
    if not on_terminal():
        raise Abort("Antigravity sends your note and transcript to Google: run rec2notes with --agent antigravity "
                    "once in a terminal, or pick it in `rec2notes` → Settings, to read what that means and confirm it")
    try:
        agreed = menu.agree_to_antigravity(ui.Console(), input)
    except menu.Quit:
        agreed = False
    if not agreed:
        raise Abort("nothing was sent. To use Claude Code instead, pass --agent claude")


def preflight(args: argparse.Namespace, model: str, caches: list[Path]) -> None:
    """Fail before a long transcription, not after it; and ask before a first run with Antigravity."""
    confirm_agent(args)
    if problems := doctor.start_problems(args.agent, args.model, model, caches):
        raise Abort("cannot start:\n  - " + "\n  - ".join(problems))


def make_run_dir(note_stem: str) -> Path:
    base = paths.runs_dir() / f"{datetime.now():%Y-%m-%d_%H%M%S}_{note_stem}"
    run_dir, n = base, 1
    while True:
        try:
            run_dir.mkdir(parents=True)
            return run_dir
        except FileExistsError:
            n += 1
            run_dir = base.with_name(f"{base.name}-{n}")


def write_output(output: Path, text: str, run_dir: Path, force: bool, reply_name: str = "reply.md") -> None:
    """One write straight into the vault: no temporary or partial files for Obsidian Sync to pick up."""
    if force and output.exists():
        shutil.copy2(output, run_dir / output.name)
    try:
        with open(output, "w" if force else "x", encoding="utf-8", newline="\n") as f:  # LF, as Obsidian writes
            f.write(text)
    except FileExistsError:
        raise Abort(f"{output} appeared during the run. The reply is in {run_dir / reply_name}; "
                    "rerun with --force to replace the file.") from None


def _home(path: Path) -> str:
    home = str(Path.home())
    return "~" + str(path)[len(home):] if str(path).startswith(home + os.sep) else str(path)


def _unquoted_hint(given: list[Path], i: int) -> str:
    """If given[i] and the arguments after it, joined with spaces, name a file, the shell split an unquoted path."""
    for j in range(i + 1, len(given)):
        joined = " ".join(str(p) for p in given[i:j + 1])
        if Path(joined).is_file():
            return f'\nIts path has spaces and was split by the shell: put it in quotes: "{joined}"'
    return ""
