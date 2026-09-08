"""The documentation's checkable claims.

`tools/docs-check` notices when described *code* changes; it cannot notice when
a number in prose stops being true. Both have happened here: the README claimed
"16 passed, 67 skipped" long after it was 20 and 88, and a test count sat at 84
while the suite grew past 140. A figure nobody can verify is worse than no
figure, because it reads as precision.

Only the mechanically checkable claims are here. Whether a paragraph is still a
good explanation is a reading job, and this does not pretend otherwise.
"""
import re
from pathlib import Path

from ..harness import test

ROOT = Path(__file__).resolve().parents[2]
DOCS = [ROOT / "README.md", ROOT / "docs" / "CODE.md"]


def documents():
    return [(p, p.read_text()) for p in DOCS if p.exists()]


@test("every file the documentation names actually exists")
def _(t):
    missing = []
    for path, text in documents():
        for m in re.finditer(
                r"`((?:pilot|tools|tests)/[\w./-]+\.(?:py|md|html|toml|yml))`", text):
            if not (ROOT / m.group(1)).exists():
                missing.append(f"{path.name} names {m.group(1)}")
    t.eq(missing, [], f"documentation naming files that are gone: {missing}")


@test("every anchor in the documentation resolves to a heading")
def _(t):
    broken = []
    for path, text in documents():
        heads = []
        for line in text.splitlines():
            m = re.match(r"^(#{2,6})\s+(.*)$", line)
            if m:
                slug = re.sub(r"[^\w\s-]", "", m.group(2).replace("`", "").lower(),
                              flags=re.UNICODE).strip()
                heads.append(slug.replace(" ", "-"))
        for link in re.findall(r"\]\(#([^)]+)\)", text):
            if link not in heads:
                broken.append(f"{path.name} -> #{link}")
    t.eq(broken, [], f"broken anchors: {broken}")


@test("every mermaid diagram is well formed and names real modules")
def _(t):
    # A diagram is documentation that looks authoritative, so a module it names
    # after a rename is worse than a stale paragraph -- it reads as the map.
    problems = []
    for path, text in documents():
        fences = text.count("```")
        if fences % 2:
            problems.append(f"{path.name} has {fences} code fences, which is odd")
        blocks = re.findall(r"```mermaid\n(.*?)```", text, re.DOTALL)
        for block in blocks:
            if not re.search(r"\b(flowchart|graph|stateDiagram|sequenceDiagram)\b",
                             block):
                problems.append(f"{path.name} has a mermaid block with no diagram type")
            for module in re.findall(r'\["?([\w-]+\.(?:py|js))', block):
                if not list(ROOT.rglob(module)):
                    problems.append(f"{path.name} diagram names {module}, which is gone")
        t.note(f"{path.name}: {len(blocks)} diagram(s)")
    t.eq(problems, [], f"diagram problems: {problems}")


@test("the test counts the documentation quotes are the real ones")
def _(t):
    """The claim that goes stale every time the suite grows.

    Counted from the runner's own registry rather than by running it, so this
    cannot recurse.
    """
    from ..harness import _REGISTRY

    # Read at the moment this test runs, by which point discovery has imported
    # every case file -- including this one.
    total = len(_REGISTRY)
    t.note(f"the registry holds {total} tests")

    for path, text in documents():
        for m in re.finditer(r"(\d+) tests\. Most of them", text):
            t.eq(int(m.group(1)), total,
                 f"{path.name}'s headline test count")
        for m in re.finditer(r"(\d+) tests, and they need a venv", text):
            t.eq(int(m.group(1)), total, f"{path.name}'s test count")


@test("the documented ROM split adds up to the whole suite")
def _(t):
    """The other pair of numbers that goes stale, and did.

    The README quotes the runner's own no-ROM output and CODE.md states the
    same figure in prose. Both said 118 and 150 long after the suite had grown
    past them, because the headline total was tested and this split was not --
    and a transcript in a README is a claim like any other.

    Checked as arithmetic rather than against a fresh no-ROM run, which would
    mean booting the suite a second time from inside itself. Every quoted
    figure has to name the same split, and the split has to account for every
    test in the registry -- so updating one number and not the others fails
    here.
    """
    from ..harness import _REGISTRY

    total = len(_REGISTRY)
    seen: list[tuple[str, int, int]] = []
    for path, text in documents():
        # "121 passed, 155 skipped" -- the runner's own line, quoted.
        for m in re.finditer(r"(\d+) passed, (\d+) skipped", text):
            seen.append((f"{path.name} transcript", int(m.group(1)),
                         int(m.group(2))))
        # "(155 tests need a ROM built from the disassembly)"
        for m in re.finditer(r"\((\d+) tests need a ROM", text):
            seen.append((f"{path.name} skip line", total - int(m.group(1)),
                         int(m.group(1))))
        # "**121 of them need nothing but the repository**"
        for m in re.finditer(r"\*\*(\d+) of them need nothing but", text):
            seen.append((f"{path.name} prose", int(m.group(1)),
                         total - int(m.group(1))))
    t.gte(len(seen), 3, f"the documents state the split somewhere ({seen})")
    for where, no_rom, needs_rom in seen:
        t.eq(no_rom + needs_rom, total,
             f"{where}: {no_rom} + {needs_rom} should be the whole suite")
    t.eq(len({(a, b) for _w, a, b in seen}), 1,
         f"every quoted figure names the same split: {seen}")
