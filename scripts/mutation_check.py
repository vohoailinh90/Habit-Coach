#!/usr/bin/env python3
"""Prove that a test actually guards the behavior it names.

A passing test proves nothing on its own. A test that would still pass with the
behavior removed is worse than no test: it reports safety that is not there, and
it teaches the next reader to trust it.

This checks the only thing that settles the question — break the behavior, and
require the test to notice:

    for each mutation:
        run the named test on clean source, require it to PASS
        apply the mutation
        run the named test again, require it to FAIL
        restore the source

The clean-source run catches a test that can never pass — a typo'd class name,
a test deleted from under the manifest — which would otherwise exit non-zero and
be read as "the mutation was caught". It runs once, so it proves the test passed
this time in this environment; it does not prove the test is deterministic, and
a genuinely flaky test can still produce either verdict by chance.

Per `deterministic_work` in agent-routing/policy.yaml, that loop is computable:
edit a file, run a test, read an exit code. It costs a reviewer three tool calls
per mutation and scales with the number of fixes, so no fixed subagent turn
ceiling can hold it — and a model reading test output can be wrong about whether
a test failed, where a script cannot. It belongs in the repository.

This copy runs pytest node ids (tests/test_x.py::test_name); the template's original runs unittest ids.

Usage:
    python3 scripts/mutation_check.py tests/mutations/<name>.yaml
    python3 scripts/mutation_check.py --all
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
# A mutation runs a test, and a test may itself exercise this checker. Without a
# depth guard, mutating a guard that stops that path turns the nesting into an
# unbounded loop — which is what happened the first time this file's own guards
# were mutation-checked.
DEPTH_VAR = "MUTATION_CHECK_DEPTH"
MAX_DEPTH = 2
MANIFEST_DIR = ROOT / "tests" / "mutations"

SURVIVED = "SURVIVED"
CAUGHT = "caught"
BROKEN = "BROKEN"


class ManifestError(RuntimeError):
    """The manifest does not describe a mutation that can be applied."""


def shown(path: pathlib.Path) -> str:
    """Repo-relative where possible; a manifest may legitimately live outside it."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def parse_porcelain(output: str) -> set[str]:
    """Every path named by `git status --porcelain`, including both sides of a rename.

    A rename is reported as `R  old -> new`, so taking `line[3:]` whole yields
    one mangled string matching neither name — which silently defeats the
    dirty-tree guard for exactly the file it was meant to protect. Both sides
    are returned, since a manifest may name either.
    """
    paths: set[str] = set()
    for line in output.splitlines():
        if not line.strip():
            continue
        entry = line[3:].strip()
        # Porcelain quotes a path containing spaces or non-ASCII.
        sides = entry.split(" -> ") if " -> " in entry else [entry]
        paths.update(side.strip().strip('"') for side in sides if side.strip())
    return paths


