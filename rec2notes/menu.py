"""The interactive menu: numbered prompts that end in the same arguments the command line takes.

Standard library only, no full-screen UI. `hub` returns the parsed arguments of a run to start, or
None if the user quit or declined; running it is the caller's job, exactly as with flags.
"""

import argparse
import os
import shutil
from pathlib import Path
from typing import Callable

from . import Abort, courses, doctor, i18n, merge, paths, setup, transcribe, ui, verify
from .i18n import t


class Quit(Exception):
    """The user left the menu: `q`, Ctrl-D or Ctrl-C at a prompt."""


Parse = Callable[[list[str]], argparse.Namespace]
CLAUDE_MODELS = (None, "opus", "sonnet", "haiku")  # None: Claude Code's default; the aliases follow its latest models


def hub(console: ui.Console, ask: Callable[[str], str], parse: Parse, parse_create: Parse,
        parse_verify: Parse) -> argparse.Namespace | None:
    """`parse`, `parse_create` and `parse_verify` turn the answers into the arguments of a run, of `rec2notes create`
    and of `rec2notes verify`."""
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
                    if what == "3":
                        return _guided_verify(console, ask, parse_verify)
                elif choice == "2":
                    doctor.run(console)
                elif choice == "3":
                    _courses(console, ask)
                elif choice == "4":
                    _settings(console, ask)
                elif choice == "q":
                    return None
                elif choice not in ("h", "?", "help"):
                    console.line(t("menu.not_an_option"))
            except Abort as e:
                console.line(str(e))
    except Quit:
        console.line()
        return None


def _options(console: ui.Console) -> None:
    """The hub's options, shown before every prompt so the user always sees what can be typed."""
    console.line()
    console.line(f"  {console.style('1', ui.CYAN)}  {t('menu.hub.run')}")
    console.line(f"  {console.style('2', ui.CYAN)}  {t('menu.hub.doctor')}")
    console.line(f"  {console.style('3', ui.CYAN)}  {t('menu.hub.courses')}")
    console.line(f"  {console.style('4', ui.CYAN)}  {t('menu.hub.settings')}")
    console.line(f"  {console.style('q', ui.CYAN)}  {t('menu.hub.quit')}")
    console.line()


def _run_choice(console, ask) -> str:
    """"1" to complete a note, "2" to create one, "3" to check one against slides, "b" to go back."""
    console.line()
    console.line(f"  {console.style('1', ui.CYAN)}  {t('menu.run.complete')}")
    console.line(f"  {console.style('2', ui.CYAN)}  {t('menu.run.create')}")
    console.line(f"  {console.style('3', ui.CYAN)}  {t('menu.run.verify')}")
    console.line(f"  {console.style('b', ui.CYAN)}  {t('menu.back')}")
    console.line()
    while (choice := _ask(ask, "> ").lower()) not in ("1", "2", "3", "b"):
        console.line(t("menu.type_1_2_3_b"))
    return choice


def _guided_run(console, ask, parse) -> argparse.Namespace | None:
    note = _ask_file(console, ask, t("menu.ask.note"))
    clean = _yes(console, ask, t("menu.ask.clean"), default=False)
    audio = _ask_recordings(console, ask)
    course = _pick_course(console, ask, note)
    args = parse([str(note), *map(str, audio), "--course", course.slug, *(["--clean"] if clean else [])])
    return _confirm(console, ask, args, course, [
        (t("menu.row.course"), [course.name], ()),
        (t("menu.row.note"), [note.name], ()),
        *([(t("menu.row.clean"), [t("menu.clean.yes"),
                                  t("menu.clean.extra_call", words=i18n.number(len(note.read_text(encoding="utf-8").split())))],
            ())] if clean else []),
        (t("menu.row.recording"), [a.name for a in audio], ()),
    ])


