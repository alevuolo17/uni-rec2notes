"""The `rec2notes` command that pipx installs, also run as `python -m rec2notes` from a checkout."""

import sys

from .cli import main as cli_main


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows writes a pipe or file in its ANSI code page otherwise
        stream.reconfigure(encoding="utf-8")
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
