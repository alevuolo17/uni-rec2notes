"""The replies of the fake agents, `claude` and `agy`, by prompt: the clean prompt (clean.md) returns its input under
"Pulito.", the create prompt (create.md) a short note naming the course, the verify prompt (verify.md)
FAKE_VERIFY_REPLY or one finding, and the merge prompt the note with an addition, a conflict footnote and a missed
topic. FAKE_CLAUDE_DROP, in mode drop, is left out of the merged note."""
import os
import re


def reply(prompt: str, stdin: str, mode: str) -> str:
    if prompt == "clean.md":
        return "Pulito.\n\n" + stdin
    if prompt == "verify.md":
        return os.environ.get("FAKE_VERIFY_REPLY", "- **Appunti:** «x» — **Correzione:** y — **Fonte:** slide 1: «z»\n")
    if prompt == "create.md":
        course = re.search(r"^name: (.*)$", stdin, re.M).group(1)
        return f"## Appunti di {course}\n\nNota creata dal transcript.\n"
    note = re.search(r"<notes>\n(.*?)</notes>", stdin, re.S).group(1)
    if mode == "drop":
        note = note.replace(os.environ["FAKE_CLAUDE_DROP"], "", 1)

    # Additions go before the note's own footnote definitions; new definitions go after them.
    lines = note.splitlines(keepends=True)
    cut = next((i for i, line in enumerate(lines) if re.match(r"\[\^[^\]]+\]:", line)), len(lines))
    body, footnotes = "".join(lines[:cut]).rstrip("\n"), "".join(lines[cut:])
    return (
        body + " Aggiunta dal transcript.[^conflitto-1]\n\n"
        "## Argomenti non presenti negli appunti\n\n"
        "- **SYN flood** [01:02:05], dopo «DMZ»: contenuto.\n\n"
        + footnotes
        + "[^conflitto-1]: **Appunti:** «x» — **Audio [00:01:01]:** «y»\n"
    )
