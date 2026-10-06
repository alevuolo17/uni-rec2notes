import signal
import unittest

from rec2notes import i18n, stopping


class Handling(unittest.TestCase):
    def test_a_signal_is_recorded_not_raised(self):
        before = signal.getsignal(signal.SIGTERM)
        with stopping.handling():
            signal.raise_signal(signal.SIGTERM)  # the handler runs here, and must not raise
            with self.assertRaises(stopping.Stopped) as raised:
                stopping.check()
        self.assertEqual((raised.exception.message, raised.exception.status), ("stopped by SIGTERM", 143))
        self.assertIs(signal.getsignal(signal.SIGTERM), before)
        stopping.check()  # nothing left over for the next command

    def test_ctrl_c_is_an_interruption(self):
        with stopping.handling():
            signal.raise_signal(signal.SIGINT)
            with self.assertRaises(stopping.Stopped) as raised:
                stopping.check()
        self.assertEqual((raised.exception.message, raised.exception.status), ("interrupted", 130))


class InItalian(unittest.TestCase):
    def test_the_messages_are_in_italian(self):
        self.addCleanup(i18n.set_language, "en")
        i18n.set_language("it")
        self.assertEqual(stopping.Stopped(signal.SIGTERM).message, "fermato da SIGTERM")
        self.assertEqual(stopping.Stopped(signal.SIGINT).message, "interrotto")
