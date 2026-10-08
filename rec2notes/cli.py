"""rec2notes: complete a lecture note from its recording, into a sibling file."""

import argparse
import os
import re
import shlex
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import (Abort, version, check, courses, doctor, i18n, menu, merge, paths, setup, stopping, transcribe, ui,
               uninstall, verify)
from .i18n import t

COMPLETED_MARKERS = ("[^conflitto-", check.MISSED_TOPICS_HEADING, "[^non-annotati]")  # the last: the old missed-topics footnote
CREATE_LENGTH = 20  # the created note's length, in % of the transcript's words; to settle from real runs
WARNING_PASSAGES = 5  # how many changed passages to show in the terminal; check.txt has all
RUN_STAMP = "%Y-%m-%d_%H%M%S"  # how a run folder's name starts
RUNS_KEPT_DAYS = 30  # run folders hold a copy of the note and the transcript: not kept forever
SLIDE_FORMATS = (".pptx", ".ppt", ".odp", ".key")  # slides verify can't read: they need exporting to PDF


class Parser(argparse.ArgumentParser):
    def print_help(self, file=None):
        """The help, under the banner when it goes to a terminal wide enough for it."""
        console = ui.Console(file or sys.stdout)
        if console.banner():
            console.line()
        super().print_help(file)


def build_parser() -> Parser:
    p = Parser(prog="rec2notes", description=t("cli.help.description"))
    p.epilog = t("cli.help.epilog")
    p.add_argument("note", type=Path, help=t("cli.help.note"))
    p.add_argument("audio", type=Path, nargs="+", help=t("cli.help.audio"))
    p.add_argument("--course", metavar="SLUG", help=t("cli.help.course"))
    add_whisper_option(p)
    add_agent_options(p)
    p.add_argument("--clean", action="store_true", help=t("cli.help.clean"))
    p.add_argument("--force", action="store_true", help=t("cli.help.force_merge"))
    p.add_argument("--dry-run", action="store_true", help=t("cli.help.dry_run"))
    p.add_argument("--version", action="version", version=f"rec2notes {version()}")
    p.set_defaults(create=False, verify=False)
    return p


def add_whisper_option(p: argparse.ArgumentParser) -> None:
    p.add_argument("--whisper-model", metavar="NAME", help=t("cli.help.whisper_model", settings=t("path.settings")))


def add_agent_options(p: argparse.ArgumentParser, agent: bool = True) -> None:
    """`agent` False: the command runs Claude only, whatever agent is saved, and its help says so."""
    if agent:
        p.add_argument("--agent", choices=tuple(merge.AGENTS), help=t("cli.help.agent", settings=t("path.settings")))
    else:
        p.set_defaults(agent="claude")
    keys = "cli.help" if agent else "cli.verify"
    p.add_argument("--model", metavar="MODEL",
                   help=t(f"{keys}.model", antigravity_model=paths.ANTIGRAVITY_MODEL, settings=t("path.settings")))
    p.add_argument("--claude-model", dest="model", help=argparse.SUPPRESS)  # the old name of --model
    p.add_argument("--effort", metavar="LEVEL",
                   help=t(f"{keys}.effort", efforts=", ".join(merge.EFFORTS), settings=t("path.settings")))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    return _checked(build_parser(), argv)


def parse_clean_args(argv: list[str]) -> argparse.Namespace:
    p = Parser(prog="rec2notes clean", description=t("cli.clean.description"))
    p.add_argument("note", type=Path, help=t("cli.clean.note"))
    add_agent_options(p)
    p.add_argument("--force", action="store_true", help=t("cli.clean.force"))
    return _checked(p, argv)


def parse_create_args(argv: list[str]) -> argparse.Namespace:
    p = Parser(prog="rec2notes create", description=t("cli.create.description"))
    p.add_argument("note", type=Path, help=t("cli.create.note"))
    p.add_argument("audio", type=Path, nargs="+", help=t("cli.help.audio"))
    p.add_argument("--course", metavar="SLUG", help=t("cli.help.course"))
    p.add_argument("--length", metavar="PCT", type=int, default=CREATE_LENGTH,
                   help=t("cli.create.length", default=CREATE_LENGTH))
    add_whisper_option(p)
    add_agent_options(p)
    p.add_argument("--force", action="store_true", help=t("cli.create.force"))
    p.add_argument("--dry-run", action="store_true", help=t("cli.help.dry_run"))
    p.set_defaults(create=True, verify=False)
    args = _checked(p, argv)
    if not 5 <= args.length <= 60:
        p.error(t("cli.error.length", length=args.length))
    return args


