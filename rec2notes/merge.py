"""The agent steps: build the merge input and run the agent (`claude -p` or `agy -p`) on it, or on a note to clean,
or a note to create."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import Abort, paths, stopping
from .courses import Course
from .i18n import t


def build_input(course: Course, note: str, transcript: str) -> str:
    return f"{_course(course)}<notes>\n{_with_final_newline(note)}</notes>\n\n{_transcript(transcript)}"


def build_create_input(course: Course, transcript: str, words: int, target: int) -> str:
    """The input of a note created from the recording alone: no notes, a length to aim for instead."""
    length = f"<length>\ntranscript: {words:,} words\ntarget: about {target:,} words\n</length>\n\n"
    return f"{_course(course)}{length}{_transcript(transcript)}"


def _course(course: Course) -> str:
    return f"<course>\nname: {course.name}\nvocab: {course.vocab}\n</course>\n\n"


def _transcript(transcript: str) -> str:
    return f"<transcript>\n{_with_final_newline(transcript)}</transcript>\n"


EFFORTS = ("low", "medium", "high", "xhigh", "max")  # what claude --effort takes


@dataclass(frozen=True)
class Agent:
    label: str  # the product's name, for messages
    program: str  # the command on PATH
    page: str  # where to get it

    def install_hint(self) -> str:
        return t("merge.install_hint", page=self.page, program=self.program)


AGENTS = {"claude": Agent("Claude Code", "claude", "https://claude.com/claude-code"),
          "antigravity": Agent("Antigravity", "agy", "https://antigravity.google/docs/cli")}
AGY_SETTINGS = {  # agy's settings.json in its throwaway home: every tool asks first, and headless mode denies what asks
    "toolPermission": "strict", "enableTelemetry": False, "enableTerminalSandbox": True,
    "allowNonWorkspaceAccess": False, "permissions": {"allow": []},
}
AGY_TIMEOUT = 60  # seconds for `agy models` (usually 2-5): it hangs when agy is not signed in
AGY_DROPPED_ENV = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
REMOVE_TRIES = 40  # a quarter of a second apart: on Windows a background agy (its updater) locks a file for a moment


@dataclass(frozen=True)
class Reply:
    text: str
    tools: list[str]  # the tools the agent used; Antigravity's web search can't be turned off


def command(agent: str, effort: str | None, model: str | None, prompt: Path = paths.MERGE_PROMPT) -> list[str]:
    return antigravity_command(model) if agent == "antigravity" else claude_command(effort, model, prompt)


def claude_command(effort: str, model: str | None, prompt: Path = paths.MERGE_PROMPT) -> list[str]:
    cmd = ["claude", "-p", "--effort", effort, "--tools", "", "--no-session-persistence",
           "--system-prompt-file", str(prompt)]
    if model:
        cmd += ["--model", model]
    return cmd


def antigravity_command(model: str) -> list[str]:
    """agy reads one stream-json message from stdin (-p only takes the prompt as an argument, too short for a
    transcript); its model names carry the effort. No slash commands or skills expanded from the message. No timeout
    in practice, as for claude."""
    return ["agy", "-p=", "--input-format", "stream-json", "--output-format", "stream-json",
            "--disable-slash-commands", "--print-timeout", "12h", "--model", model]


def agy_env(home: Path) -> dict[str, str]:
    """agy's environment: its home at `home`, so it reads only rec2notes' settings (none of the user's allowed
    commands, GEMINI.md, MCP servers or plugins) and saves the conversation there; without the XDG folders, so
    nothing lands in the real ones. The login stays reachable: it is in the system keyring."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in AGY_DROPPED_ENV}
    env["HOME"] = str(home)
    if paths.WINDOWS:
        env["USERPROFILE"] = str(home)
    return env


