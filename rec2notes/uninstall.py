"""uninstall: delete what rec2notes keeps in the rec2notes folder, and the pointer to it, after asking.

Only rec2notes' own entries go, and then the folder if that left it empty: a folder picked by
mistake, such as the home folder, keeps everything else. The package itself is pipx's to remove,
after this command has exited (Windows can't delete the running rec2notes.exe).
"""

import argparse
import shutil
import sys
from pathlib import Path

from . import Abort, paths

ENTRIES = {  # what rec2notes creates in its folder
    "courses.toml": "your courses",
    "folders.toml": "each course's vault folder on this computer",
    "settings.toml": "the Whisper model setup downloaded",
    "whisper.cpp": "whisper.cpp and the Whisper models",
    "cache": "cached transcripts and past runs",
}
PIPX_UNINSTALL = "pipx uninstall uni-rec2notes"


def main(argv: list[str]) -> int:
    argparse.ArgumentParser(prog="rec2notes uninstall",
                            description="Delete your rec2notes folder's contents and the pointer to it, after asking. "
                                        "Your notes are never touched.").parse_args(argv)
    try:
        return run()
    except Abort as e:
        print(f"rec2notes: {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError) as e:
        print(f"\nrec2notes: {'interrupted' if isinstance(e, KeyboardInterrupt) else 'no answer'}, nothing deleted",
              file=sys.stderr)
        return 130 if isinstance(e, KeyboardInterrupt) else 1


def run() -> int:
    pointer = paths.pointer_file()
    folder = paths.pointed_folder()
    doomed = [(folder / name, what) for name, what in ENTRIES.items() if (folder / name).exists()] if folder else []
    if pointer.exists():
        doomed.append((pointer, "where your rec2notes folder is"))
    if not doomed:
        print("No rec2notes folder or pointer on this computer.")
    else:
        if folder and not folder.is_dir():
            print(f"Your rec2notes folder {folder} is already gone.")
        print("This deletes:")
        width = max(len(str(path)) for path, _ in doomed)
        for path, what in doomed:
            print(f"  {str(path):<{width}}  {what}")
        print("Your notes and their '(completo)' files are not touched.")
        if input("Delete them? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing deleted.")
            return 0
        for path, _ in doomed:
            _delete(path)
        for emptied in (folder, pointer.parent):
            _remove_if_empty(emptied)
        if folder and folder.is_dir():
            print(f"Kept {folder}: it holds files rec2notes didn't make.")
        print("Deleted.")
    print(f"To remove the rec2notes command too, run: {PIPX_UNINSTALL}")
    return 0


def _delete(path: Path) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as e:
        raise Abort(f"could not delete {e.filename or path}: {e.strerror}") from None


def _remove_if_empty(folder: Path | None) -> None:
    if folder is None:
        return
    try:
        folder.rmdir()
    except OSError:  # not empty, or already gone
        pass
