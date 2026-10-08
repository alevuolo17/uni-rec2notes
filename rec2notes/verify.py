"""Check a finished note against the slides (and the lecture's transcript): the agent lists likely errors, which go
into the note as a section for the student to judge. The note's own text never goes through the agent."""

import base64
import hashlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import Abort, check, merge, paths, stopping
from .i18n import t

SECTION_HEADING = "## Correzioni (da verificare e applicare)"
NO_FINDINGS = "Nessuna correzione"  # the prompt's sentinel
MAX_BYTES = 32 * 1024 * 1024  # what Claude takes in one request, the pages base64-encoded
MAX_PAGES = 100  # the images Claude takes in one request
SCALE_TO = 960  # pixels, the page's longer side: about 880 tokens a page, measured
TOKENS_PER_PAGE = 880
TOKENS_PER_CALL = 1900  # the verify prompt and the call's own overhead, measured
POPPLER_TIMEOUT = 300  # seconds; rendering 100 pages takes well under a minute
RANGE = re.compile(r"(.+\.pdf):(\d+)(?:-(\d+))?", re.I)  # slides.pdf:12-30; C:\x.pdf has no range after its colon
POPPLER = ("pdfinfo", "pdftoppm")
PAGE_FILE = re.compile(r"-(\d+)\.png$")  # pdftoppm's PREFIX-NNN.png
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t#]*$")
FENCE = re.compile(r"^ {0,3}(```|~~~)")


@dataclass(frozen=True)
class Slides:
    pdf: Path
    first: int | None = None  # None: from the first page
    last: int | None = None  # None: to the last page

    @property
    def pages(self) -> int:
        return self.last - self.first + 1


@dataclass(frozen=True)
class Result:
    written: Path  # the note, or a copy in the run directory when the note changed during the run
    findings: int  # the section's list items; 0 for the sentinel
    in_vault: bool


def check_agent(agent: str) -> None:
    if agent != "claude":
        raise Abort(t("verify.claude_only", label=merge.AGENTS[agent].label))


def verify(note: Path, attachments: list[dict], transcript: str | None, run_dir: Path, agent: str,
           effort: str | None, model: str | None, notify: Callable[[str], None] = lambda message: None) -> Result:
    """Ask the agent for the corrections section and write the note with it, replacing an older one.
    `attachments` are the slides, from slide_blocks."""
    check_agent(agent)
    before = note_state(note)
    original = note.read_text(encoding="utf-8")
    input_text = build_input(without_section(original), transcript)
    (run_dir / "verify-input.txt").write_text(input_text, encoding="utf-8")
    reply = merge.run_agent(agent, input_text, run_dir, effort, model, notify, prompt=paths.VERIFY_PROMPT,
                            file_prefix="verify-", attachments=attachments)
    body = section_body(reply.text)
    text = with_section(original, body)
    stopping.check()
    if note_state(note) != before:  # Obsidian Sync or the student changed it: don't overwrite their edit
        copy = run_dir / note.name
        copy.write_text(text, encoding="utf-8", newline="\n")
        return Result(copy, findings(body), in_vault=False)
    shutil.copy2(note, run_dir / f"{note.stem} (before verify).md")
    with open(note, "w", encoding="utf-8", newline="\n") as f:  # one write: no temporary file in the vault
        f.write(text)
    return Result(note, findings(body), in_vault=True)


def note_state(note: Path) -> tuple[int, str]:
    return note.stat().st_mtime_ns, hashlib.sha256(note.read_bytes()).hexdigest()


def parse_slides(arg: str) -> Slides:
    """`slides.pdf`, or `slides.pdf:12-30` (`:12` is one page). The range is checked by `with_pages`."""
    if match := RANGE.fullmatch(arg):
        return Slides(Path(match[1]), int(match[2]), int(match[3] or match[2]))
    return Slides(Path(arg))