def antigravity_models() -> list[str]:
    """The models the signed-in account can use, from `agy models` (it sends no prompt)."""
    home = Path(tempfile.mkdtemp(prefix="rec2notes-agy-"))
    try:
        listing = subprocess.run([paths.program("agy"), "models"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, cwd=home,
                                 env=agy_env(home), timeout=AGY_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise Abort(t("merge.agy_timeout", seconds=AGY_TIMEOUT)) from None
    except OSError as e:
        raise Abort(t("merge.agy_cannot_run", reason=e.strerror)) from None
    finally:
        try:
            remove_home(home)
        except OSError:
            pass  # it holds no prompt: agy only listed the models
    models = [line.split("\t")[0] for line in listing.stdout.splitlines() if "\t" in line]
    if listing.returncode or not models:
        last = (listing.stderr.strip().splitlines() or [t("merge.no_models")])[-1]
        raise Abort(t("merge.agy_models_failed", last=last.rstrip(".")))
    return models


def remove_home(folder: Path) -> None:
    """Delete one of agy's throwaway folders, trying again while something still holds a file in it."""
    for _ in range(REMOVE_TRIES - 1):
        try:
            shutil.rmtree(folder)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.25)
    shutil.rmtree(folder)


def run_agent(agent: str, input_text: str, run_dir: Path, effort: str | None, model: str | None,
              notify: Callable[[str], None] = lambda message: print(message, file=sys.stderr),
              prompt: Path = paths.MERGE_PROMPT, file_prefix: str = "") -> Reply:
    """The agent's reply to `input_text` under the system prompt `prompt`, retrying once when the call fails
    or the reply is empty. The reply and stderr are kept in the run directory under `file_prefix`.

    Runs in the run directory (Antigravity: an empty folder in it) so that no CLAUDE.md, AGENTS.md or settings
    from the caller's working directory reach the agent. No timeout: a full lecture takes minutes.
    """
    program = AGENTS[agent].program
    reply_path, stderr_path = run_dir / f"{file_prefix}reply.md", run_dir / f"{file_prefix}{program}-stderr.txt"
    denied: list[str] = []
    for attempt in (1, 2):
        stopping.check()
        if agent == "antigravity":
            status, reply, tools, denied = _call_antigravity(input_text, run_dir, model, prompt, stderr_path,
                                                             run_dir / f"{file_prefix}agy-events.jsonl", notify)
        else:
            status, reply, tools = _call_claude(input_text, run_dir, effort, model, prompt, stderr_path)
        reply_path.write_text(reply, encoding="utf-8")
        stopping.check()
        if status == 0 and reply.strip():
            return Reply(reply, tools)
        problem = t("merge.exited", status=status) if status else t("merge.empty_reply")
        if attempt == 1:
            notify(t("merge.retrying", program=program, problem=problem))
    tail = stderr_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-5:]
    details = ("\n  " + "\n  ".join(tail)) if tail else ""
    blocked = "\n" + t("merge.blocked", tools=", ".join(denied)) if denied else ""
    raise Abort(t("merge.twice", program=program, problem=problem, run_dir=run_dir, details=details, blocked=blocked))


def _call_claude(input_text: str, run_dir: Path, effort: str, model: str | None, prompt: Path,
                 stderr_path: Path) -> tuple[int, str, list[str]]:
    cmd = claude_command(effort, model, prompt)
    cmd[0] = paths.program(cmd[0])
    with (open(stderr_path, "a", encoding="utf-8") as stderr,
          subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=stderr, text=True, encoding="utf-8", cwd=run_dir) as proc,
          stopping.child(proc)):
        reply, _ = proc.communicate(input_text)
    return proc.returncode, reply, []  # --tools "": it has none


def _call_antigravity(input_text: str, run_dir: Path, model: str, prompt: Path, stderr_path: Path,
                      events_path: Path, notify: Callable[[str], None]) -> tuple[int, str, list[str], list[str]]:
    """One agy call in a throwaway home holding only rec2notes' settings, deleted afterwards with the conversation
    agy saves there. agy has no system prompt option, so the prompt opens the message."""
    home, work = run_dir / "agy-home", run_dir / "agy-work"
    settings = home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)  # exists only if deleting it after the last attempt failed
    settings.write_text(json.dumps(AGY_SETTINGS), encoding="utf-8")
    work.mkdir(exist_ok=True)
    content = f"{prompt.read_text(encoding='utf-8')}\n\n{input_text}"
    cmd = antigravity_command(model)
    cmd[0] = paths.program(cmd[0])
    try:
        with (open(stderr_path, "a", encoding="utf-8") as stderr,
              subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr, text=True,
                               encoding="utf-8", errors="replace", cwd=work, env=agy_env(home)) as proc,
              stopping.child(proc)):
            events, _ = proc.communicate(json.dumps({"event": "user", "message": {"content": content}}) + "\n")
    finally:
        for folder in (home, work):
            try:
                remove_home(folder)
            except OSError as e:
                notify(t("merge.cannot_delete", folder=folder, reason=e.strerror))
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(events)
    return proc.returncode, *_read_events(events)


def _read_events(events: str) -> tuple[str, list[str], list[str]]:
    """(reply, tools used, tools denied) from agy's stream-json output."""
    reply, tools, denied = "", [], []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        step, result = event.get("step_update") or {}, event.get("result") or {}
        if step.get("step_type") == "tool" and step.get("tool_name") and step["tool_name"] not in tools:
            tools.append(step["tool_name"])
        if event.get("event") == "result":
            reply = result.get("response") or ""
            denied = [a.get("action", "?") for a in result.get("denied_actions") or []]
    return reply, tools, denied


def _with_final_newline(text: str) -> str:
    return text if not text or text.endswith("\n") else text + "\n"
