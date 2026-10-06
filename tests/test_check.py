import unittest

from rec2notes import check, i18n

NOTE = """# Handshake

Il three-way handshake si compone di tre messaggi. Serve a sincronizzare i *sequence number*[^1].

- Se il server non risponde: [?]

[^1]: Detti anche ISN.
"""


class OnlyAdditions(unittest.TestCase):
    def test_additions_and_footnotes_pass(self):
        reply = """# Handshake

Il three-way handshake si compone di tre messaggi.[^conflitto-1] Serve a sincronizzare i *sequence number*[^1] iniziali.

- Se il server non risponde: il client ritrasmette il **SYN**.

## Argomenti non presenti negli appunti

- **SYN flood** [00:42:40], dopo «Handshake»: molti SYN.

[^1]: Detti anche ISN.
[^conflitto-1]: **Appunti:** «tre messaggi» — **Audio [00:41:02]:** «tre messaggi»
"""
        self.assertEqual(check.missing_passages(NOTE, reply), [])

    def test_reworded_passage_is_reported(self):
        reply = NOTE.replace("sincronizzare i", "allineare i")
        self.assertEqual(check.missing_passages(NOTE, reply), [check.Change("sincronizzare", "allineare")])

    def test_deleted_passage_is_reported(self):
        reply = NOTE.replace("- Se il server non risponde: [?]\n", "")
        self.assertEqual(check.missing_passages(NOTE, reply), [check.Change("Se il server non risponde", "")])

    def test_text_moved_into_a_footnote_is_reported(self):
        reply = NOTE.replace("Serve a sincronizzare i *sequence number*[^1].", "[^1]") + "[^2]: Serve a sincronizzare i sequence number.\n"
        changes = check.missing_passages(NOTE, reply)
        self.assertEqual([c.note for c in changes], ["Serve a sincronizzare i *sequence number"])


class Summary(unittest.TestCase):
    def test_counts_conflicts_and_detects_missed_topics(self):
        reply = "A.[^conflitto-1] B.[^conflitto-2]\n\n## Argomenti non presenti negli appunti\n\n- z\n\n[^conflitto-1]: x\n[^conflitto-2]: y\n"
        self.assertEqual(check.summary(reply), (2, True))
        self.assertEqual(check.summary("Solo testo.\n"), (0, False))
        self.assertEqual(check.summary("Argomenti non presenti negli appunti, in una frase.\n"), (0, False))


class InItalian(unittest.TestCase):
    def setUp(self):
        i18n.set_language("it")
        self.addCleanup(i18n.set_language, "en")

    def test_the_report_and_the_descriptions_are_in_italian_but_the_markers_are_not_translated(self):
        change = check.Change("tre messaggi", "due messaggi")
        self.assertEqual(check.describe(change), "«tre messaggi» è diventato «due messaggi»")
        self.assertEqual(check.describe(check.Change("tre messaggi", "")), "«tre messaggi» manca")
        report = check.report([change], 2, True)
        self.assertIn("Note di conflitto: 2\nSezione degli argomenti persi: sì\n", report)
        self.assertIn("1. appunti:  «tre messaggi»\n   risposta: «due messaggi»", report)
        self.assertEqual(check.summary("x[^conflitto-1]\n[^conflitto-1]: a\n\n## Argomenti non presenti negli appunti\n"), (1, True))