def parse_verify_args(argv: list[str]) -> argparse.Namespace:
    """FILE... splits into the slides (.pdf, each with its page range if given) and the recordings (the rest), each
    kept in order; `files` are their paths."""
    p = Parser(prog="rec2notes verify", description=t("cli.verify.description"))
    p.add_argument("note", type=Path, help=t("cli.verify.note"))
    p.add_argument("files", nargs="+", metavar="FILE", help=t("cli.verify.files", max=verify.MAX_PAGES))
    p.add_argument("--course", metavar="SLUG", help=t("cli.verify.course"))
    add_whisper_option(p)
    add_agent_options(p, agent=False)
    p.set_defaults(create=False, verify=True)
    args = _checked(p, argv)
    given = [verify.parse_slides(arg) for arg in args.files]
    for slides in given:
        if slides.pdf.suffix.lower() in SLIDE_FORMATS:
            p.error(t("cli.verify.export_pdf", file=slides.pdf))
    args.files = [slides.pdf for slides in given]
    args.slides = [s for s in given if s.pdf.suffix.lower() == ".pdf"]
    args.audio = [s.pdf for s in given if s.pdf.suffix.lower() != ".pdf"]
    if not args.slides:
        p.error(t("cli.verify.no_pdf"))
    return args


def _checked(p: argparse.ArgumentParser, argv: list[str] | None) -> argparse.Namespace:
    args = p.parse_args(argv)
    args.agent = args.agent or paths.setting_choice("agent")
    if args.agent not in merge.AGENTS:
        p.error(t("cli.error.agent", names=", ".join(merge.AGENTS), agent=args.agent))
    args.model = args.model or paths.model_choice(args.agent)
    if args.agent == "claude":
        args.effort = args.effort or paths.setting_choice("effort")
        if args.effort not in merge.EFFORTS:
            p.error(t("cli.error.effort", names=", ".join(merge.EFFORTS), effort=args.effort))
    else:
        if args.effort:
            print("rec2notes: " + t("cli.effort_ignored", label=merge.AGENTS[args.agent].label), file=sys.stderr)
        args.effort = None
    if "whisper_model" in args and not args.whisper_model:
        args.whisper_model = paths.whisper_model_choice()
    return args


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    i18n.load()
    if argv == ["doctor"]:
        return doctor.run(ui.Console())
    if argv[:1] == ["setup"]:
        return setup.main(argv[1:])
    if argv[:1] == ["uninstall"]:
        return uninstall.main(argv[1:])
    if argv[:1] == ["course"]:
        return course_command(argv[1:])
    clean_only, create, verifying = argv[:1] == ["clean"], argv[:1] == ["create"], argv[:1] == ["verify"]
    menu_wanted = not argv and on_terminal()
    if not argv and not menu_wanted:  # bare `rec2notes` in a pipe: show what it is and how to use it
        build_parser().print_help()
        return 0
    console = ui.Console()
    prefix = console.style("rec2notes:", ui.BOLD, ui.RED) if console.err.isatty() else "rec2notes:"
    try:
        args = (None if menu_wanted else parse_clean_args(argv[1:]) if clean_only
                else parse_create_args(argv[1:]) if create else parse_verify_args(argv[1:]) if verifying
                else parse_args(argv))
        if menu_wanted:  # bare `rec2notes` in a terminal: the menu picks the run
            args = menu.hub(console, input, parse_args, parse_create_args, parse_verify_args)
            if args is None:
                return 0
        with stopping.handling():
            if clean_only:
                return run_clean(args, console)
            if args.verify:
                return run_verify(args, console)
            return run_create(args, console) if args.create else run(args, console)
    except Abort as e:
        console.line(f"{prefix} {e}", err=True)
        return 1
    except stopping.Stopped as e:
        console.line(f"{prefix} {e.message}", err=True)
        return e.status


