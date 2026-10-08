import base64
import json
import os
import unittest
from unittest import mock

from rec2notes import Abort, merge, paths, verify

from .helpers import Sandbox

FINDING = "- **Appunti:** «x» — **Correzione:** y — **Fonte:** slide 1: «z»"
SECTION = f"{verify.SECTION_HEADING}\n\n{FINDING}\n"


class Verify(Sandbox):
    def setUp(self):
        super().setUp()
        self.slides = self.tmp / "slides" / "Lezione 1.pdf"
        self.slides.parent.mkdir()
        self.slides.write_bytes(b"%PDF-1.4\nfake slides\n%%EOF\n")
        self.run_dir = self.tmp / "run"
        self.run_dir.mkdir()
        self.message = self.tmp / "message.json"
        os.environ["FAKE_CLAUDE_MESSAGE"] = str(self.message)

    def verify(self, transcript=None, agent="claude", slides=None):
        given = [verify.with_pages(verify.Slides(pdf)) for pdf in slides or [self.slides]]
        return verify.verify(self.note, verify.slide_blocks(given, self.run_dir), transcript, self.run_dir, agent,
                             "high", None)

    def test_appends_the_section_and_keeps_the_note_text(self):
        original = self.note.read_text(encoding="utf-8")
        result = self.verify()
        self.assertEqual(result, verify.Result(self.note, 1, in_vault=True))
        self.assertEqual(self.note.read_text(encoding="utf-8"), f"{original.rstrip()}\n\n{SECTION}")
        self.assertNotIn(b"\r\n", self.note.read_bytes())
        self.assertEqual(len(list(self.note.parent.iterdir())), 1, "nothing else may appear in the vault")
        self.assertEqual((self.run_dir / "Lezione 1 (before verify).md").read_text(encoding="utf-8"), original)

    def test_a_rerun_replaces_the_section(self):
        original = self.note.read_text(encoding="utf-8")
        self.verify()
        os.environ["FAKE_VERIFY_REPLY"] = "Nessuna correzione.\n"
        self.assertEqual(self.verify().findings, 0)
        self.assertEqual(self.note.read_text(encoding="utf-8"),
                         f"{original.rstrip()}\n\n{verify.SECTION_HEADING}\n\nNessuna correzione.\n")
        self.assertNotIn(verify.SECTION_HEADING, (self.run_dir / "verify-input.txt").read_text(encoding="utf-8"),
                         "the agent must not see its old section")

    def test_each_page_goes_as_an_image_named_by_file_and_page_before_the_text(self):
        self.slides.write_bytes(b"%PDF-1.4\npages 12\n")
        given = verify.with_pages(verify.parse_slides(f"{self.slides}:9-11"))
        verify.verify(self.note, verify.slide_blocks([given], self.run_dir), "[00:00:01] buongiorno\n",
                      self.run_dir, "claude", "high", None)
        self.assertEqual(self.calls("claude"), [merge.claude_command("high", None, paths.VERIFY_PROMPT)[1:] + merge.STREAM_JSON])
        message = json.loads(self.message.read_text(encoding="utf-8"))
        self.assertEqual(message["type"], "user")
        *pages, text = message["message"]["content"]
        self.assertEqual([block["text"] for block in pages[::2]],
                         [f'<slide file="Lezione 1.pdf" page="{n}">' for n in (9, 10, 11)])
        self.assertEqual([base64.b64decode(block["source"]["data"]) for block in pages[1::2]],
                         [b"\x89PNG fake page %d" % n for n in (9, 10, 11)])
        self.assertEqual({block["source"]["media_type"] for block in pages[1::2]}, {"image/png"})
        self.assertTrue(text["text"].startswith("<notes>\n---\n"))
        self.assertTrue(text["text"].endswith("</notes>\n\n<transcript>\n[00:00:01] buongiorno\n</transcript>\n"))

    def test_without_audio_there_is_no_transcript(self):
        self.verify()
        self.assertNotIn("<transcript>", (self.run_dir / "verify-input.txt").read_text(encoding="utf-8"))

    def test_a_note_changed_during_the_run_is_left_alone(self):
        os.environ["FAKE_CLAUDE_TOUCH"] = str(self.note)
        result = self.verify()
        edited_by_fake = self.note.read_text(encoding="utf-8")
        self.assertFalse(result.in_vault)
        self.assertEqual(result.written, self.run_dir / self.note.name)
        self.assertNotIn(verify.SECTION_HEADING, edited_by_fake)
        self.assertIn(verify.SECTION_HEADING, result.written.read_text(encoding="utf-8"))

    def test_an_error_result_is_retried_then_reported(self):
        os.environ["FAKE_CLAUDE_MODE"] = "error-result"
        original = self.note.read_bytes()
        with self.assertRaises(Abort) as raised:
            self.verify()
        self.assertIn("Prompt is too long", str(raised.exception))
        self.assertEqual(len(self.calls("claude")), 2)
        self.assertEqual(self.note.read_bytes(), original)

    def test_antigravity_is_refused_before_anything_is_sent(self):
        with self.assertRaises(Abort):
            self.verify(agent="antigravity")
        self.assertEqual(self.calls("agy"), [])

    def test_slides_over_the_request_limit_are_refused(self):
        with mock.patch.object(verify, "MAX_BYTES", 10), self.assertRaises(Abort):
            self.verify()
        self.assertEqual(self.calls("claude"), [])

    def test_a_file_that_is_not_a_pdf_is_refused(self):
        other = self.tmp / "slides.pptx"
        other.write_bytes(b"PK\x03\x04")
        with self.assertRaises(Abort):
            self.verify(slides=[other])
        self.assertEqual(self.calls("claude"), [])

    def test_the_estimate_counts_pages_and_the_note(self):
        self.assertEqual(verify.estimate_tokens(10, "x" * 3000), 10 * 880 + 1000 + 1900)
        self.assertEqual(verify.tokens_label(9400), "≈ 9k tokens")
        self.assertEqual(verify.tokens_label(100), "≈ 1k tokens")


