#!/usr/bin/env python3
"""Check that an app's Japanese and English message catalogs are in step.

Per `deterministic_work` in agent-routing/policy.yaml, "does every key exist in
every language, with the same placeholders" is computable, so it is a script
and a CI step rather than something a reviewer is asked to eyeball. A reviewer
reading two 300-line JSON files side by side can miss a key; this cannot.

A catalog directory is any directory named `locales/` (or a link of that name,
which the runtimes follow) holding one `<language>.json` per language
(`ja.json`, `en.json`, optionally more), named by the language code alone
because that is what the runtimes select. Each file is a JSON object whose
leaves are strings, nested for grouping:

    {"actions": {"save": "保存"}, "files": {"processed_one": "...", "processed_other": "..."}}

Errors (exit 1):
  - a `locales` link leads to no directory (the runtimes would find nothing)
  - a required locale (default: ja, en) has no file
  - a JSON file in the directory is not named `<locale>.json` (the runtimes
    would skip it, so it would never be checked or used)
  - a file is not valid UTF-8 JSON, is not an object, or has a non-string leaf
  - an object names the same key twice (JSON silently keeps only the last)
  - a key segment contains "." (the runtimes look keys up by dotted path)
  - a key present in one locale is missing from another
  - a message is empty or whitespace
  - a message's {placeholders} differ between locales
  - a plural key `X_one` has no `X_other`, or the reverse

Warnings (reported, exit 0): a message identical in two locales and containing
letters — usually English pasted into ja.json to satisfy the parity check.
Brand names and acronyms ("PDF", "OK") trip it legitimately; that is why it is
not an error.

Usage:
    python3 scripts/i18n_check.py                 # every locales/ dir in the repo
    python3 scripts/i18n_check.py app/locales     # just these
    python3 scripts/i18n_check.py --require ja,en,vi

Exit status: 0 clean (or no catalogs found), 1 findings, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIRED = ("ja", "en")
CATALOG_DIR_NAME = "locales"

# Must match PLACEHOLDER in .claude/skills/bilingual/runtime/python/i18n.py and
# runtime/web/i18n.ts; tests/test_bilingual.py fails if the three drift apart.
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# A catalog is named by its language code alone (ja.json, not ja-JP.json):
# the runtimes select a locale by that code, so a regional name would pass
# here and never be chosen there.
LOCALE_FILE = re.compile(r"^[a-z]{2,3}$")
LETTER = re.compile(r"[A-Za-z]")
PLURAL_SUFFIXES = ("_one", "_other")

# Directories that hold dependencies or build output, never an app's own catalogs.
SKIP_DIRS = frozenset({
    "node_modules", "venv", "env", "dist", "build", "out", "target", "coverage",
    "site-packages", "__pycache__",
})


def skipped(name: str) -> bool:
    """Hidden, dependency and build directories never hold an app's own catalogs."""
    return name.startswith(".") or name in SKIP_DIRS


def inside_skipped(real: str, top: Path) -> bool:
    """Whether a link's target lies in a hidden, dependency or build directory.

    `app -> node_modules/pkg` is a dependency however it is reached. Only the
    part of the target below what it shares with root is inspected, so a
    repository that itself sits under a hidden directory (~/.work/repo) can
    still link to a sibling.
    """
    try:
        base = os.path.commonpath([str(top), real])
    except ValueError:  # different drives on Windows: the two share nothing
        base = Path(real).anchor
    return any(skipped(part) for part in Path(os.path.relpath(real, base)).parts)