def course_command(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="rec2notes course", description=t("cli.course.description"))
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("list", help=t("cli.course.list"))
    add = sub.add_parser("add", help=t("cli.course.add"))
    add.add_argument("slug", help=t("cli.course.add_slug"))
    add.add_argument("name", help=t("cli.course.add_name"))
    add.add_argument("vocab", help=t("cli.course.add_vocab"))
    add.add_argument("--folder", metavar="PATH", help=t("cli.course.add_folder"))
    add.add_argument("--create", action="store_true", help=t("cli.course.create"))
    edit = sub.add_parser("edit", help=t("cli.course.edit"))
    edit.add_argument("slug", help=t("cli.course.edit_slug"))
    edit.add_argument("--slug", dest="new_slug", help=t("cli.course.edit_new_slug"))
    edit.add_argument("--name", help=t("cli.course.edit_name"))
    edit.add_argument("--vocab", help=t("cli.course.edit_vocab"))
    edit.add_argument("--folder", metavar="PATH", help=t("cli.course.edit_folder"))
    edit.add_argument("--create", action="store_true", help=t("cli.course.create"))
    args = p.parse_args(argv)
    try:
        if args.action == "edit":
            if args.slug not in courses.load_courses():
                raise Abort(t("courses.unknown", slug=args.slug, names=", ".join(courses.load_courses())))
            if args.name is None and args.vocab is None and not args.folder and not args.new_slug:
                raise Abort(t("cli.course.nothing_to_change"))
            if args.new_slug:
                courses.check_new_slug(args.new_slug)
            folder = _folder_to_use(args)  # before anything is written
            if args.name is not None or args.vocab is not None:
                courses.update_course(args.slug, args.name, args.vocab)
            if folder:
                courses.set_folder(args.slug, str(folder))
            if args.new_slug:  # last, so the other changes find the course under its old slug
                courses.rename_course(args.slug, args.new_slug)
            print(t("cli.course.updated", slug=args.new_slug or args.slug))
        elif args.action == "add":
            folder = _folder_to_use(args)  # before anything is written
            courses.add_course(args.slug, args.name, args.vocab)
            print(t("cli.course.added", slug=args.slug, file=paths.courses_file()))
            if folder:
                courses.set_folder(args.slug, str(folder))
                print(t("cli.course.folder_of", slug=args.slug, folder=folder))
        else:
            all_courses = courses.load_courses()
            folders = courses.load_folders(all_courses)
            for slug, course in all_courses.items():
                print(f"{slug:<10} {course.name}  [{folders.get(slug) or t('menu.course.no_folder')}]")
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
            raise Abort(t("cli.course.folder_missing", folder=folder))
        try:
            folder.mkdir(parents=True)
        except OSError as e:
            raise Abort(t("setup.cannot_create", folder=folder, reason=e.strerror)) from None
    return folder


def run(args: argparse.Namespace, console: ui.Console) -> int:
    given = [args.note, *args.audio]
    note = Path(os.path.abspath(args.note))
    if not note.is_file():
        raise Abort(t("cli.note_not_found", note=args.note) + _unquoted_hint(given, 0))
    for i, audio in enumerate(args.audio, 1):
        if not audio.is_file():
            raise Abort(t("cli.audio_not_found", audio=audio) + _unquoted_hint(given, i))
    note_text = note.read_text(encoding="utf-8")
    if any(marker in note_text for marker in COMPLETED_MARKERS):
        raise Abort(t("cli.already_completed", name=note.name))
    cleaned = clean_path(note)
    if args.clean:
        check_free(cleaned, args.force)
    merged = cleaned if args.clean else note  # the note the merge completes
    output = merged.with_name(f"{merged.stem} (completo).md")
    check_free(output, args.force)

    all_courses = courses.load_courses()
    folders = {} if args.course else courses.load_folders(all_courses)
    course = courses.resolve_course(note, all_courses, folders, args.course)
    caches = [paths.transcript_cache(args.whisper_model, transcribe.sha256_file(a), course.vocab) for a in args.audio]

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
    merged_reply = call_agent(console, t("step.merge"), agent_detail(args), args, input_text, run_dir)
    reply, tools = merged_reply.text, [*tools, *(tool for tool in merged_reply.tools if tool not in tools)]
    if not reply.endswith("\n"):
        reply += "\n"

    changes = check.missing_passages(note_text, reply)
    conflicts, missed = check.summary(reply)
    (run_dir / "check.txt").write_text(check.report(changes, conflicts, missed), encoding="utf-8")
    if changes:
        console.mark("warn", t("step.check"), t("cli.check.passages" if len(changes) > 1 else "cli.check.passage", n=len(changes)))
    else:
        console.mark("ok", t("step.check"), t("cli.check.only_additions"))
    if check.TRANSCRIPT_MISMATCH in reply:
        console.mark("warn", t("step.check"), t("cli.check.mismatch"))
    stopping.check()
    write_output(output, reply, run_dir, args.force)
    console.mark("ok", t("step.write"), output.name)

    rows = [(t("row.conflicts"), [str(conflicts)], (ui.DIM,)), (t("row.missed"), [t("yes" if missed else "no")], (ui.DIM,))]
    if changes:
        shown = [check.describe(c, width=60) for c in changes[:WARNING_PASSAGES]]
        if len(changes) > WARNING_PASSAGES:
            shown.append(t("cli.more_passages", n=len(changes) - WARNING_PASSAGES))
        rows.append((t("row.changed"), shown, (ui.YELLOW,)))
    rows += tools_rows(tools)
    rows.append((t("row.run"), [_home(run_dir)], (ui.DIM,)))
    console.line()
    console.summary(rows)
    return 0


