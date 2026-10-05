"""Japanese/English runtime for Python apps and tools. Standard library only.

Copy this file into the app (e.g. `myapp/i18n.py`) and keep the catalogs in a
`locales/` directory beside the code, one JSON file per locale:

    locales/en.json   {"actions": {"save": "Save"}, "files": {"processed_one": "...", ...}}
    locales/ja.json   {"actions": {"save": "保存"}, ...}

    from myapp.i18n import Translator

    tr = Translator.from_dir(Path(__file__).parent / "locales", locale=args.lang)
    print(tr.t("actions.save"))
    print(tr.t("files.processed", count=3))
    print(tr.t("status.error", message=str(exc)))

`scripts/i18n_check.py` is what keeps the catalogs in step (same keys, same
placeholders, no empty strings); this module assumes that check passes and
degrades visibly rather than crashing when it does not: a missing key falls
back to the fallback locale, then to the key itself, so the gap shows on
screen instead of raising in front of a user.

Placeholders are `{name}`. Substitution is a plain regex replacement, never
`str.format`, so a message containing an unmatched brace cannot raise and a
translator cannot reach attributes through a format spec. The web runtime
(`runtime/web/i18n.ts`) uses the same pattern, so one catalog serves both.
"""

from __future__ import annotations

import json
import locale as _locale
import numbers
import os
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Mapping

SUPPORTED_LOCALES = ("ja", "en", "vi")
DEFAULT_LOCALE = "vi"
FALLBACK_LOCALE = "en"
ENV_VAR = "APP_LANG"

# Endonyms: each language named in itself, identical in every catalog, so they
# are code constants rather than catalog entries a translator could "translate".
LANGUAGE_NAMES = {"ja": "日本語", "en": "English", "vi": "Tiếng Việt"}

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Which `<name>.json` files in a catalog directory are catalogs: the language
# code alone, because that is the key detect_locale selects by (a ja-JP.json
# would load and never be chosen). Must match LOCALE_FILE in
# scripts/i18n_check.py, which rejects any other JSON file there.
LOCALE_FILE = re.compile(r"^[a-z]{2,3}$")

# `C` and `POSIX` ask for untranslated output (scripts set LC_ALL=C to get stable
# text to parse), and the language these catalogs are written from is English.
_UNTRANSLATED = frozenset({"c", "posix"})

# Locales whose plural rules have a separate "one" form. Japanese has a single
# form ("other") whatever the count; English uses "one" for 1 and -1 (CLDR looks
# at the absolute value), which is what Intl.PluralRules gives the web runtime.
# Extend this, or plural_category, when adding a locale with different rules.
ONE_FORM_LOCALES = frozenset({"en"})

# Windows reports its locale by English name ("Japanese_Japan", "English_United
# States"), not by a BCP 47 tag, so those names are mapped explicitly.
_WINDOWS_NAMES = {"japanese": "ja", "english": "en", "vietnamese": "vi"}

_MONTHS_EN = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def normalize_locale(tag: str | None) -> str | None:
    """'ja_JP.UTF-8' -> 'ja', 'en-US' -> 'en', 'Japanese_Japan' -> 'ja', '' -> None."""
    if not tag or not tag.strip():
        return None
    base = re.split(r"[-_.@]", tag.strip(), maxsplit=1)[0].lower()
    return _WINDOWS_NAMES.get(base, base) or None


def _os_locale() -> str | None:
    try:
        return _locale.getlocale()[0]
    except (ValueError, TypeError):
        return None


def detect_locale(
    explicit: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    os_locale: str | None = None,
    supported: Iterable[str] = SUPPORTED_LOCALES,
    default: str = DEFAULT_LOCALE,
) -> str:
    """Pick the locale, in order:

    1. an explicit choice (a `--lang` flag, a saved setting), then `$APP_LANG` —
       each skipped if unset or unsupported;
    2. the POSIX variables, as POSIX reads them: the first of `$LC_ALL`,
       `$LC_MESSAGES`, `$LANG` that is set decides alone. `C` or `POSIX` there
       means untranslated, i.e. English; a language this app lacks means
       `default`, not a lower-priority variable (`LC_ALL=C LANG=ja_JP.UTF-8` is
       English, as it is for every gettext program);
    3. only when none of them is set, the OS locale (the usual source on
       Windows). Pass `os_locale=""` to skip it.

    If nothing decides, `default` is returned.
    """
    env = os.environ if env is None else env
    supported = tuple(supported)
    for candidate in (explicit, env.get(ENV_VAR)):
        found = normalize_locale(candidate)
        if found in supported:
            return found
    posix = next((v for v in (env.get("LC_ALL"), env.get("LC_MESSAGES"), env.get("LANG")) if v and v.strip()), None)
    if posix is not None:
        found = normalize_locale(posix)
        if found in _UNTRANSLATED:
            found = FALLBACK_LOCALE
        return found if found in supported else default
    found = normalize_locale(_os_locale() if os_locale is None else os_locale)
    return found if found in supported else default


