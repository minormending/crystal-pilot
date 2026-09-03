"""The web UI's page, which nothing checked.

`pilot/web/index.html` is 478 lines of markup, CSS and JavaScript served to a
phone, and it had no checks at all -- while the mobile app's equivalent has
four. It is also the surface a person actually drives, so a selector that
resolves to nothing or an endpoint that no longer exists shows up as a control
that silently does not work.

None of this needs a browser. What it needs is to read the page and the server
and see whether they still agree with each other.
"""
import re
from pathlib import Path

from ..harness import test

PAGE = Path(__file__).resolve().parents[2] / "pilot" / "web" / "index.html"
WEBUI = Path(__file__).resolve().parents[2] / "pilot" / "webui.py"


def page() -> str:
    return PAGE.read_text()


@test("every element the page looks up exists in its own markup")
def _(t):
    src = page()
    ids = set(re.findall(r'id="([\w-]+)"', src))
    t.gt(len(ids), 10, "the page has a good few elements")

    # q("#foo") is the page's lookup helper.
    wanted = set(re.findall(r'q\(\s*"#([\w-]+)"\s*\)', src))
    # And one place builds the id from a list of screen names.
    for block in re.findall(r'\[([^\]]*)\]\s*\.\s*forEach\(\s*s\s*=>\s*q\("#"\+s\)',
                            src, re.DOTALL):
        wanted |= set(re.findall(r'"([\w-]+)"', block))

    t.gt(len(wanted), 5, "and the script looks a good few of them up")
    missing = sorted(w for w in wanted if w not in ids)
    t.eq(missing, [], f"these are looked up but not in the markup: {missing}")


@test("every endpoint the page calls is one the server serves")
def _(t):
    # The failure this catches is a control that looks fine and does nothing:
    # a renamed route leaves the button there, the fetch 404s, and the only
    # sign is in a console nobody has open on a phone.
    called = set(re.findall(r'["\'`]/(api/[a-z]+)', page()))
    served = set(re.findall(r'["\']/(api/[a-z]+)["\']', WEBUI.read_text()))
    t.gt(len(called), 2, "the page calls a few endpoints")
    t.gt(len(served), 2, "and the server declares a few")
    unknown = sorted(c for c in called if c not in served)
    t.eq(unknown, [], f"the page calls endpoints the server does not serve: {unknown}")


@test("the page's tags and CSS braces balance")
def _(t):
    src = page()
    # Not a parser: a count, which is enough to catch the mistake that matters
    # -- an unclosed block silently swallowing everything after it.
    for tag in ("div", "script", "style", "button", "section"):
        opens = len(re.findall(rf"<{tag}\b", src))
        closes = len(re.findall(rf"</{tag}>", src))
        t.eq(opens, closes, f"<{tag}> opens and closes match")

    style = re.search(r"<style>(.*?)</style>", src, re.DOTALL)
    t.true(style is not None, "there is a stylesheet")
    css = style.group(1)
    t.eq(css.count("{"), css.count("}"), "CSS braces balance")


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(c) for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@test("the page's text meets contrast against what it sits on")
def _(t):
    # The palette is one theme, declared once, so this is a direct read. 4.5:1
    # is WCAG AA for text; 3.0:1 is the bar for a boundary that carries meaning.
    root = re.search(r":root\s*\{(.*?)\}", page(), re.DOTALL)
    t.true(root is not None, "the palette is declared on :root")
    tokens = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,6})", root.group(1)))
    for name in ("bg", "panel", "ink", "dim", "accent", "ok", "warn", "bad", "line", "key"):
        t.true(name in tokens, f"--{name} is defined")

    # Each pair is checked at the bar for what it actually is. The first
    # version of this test measured --accent and --bad at 4.5:1 as though they
    # were text, and reported two failures; both are only ever a button's
    # background or a border, where the bars are "the label on top of it" and
    # 3.0:1 respectively. Applying a text threshold to a fill invents defects.
    text_pairs = [
        ("body text on the page", "ink", "bg", 4.5),
        ("body text on a card", "ink", "panel", 4.5),
        ("dim text on a card", "dim", "panel", 4.5),
    ]
    # Filled controls: what matters is the label, which is white.
    on_fill = [
        ("the label on a primary button", "accent", 4.5),
        ("the label on a held key", "accent", 4.5),
    ]
    # Boundaries and status dots carry meaning without carrying text.
    marks = [
        ("a card's edge against the page", "line", "bg", 1.2),
        ("an accent border against a card", "accent", "panel", 3.0),
        ("the busy dot on a card", "warn", "panel", 3.0),
        ("the ok dot on a card", "ok", "panel", 3.0),
        ("the bad dot on a card", "bad", "panel", 3.0),
        ("a key against its card", "key", "panel", 1.1),
    ]
    problems = []
    for label, fg, bg, need in text_pairs + marks:
        got = _ratio(tokens[fg], tokens[bg])
        if got < need:
            problems.append(f"{label} is {got:.2f}:1, needs {need}")
    for label, fill, need in on_fill:
        got = _ratio("#ffffff", tokens[fill])
        if got < need:
            problems.append(f"{label} is {got:.2f}:1, needs {need}")
    t.eq(problems, [], f"contrast failures: {problems}")