def transcribe_all(args: argparse.Namespace, course: courses.Course, caches: list[Path], run_dir: Path,
                   console: ui.Console) -> str:
    """Transcribe each part not cached yet; returns the whole lecture's transcript."""
    for part, (audio, cache) in enumerate(zip(args.audio, caches), 1):
        which = t("cli.part", part=part, total=len(args.audio)) if len(args.audio) > 1 else ""
        if cache.exists():
            console.mark("ok", t("step.transcribe"), t("cli.cached", which=which, model=args.whisper_model))
            continue
        with console.step(t("step.transcribe"), f"{which}{audio.name} · {args.whisper_model}") as step:
            seconds = transcribe.transcribe(audio, cache, args.whisper_model, course.vocab, run_dir, part, step.progress)
            length = t("cli.of_audio", duration=ui.duration(seconds)) if seconds else ""
            step.end("ok", f"{which}{length}{args.whisper_model}", ui.duration(step.elapsed))
    transcript = transcribe.label_parts([c.read_text(encoding="utf-8") for c in caches])
    if not transcript.strip():
        raise Abort(t("cli.transcript_empty"))
    return transcript


def run_create(args: argparse.Namespace, console: ui.Console) -> int:
    output = Path(os.path.abspath(args.note))
    if output.suffix.lower() != ".md":
        output = output.with_name(output.name + ".md")
    given = [args.note, *args.audio]
    for i, audio in enumerate(args.audio, 1):
        if not audio.is_file():
            raise Abort(t("cli.audio_not_found", audio=audio) + _unquoted_hint(given, i))
    if not output.parent.is_dir():
        raise Abort(t("cli.note_folder_missing", folder=output.parent))
    check_free(output, args.force)

    all_courses = courses.load_courses()
    folders = {} if args.course else courses.load_folders(all_courses)
    course = courses.resolve_course(output, all_courses, folders, args.course)
    caches = [paths.transcript_cache(args.whisper_model, transcribe.sha256_file(a), course.vocab) for a in args.audio]

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
    created = call_agent(console, t("step.create"), t("cli.length_detail", detail=agent_detail(args), length=args.length), args, input_text, run_dir,
                         prompt=paths.CREATE_PROMPT)
    reply = created.text
    if not reply.endswith("\n"):
        reply += "\n"
    stopping.check()
    write_output(output, reply, run_dir, args.force)
    console.mark("ok", t("step.write"), output.name)

    note_words = len(reply.split())
    length = t("cli.created_length", words=i18n.number(note_words), percent=round(100 * note_words / max(words, 1)),
               aimed=args.length)
    console.line()
    console.summary([(t("row.length"), [length], (ui.DIM,)), *tools_rows(created.tools),
                     (t("row.run"), [_home(run_dir)], (ui.DIM,))])
    return 0