def discover(root: Path) -> list[Path]:
    """Every `locales/` directory under root, nested ones included, skipping hidden, dependency and build directories.

    Directory links are followed, as the runtimes follow them: `app/locales ->
    ../shared` and `app -> ../shared-app` are both checked where the app would
    load them. Each real directory is walked once, which ends a link cycle, and
    each catalog is checked once, under the first path found. A `locales` entry
    is checked wherever it leads, and one whose target is missing is recorded
    for check_dir to report. Walking skips a link by its own name or by where
    its target lies (a link into `node_modules` is still a dependency), and
    does not follow one to root or above it, since that would walk everything
    around the repository.
    """
    top = Path(os.path.realpath(root))
    walked = {str(top)}
    recorded: set[str] = set()
    found: list[Path] = []

    def record(path: str) -> None:
        real = os.path.realpath(path)
        if real not in recorded:
            recorded.add(real)
            found.append(Path(path))

    if root.name == CATALOG_DIR_NAME:
        record(os.fspath(root))
    for current, dirs, files in os.walk(root, followlinks=True):
        keep = []
        for name in sorted(dirs):  # a link to a directory is listed here too
            path = os.path.join(current, name)
            real = os.path.realpath(path)
            if skipped(name):
                continue
            if name == CATALOG_DIR_NAME:
                record(path)  # wherever it leads: the app loads it from here
            if inside_skipped(real, top):
                continue  # a link into node_modules is still a dependency, not walked
            if real in walked or top.is_relative_to(real):
                continue  # walked already (a cycle, or a second path), or root itself or above it
            walked.add(real)
            keep.append(name)
        dirs[:] = keep
        if CATALOG_DIR_NAME in files and os.path.islink(os.path.join(current, CATALOG_DIR_NAME)):
            record(os.path.join(current, CATALOG_DIR_NAME))  # a link whose target is missing
    return found


class Members(list):
    """A JSON object as its members in source order, duplicates kept.

    `json.loads` keeps only the last of two members with the same name, so a
    catalog whose source visibly holds a key could still lose it. Parsing into
    this instead lets `flatten` report the duplicate before it disappears.
    """


def flatten(value: object, prefix: str, where: str, errors: list[str]) -> dict[str, str]:
    """Nested object -> {"a.b": "text"}, reporting anything the runtimes could not look up."""
    flat: dict[str, str] = {}
    if not isinstance(value, Members):
        errors.append(f"{where}: {prefix.rstrip('.') or 'top level'} must be an object, got {type(value).__name__}")
        return flat
    names = [key for key, _child in value]
    for key in sorted({key for key in names if names.count(key) > 1}):
        errors.append(f"{where}: duplicate key {prefix + key!r} — JSON keeps only the last one")
    for key, child in dict(value).items():  # last one wins, as in every JSON parser the runtimes use
        path = f"{prefix}{key}"
        if "." in key or not key:
            errors.append(f"{where}: key {path!r} — a key segment may not be empty or contain '.'")
            continue
        if isinstance(child, Members):
            flat.update(flatten(child, f"{path}.", where, errors))
        elif isinstance(child, str):
            flat[path] = child
        else:
            errors.append(f"{where}: {path!r} must be a string, got {type(child).__name__}")
    return flat


def load(path: Path, errors: list[str]) -> dict[str, str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=Members)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: not valid UTF-8 JSON ({exc})")
        return None
    return flatten(data, "", str(path), errors)


def placeholders(message: str) -> set[str]:
    return set(PLACEHOLDER.findall(message))


def check_locale(where: Path, flat: dict[str, str], catalogs: dict[str, dict[str, str]],
                 all_keys: list[str], errors: list[str]) -> None:
    """Findings inside one locale file: missing keys, empty messages, unpaired plurals."""
    for key in all_keys:
        if key not in flat:
            having = ", ".join(other for other, cat in catalogs.items() if key in cat)
            errors.append(f"{where}: missing key {key!r} (present in {having})")
    for key, message in flat.items():
        if not message.strip():
            errors.append(f"{where}: {key!r} is empty")
        for suffix, partner in (PLURAL_SUFFIXES, PLURAL_SUFFIXES[::-1]):
            if key.endswith(suffix) and key[: -len(suffix)] + partner not in flat:
                errors.append(f"{where}: plural key {key!r} has no {key[: -len(suffix)] + partner!r}")