def _guided_create(console, ask, parse) -> argparse.Namespace | None:
    note = _ask_new_note(console, ask)
    audio = _ask_recordings(console, ask)
    course = _pick_course(console, ask, note)
    args = parse([str(note), *map(str, audio), "--course", course.slug])
    return _confirm(console, ask, args, course, [
        (t("menu.row.course"), [course.name], ()),
        (t("menu.row.new_note"), [note.name], ()),
        (t("menu.row.recording"), [a.name for a in audio], ()),
        (t("menu.row.length"), [t("menu.length_about", length=args.length)], ()),
    ])


def _guided_verify(console, ask, parse) -> argparse.Namespace | None:
    """Slides only: a recording is for `rec2notes verify` on the command line."""
    note = _ask_file(console, ask, t("menu.ask.note"))
    slides = [_ask_pages(console, ask, _ask_pdf(console, ask, t("menu.ask.slides")), [])]
    while more := _ask(ask, t("menu.ask.another_pdf"), allow_empty=True):
        if (path := _file_or_none(console, more)) and _is_pdf(console, path):
            slides.append(_ask_pages(console, ask, path, slides))
    args = parse([str(note), *(f"{s.pdf}:{s.first}-{s.last}" for s in slides)])
    tokens = verify.estimate_tokens(sum(s.pages for s in slides), note.read_text(encoding="utf-8"))
    return _confirm(console, ask, args, None, [
        (t("menu.row.note"), [note.name], ()),
        (t("menu.row.slides"), [t("menu.slides_pages", name=s.pdf.name, first=s.first, last=s.last) for s in slides], ()),
        (t("menu.row.estimate"), [verify.tokens_label(tokens)], ()),
    ])


def _ask_pages(console, ask, pdf: Path, before: list[verify.Slides]) -> verify.Slides:
    """The pages of `pdf` to check, Enter for all; with `before`, the PDFs already given, within Claude's limit."""
    count = verify.with_pages(verify.Slides(pdf)).last
    while True:
        answer = _ask(ask, t("menu.ask.pages", count=count), allow_empty=True).replace(" ", "")
        slides = verify.parse_slides(f"{pdf}:{answer}") if answer else verify.Slides(pdf)
        if answer and slides.first is None:
            console.line(t("menu.bad_pages", count=count))
            continue
        try:
            slides = verify.with_pages(slides)
            verify.check_pages([*before, slides])
            return slides
        except Abort as e:
            console.line(str(e))


def _ask_pdf(console, ask, prompt: str) -> Path:
    while True:
        if (path := _ask_file(console, ask, prompt)) and _is_pdf(console, path):
            return path


def _is_pdf(console, path: Path) -> bool:
    if path.suffix.lower() == ".pdf":
        return True
    console.line(t("menu.not_a_pdf", path=path))
    return False


def _confirm(console, ask, args: argparse.Namespace, course: courses.Course | None,
             rows: list[tuple[str, list[str], tuple]]) -> argparse.Namespace | None:
    """Show what will run, with the settings and anything that stops it; Enter starts it, `c` changes the
    settings for this run only (not the agent of a verify: it is Claude only; Whisper only with recordings).
    `course` is None only without recordings."""
    sums = [transcribe.sha256_file(a) for a in args.audio]
    seconds = [transcribe.audio_seconds(a) for a in args.audio]
    while True:
        caches = [paths.transcript_cache(args.whisper_model, s, course.vocab) for s in sums]
        console.line()
        console.summary([*rows, *_settings_rows(args), *(_transcript_rows(seconds, caches) if args.audio else [])])
        console.line()
        problems = doctor.start_problems(args.agent, args.model, args.whisper_model, caches)
        if problems:
            console.line(t("menu.cant_start"))
            for problem in problems:
                console.line(f"  - {problem}")
            console.line()
        prompt, answers = ((t("menu.prompt.problems"), ("c", "n", "no")) if problems else
                           (t("menu.prompt.start"), ("", *i18n.YES, "c", "n", "no")))
        while (answer := _ask(ask, prompt, allow_empty=True).lower()) not in answers:
            console.line(t("menu.type_c_n") if problems else t("menu.type_y_n_c"))
        if answer in ("n", "no"):
            return None
        if answer != "c":
            return args
        agent = args.agent if args.verify else _ask_agent(console, ask, args.agent)
        if agent != args.agent:
            args.agent, args.model = agent, paths.model_choice(agent)
            args.effort = paths.setting_choice("effort") if agent == "claude" else None
        args.model = _ask_model(console, ask, args.agent, args.model)
        if args.agent == "claude":
            args.effort = _ask_effort(console, ask, args.effort)
        if args.audio:
            args.whisper_model = _ask_whisper_model(console, ask, args.whisper_model) or args.whisper_model