def run_verify(args: argparse.Namespace, console: ui.Console) -> int:
    given = [args.note, *args.files]
    note = Path(os.path.abspath(args.note))
    if not note.is_file():
        raise Abort(t("cli.note_not_found", note=args.note) + _unquoted_hint(given, 0))
    for i, file in enumerate(args.files, 1):
        if not file.is_file():
            raise Abort(t("cli.file_not_found", file=file) + _unquoted_hint(given, i))
    verify.check_agent(args.agent)
    slides = [verify.with_pages(s) for s in args.slides]  # a bad PDF or range stops here, before a long transcription
    verify.check_pages(slides)
    course, caches = None, []
    if args.audio:
        all_courses = courses.load_courses()
        folders = {} if args.course else courses.load_folders(all_courses)
        course = courses.resolve_course(note, all_courses, folders, args.course)
        caches = [paths.transcript_cache(args.whisper_model, transcribe.sha256_file(a), course.vocab) for a in args.audio]

    preflight(args, args.whisper_model, caches)
    run_dir = make_run_dir(note.stem)
    console.header(course.name if course else t("cli.verify.title"), note.name)
    pages = sum(s.pages for s in slides)
    tokens = verify.tokens_label(verify.estimate_tokens(pages, note.read_text(encoding="utf-8")))
    with console.step(t("step.slides"), t("cli.verify.slides", n=len(slides), pages=pages, tokens=tokens)) as step:
        blocks = verify.slide_blocks(slides, run_dir)
        step.end("ok", step.detail, ui.duration(step.elapsed))
    transcript = None
    if args.audio:
        transcript = transcribe_all(args, course, caches, run_dir, console)
        confirm_size(transcript, console)
    with console.step(t("step.verify"), agent_detail(args)) as step:
        result = verify.verify(note, blocks, transcript, run_dir, args.agent, args.effort, args.model,
                               notify=lambda message: step.note("warn", message))
        step.end("ok", step.detail, ui.duration(step.elapsed))
    if result.in_vault:
        console.mark("ok", t("step.write"), note.name)
    else:
        console.mark("warn", t("step.write"), t("cli.verify.left_alone", name=note.name, copy=_home(result.written)))

    console.line()
    console.summary([(t("row.findings"), [str(result.findings)], (ui.YELLOW,) if result.findings else (ui.DIM,)),
                     (t("row.run"), [_home(run_dir)], (ui.DIM,))])
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
        console.mark("ok", t("step.transcript"), t("cli.words", words=i18n.number(words)))
        return
    console.mark("warn", t("step.transcript"), t("cli.words_long", words=i18n.number(words)))
    if on_terminal():
        try:
            answer = input(t("cli.ask.continue"))
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer.strip().lower() in ("n", "no"):
            raise Abort(t("cli.stopped_before_merge"))


def run_clean(args: argparse.Namespace, console: ui.Console) -> int:
    note = Path(os.path.abspath(args.note))
    if not note.is_file():
        raise Abort(t("cli.note_not_found", note=args.note))
    note_text = note.read_text(encoding="utf-8")
    output = clean_path(note)
    check_free(output, args.force)
    preflight(args, paths.default_whisper_model(), [])  # no recording: nothing to transcribe
    run_dir = make_run_dir(note.stem)
    cleaned = clean_note(note_text, output, run_dir, args, console)
    console.line()
    console.summary([*tools_rows(cleaned.tools), (t("row.run"), [_home(run_dir)], (ui.DIM,))])
    return 0


def clean_path(note: Path) -> Path:
    return note.with_name(f"{note.stem} (pulito).md")


def clean_note(note_text: str, output: Path, run_dir: Path, args: argparse.Namespace, console: ui.Console) -> merge.Reply:
    """Clean the note with the agent and write the result to `output`; returns the agent's reply."""
    cleaned = call_agent(console, t("step.clean"), agent_detail(args), args, note_text, run_dir,
                         prompt=paths.CLEAN_PROMPT, file_prefix="clean-")
    reply = cleaned.text if cleaned.text.endswith("\n") else cleaned.text + "\n"
    stopping.check()
    write_output(output, reply, run_dir, args.force, "clean-reply.md")
    console.mark("ok", t("step.write"), output.name)
    return merge.Reply(reply, cleaned.tools)


def call_agent(console: ui.Console, label: str, detail: str, args: argparse.Namespace, input_text: str, run_dir: Path,
               **options) -> merge.Reply:
    """One agent call as a checklist step, warning when the agent used a tool."""
    with console.step(label, detail) as step:
        reply = merge.run_agent(args.agent, input_text, run_dir, args.effort, args.model,
                                notify=lambda message: step.note("warn", message), **options)
        if reply.tools:
            step.note("warn", t("cli.tools_step", tools=", ".join(reply.tools)))
        step.end("ok", step.detail, ui.duration(step.elapsed))
    return reply


