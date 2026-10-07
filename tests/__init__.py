"""The whole suite runs in an empty home: a test outside `helpers.Sandbox` finds no settings, never the user's."""
import atexit
import os
import shutil
import tempfile

_HOME = tempfile.mkdtemp(prefix="rec2notes-tests-home-")
atexit.register(shutil.rmtree, _HOME, ignore_errors=True)
for _var in ("HOME", "USERPROFILE", "APPDATA"):  # APPDATA: the pointer's folder on Windows
    os.environ[_var] = _HOME
os.environ["XDG_CONFIG_HOME"] = os.path.join(_HOME, ".config")
for _var in [v for v in os.environ if v.startswith("REC2NOTES_")]:
    del os.environ[_var]
