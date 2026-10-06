import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.M | re.S)


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def commands(text: str) -> list[list[str]]:
    """Each code block but the diagrams, without its comments: what must stay the same in every language."""
    return [[line for line in body.splitlines() if not line.lstrip().startswith("#")]
            for lang, body in FENCE.findall(text) if lang != "mermaid"]


def outline(text: str) -> list:
    prose = FENCE.sub("", text)
    return [len(re.findall(rf"^{'#' * level} ", prose, re.M)) for level in (1, 2, 3)] + [prose.count("<details>")]


class ItalianReadme(unittest.TestCase):
    """README.it.md translates README.md: the prose may differ, the commands and the sections may not."""

    def setUp(self):
        self.english, self.italian = read("README.md"), read("README.it.md")

    def test_same_commands(self):
        self.assertEqual(commands(self.english), commands(self.italian))

    def test_same_sections(self):
        self.assertEqual(outline(self.english), outline(self.italian))

    def test_each_links_to_the_other(self):
        self.assertIn('href="README.it.md"', self.english)
        self.assertIn('href="README.md"', self.italian)
