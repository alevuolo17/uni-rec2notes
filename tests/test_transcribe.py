import json
import os
import unittest
from unittest import mock

from rec2notes import paths, transcribe

from .helpers import MODEL, Sandbox


class Formatting(unittest.TestCase):
    def test_timestamps(self):
        self.assertEqual(transcribe.format_timestamp(0), "00:00:00")
        self.assertEqual(transcribe.format_timestamp(61_999), "00:01:01")
        self.assertEqual(transcribe.format_timestamp(3_725_000), "01:02:05")

    def test_segments_become_lines_and_blank_ones_are_dropped(self):
        raw = json.dumps({"transcription": [
            {"offsets": {"from": 0, "to": 900}, "text": " ciao  a\ttutti"},
            {"offsets": {"from": 1000, "to": 2000}, "text": "   "},
            {"offsets": {"from": 61_500, "to": 63_000}, "text": " il firewall"},
        ]}).encode()
        self.assertEqual(transcribe.format_segments(transcribe.parse_whisper_json(raw)),
                         "[00:00:00] ciao a tutti\n[00:01:01] il firewall\n")

    def test_one_part_keeps_plain_timestamps(self):
        self.assertEqual(transcribe.label_parts(["[00:00:01] a\n"]), "[00:00:01] a\n")

    def test_several_parts_are_labelled_in_order(self):
        self.assertEqual(transcribe.label_parts(["[00:00:01] a\n[00:10:00] b\n", "[00:00:02] c\n"]),
                         "[p1 00:00:01] a\n[p1 00:10:00] b\n[p2 00:00:02] c\n")


class Transcription(Sandbox):
    def input_text(self):
        return (self.run_dirs()[-1] / "input.txt").read_text(encoding="utf-8")

    def test_multi_part_lecture(self):
        second = self.make_audio("lezione-2.m4a", b"audio two")
        code, _, err = self.rec2notes(self.note, self.audio, second)
        self.assertEqual(code, 0, err)
        self.assertIn("[p1 00:00:00] prima frase\n[p1 00:01:01] seconda frase\n[p1 01:02:05] terza frase\n"
                      "[p2 00:00:00] prima frase\n", self.input_text())

    def test_rerun_and_renamed_file_hit_the_cache(self):
        self.assertEqual(self.rec2notes(self.note, self.audio)[0], 0)
        renamed = self.make_audio("rinominato.mp3", self.audio.read_bytes())
        code, out, err = self.rec2notes(self.note, renamed, "--force")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.calls("whisper")), 1)
        self.assertIn("cached", out)
        self.assertIn("[00:01:01] seconda frase", self.input_text())

    def test_an_edited_vocab_gets_its_own_transcript(self):
        self.assertEqual(self.rec2notes(self.note, self.audio)[0], 0)
        self.assertEqual(self.rec2notes("course", "edit", "net", "--vocab", "Lezione di reti: TCP, UDP.")[0], 0)
        code, out, err = self.rec2notes(self.note, self.audio, "--force")
        self.assertEqual(code, 0, err)
        first, second = self.calls("whisper")
        self.assertEqual(second[second.index("--prompt") + 1], "Lezione di reti: TCP, UDP.")
        self.assertNotIn("cached", out)

    def test_another_model_gets_its_own_transcript(self):
        other = "large-v3-turbo-q5_0"
        self.add_model(other)
        self.assertEqual(self.rec2notes(self.note, self.audio)[0], 0)
        code, _, err = self.rec2notes(self.note, self.audio, "--force", "--whisper-model", other)
        self.assertEqual(code, 0, err)
        first, second = self.calls("whisper")
        self.assertEqual(first[first.index("-m") + 1], str(paths.whisper_model(MODEL)))
        self.assertEqual(second[second.index("-m") + 1], str(paths.whisper_model(other)))
        self.assertTrue(self.transcript_cache(MODEL).exists())
        self.assertTrue(self.transcript_cache(other).exists())

    def test_whisper_gets_the_lessons_learned_flags(self):
        self.assertEqual(self.rec2notes(self.note, self.audio)[0], 0)
        (args,) = self.calls("whisper")
        value = lambda flag: args[args.index(flag) + 1]  # noqa: E731
        self.assertEqual(value("-l"), "it")
        self.assertEqual(value("--max-context"), "0")
        self.assertIn("--vad", args)
        self.assertEqual(value("--vad-model"), str(paths.vad_model()))
        self.assertIn("-oj", args)
        self.assertTrue(value("--prompt").startswith("Lezione di reti."))

    def test_whisper_opens_the_audio_and_output_by_name_in_the_run_directory(self):
        # The fake checks it was given only a response file and that -f exists in its working directory.
        code, _, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        (args,) = self.calls("whisper")
        self.assertEqual(args[args.index("-f") + 1], "p1.wav")
        self.assertEqual(args[args.index("-of") + 1], "p1")

    def test_vocab_reaches_whisper_as_one_utf8_line(self):
        self.write_courses('[net]\nname = "Reti di calcolatori"\nvocab = "Lezione: autenticità,\\nintegrità."\n')
        code, _, err = self.rec2notes(self.note, self.audio)
        self.assertEqual(code, 0, err)
        (args,) = self.calls("whisper")
        self.assertEqual(args[args.index("--prompt") + 1], "Lezione: autenticità, integrità.")

    def test_a_missing_dll_on_windows_names_the_vc_runtime(self):
        os.environ["FAKE_WHISPER_EXIT"] = "53"  # the real status, 0xC0000135, doesn't fit in a Linux exit status
        with mock.patch.object(transcribe, "DLL_NOT_FOUND", 53), mock.patch.object(paths, "WINDOWS", True):
            code, _, err = self.rec2notes(self.note, self.audio, "--whisper-model", MODEL)
        self.assertEqual(code, 1)
        self.assertIn("whisper-cli failed on", err)
        self.assertIn("a DLL it needs is missing. Install the Visual C++ runtime with "
                      "`winget install Microsoft.VCRedist.2015+.x64`", err)

    def test_the_length_is_read_without_converting(self):
        lecture = self.make_audio("lunga.m4a", b"x" * 5401)
        self.assertEqual(transcribe.audio_seconds(lecture), 5401.0)
        self.assertEqual(lecture.read_bytes(), b"x" * 5401)

    def test_a_length_ffmpeg_cannot_read_is_none(self):
        self.assertIsNone(transcribe.audio_seconds(self.make_audio("vuota.m4a", b"")))

    def test_missing_model_fails_before_transcribing(self):
        code, _, err = self.rec2notes(self.note, self.audio, "--whisper-model", "medium")
        self.assertEqual(code, 1)
        self.assertIn("ggml-medium.bin is missing", err)
        self.assertIn("--whisper-model medium", err)
        self.assertEqual(self.calls("whisper"), [])
        self.assertEqual(self.run_dirs(), [])