def _settings_rows(args: argparse.Namespace) -> list[tuple[str, list[str], tuple]]:
    return [
        (t("menu.row.agent"), [merge.AGENTS[args.agent].label], ()),
        (t("menu.row.model"), [_model_name(args.model)], ()),
        *([(t("menu.row.effort"), [args.effort], ())] if args.effort else []),
        *([(t("menu.row.whisper"), [args.whisper_model], ())] if args.audio else []),
    ]


def _transcript_rows(seconds: list[float | None], caches: list[Path]) -> list[tuple[str, list[str], tuple]]:
    """The transcript's size, which is most of what the merge costs: exact for the cached parts, else guessed from
    the recordings' length."""
    words, guessed = 0, False
    for part_seconds, cache in zip(seconds, caches):
        if cache.exists():
            words += len(cache.read_text(encoding="utf-8").split())
        elif part_seconds is None:
            return [(t("menu.row.transcript"), [t("menu.transcript.unknown")], ())]
        else:
            words += round(part_seconds / 60 * transcribe.WORDS_PER_MINUTE)
            guessed = True
    audio = ui.rough(sum(s for s in seconds if s))
    size = (t("menu.transcript.guessed", words=i18n.number(max(100, round(words, -2))), audio=audio) if guessed
            else t("menu.transcript.cached", words=i18n.number(words)))
    if words <= transcribe.LONG_TRANSCRIPT_WORDS:
        return [(t("menu.row.transcript"), [size], ())]
    return [(t("menu.row.transcript"), [size, t("menu.transcript.long")], (ui.YELLOW,))]


def _ask_recordings(console, ask) -> list[Path]:
    """The recording, and any further parts of the same lecture, in order."""
    audio = [_ask_file(console, ask, t("menu.ask.recording"))]
    while part := _ask(ask, t("menu.ask.another_part"), allow_empty=True):
        if path := _file_or_none(console, part):
            audio.append(path)
    return audio


def _ask_new_note(console, ask) -> Path:
    """The path of a note to create: in a folder that exists, not taken; ".md" is added when missing."""
    while True:
        typed = _clean(_ask(ask, t("menu.ask.new_note")))
        note = Path(os.path.abspath(os.path.expanduser(typed)))
        if note.suffix.lower() != ".md":
            note = note.with_name(note.name + ".md")
        if not note.parent.is_dir():
            hint = " " + t("menu.variables_hint") if "$" in typed or "%" in typed else ""
            console.line(t("menu.no_such_folder", folder=note.parent, hint=hint))
        elif note.exists():
            console.line(t("menu.note_exists", note=note))
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
    labels = [all_courses[s].name + ("  " + t("menu.from_note_folder") if i == default else "") for i, s in enumerate(slugs)]
    i = _pick(console, ask, t("menu.pick.course"), labels, default, new=t("menu.pick.new_course"))
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
        console.line(t("menu.type_number_or_n" if new else "menu.type_number", n=len(labels)))


