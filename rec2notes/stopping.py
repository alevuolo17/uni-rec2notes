"""Stopping cleanly on Ctrl-C, SIGTERM (a stopped task) or SIGHUP (a closed terminal; not on Windows).

Python's default raises KeyboardInterrupt wherever the program happens to be, and on
Python 3.14 that can land between taking a lock and entering its `with` block, leaving
the lock held and the cleanup hung. So the handler never raises: it records the signal
and stops the running child process (a second signal kills it). The code then raises
Stopped at safe points, `check()`, once the child is gone or before starting another.
"""

import contextlib
import signal
import subprocess

from .i18n import t

SIGNALS = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))

_received: list[int] = []
_children: list[subprocess.Popen] = []


class Stopped(Exception):
    def __init__(self, signum: int):
        super().__init__(signum)
        self.signum = signum
        self.status = 128 + signum
        self.message = (t("stopping.interrupted") if signum == signal.SIGINT
                        else t("stopping.stopped_by", name=signal.Signals(signum).name))


@contextlib.contextmanager
def handling():
    """Install the handler for the duration of a command. Main thread only."""
    def handler(signum, frame):
        _received.append(signum)
        for proc in list(_children):
            if proc.poll() is None:  # poll(), terminate() and kill() only try the Popen's lock, never wait on it
                if len(_received) == 1:
                    proc.terminate()
                else:
                    proc.kill()

    previous = {s: signal.signal(s, handler) for s in SIGNALS}
    try:
        yield
    finally:
        for s, old in previous.items():
            signal.signal(s, old)
        _received.clear()
        _children.clear()


def check() -> None:
    """Raise Stopped if a stop signal has arrived."""
    if _received:
        raise Stopped(_received[0])


@contextlib.contextmanager
def child(proc: subprocess.Popen):
    """Let a stop signal reach `proc` while the block runs."""
    _children.append(proc)
    try:
        if _received:  # a signal that came just before registration
            proc.terminate()
        yield proc
    finally:
        _children.remove(proc)
