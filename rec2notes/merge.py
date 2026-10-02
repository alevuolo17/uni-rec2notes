"""The agent steps: build the merge input and run `claude -p` on it (or on a note to clean, or a note to create)."""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import Abort, paths, stopping
from .courses import Course


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


def claude_command(effort: str, model: str | None, prompt: Path = paths.MERGE_PROMPT) -> list[str]:
    cmd = ["claude", "-p", "--effort", effort, "--tools", "", "--no-session-persistence",
           "--system-prompt-file", str(prompt)]
    if model:
        cmd += ["--model", model]
    return cmd


def run_claude(input_text: str, run_dir: Path, effort: str, model: str | None,
               notify: Callable[[str], None] = lambda message: print(message, file=sys.stderr),
               prompt: Path = paths.MERGE_PROMPT, file_prefix: str = "") -> str:
    """Claude's reply to `input_text` under the system prompt `prompt`, retrying once when the call fails
    or the reply is empty. The reply and stderr are kept in the run directory under `file_prefix`.

    Runs in the run directory so that no CLAUDE.md or settings from the caller's
    working directory reach the agent. No timeout: a full lecture takes minutes.
    """
    reply_path, stderr_path = run_dir / f"{file_prefix}reply.md", run_dir / f"{file_prefix}claude-stderr.txt"
    cmd = claude_command(effort, model, prompt)
    cmd[0] = paths.program(cmd[0])
    for attempt in (1, 2):
        stopping.check()
        with (open(stderr_path, "a", encoding="utf-8") as stderr,
              subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=stderr, text=True, encoding="utf-8", cwd=run_dir) as proc,
              stopping.child(proc)):
            reply, _ = proc.communicate(input_text)
        reply_path.write_text(reply, encoding="utf-8")
        stopping.check()
        if proc.returncode == 0 and reply.strip():
            return reply
        problem = f"exited with status {proc.returncode}" if proc.returncode else "returned an empty reply"
        if attempt == 1:
            notify(f"claude {problem}; retrying once...")
    tail = stderr_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-5:]
    details = ("\n  " + "\n  ".join(tail)) if tail else ""
    raise Abort(f"claude {problem} twice. The reply and claude's stderr are kept in {run_dir}{details}")


def _with_final_newline(text: str) -> str:
    return text if not text or text.endswith("\n") else text + "\n"
