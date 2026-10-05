"""Complete a formatted lecture note from the lecture recording."""

from importlib import metadata


class Abort(Exception):
    """A failure reported to the user as a plain message, without a traceback."""


def version() -> str:
    """The installed version; a checkout run with `python -m rec2notes` has none."""
    try:
        return metadata.version("uni-rec2notes")
    except metadata.PackageNotFoundError:
        return "unknown (not installed)"