def dirty_files() -> set[str]:
    """Paths git reports as modified, staged or untracked."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return parse_porcelain(result.stdout)


def load_manifest(path: pathlib.Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("mutations"), list):
        raise ManifestError(f"{path}: expected a mapping with a 'mutations' list")
    for index, entry in enumerate(data["mutations"]):
        missing = {"label", "find", "replace", "test"} - set(entry or {})
        if missing:
            raise ManifestError(f"{path}: mutation {index} is missing {sorted(missing)}")
    return data


def resolve(entry: dict, manifest: dict) -> pathlib.Path:
    return ROOT / entry.get("file", manifest.get("target", ""))


def mutate(source: str, entry: dict, where: pathlib.Path) -> str:
    """Apply one mutation, refusing anything that does not land exactly once.

    A `find` that matches zero times is a manifest that has drifted from the
    code; one that matches twice mutates an unintended site. Either way the
    result would be reported as a verdict about a change that was never made,
    which is the failure this script exists to prevent.
    """
    occurrences = source.count(entry["find"])
    if occurrences != 1:
        raise ManifestError(
            f"{shown(where)}: {entry['label']!r} matched {occurrences} times, "
            "expected exactly 1 — the manifest has drifted from the source"
        )
    return source.replace(entry["find"], entry["replace"], 1)


def run_test(target: str) -> bool:
    """True when the test failed, which is what a mutation must cause."""
    environment = dict(os.environ)
    environment[DEPTH_VAR] = str(depth() + 1)
    # A same-size edit (all -> any, max -> min) made within a second of the clean compile keeps the old
    # source mtime and size, so Python would reuse the stale .pyc and the mutation would silently never run.
    with tempfile.TemporaryDirectory() as pycache:
        environment["PYTHONPYCACHEPREFIX"] = pycache
        return _run_pytest(target, environment)


def _run_pytest(target: str, environment: dict) -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", target],
        cwd=ROOT, capture_output=True, text=True, check=False, env=environment,
        timeout=300,
    )
    return result.returncode != 0


def depth() -> int:
    try:
        return int(os.environ.get(DEPTH_VAR, "0"))
    except ValueError:
        return 0


def inside_repo(path: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
    except ValueError:
        return False
    return True


def check(manifest_path: pathlib.Path, allow_outside: bool = False) -> list[tuple[str, str, str]]:
    """Apply each mutation and report whether its named test noticed.

    `allow_outside` exists for this script's own tests, which mutate a scratch
    file in a temporary directory. It is never set from the command line: a
    manifest is repository content, and a `file:` that escapes the repository
    would rewrite paths the dirty-tree guard cannot see, since git reports
    nothing about a file it does not track.
    """
    manifest = load_manifest(manifest_path)
    results: list[tuple[str, str, str]] = []

    for entry in manifest["mutations"]:
        where = resolve(entry, manifest)
        if not allow_outside and not inside_repo(where):
            results.append((BROKEN, entry["label"], f"outside the repository: {shown(where)}"))
            continue
        if not where.is_file():
            results.append((BROKEN, entry["label"], f"no such file: {shown(where)}"))
            continue
        if run_test(entry["test"]):
            # A missing or already-failing test exits non-zero for reasons that
            # have nothing to do with the mutation, and "non-zero" is exactly
            # what this script reads as caught. Without this baseline, a typo in
            # a class name produces a green verdict for a behavior nothing
            # guards — which is the failure the whole script exists to prevent.
            results.append((BROKEN, entry["label"],
                            f"{entry['test']} does not pass on unmutated source "
                            "(missing, or already failing)"))
            continue
        original = where.read_text(encoding="utf-8")
        try:
            mutated = mutate(original, entry, where)
        except ManifestError as exc:
            # One drifted entry must not hide the verdicts of the others: report
            # it in place and keep going, then fail the run at the end.
            results.append((BROKEN, entry["label"], str(exc)))
            continue
        try:
            where.write_text(mutated, encoding="utf-8")
            caught = run_test(entry["test"])
        finally:
            # Restore before anything else can fail: a crash mid-run must never
            # leave a mutated source behind. If the restore write itself fails
            # there is nothing left to try, so name the file unmistakably —
            # an exception whose message does not say which file is corrupted
            # leaves the operator worse off than no message.
            try:
                where.write_text(original, encoding="utf-8")
            except OSError as exc:
                print(f"  !! NOT RESTORED: {shown(where)} is still mutated ({exc}). "
                      "Restore it from git before running anything else.", file=sys.stderr)
                raise
        results.append((CAUGHT if caught else SURVIVED, entry["label"], entry["test"]))

    return results


def manifests(selected: str | None) -> list[pathlib.Path]:
    if selected:
        return [pathlib.Path(selected).resolve()]
    return sorted(MANIFEST_DIR.glob("*.yaml"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", nargs="?", help="manifest to run; omit with --all")
    parser.add_argument("--all", action="store_true", help="run every manifest")
    parser.add_argument(
        "--allow-dirty", action="store_true",
        help="run even with uncommitted changes to the files being mutated",
    )
    args = parser.parse_args(argv)

    if not args.manifest and not args.all:
        parser.error("give a manifest path or --all")

    if depth() >= MAX_DEPTH:
        print(f"  refusing to run at nesting depth {depth()}: a test under mutation "
              "invoked this checker again. Narrow that test's target.")
        return 2

    survivors = 0
    broken = 0
    for path in manifests(args.manifest):
        try:
            manifest = load_manifest(path)
        except (ManifestError, yaml.YAMLError) as exc:
            print(f"  ERROR  {exc}")
            return 2

        targets = {entry.get("file", manifest.get("target", "")) for entry in manifest["mutations"]}
        conflicts = targets & dirty_files()
        if conflicts and not args.allow_dirty:
            print(f"  ERROR  uncommitted changes in {sorted(conflicts)}.")
            print("         This rewrites those files; commit or stash first, "
                  "or pass --allow-dirty if you accept the risk.")
            return 2

        print(f"\n{shown(path)}")
        for verdict, label, detail in check(path):
            shown_detail = detail if verdict == BROKEN else detail.split(".")[-1][:44]
            print(f"  {verdict:9s} {label:46s} {shown_detail}")
            survivors += verdict == SURVIVED
            broken += verdict == BROKEN

    print()
    if broken:
        print(f"{broken} manifest entry/entries could not be applied — the manifest has "
              "drifted from the source. A verdict was not produced for them.")
        return 2
    if survivors:
        print(f"{survivors} mutation(s) SURVIVED: the named test does not guard the "
              "behavior it claims to. Fix the test, not this script.")
        return 1
    print("Every mutation was caught. No test in these manifests is vacuous.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