def _courses(console, ask) -> None:
    """List the courses and their folders; add one, or change one."""
    while True:
        all_courses = courses.load_courses()
        folders = courses.load_folders(all_courses)
        console.line()
        for slug, course in all_courses.items():
            console.line(f"  {console.style(slug, ui.CYAN)}  {course.name}  [{folders.get(slug) or t('menu.course.no_folder')}]")
        console.line()
        console.line(f"  {console.style('a', ui.CYAN)}  {t('menu.courses.add')}    {console.style('e', ui.CYAN)}  {t('menu.courses.edit')}    "
                     f"{console.style('b', ui.CYAN)}  {t('menu.back')}")
        choice = _ask(ask, "> ").lower()
        if choice == "b":
            return
        if choice == "a":
            _new_course(console, ask, with_folder=True)
        elif choice == "e" and all_courses:
            _edit_course(console, ask, all_courses)
        else:
            console.line(t("menu.type_a_e_b"))


def _edit_course(console, ask, all_courses: dict[str, courses.Course]) -> None:
    """Change a course's name, vocab and folder; Enter keeps what it has. Nothing is written until all is valid."""
    while (slug := _ask(ask, t("menu.ask.which_course"))) not in all_courses:
        console.line(t("menu.not_a_course", slug=slug))
    course = all_courses[slug]
    new_slug = _ask_new_slug(console, ask, slug)
    name = _ask(ask, t("menu.ask.course_name_keep", name=course.name), allow_empty=True)
    vocab = _ask(ask, t("menu.ask.vocab_keep", vocab=course.vocab), allow_empty=True)
    folder = _ask_folder(console, ask, slug, keep=True)
    if name or vocab:
        courses.update_course(slug, name or None, vocab or None)
    if folder:
        courses.set_folder(slug, folder)
    if new_slug:  # last, so the other changes find the course under its old slug
        courses.rename_course(slug, new_slug)
    console.line(t("menu.course.updated", name=name or course.name))


def _ask_new_slug(console, ask, slug: str) -> str | None:
    """A new short name for the course (its folder is kept), or None to keep [slug]."""
    while new := _ask(ask, t("menu.ask.slug_keep", slug=slug), allow_empty=True):
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
        slug = _ask(ask, t("menu.ask.slug"))
        try:
            courses.check_new_slug(slug)
            break
        except Abort as e:
            console.line(str(e))
    name = _ask(ask, t("menu.ask.course_name"))
    vocab = _ask(ask, t("menu.ask.vocab"))
    folder = _ask_folder(console, ask, slug) if with_folder else None
    courses.add_course(slug, name, vocab)
    if folder:
        courses.set_folder(slug, folder)
    if folder:
        console.line(t("menu.course.added_with_folder", name=name, folder=folder))
    elif with_folder:
        console.line(t("menu.course.added_no_folder", name=name, slug=slug, file=paths.folders_file()))
    else:
        console.line(t("menu.course.added", name=name))
    return courses.load_courses()[slug]


def _ask_folder(console, ask, slug: str, keep: bool = False) -> str | None:
    """The folder of a course's notes as an absolute path that exists (offering to create it), or None to skip."""
    while True:
        typed = _ask(ask, t("menu.ask.folder", action=t("menu.folder.keep" if keep else "menu.folder.skip")), allow_empty=True)
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
        if _yes(console, ask, t("menu.ask.create_folder", folder=folder), default=False):
            try:
                folder.mkdir(parents=True)
            except OSError as e:
                console.line(t("menu.cannot_create", folder=folder, reason=e.strerror))
                continue
            return str(folder)


