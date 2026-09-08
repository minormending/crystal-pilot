"""The quality gates, checked against what they claim to cover.

This repository has four: `./run-tests`, `--self-check`, `tools/docs-check` and
ruff. Each one is a promise about the whole codebase, and a gate that quietly
covers less than it says is worse than no gate, because it is read as rigour.

`ruff.toml` exists for exactly that reason -- eight `# noqa: BLE001` comments
addressed to a linter that had never been configured. Then the linter it
configured turned out to have the same shape of hole: `ruff check pilot tests`
in CI and the pre-commit hook, and `ruff check .` locally, all reported "All
checks passed" without ever opening `tools/coverage` or `tools/docs-check`.
Those two are Python with a shebang and no extension, and ruff will lint such a
file when it is named but will not *discover* it from a directory -- `ruff check
tools` says "No Python files found". Naming them in `ruff.toml` immediately
found a real error in one.

So these tests check the gates rather than the code. Nothing here needs a ROM.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..harness import test

ROOT = Path(__file__).resolve().parent.parent.parent


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(ROOT), *args],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def python_files() -> list[str]:
    """Every tracked file that is Python, by extension or by shebang.

    The shebang half is the point. An extension test is what let the two tools
    slip out of every gate at once: the pre-commit hook filtered staged files
    on `\\.py$`, so editing one ran neither the linter nor the docs check.
    """
    out = []
    for rel in _git("ls-files"):
        path = ROOT / rel
        if not path.is_file():
            continue
        if rel.endswith(".py"):
            out.append(rel)
            continue
        try:
            first = path.open("rb").readline(200).decode("utf-8", "replace")
        except OSError:
            continue
        if first.startswith("#!") and "python" in first:
            out.append(rel)
    return out


@test("ruff's own file list covers every Python file in the repository")
def _(t):
    if not shutil.which("ruff") and not (ROOT / ".venv/bin/ruff").exists():
        t.skip("ruff is not installed")
    ruff = str(ROOT / ".venv/bin/ruff")
    if not Path(ruff).exists():
        ruff = "ruff"
    # Asked of ruff rather than reasoned about: `--show-files` is the list it
    # would actually check, with this config, so this cannot agree with a
    # mistaken second model of ruff's discovery rules.
    out = subprocess.run([ruff, "check", "--show-files", "."],
                         cwd=ROOT, capture_output=True, text=True)
    checked = set()
    for line in out.stdout.splitlines():
        p = Path(line.strip())
        if p.is_absolute() and p.is_relative_to(ROOT):
            checked.add(str(p.relative_to(ROOT)))
        elif line.strip():
            checked.add(line.strip())
    ours = python_files()
    t.gt(len(ours), 30, f"the repository has Python files to check ({len(ours)})")
    t.gt(len(checked), 30, f"and ruff listed some ({len(checked)})")
    missed = sorted(set(ours) - checked)
    # The message names the fix, because it is always the same one: add the
    # file to `extend-include` in ruff.toml.
    t.eq(missed, [],
         f"ruff would not check these: {missed} -- name them in ruff.toml's "
         f"extend-include")
    # The two that were missed, named, so a regression in the walk above cannot
    # make this pass by finding nothing.
    for rel in ("tools/coverage", "tools/docs-check"):
        t.true(rel in ours, f"{rel} is recognised as Python")
        t.true(rel in checked, f"and ruff checks {rel}")


@test("CI and the pre-commit hook lint the same scope")
def _(t):
    """Two copies of a scope is how the hole appeared in the first place.

    Both say `ruff check .` now, and both are read off the files rather than
    trusted, because a scope written in two places drifts. `.` also means a new
    directory is covered the day it appears rather than the day somebody
    remembers to add it.
    """
    hook = (ROOT / ".githooks/pre-commit").read_text()
    ci = (ROOT / ".github/workflows/tests.yml").read_text()
    for where, text in (("the pre-commit hook", hook), ("CI", ci)):
        t.contains(text, "ruff check .", f"{where} lints the whole repository")
        # `ruff check pilot tests` is the narrower scope this replaced. It
        # would still pass every rule and still miss the tools.
        t.false("ruff check pilot tests" in text,
                f"{where} does not use the narrower scope")


@test("the pre-commit hook's file filter sees every kind of file it gates on")
def _(t):
    """The hook gates on staged Python files, and decides that by pattern.

    A pattern is a second model of "which files matter", so it is checked
    against the first: everything `python_files()` finds has to match, or
    editing it skips both the lint and the documentation check.
    """
    import re

    hook = (ROOT / ".githooks/pre-commit").read_text()
    m = re.search(r"grep -E '([^']+)'", hook)
    t.true(m is not None, "the hook still filters staged files with grep -E")
    pattern = m.group(1).replace("\\.", r"\.")
    rx = re.compile(pattern)
    unseen = [rel for rel in python_files() if not rx.search(rel)]
    t.eq(unseen, [],
         f"the hook's filter would skip these: {unseen} -- widen the grep")
    # And it still catches the one non-Python file it deliberately gates on.
    t.true(rx.search("run-tests"), "the shell runner is still gated too")
