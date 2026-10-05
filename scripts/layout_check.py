#!/usr/bin/env python3
"""Check that a repository's root holds only what a user needs to run the app.

Per `deterministic_work` in agent-routing/policy.yaml, "is there a test file in
the root, is this root .py a module or an entry point" is computable, so it is
a script and a CI step rather than a convention agents are trusted to follow.
A root with forty .py files, half of them tests, leaves the user unable to tell
which file starts the app; that is the failure this gate exists to prevent.
See .claude/skills/repo-layout/SKILL.md for the layout it enforces.

Only the top level of the repository is inspected; everything below a
directory is that directory's business. Hidden files are inspected like any
other (`.helper.py` is still a root module); a hidden directory is tool state
(.git, .venv, .tox) and only fails when it is named like a test directory
(`.test_support/`). Links are classified by what they
lead to. Test names are matched by word (split on _ - . and CamelCase), so a
name glued together without a boundary (`runtests.py`) is not caught.

Errors (exit 1):
  - a test file in the root: test_*.py, *_test.py, conftest.py, or any .py
    whose name has a `test`/`tests` word (run_tests_sample.py), or a
    JavaScript/TypeScript file named the same way or as a spec (x.test.mjs,
    test-x.js, x_test.js, test.js, app.spec.ts) -> tests/
  - a test directory in the root other than tests/ (test_support/, testdata/,
    or a spec/ or specs/ that holds test code)
    -> under tests/
  - a root .py that is neither an entry point nor a tool file: a library
    module, not something a user runs -> into the app's package. An entry
    point has a top-level `if __name__ == "__main__":` guard, or declares
    itself with `# layout: entry-point` in its first 10 lines (a Streamlit
    app, run by `streamlit run`, has no guard and uses the marker). Both are
    read exactly; nothing is guessed from what the code does, because no
    static reading of Python tells an app from a helper reliably. setup.py
    and noxfile.py are tool files and exempt
  - a root .py that cannot be read or parsed: a broken link, unreadable,
    undecodable in its declared encoding, or a syntax error
  - more Python entry points (.py/.pyw) in the root than --max-root-scripts
    (default 3; .bat/.sh shortcuts are not counted): the user still
    cannot tell which one to run -> keep the launchers, move the rest
  - a name given to --allow-root-module that is not a library module in the
    root (missing, or an entry point): an exemption must not outlive its file

--allow-root-module NAME (repeatable) exempts one named root module from the
library-module rule, for a repository whose packaging needs it beside the
entry point (a PyInstaller build that compiles the root siblings). It is a
declared list of exact file names (letter case included): any module not on it still fails, and the repository's
CLAUDE.md records why the list exists.

Usage:
    python3 scripts/layout_check.py                  # this repository
    python3 scripts/layout_check.py path/to/repo
    python3 scripts/layout_check.py --max-root-scripts 2
    python3 scripts/layout_check.py --allow-root-module helpers.py

Exit status: 0 clean, 1 findings, 2 bad arguments.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_ROOT_SCRIPTS = 3
TEST_DIR = "tests"
# .pyw is a Windows GUI script (pythonw), as runnable from the root as .py.
PYTHON_SUFFIXES = frozenset({".py", ".pyw"})
# JavaScript/TypeScript files. Tests live in tests/ whatever the language, so a
# root JS/TS file named like a test fails; any other JS/TS file is not checked.
JS_SUFFIXES = frozenset({".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx"})
# Jest/Vitest's `spec` word (app.spec.ts, CalculatorSpec.js); `test` as a word
# is covered by is_test_name, which with node --test's patterns (test.js,
# test-x.js, x-test.js, x_test.js, x.test.js) splits like the Python names.
JS_TEST_WORDS = frozenset({"spec", "specs"})
# RSpec/Jasmine keep tests in a root spec/ or specs/, but GitHub Spec Kit keeps
# specification documents in specs/; such a directory is a test directory only
# when it holds a code file named like a test (foo_spec.rb, app.spec.ts).
SPEC_DIRS = frozenset({"spec", "specs"})
SPEC_CODE_SUFFIXES = PYTHON_SUFFIXES | JS_SUFFIXES | {".rb"}
# Python tooling reads these from the root by name and they carry no main guard.
TOOL_ENTRY_POINTS = frozenset({"setup.py", "noxfile.py"})
# `# layout: entry-point` in the first lines declares a root launcher outright.
ENTRY_POINT_MARKER = "# layout: entry-point"
ENTRY_POINT_MARKER_LINES = 10
TEST_WORDS = frozenset({"test", "tests", "testing", "testdata"})
WORD_SPLIT = re.compile(r"[_\-.]+")
# camelCase -> camel_Case, and an acronym before a word: APITest -> API_Test.
CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def name_words(stem: str) -> list[str]:
    """Lower-cased words of a name, split on _ - . and CamelCase: APITest -> api, test."""
    return [word.lower() for word in WORD_SPLIT.split(CAMEL_BOUNDARY.sub("_", stem))]


def is_test_name(stem: str) -> bool:
    """test_x, x_test, run_tests_sample, TestRunner, conftest — but not latest or attest."""
    if stem.lower() == "conftest":
        return True
    return any(word in TEST_WORDS for word in name_words(stem))


def is_js_test_name(stem: str) -> bool:
    """x.test.mjs, test-x.js, app.spec.ts, CalculatorSpec.js — but not inspect.js or special.ts."""
    return is_test_name(stem) or any(word in JS_TEST_WORDS for word in name_words(stem))


def has_main_guard(tree: ast.Module) -> bool:
    """Whether the module has a top-level `if __name__ == "__main__":`."""
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
            continue
        sides = [test.left, test.comparators[0]]
        names = [side for side in sides if isinstance(side, ast.Name) and side.id == "__name__"]
        mains = [side for side in sides if isinstance(side, ast.Constant) and side.value == "__main__"]
        if names and mains:
            return True
    return False


def imports_streamlit(tree: ast.Module) -> bool:
    """Whether the module imports streamlit at the top level.

    Used only to word the error: a Streamlit app (`streamlit run app.py`) has
    no main guard, so it is declared with the marker. Classification never
    guesses from this, because no static reading of Python can tell an app
    from a helper reliably.
    """
    for node in tree.body:
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "streamlit" for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == "streamlit":
            return True
    return False


def declares_entry_point(source: bytes) -> bool:
    """Whether one of the first lines carries the explicit `# layout: entry-point` marker.

    It declares a launcher that has no main guard: a Streamlit app started by
    `streamlit run`, a script a scheduler runs. The author states it and the
    check takes it at its word, so it is for launchers only.
    """
    # Comment tokens, not text lines: the same words inside a docstring or any
    # other string literal are not a declaration.
    try:
        for token in tokenize.tokenize(io.BytesIO(source).readline):
            if token.start[0] > ENTRY_POINT_MARKER_LINES:
                break
            if token.type == tokenize.COMMENT and token.string.strip().lower() == ENTRY_POINT_MARKER:
                return True
    except (tokenize.TokenError, SyntaxError):
        return False
    return False


def holds_test_code(directory: Path) -> bool:
    """Whether a file below the directory is code named like a test, not a document."""
    return any(path.suffix.lower() in SPEC_CODE_SUFFIXES and is_js_test_name(path.stem) and path.is_file()
               for path in directory.rglob("*"))


def check(root: Path, max_root_scripts: int, allowed_modules: frozenset[str] = frozenset()) -> list[str]:
    errors: list[str] = []
    entry_points: list[str] = []
    # Exact names, as iterdir() returns them: case-folding let `helper.py`
    # exempt a different file `Helper.py` on a case-sensitive file system.
    unused_allowances = set(allowed_modules)
    for path in sorted(root.iterdir(), key=lambda p: p.name):
        name = path.name
        if path.is_dir():
            # A hidden directory is tool state (.git, .venv, .tox, .pytest_cache)
            # and is skipped, unless it is named like a test directory.
            if name != TEST_DIR and (is_test_name(name) or name.lower() in SPEC_DIRS and holds_test_code(path)):
                errors.append(f"{name}/: test directory in the root -> move it under {TEST_DIR}/")
            continue
        if path.suffix.lower() in JS_SUFFIXES and is_js_test_name(path.stem):
            errors.append(f"{name}: test file in the root -> move it to {TEST_DIR}/")
            continue
        # Case-folded: Windows runs helper.PY as readily as helper.py.
        if path.suffix.lower() not in PYTHON_SUFFIXES:
            continue
        if is_test_name(path.stem):
            errors.append(f"{name}: test file in the root -> move it to {TEST_DIR}/")
            continue
        if name.lower() in TOOL_ENTRY_POINTS:
            continue
        try:
            # Bytes, not text: the parser then honours a UTF-8 BOM and a coding
            # cookie exactly as the interpreter does when it runs the file.
            source = path.read_bytes()
            tree = ast.parse(source, filename=name)
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as exc:
            errors.append(f"{name}: cannot be read or parsed ({exc.__class__.__name__}), so it cannot be classified")
            continue
        if not (declares_entry_point(source) or has_main_guard(tree)):
            if name in unused_allowances:
                unused_allowances.discard(name)
                continue
            if imports_streamlit(tree):
                hint = " -> a Streamlit app? declare it with `# layout: entry-point` in its first lines;" \
                       " otherwise move it into the app's package"
            else:
                hint = " -> move it into the app's package, or declare a real launcher `# layout: entry-point`"
            errors.append(f"{name}: library module in the root (no `if __name__ == \"__main__\":`, no marker){hint}")
            continue
        entry_points.append(name)
    for allowed in sorted(unused_allowances):
        errors.append(f"{allowed}: given to --allow-root-module but not a library module in the root"
                      " (missing, or an entry point) -> remove it from the list")
    if len(entry_points) > max_root_scripts:
        errors.append(
            f"{len(entry_points)} entry points in the root ({', '.join(entry_points)}),"
            f" limit {max_root_scripts} -> keep the launchers users run, move the rest into the package"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("root", nargs="?", default=str(ROOT), help="repository root (default: this repository)")
    parser.add_argument("--max-root-scripts", type=int, default=DEFAULT_MAX_ROOT_SCRIPTS,
                        help=f"root entry points allowed (default {DEFAULT_MAX_ROOT_SCRIPTS})")
    parser.add_argument("--allow-root-module", action="append", default=[], metavar="NAME",
                        help="a root library module the packaging needs there (repeatable; exact file name)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    root = Path(args.root)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    if args.max_root_scripts < 0:
        print("error: --max-root-scripts must be >= 0", file=sys.stderr)
        return 2
    for name in args.allow_root_module:
        if Path(name).name != name or Path(name).suffix.lower() not in PYTHON_SUFFIXES:
            print(f"error: --allow-root-module takes a root .py file name, not {name!r}", file=sys.stderr)
            return 2
    errors = check(root, args.max_root_scripts, frozenset(args.allow_root_module))
    # A root file name the console encoding cannot show (a Japanese name under
    # cp1252) must still be reported, escaped, never crash the gate.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    for error in errors:
        print(f"ERROR {error}")
    print(f"layout_check: {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