def _settings(console, ask) -> None:
    """Show the rec2notes folder and the defaults runs use; point to the folder where the user moved it, change
    a default. The folder comes from the pointer, not `paths.folder()`, so this works while the folder is missing:
    that is when it is needed."""
    while True:
        folder = paths.pointed_folder()
        if folder is None:
            shown = t("menu.settings.none_yet", setup=paths.SETUP)
        else:
            shown = str(folder) if folder.is_dir() else t("menu.settings.missing", folder=folder)
        agent = paths.setting_choice("agent")
        if agent not in merge.AGENTS:
            raise Abort(t("menu.unknown_agent", agent=agent, names=", ".join(merge.AGENTS),
                          where=_from_env("agent") or " " + t("menu.in_settings_toml")))
        claude = agent == "claude"
        model_key = f"{agent}_model"
        console.line()
        label = {key: t(f"menu.label.{key}") for key in ("folder", "agent", "model", "effort", "whisper_model", "language")}
        width = max(16, *map(len, label.values()))  # the label column; English fits in 16
        console.line(f"  {label['folder']:<{width}}  {shown}")
        console.line(f"  {label['agent']:<{width}}  {merge.AGENTS[agent].label}{_from_env('agent')}")
        console.line(f"  {label['model']:<{width}}  {_model_name(paths.model_choice(agent))}"
                     f"{_from_env('REC2NOTES_MODEL', paths.SETTING_ENV[model_key])}")
        if claude:
            console.line(f"  {label['effort']:<{width}}  {paths.setting_choice('effort')}{_from_env('effort')}")
        console.line(f"  {label['whisper_model']:<{width}}  {paths.whisper_model_choice()}{_from_env('whisper_model')}")
        console.line(f"  {label['language']:<{width}}  {i18n.LANGUAGES[i18n.current()]}{_from_env('language')}")
        console.line()
        keys = ["a", "m", *(["e"] if claude else []), "w", "l"]
        names = {"a": label["agent"], "m": label["model"], "e": label["effort"], "w": label["whisper_model"],
                 "l": label["language"], "b": t("menu.back")}
        console.line(f"  {console.style('c', ui.CYAN)}  {t('menu.settings.change_folder')}")
        console.line("  " + "    ".join(f"{console.style(k, ui.CYAN)}  {names[k]}" for k in [*keys, "b"]))
        choice = _ask(ask, "> ").lower()
        if choice == "b":
            return
        if choice == "c":
            _repoint(console, ask)
        elif choice == "a":
            paths.save_setting("agent", _ask_agent(console, ask, agent))
        elif choice == "m":
            model = _ask_model(console, ask, agent, paths.setting_choice(model_key))
            # the built-in Antigravity model isn't saved, so a later rec2notes' default reaches whoever kept it
            paths.save_setting(model_key, None if model == paths.ANTIGRAVITY_MODEL else model)
        elif choice == "e" and claude:
            paths.save_setting("effort", _ask_effort(console, ask, paths.setting_choice("effort")))
        elif choice == "w" and (model := _ask_whisper_model(console, ask, paths.whisper_model_choice())):
            paths.save_setting("whisper_model", model)
        elif choice == "l":
            paths.save_setting("language", _ask_language(console, ask))
            i18n.load()  # not set_language: an environment variable that wins still does
        else:
            console.line(t("menu.type_settings", keys=", ".join(keys)))


def _from_env(*names: str) -> str:
    """Says which environment variable sets a default, so the saved one is ignored. A name without `REC2NOTES_`
    is a setting's key; the first variable set wins."""
    for name in names:
        env = name if name.startswith("REC2NOTES_") else paths.SETTING_ENV[name]
        if os.environ.get(env):
            return "  " + t("menu.from_env", env=env)
    return ""


def _model_name(model: str | None) -> str:
    return model or t("menu.claude_default")


def antigravity_agreed() -> bool:
    return paths.saved_settings().get("antigravity_consent") == "yes"


def agree_to_antigravity(console: ui.Console, ask: Callable[[str], str]) -> bool:
    """Antigravity sends the note and transcript to Google: say so and ask, once; the yes is saved in settings.toml."""
    if antigravity_agreed():
        return True
    console.line(t("menu.antigravity_notice"))
    if not _yes(console, ask, t("menu.ask.use_antigravity"), default=False):
        return False
    paths.save_setting("antigravity_consent", "yes")
    return True


