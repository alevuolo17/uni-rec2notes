# Changelog

## 0.4.2 (2026-10-07)

- Keep MCP servers out of claude calls with --strict-mcp-config

## 0.4.1 (2026-10-07)

- runs: delete run folders after 30 days, and say what is kept
- transcribe: key the transcript cache on the course vocabulary too
- check: compare symbols and line breaks too, and say exactly what it guarantees
- tests: keep the banner tests and the whole suite away from the real settings

## 0.4.0 (2026-10-06)

- test_doctor: take the VAD model name from paths, not a literal
- docs: say that rec2notes speaks English or Italian, and use the Italian menu names
- check, stopping, transcribe: translate their messages into Italian
- merge, uninstall, paths: translate their messages into Italian
- doctor: translate the checklist and its fixes into Italian
- courses: translate the course errors and the folders.toml header into Italian
- cli: translate the command line and the run's output into Italian
- menu: translate the menu into Italian, names included
- settings: add a Language row and key, and load the saved language on every run
- setup: ask the language first and speak it, in English or Italian
- i18n: add the language setting and a message catalog, nothing translated yet

## 0.3.2 (2026-10-06)

- readme: split the agent install by agent, then by OS

## 0.3.1 (2026-10-06)

- readme: install the AI agent first, for Claude Code and Antigravity alike

## 0.3.0 (2026-10-06)

- agents: Antigravity defaults to gemini-3.8-flash-high, no model matrix
- agents: retry deleting agy's home on Windows, no slash commands
- readme: setup picks the agent, Settings switches it
- setup: use the agent installed, and ask when both are
- menu: pick the agent and its model in Settings and at c
- readme: Antigravity instead of Claude Code, and privacy and security
- agents: doctor and setup check the chosen agent
- cli: one on_terminal() for the three terminal checks
- agents: ask once before Antigravity sends a note to Google
- agents: run the merge, clean and create with Antigravity's agy (--agent antigravity)
- cli: name the agent's model args.model, not args.claude_model
- readme: add an Italian version, switchable from the top
- setup: clone whisper.cpp at a pinned tag and commit on Linux

## 0.2.1 (2026-10-05)

- menu: show the transcript's size before a run starts
- menu: keep backslash-space in Windows paths, where the backslash separates folders
- readme: drop the pipx --force warning, the PyPI names are placeholders now
- readme: setup keeps your backend on a rerun
- setup: a rerun keeps the installed backend unless --backend changes it
- readme: update with pipx upgrade, check the version, watch releases
- cli, doctor: rec2notes --version, and the version as doctor's first row

## 0.2.0 (2026-10-02)

- tests: the banner test's stdin is not a terminal, so a bare rec2notes never opens the menu
- menu: a wrong answer at Start asks again without repeating the summary
- readme: the course question, c at Start and the defaults in Settings
- menu: say that a new note's path takes ~, not $HOME
- menu: say what stops a run before Start, not after
- menu: c at Start changes the Claude model, effort and Whisper model for this run
- menu: Settings saves the default Claude model, effort and Whisper model
- paths: settings.toml also holds the default Claude model and effort
- menu: always ask the course; the note's folder only pre-selects it

## 0.1.2 (2026-10-01)

- transcribe: say to install the VC++ runtime when whisper-cli can't start
- cli: write the vault notes with LF line endings on Windows too
- setup, doctor: point a fresh install at the Courses screen
- setup: save the Whisper model it downloads, and run with it

## 0.1.1 (2026-10-01)

- setup: pin the model downloads to Hugging Face commits and check their SHA-256

## 0.1.0 (2026-10-01)

- setup: pin the Vulkan zip rebuilt without the build machine's home path
- whisper_vulkan: build outside the home folder and refuse a zip that names it
- licence: MIT
- readme: purpose first, Windows and Linux install, use through the menu
- tests, help: made-up courses instead of the author's
- readme: made-up examples instead of the author's lecture and courses
- docs: whisper_vulkan.py builds the Windows Vulkan zip
- setup: a Vulkan backend on Windows, from a hosted prebuilt
- create: write a note from a recording alone
- cli: transcribe_all and cached_transcript out of run and dry_run
- setup: install the prebuilt whisper.cpp on Windows; turbo is its default
- setup: download the models with urllib, not whisper.cpp's sh scripts
- tests: skip the symlink test where symlinks can't be made
- readme: cut to the essentials
- uninstall: `rec2notes uninstall` deletes the rec2notes folder and its pointer
- menu: Settings shows the rec2notes folder and points to one the user moved
- tests: the sandbox drops FORCE_COLOR, which coloured argparse's usage
- folder: everything in one rec2notes folder, chosen in setup
- package: install with pipx; `rec2notes setup` replaces bin/setup
- transcribe: whisper-cli's arguments from a UTF-8 file, run in the run directory
- paths: whisper-cli's built path per platform
- tests: run on Windows
- bin: write stdout and stderr as UTF-8
- courses, doctor: folders with drive letters, matched ignoring case on Windows
- paths: AppData folders on Windows; run claude and ffmpeg by their full path
- stopping: handle only the signals the platform has (no SIGHUP on Windows)
- setup: default to large-v3-turbo on the CPU backend
- merge: show the transcript size before the merge, confirm when unusually long
- clean: optional note cleaning (rec2notes clean, --clean, menu question)
- courses: rename a course's slug (course edit --slug, menu Courses > e)
- courses: edit a course's name, vocab or folder (course edit, menu Courses > e)
- courses: the repo ships none; read only the user's courses.toml
- doctor: blame only the outer folder of an overlap, and name the courses inside it
- doctor: check each course's folder (overlap, exists, writable)
- courses: refuse a folder that is, contains or is inside another course's folder; say the folder is used as it is
- menu: say when a new course was added without a folder, and what to do about it
- courses: refuse a folder you can't write in, or can't create because its nearest existing parent isn't writable
- menu: ask for the new course's slug first and refuse a bad or taken one before asking anything else
- courses: the folder typed when adding a course must be an absolute path; offer to create it if missing
- courses: users define their own; course add|list, menu Courses screen, remember a picked folder
- tests: the doctor alignment test only looks at the passing rows
- doctor: tell the user what to do next when something failed
- doctor: align the details to the longest label
- menu: show the options before every prompt; h, ? and help show them again
- doctor: check the setup and print a fix for each failure; menu entry; handoff: note cleaning allowed as opt-in
- menu: reject non-ASCII digits in the course picker; ask again on an unclear confirmation
- docs: mention the menu; handoff: first menu slice done
- menu: hub and guided run when rec2notes is typed alone in a terminal
- prompts: add the note-cleaning prompt
- merge: missed topics as a ## section instead of a footnote
- setup: offer to put rec2notes on PATH at the end
- setup: ask before installing, then for the backend and the model
- README: restyle for GitHub, with the banner as SVG
- README: add the clone step to the per-computer setup
- gitignore: keep recordings, models and local settings out
- Checklist output with progress and banner; stop cleanly on signals
- rec2notes: hint when an unquoted path with spaces was split
- test: absolute folder paths in folders.toml
- docs: record setup on the dev machine and next steps
- setup: refuse to run under sudo or pkexec
- Build rec2notes: complete lecture notes from the recording