class Section(unittest.TestCase):
    def test_headings_in_the_reply_become_bold_lines(self):
        reply = f"{verify.SECTION_HEADING}\n\n## Slide\n\n{FINDING}\n# Altro ##\n###\n"
        self.assertEqual(verify.section_body(reply), f"**Slide**\n\n{FINDING}\n**Altro**")

    def test_footnote_definitions_in_the_reply_are_escaped(self):
        self.assertEqual(verify.section_body("[^1]: nota\n"), "\\[^1]: nota")

    def test_code_blocks_are_left_as_they_are(self):
        reply = "```\n# commento\n[^1]: x\n```\n"
        self.assertEqual(verify.section_body(reply), reply.strip())

    def test_an_empty_reply_is_the_sentinel(self):
        self.assertEqual(verify.section_body("## \n"), verify.NO_FINDINGS)

    def test_the_old_section_ends_at_the_next_heading_or_footnote(self):
        note = f"# T\n\ntesto\n\n{SECTION}\n## Dopo\n\naltro\n"
        self.assertEqual(verify.without_section(note), "# T\n\ntesto\n\n## Dopo\n\naltro\n")
        note = f"# T\n\ntesto[^1]\n\n{SECTION}\n[^1]: nota\n"
        self.assertEqual(verify.without_section(note), "# T\n\ntesto[^1]\n\n[^1]: nota\n")
        self.assertEqual(verify.with_section(note, "Nessuna correzione."),
                         f"# T\n\ntesto[^1]\n\n[^1]: nota\n\n{verify.SECTION_HEADING}\n\nNessuna correzione.\n")

    def test_a_note_of_only_the_section(self):
        self.assertEqual(verify.without_section(SECTION), "")
        self.assertEqual(verify.with_section(SECTION, "Nessuna correzione."),
                         f"{verify.SECTION_HEADING}\n\nNessuna correzione.\n")

    def test_findings_count_the_list_items(self):
        self.assertEqual(verify.findings(f"{FINDING}\n  - sotto\n1. altro\nNessuna"), 2)
