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