def check_across(directory: Path, catalogs: dict[str, dict[str, str]], all_keys: list[str],
                 errors: list[str], warnings: list[str]) -> None:
    """Findings between locales: placeholder sets that differ, text left untranslated."""
    names = sorted(catalogs)
    for key in all_keys:
        present = [name for name in names if key in catalogs[name]]
        shapes = {name: placeholders(catalogs[name][key]) for name in present}
        if len({frozenset(s) for s in shapes.values()}) > 1:
            shown = "; ".join(f"{name}: {sorted(s) or 'none'}" for name, s in shapes.items())
            errors.append(f"{directory}: {key!r} has different placeholders across locales ({shown})")
        for i, first in enumerate(present):
            for second in present[i + 1:]:
                message = catalogs[first][key]
                if message == catalogs[second][key] and LETTER.search(message):
                    warnings.append(
                        f"{directory}: {key!r} is identical in {first} and {second} ({message!r}) — untranslated?"
                    )


def check_dir(directory: Path, required: tuple[str, ...]) -> tuple[list[str], list[str], int]:
    """(errors, warnings, number of keys) for one catalog directory."""
    errors: list[str] = []
    warnings: list[str] = []
    if not directory.is_dir():
        # Discovery records a `locales` link even when its target is missing.
        link = f" (a link to {os.readlink(directory)!r})" if directory.is_symlink() else ""
        errors.append(f"{directory}: not a directory{link} — the runtimes would find no catalogs here")
        return errors, warnings, 0

    files: dict[str, Path] = {}
    for path in sorted(directory.glob("*.json")):
        if LOCALE_FILE.match(path.stem):
            files[path.stem] = path
        else:
            errors.append(f"{path}: not a <language>.json name (ja.json, en.json; not ja-JP.json, which "
                          f"the runtimes cannot select) — rename it or move it out of {CATALOG_DIR_NAME}/")
    for name in required:
        if name not in files:
            errors.append(f"{directory}: missing {name}.json (required locales: {', '.join(required)})")

    catalogs: dict[str, dict[str, str]] = {}
    for name, path in files.items():
        flat = load(path, errors)
        if flat is not None:
            catalogs[name] = flat

    all_keys = sorted(set().union(*catalogs.values())) if catalogs else []
    for name, flat in catalogs.items():
        check_locale(files[name], flat, catalogs, all_keys, errors)
    check_across(directory, catalogs, all_keys, errors, warnings)
    return errors, warnings, len(all_keys)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that locale catalogs are complete and consistent.")
    parser.add_argument("dirs", nargs="*", type=Path, help="catalog directories (default: discover locales/ dirs)")
    parser.add_argument("--root", type=Path, default=ROOT, help="where to discover catalogs (default: repo root)")
    parser.add_argument("--require", default=",".join(DEFAULT_REQUIRED),
                        help="comma-separated locales every catalog must have (default: ja,en)")
    args = parser.parse_args(argv)

    required = tuple(name.strip() for name in args.require.split(",") if name.strip())
    if not required:
        parser.error("--require needs at least one locale")
    for name in required:
        if not LOCALE_FILE.match(name):
            parser.error(f"--require: {name!r} is not a language code the runtimes can select (ja, en, vi)")
    for directory in args.dirs:
        if not directory.is_dir():
            parser.error(f"not a directory: {directory}")
    if not args.dirs and not args.root.is_dir():
        # Discovering nothing under a root that is not there would report
        # "nothing to check" and pass: a typo would switch the gate off.
        parser.error(f"--root is not a directory: {args.root}")

    directories = args.dirs or discover(args.root)
    if not directories:
        print(f"i18n: no {CATALOG_DIR_NAME}/ directories under {args.root} — nothing to check")
        return 0

    total_errors: list[str] = []
    total_warnings: list[str] = []
    total_keys = 0
    for directory in directories:
        errors, warnings, keys = check_dir(directory, required)
        total_errors += errors
        total_warnings += warnings
        total_keys += keys

    for line in total_errors:
        print(f"error: {line}")
    for line in total_warnings:
        print(f"warning: {line}")
    print(
        f"i18n: {len(directories)} catalog dir(s), {total_keys} key(s), "
        f"{len(total_errors)} error(s), {len(total_warnings)} warning(s)"
    )
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
