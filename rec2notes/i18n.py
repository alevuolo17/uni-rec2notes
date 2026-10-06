"""The language of the tool's own screens (not of the notes: those keep their Italian markers)."""
from rec2notes import Abort
from rec2notes.messages import en, it

YES = ("y", "yes", "s", "si", "sì")  # what a yes is, in either language
LANGUAGES = {"en": "English", "it": "Italiano"}  # code → the name each language goes by in the language question
DEFAULT = "en"
_CATALOGS = {"en": en.MESSAGES, "it": it.MESSAGES}
_current = DEFAULT


def current() -> str:
    return _current


def set_language(code: str) -> None:
    global _current
    if code not in LANGUAGES:
        raise ValueError(f"unknown language {code!r}")
    _current = code


def setting_language() -> str:
    """The language of the setting (environment, else saved); English if it names none we have."""
    from . import paths  # here, not at the top: paths imports this module for its messages
    try:
        code = paths.setting_choice("language")
    except Abort:  # a broken settings.toml or pointer: the language is cosmetic, and doctor must still run
        return DEFAULT
    return code if code in LANGUAGES else DEFAULT


def load() -> None:
    set_language(setting_language())


def t(key: str, /, **fields: object) -> str:
    """The text for key in the current language, else in English, else the key itself."""
    text = _CATALOGS[_current].get(key) or _CATALOGS[DEFAULT].get(key) or key
    return text.format(**fields) if fields else text


def number(n: int) -> str:
    """A whole number with its thousands separator: 9,807 in English, 9.807 in Italian."""
    text = f"{n:,}"
    return text.replace(",", ".") if _current == "it" else text


def labels(prefix: str) -> list[str]:
    """The texts of every key starting with prefix, in the current language."""
    return [t(key) for key in _CATALOGS[DEFAULT] if key.startswith(prefix)]