def tools_rows(tools: list[str]) -> list[tuple[str, list[str], tuple]]:
    """The summary's warning about tools the agent used: what they brought in isn't from the lecture, and a
    transcript may hold instructions that made the agent use them."""
    if not tools:
        return []
    return [(t("row.tools"), [t("cli.tools_used", tools=", ".join(tools)), t("cli.tools_check")], (ui.YELLOW,))]


def agent_detail(args: argparse.Namespace) -> str:
    if args.agent != "claude":
        return f"{args.agent} {args.model}"
    claude = f"claude {args.model}" if args.model else "claude"
    return f"{claude} · effort {args.effort}"


def check_free(output: Path, force: bool) -> None:
    if output.exists() and not force:
        raise Abort(t("cli.exists", output=output))


def dry_run(args: argparse.Namespace, course: courses.Course, note_text: str, caches: list[Path]) -> int:
    if args.clean:
        print(t("cli.dry.clean"), file=sys.stderr)
    sys.stdout.write(merge.build_input(course, note_text, cached_transcript(args, caches)))
    print_command(args, paths.MERGE_PROMPT)
    return 0


def print_command(args: argparse.Namespace, prompt: Path) -> None:
    print(t("cli.dry.pipe", command=shlex.join(merge.command(args.agent, args.effort, args.model, prompt))), file=sys.stderr)
    if args.agent == "antigravity":
        print(t("cli.dry.antigravity", prompt=prompt.name), file=sys.stderr)


def cached_transcript(args: argparse.Namespace, caches: list[Path]) -> str:
    """The transcript a dry run shows: the cached parts, and a placeholder for each part not transcribed yet."""
    parts = []
    for audio, cache in zip(args.audio, caches):
        if cache.exists():
            parts.append(cache.read_text(encoding="utf-8"))
        else:
            print(t("cli.dry.not_cached", model=args.whisper_model, audio=audio.name), file=sys.stderr)
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
        raise Abort(t("cli.antigravity_consent", settings=t("path.settings")))
    try:
        agreed = menu.agree_to_antigravity(ui.Console(), input)
    except menu.Quit:
        agreed = False
    if not agreed:
        raise Abort(t("cli.nothing_sent"))


def preflight(args: argparse.Namespace, model: str, caches: list[Path]) -> None:
    """Fail before a long transcription, not after it; and ask before a first run with Antigravity."""
    confirm_agent(args)
    if problems := doctor.start_problems(args.agent, args.model, model, caches):
        raise Abort(t("cli.cannot_start") + "\n  - " + "\n  - ".join(problems))


def make_run_dir(note_stem: str) -> Path:
    prune_runs()
    base = paths.runs_dir() / f"{datetime.now():{RUN_STAMP}}_{note_stem}"
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
        raise Abort(t("cli.appeared", output=output, reply=run_dir / reply_name)) from None


def prune_runs() -> None:
    """Delete the run folders older than RUNS_KEPT_DAYS; only folders named as make_run_dir names them."""
    oldest = datetime.now() - timedelta(days=RUNS_KEPT_DAYS)
    runs = paths.runs_dir()
    for run_dir in runs.iterdir() if runs.is_dir() else []:
        try:
            started = datetime.strptime(run_dir.name[:len(f"{oldest:{RUN_STAMP}}")], RUN_STAMP)
        except ValueError:
            continue
        if started < oldest and run_dir.is_dir() and not run_dir.is_symlink():
            try:
                shutil.rmtree(run_dir)
            except OSError:  # a file still in use on Windows: the next run tries again
                pass

def _home(path: Path) -> str:
    home = str(Path.home())
    return "~" + str(path)[len(home):] if str(path).startswith(home + os.sep) else str(path)


def _unquoted_hint(given: list[Path], i: int) -> str:
    """If given[i] and the arguments after it, joined with spaces, name a file, the shell split an unquoted path."""
    for j in range(i + 1, len(given)):
        joined = " ".join(str(p) for p in given[i:j + 1])
        if Path(joined).is_file():
            return "\n" + t("cli.unquoted", joined=joined)
    return ""