def _ask_agent(console, ask, current: str) -> str:
    """The agent the user picks; Antigravity only once its notice is agreed to, else `current` stays."""
    names = list(merge.AGENTS)
    labels = [agent.label + ("" if shutil.which(agent.program) else "  " + t("menu.not_installed", hint=agent.install_hint()))
              for agent in merge.AGENTS.values()]
    agent = names[_pick(console, ask, t("menu.pick.agent"), labels, names.index(current))]
    if agent == "antigravity" and not agree_to_antigravity(console, ask):
        console.line(t("menu.keeping", label=merge.AGENTS[current].label))
        return current
    return agent


def _ask_model(console, ask, agent: str, current: str | None) -> str | None:
    return _ask_claude_model(console, ask, current) if agent == "claude" else _ask_antigravity_model(console, ask, current)


def _ask_antigravity_model(console, ask, current: str) -> str:
    """One of the models `agy models` lists; if agy can't list them, any name typed."""
    console.line(t("menu.asking_agy"))
    try:
        models = merge.antigravity_models()
    except Abort as e:
        console.line(str(e))
        return _ask(ask, t("menu.ask.agy_model", current=current), allow_empty=True) or current
    labels = [m + ("  " + t("menu.our_default") if m == paths.ANTIGRAVITY_MODEL else "") for m in models]
    return models[_pick(console, ask, t("menu.pick.antigravity_model"), labels,
                        models.index(current) if current in models else None)]


def _ask_claude_model(console, ask, current: str | None) -> str | None:
    default = CLAUDE_MODELS.index(current) if current in CLAUDE_MODELS else None
    return CLAUDE_MODELS[_pick(console, ask, t("menu.pick.claude_model"), [_model_name(m) for m in CLAUDE_MODELS], default)]


def _ask_effort(console, ask, current: str) -> str:
    default = merge.EFFORTS.index(current) if current in merge.EFFORTS else None
    return merge.EFFORTS[_pick(console, ask, t("menu.pick.effort"), list(merge.EFFORTS),
                               default)]


def _ask_language(console, ask) -> str:
    codes = list(i18n.LANGUAGES)
    return codes[_pick(console, ask, t("menu.pick.language"), list(i18n.LANGUAGES.values()),
                       codes.index(i18n.current()))]


def _ask_whisper_model(console, ask, current: str) -> str | None:
    """One of the downloaded Whisper models, or None if there is none."""
    models = paths.downloaded_whisper_models()
    if not models:
        console.line(t("menu.no_whisper_model", setup=paths.SETUP))
        return None
    labels = [f"{m}  ({setup.model_description(m)})" if m in setup.MODELS else m for m in models]
    return models[_pick(console, ask, t("menu.pick.whisper_model"), labels,
                        models.index(current) if current in models else None)]


def _repoint(console, ask) -> None:
    """Point to a rec2notes folder the user moved; it moves and creates nothing."""
    while typed := _ask(ask, t("menu.ask.repoint"), allow_empty=True):
        folder = Path(os.path.expanduser(_clean(typed)))
        if not folder.is_absolute():
            console.line(t("absolute_windows" if paths.WINDOWS else "absolute_other"))
            continue
        folder = Path(os.path.normpath(folder))
        if (folder / "courses.toml").is_file() or (folder / "whisper.cpp" / "models").is_dir():
            paths.write_pointer(folder)
            console.line(t("menu.now_using", folder=folder))
            return
        console.line(t("menu.not_a_rec2notes_folder", folder=folder, setup=paths.SETUP))


def _yes(console, ask, prompt: str, default: bool = True) -> bool:
    while True:
        answer = _ask(ask, prompt, allow_empty=True).lower()
        if not answer:
            return default
        if answer in i18n.YES:
            return True
        if answer in ("n", "no"):
            return False
        console.line(t("menu.type_y_n"))


def _ask_file(console, ask, prompt: str) -> Path:
    while True:
        path = _file_or_none(console, _ask(ask, prompt))
        if path:
            return path


def _file_or_none(console, text: str) -> Path | None:
    path = Path(os.path.abspath(os.path.expanduser(_clean(text))))
    if path.is_file():
        return path
    console.line(t("menu.not_a_file", path=path))
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
