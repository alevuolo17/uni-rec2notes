"""Checks on Claude's reply: the only-additions check and the conflicts and missed-topics summary."""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .i18n import t

FOOTNOTE_DEFINITION = re.compile(r"^\[\^[^\]\s]+\]:")
FOOTNOTE_REFERENCE = re.compile(r"\[\^[^\]\s]+\]")
WORD = re.compile(r"\w+")
TRANSCRIPT_MISMATCH = "Il transcript non sembra corrispondere a questi appunti"
MISSED_TOPICS_HEADING = "## Argomenti non presenti negli appunti"


@dataclass(frozen=True)
class Change:
    note: str   # a passage of the note that the reply lacks
    reply: str  # what the reply has in its place, "" if nothing


def strip_footnotes(text: str) -> str:
    """Drop footnote definitions (with their indented continuation lines) and references."""
    kept, in_definition = [], False
    for line in text.splitlines():
        if FOOTNOTE_DEFINITION.match(line):
            in_definition = True
        elif not (in_definition and (not line.strip() or line[0] in " \t")):
            in_definition = False
            kept.append(line)
    return FOOTNOTE_REFERENCE.sub("", "\n".join(kept))


def missing_passages(note: str, reply: str) -> list[Change]:
    """Passages of the note whose words don't appear, in order, in the reply.

    Footnotes are stripped from both sides, so the note's own footnotes, which the
    reply must keep, are compared the same way as the rest.
    """
    note_text, reply_text = strip_footnotes(note), strip_footnotes(reply)
    note_words, reply_words = list(WORD.finditer(note_text)), list(WORD.finditer(reply_text))
    a, b = [m.group() for m in note_words], [m.group() for m in reply_words]
    remaining = iter(b)
    if all(word in remaining for word in a):
        return []
    return [
        Change(_span(note_text, note_words, i1, i2), _span(reply_text, reply_words, j1, j2))
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
        if tag in ("delete", "replace")
    ]


def describe(change: Change, width: int | None = None) -> str:
    if change.reply:
        return t("check.became", note=_clip(change.note, width), reply=_clip(change.reply, width))
    return t("check.is_missing", note=_clip(change.note, width))


def summary(reply: str) -> tuple[int, bool]:
    """(number of conflict footnotes, whether there is a missed-topics section)"""
    conflicts = set(re.findall(r"^\[\^(conflitto-[^\]\s]+)\]:", reply, re.M))
    missed = re.search(rf"^{re.escape(MISSED_TOPICS_HEADING)}[ \t]*$", reply, re.M) is not None
    return len(conflicts), missed


def report(changes: list[Change], conflicts: int, missed: bool) -> str:
    lines = [t("check.conflicts", n=conflicts), t("check.missed", answer=t("yes" if missed else "no")), ""]
    if not changes:
        lines.append(t("check.passed"))
    else:
        lines.append(t("check.failed", n=len(changes)))
        lines.append(t("check.footnotes_ignored"))
        for n, change in enumerate(changes, 1):
            lines += ["", t("check.item_note", n=n, note=change.note),
                      t("check.item_reply", reply=change.reply) if change.reply else t("check.item_nothing")]
    return "\n".join(lines) + "\n"


def _span(text: str, words: list[re.Match], i: int, j: int) -> str:
    if i == j:
        return ""
    return " ".join(text[words[i].start():words[j - 1].end()].split())


def _clip(text: str, width: int | None) -> str:
    if width is None or len(text) <= width:
        return text
    return text[:width - 1] + "…"