def with_pages(slides: Slides) -> Slides:
    """The slides with their range filled in from the PDF's page count, after checking that it is a PDF and that
    the range is within it."""
    with open(slides.pdf, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise Abort(t("verify.not_a_pdf", file=slides.pdf))
    count = page_count(slides.pdf)
    first, last = (1, count) if slides.first is None else (slides.first, slides.last)
    if not 1 <= first <= last <= count:
        raise Abort(t("verify.bad_range", file=slides.pdf, first=first, last=last, count=count))
    return Slides(slides.pdf, first, last)


def check_pages(slides: list[Slides]) -> None:
    if (pages := sum(s.pages for s in slides)) > MAX_PAGES:
        raise Abort(t("verify.too_many_pages", pages=pages, max=MAX_PAGES))


def estimate_tokens(pages: int, note: str) -> int:
    return pages * TOKENS_PER_PAGE + len(note) // 3 + TOKENS_PER_CALL


def tokens_label(tokens: int) -> str:
    return t("verify.tokens", k=max(1, round(tokens / 1000)))


def page_count(pdf: Path) -> int:
    output = _poppler(["pdfinfo", os.path.abspath(pdf)], pdf)
    if match := re.search(r"^Pages:\s+(\d+)", output, re.M):
        return int(match[1])
    raise Abort(t("verify.unreadable", file=pdf, error=output.strip()[-200:]))


def slide_blocks(slides: list[Slides], run_dir: Path) -> list[dict]:
    """Each page rendered in the run directory (never the vault), then sent as a text block naming its file and page,
    so findings can cite them, and a base64 PNG image block. `slides` come from `with_pages`."""
    check_pages(slides)
    blocks, size = [], 0
    for k, s in enumerate(slides, 1):
        prefix = run_dir / f"slides-{k}"
        _poppler(["pdftoppm", "-f", str(s.first), "-l", str(s.last), "-scale-to", str(SCALE_TO), "-png",
                  os.path.abspath(s.pdf), str(prefix)], s.pdf)
        images = sorted(run_dir.glob(f"slides-{k}-*.png"), key=lambda p: int(PAGE_FILE.search(p.name)[1]))
        for image in images:
            encoded = base64.b64encode(image.read_bytes()).decode("ascii")
            size += len(encoded)
            page = int(PAGE_FILE.search(image.name)[1])
            blocks += [{"type": "text", "text": f"<slide file=\"{s.pdf.name}\" page=\"{page}\">"},
                       {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded}}]
    if size > MAX_BYTES:
        raise Abort(t("verify.too_big", mb=round(size / 1024 / 1024), max=MAX_BYTES // 1024 // 1024))
    return blocks


def _poppler(command: list[str], pdf: Path) -> str:
    """Run a poppler tool on an absolute path (never one starting with `-`), without a shell; its stdout."""
    try:
        result = subprocess.run([paths.program(command[0]), *command[1:]], capture_output=True,
                                timeout=POPPLER_TIMEOUT, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise Abort(t("verify.no_poppler", hint=poppler_hint())) from None
    except subprocess.TimeoutExpired:
        raise Abort(t("verify.unreadable", file=pdf, error=t("verify.timed_out"))) from None
    if result.returncode != 0:
        raise Abort(t("verify.unreadable", file=pdf, error=result.stderr.strip()[-200:]))
    return result.stdout


def poppler_installed() -> bool:
    return all(shutil.which(tool) for tool in POPPLER)


def poppler_hint() -> str:
    return t("verify.poppler_windows" if paths.WINDOWS else "verify.poppler_linux")


def build_input(note: str, transcript: str | None) -> str:
    text = f"<notes>\n{merge.with_final_newline(note)}</notes>\n"
    if transcript is not None:
        text += f"\n<transcript>\n{merge.with_final_newline(transcript)}</transcript>\n"
    return text


def without_section(note: str) -> str:
    """The note without its corrections section: from the heading to the next heading of level 1 or 2, the next
    footnote definition (the note's own, kept), or the end."""
    lines = note.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.rstrip() == SECTION_HEADING), None)
    if start is None:
        return note
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"#{1,2}[ \t]", lines[i])
                or check.FOOTNOTE_DEFINITION.match(lines[i])), len(lines))
    kept = "".join(lines[:start]).rstrip("\n")
    rest = "".join(lines[end:])
    return f"{kept}\n\n{rest}" if kept and rest else (kept + "\n" if kept else rest)


def section_body(reply: str) -> str:
    """The reply as the section's body: headings become bold lines and footnote definitions are escaped, so it can't
    open sections or footnotes of its own and a rerun finds where it ends; a copy of the section heading is dropped."""
    lines, in_fence = [], False
    for line in reply.strip().splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else HEADING.match(line)
        if heading and line.strip() == SECTION_HEADING:
            continue
        if heading:
            line = f"**{heading.group(2)}**" if heading.group(2) else ""
        elif not in_fence and check.FOOTNOTE_DEFINITION.match(line):
            line = "\\" + line
        lines.append(line)
    return "\n".join(lines).strip() or NO_FINDINGS


def with_section(note: str, body: str) -> str:
    kept = without_section(note).rstrip()
    return f"{kept}\n\n{SECTION_HEADING}\n\n{body}\n" if kept else f"{SECTION_HEADING}\n\n{body}\n"


def findings(body: str) -> int:
    return len(re.findall(r"^(?:[-*+]|\d+[.)])[ \t]", body, re.M))
