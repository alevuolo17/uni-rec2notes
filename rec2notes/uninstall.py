"""uninstall: delete what rec2notes keeps in the rec2notes folder, and the pointer to it, after asking.

Only rec2notes' own entries go, and then the folder if that left it empty: a folder picked by
mistake, such as the home folder, keeps everything else. The package itself is pipx's to remove,
after this command has exited (Windows can't delete the running rec2notes.exe).
"""

import argparse
import shutil
import sys
from pathlib import Path

from . import Abort, i18n, paths
from .i18n import t

ENTRIES = ("courses.toml", "folders.toml", "settings.toml", "whisper.cpp", "cache")  # what rec2notes creates in its folder
PIPX_UNINSTALL = "pipx uninstall uni-rec2notes"


def main(argv: list[str]) -> int:
    argparse.ArgumentParser(prog="rec2notes uninstall", description=t("uninstall.description")).parse_args(argv)
    try:
        return run()
    except Abort as e:
        print(f"rec2notes: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError) as e:
        print("\nrec2notes: " + t("uninstall.interrupted" if isinstance(e, KeyboardInterrupt) else "uninstall.no_answer"),
              file=sys.stderr)
        return 130 if isinstance(e, KeyboardInterrupt) else 1


def run() -> int:
    pointer = paths.pointer_file()
    folder = paths.pointed_folder()
    doomed = [(folder / name, t(f"uninstall.entry.{name}")) for name in ENTRIES if (folder / name).exists()] if folder else []
    if pointer.exists():
        doomed.append((pointer, t("uninstall.entry.pointer")))
    if not doomed:
        print(t("uninstall.nothing"))
    else:
        if folder and not folder.is_dir():
            print(t("uninstall.already_gone", folder=folder))
        print(t("uninstall.deletes"))
        width = max(len(str(path)) for path, _ in doomed)
        for path, what in doomed:
            print(f"  {str(path):<{width}}  {what}")
        print(t("uninstall.notes_safe"))
        if input(t("uninstall.ask")).strip().lower() not in i18n.YES:
            print(t("uninstall.nothing_deleted"))
            return 0
        for path, _ in doomed:
            _delete(path)
        for emptied in (folder, pointer.parent):
            _remove_if_empty(emptied)
        if folder and folder.is_dir():
            print(t("uninstall.kept", folder=folder))
        print(t("uninstall.deleted"))
    print(t("uninstall.remove_command", command=PIPX_UNINSTALL))
    return 0


def _delete(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as e:
        raise Abort(t("uninstall.cannot_delete", name=e.filename or path, reason=e.strerror)) from None


def _remove_if_empty(folder: Path | None) -> None:
    if folder is None:
        return
    try:
        folder.rmdir()
    except OSError:  # not empty, or already gone
        pass