def plural_category(locale: str, count: int | float | Decimal) -> str:
    """The CLDR plural category a count selects: 'one' or 'other'."""
    # A NaN is never "one" (Intl.PluralRules says "other"), and abs() of a
    # signalling Decimal NaN raises instead of answering.
    if isinstance(count, Decimal) and count.is_nan():
        return "other"
    return "one" if locale in ONE_FORM_LOCALES and abs(count) == 1 else "other"


def format_message(template: str, params: Mapping[str, object]) -> str:
    """Replace each `{name}` with params[name]; an unknown name is left as written."""
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(params[name]) if name in params else match.group(0)

    return PLACEHOLDER.sub(substitute, template)


def flatten(messages: Mapping[str, object], prefix: str = "") -> dict[str, str]:
    """{"a": {"b": "x"}} -> {"a.b": "x"}. Non-string leaves are ignored here; the checker rejects them."""
    flat: dict[str, str] = {}
    for key, value in messages.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(flatten(value, f"{path}."))
        elif isinstance(value, str):
            flat[path] = value
    return flat


def format_date(value: date, locale: str, style: str = "long") -> str:
    """A date as each audience writes it, independent of the process's C locale.

    long:  ja 2026年9月25日   en September 25, 2026   vi 25 tháng 9 năm 2026
    short: ja 2026/09/25      en 2026-09-25 (ISO: 09/25 vs 25/09 is ambiguous across US and EU readers)
           vi 25/09/2026      (day first, as Vietnamese readers write it)
    """
    if style not in ("long", "short"):
        raise ValueError(f"unknown date style {style!r}; use 'long' or 'short'")
    if locale == "ja":
        if style == "long":
            return f"{value.year}年{value.month}月{value.day}日"
        return f"{value.year:04d}/{value.month:02d}/{value.day:02d}"
    if locale == "vi":
        if style == "long":
            return f"{value.day} tháng {value.month} năm {value.year}"
        return f"{value.day:02d}/{value.month:02d}/{value.year:04d}"
    if style == "long":
        return f"{_MONTHS_EN[value.month - 1]} {value.day}, {value.year}"
    return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"


class Translator:
    """Message lookup for one active locale, with a fallback locale behind it."""

    def __init__(
        self,
        catalogs: Mapping[str, Mapping[str, object]],
        locale: str | None = None,
        *,
        fallback: str = FALLBACK_LOCALE,
        default: str = DEFAULT_LOCALE,
    ) -> None:
        if not catalogs:
            raise ValueError("no catalogs given")
        self._catalogs = {name: flatten(messages) for name, messages in catalogs.items()}
        self.fallback = fallback
        self.locale = detect_locale(locale, supported=self._catalogs, default=default)

    @classmethod
    def from_dir(cls, directory: str | os.PathLike[str], locale: str | None = None, **options) -> "Translator":
        """Load every `<locale>.json` in `directory` (UTF-8); other JSON files are not catalogs."""
        catalogs = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(Path(directory).glob("*.json"))
            if LOCALE_FILE.match(path.stem)
        }
        if not catalogs:
            raise FileNotFoundError(f"no <locale>.json catalogs in {directory}")
        return cls(catalogs, locale, **options)

    @property
    def locales(self) -> tuple[str, ...]:
        return tuple(self._catalogs)

    def set_locale(self, locale: str) -> None:
        found = normalize_locale(locale)
        if found not in self._catalogs:
            raise ValueError(f"unsupported locale {locale!r}; available: {', '.join(self._catalogs)}")
        self.locale = found

    def _lookup(self, key: str) -> str | None:
        for name in (self.locale, self.fallback):
            message = self._catalogs.get(name, {}).get(key)
            if message is not None:
                return message
        return None

    def t(self, key: str, count: int | float | Decimal | None = None, **params: object) -> str:
        """The message for `key` in the active locale, with `{placeholders}` filled.

        With `count` (any real number, `Decimal` included; not a string or a
        bool), `key_one` / `key_other` is chosen by the plural rule of the
        locale whose catalog supplies the message — so an English fallback
        says "1 file", not "1 files" — and `{count}` is available to the
        message.
        """
        message = None
        if count is not None:
            # Decimal is not registered as numbers.Real, but quantities from
            # financial or database code arrive as one, and abs() works on it.
            if isinstance(count, bool) or not isinstance(count, (numbers.Real, Decimal)):
                raise TypeError(f"count must be a number, not {type(count).__name__}")
            params.setdefault("count", count)
            for source in dict.fromkeys((self.locale, self.fallback)):
                catalog = self._catalogs.get(source, {})
                category = plural_category(source, count)
                message = catalog.get(f"{key}_{category}", catalog.get(f"{key}_other"))
                if message is not None:
                    break
        if message is None:
            message = self._lookup(key)
        return format_message(key if message is None else message, params)
