"""Drawing the pilot's own menus onto the emulator screen.

The Game Boy screen is 160x144, and PyBoy's `screen.ndarray` is a live view of
the very buffer the SDL window presents from. So an overlay is: draw into that
buffer, then ask the window to present -- no ticking, so the game stays frozen
underneath and nothing we draw reaches the game's own state.

Everything is laid out for 160x144. That is genuinely tight: the game's own font
fits about 18 characters across, and so does this.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFont

GB_W, GB_H = 160, 144

# A Game Boy-ish palette, so the overlay does not look pasted on from elsewhere.
INK = (24, 24, 32)
PAPER = (248, 248, 240)
DIM = (120, 120, 132)
ACCENT = (48, 96, 200)
SHADOW = (0, 0, 0, 110)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:            # very old Pillow: fixed-size bitmap font
        return ImageFont.load_default()


class Overlay:
    """Draws panels over the current frame and presents them."""

    def __init__(self, pyboy):
        self.pyboy = pyboy
        self.font = _font(9)
        self.small = _font(8)
        self._frozen: np.ndarray | None = None

    # --- frame handling ----------------------------------------------------
    def freeze(self) -> None:
        """Keep a copy of the current frame to draw on top of."""
        self._frozen = self.pyboy.screen.ndarray.copy()

    def thaw(self) -> None:
        self._frozen = None

    def _base(self) -> Image.Image:
        src = self._frozen if self._frozen is not None else self.pyboy.screen.ndarray
        return Image.fromarray(src[:, :, :3], "RGB").convert("RGBA")

    def present(self, img: Image.Image) -> None:
        """Push an image to the window without advancing the emulator."""
        rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
        buf = self.pyboy.screen.ndarray
        buf[:, :, 0:3] = rgb
        buf[:, :, 3] = 0xFF
        # post_tick presents from the same buffer this view points at, so the
        # frame appears without a tick and the game does not advance. With no
        # window (tests, headless runs) the buffer write is still the useful
        # part, so a missing presenter is not an error.
        pm = getattr(self.pyboy, "_plugin_manager", None)
        if pm is not None:
            try:
                pm.post_tick()
            except Exception:
                pass

    # --- pieces ------------------------------------------------------------
    def _panel(self, draw, box, title=None):
        x0, y0, x1, y1 = box
        draw.rectangle([x0 + 2, y0 + 2, x1 + 2, y1 + 2], fill=SHADOW)
        draw.rectangle([x0, y0, x1, y1], fill=PAPER, outline=INK, width=2)
        if title:
            draw.rectangle([x0, y0, x1, y0 + 13], fill=INK)
            draw.text((x0 + 4, y0 + 2), title[:24], font=self.small, fill=PAPER)

    def menu(self, title: str, items: list[str], selected: int,
             footer: str | None = None, top: int = 0) -> Image.Image:
        """A scrolling list with a cursor, sized to the Game Boy screen."""
        img = self._base()
        img.alpha_composite(Image.new("RGBA", (GB_W, GB_H), (0, 0, 0, 90)))
        d = ImageDraw.Draw(img)
        rows = 7 if footer else 8
        top = max(0, min(top, max(0, len(items) - rows)))
        visible = items[top:top + rows]
        height = 18 + len(visible) * 13 + (12 if footer else 4)
        y0 = max(4, (GB_H - height) // 2)
        self._panel(d, (6, y0, GB_W - 7, y0 + height), title)

        y = y0 + 17
        for i, label in enumerate(visible):
            idx = top + i
            chosen = idx == selected
            if chosen:
                d.rectangle([9, y - 1, GB_W - 10, y + 11], fill=ACCENT)
            d.text((14, y), label[:22], font=self.font,
                   fill=PAPER if chosen else INK)
            y += 13
        # Show that the list continues past the window.
        if top > 0:
            d.text((GB_W - 18, y0 + 16), "^", font=self.small, fill=DIM)
        if top + rows < len(items):
            d.text((GB_W - 18, y - 11), "v", font=self.small, fill=DIM)
        if footer:
            d.text((11, y0 + height - 12), footer[:26], font=self.small, fill=DIM)
        return img

    def lines(self, title: str, body: list[str],
              footer: str | None = None) -> Image.Image:
        """A read-only panel: status, results, errors."""
        img = self._base()
        img.alpha_composite(Image.new("RGBA", (GB_W, GB_H), (0, 0, 0, 110)))
        d = ImageDraw.Draw(img)
        body = body[:8]
        height = 18 + len(body) * 12 + (12 if footer else 4)
        y0 = max(4, (GB_H - height) // 2)
        self._panel(d, (6, y0, GB_W - 7, y0 + height), title)
        y = y0 + 17
        for line in body:
            d.text((11, y), line[:26], font=self.small, fill=INK)
            y += 12
        if footer:
            d.text((11, y0 + height - 12), footer[:26], font=self.small, fill=DIM)
        return img

    def working(self, title: str, status: str, spinner: int = 0) -> Image.Image:
        """A compact banner shown while a task runs."""
        img = self._base()
        img.alpha_composite(Image.new("RGBA", (GB_W, GB_H), (0, 0, 0, 130)))
        d = ImageDraw.Draw(img)
        y0 = GB_H // 2 - 22
        self._panel(d, (6, y0, GB_W - 7, y0 + 44), title)
        tick = "|/-\\"[spinner % 4]
        d.text((11, y0 + 18), f"{tick} working", font=self.small, fill=INK)
        d.text((11, y0 + 30), status[:26], font=self.small, fill=DIM)
        return img
