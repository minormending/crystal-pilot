"""Records what the pilot does, as a sped-up video.

A grind is hundreds of thousands of emulated frames -- hours of game time -- so
recording every frame would produce a video nobody will watch. Instead we sample
one frame every N, which makes the result a timelapse: `speed=30` plays back
thirty times faster than the game ran.

Sampling is also what makes recording cheap. Rendering costs about 4x, but only
sampled frames need to be rendered, so at speed 30 roughly one frame in sixty is
drawn and the pilot still runs at hundreds of times real time.

A caption strip is drawn under the picture because at 30x a bare timelapse is an
unreadable blur -- the text is what tells you the level went up, or that it
walked to a Pokemon Center.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

GB_W, GB_H = 160, 144


class Recorder:
    def __init__(self, path: str | Path, speed: float = 30.0, fps: int = 30,
                 scale: int = 3, hud: bool = True, title: str = "",
                 caption_fn=None, crf: int = 23, log=print):
        self.path = Path(path)
        self.fps = max(1, int(fps))
        self.speed = max(1.0, float(speed))
        # One captured frame every `every` emulated frames. The game runs at
        # 60 fps, so playing back `every` frames per output frame at `fps`
        # gives speed = every * fps / 60.
        self.every = max(1, round(self.speed * 60.0 / self.fps))
        self.actual_speed = self.every * self.fps / 60.0
        self.scale = max(1, int(scale))
        self.hud = hud
        self.crf = int(crf)
        self.title = title
        self.caption_fn = caption_fn
        self.log = log

        self.width = GB_W * self.scale
        self.strip = 0
        self._font = None
        if self.hud:
            size = max(10, 5 * self.scale)
            try:
                self._font = ImageFont.load_default(size=size)
            except TypeError:              # very old Pillow
                self._font = ImageFont.load_default()
                self.hud = self._font is not None
            self.strip = 2 * (size + 5) + 6
        self.height = GB_H * self.scale + self.strip
        if self.height % 2:                # libx264 + yuv420p needs even dims
            self.height += 1
            self.strip += 1

        self.frames = 0
        self._proc = None
        self._gif_frames = None
        self._caption_cache: tuple[str, Image.Image] | None = None
        self._closed = False
        self._open()

    # --- lifecycle ---------------------------------------------------------
    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            self.log("recording: ffmpeg not found, falling back to an animated GIF")
            self.path = self.path.with_suffix(".gif")
            self._gif_frames = []
            return
        cmd = [
            ffmpeg, "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{self.width}x{self.height}", "-r", str(self.fps),
            "-i", "-",
        ]
        if self.path.suffix.lower() == ".gif":
            cmd += ["-filter_complex", "[0:v]split[a][b];[a]palettegen[p];[b][p]paletteuse"]
        else:
            cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", str(self.crf), "-preset", "veryfast",
                    "-movflags", "+faststart"]
        cmd.append(str(self.path))
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.PIPE)

    def close(self) -> dict:
        if self._closed:
            return self.stats()
        self._closed = True
        if self._proc is not None:
            try:
                self._proc.stdin.close()
            except (BrokenPipeError, ValueError):
                pass
            err = self._proc.stderr.read().decode(errors="replace").strip()
            self._proc.wait()
            if self._proc.returncode != 0 and err:
                self.log(f"recording: ffmpeg said: {err.splitlines()[-1]}")
        elif self._gif_frames:
            head, *rest = self._gif_frames
            head.save(self.path, save_all=True, append_images=rest,
                      duration=int(1000 / self.fps), loop=0, optimize=True)
        return self.stats()

    def stats(self) -> dict:
        size = self.path.stat().st_size if self.path.exists() else 0
        return {
            "path": str(self.path),
            "frames": self.frames,
            "seconds": self.frames / self.fps,
            "speed": self.actual_speed,
            "bytes": size,
        }

    def describe(self) -> str:
        s = self.stats()
        mb = s["bytes"] / 1_048_576
        return (f"recorded {s['path']} -- {s['seconds']:.0f}s of video at "
                f"{s['speed']:.0f}x ({s['frames']:,} frames, {mb:.1f} MB)")

    # --- capture -----------------------------------------------------------
    def should_capture(self, frame_index: int) -> bool:
        return frame_index % self.every == 0

    def capture(self, pyboy) -> None:
        """Grab the current screen. Only call when the frame was rendered."""
        if self._closed:
            return
        rgba = pyboy.screen.ndarray                 # (144, 160, 4)
        img = Image.fromarray(rgba[:, :, :3], "RGB")
        if self.scale != 1:
            img = img.resize((self.width, GB_H * self.scale), Image.NEAREST)
        if self.strip:
            canvas = Image.new("RGB", (self.width, self.height), (16, 16, 20))
            canvas.paste(img, (0, 0))
            canvas.paste(self._caption(), (0, GB_H * self.scale))
            img = canvas
        self._write(img)
        self.frames += 1

    def _write(self, img: Image.Image) -> None:
        if self._proc is not None:
            try:
                self._proc.stdin.write(np.asarray(img, dtype=np.uint8).tobytes())
            except (BrokenPipeError, ValueError):
                self._closed = True
                self.log("recording: encoder closed early; stopping capture")
        else:
            self._gif_frames.append(img.copy())

    def _caption(self) -> Image.Image:
        text = ""
        if self.caption_fn is not None:
            try:
                text = self.caption_fn() or ""
            except Exception:  # noqa: BLE001 -- a caption failure must not stop the recording
                text = ""
        key = f"{self.title}\n{text}"
        # The caption changes rarely (a level-up, a new battle), so rendering it
        # once per distinct string keeps compositing off the hot path.
        if self._caption_cache and self._caption_cache[0] == key:
            return self._caption_cache[1]
        strip = Image.new("RGB", (self.width, self.strip), (16, 16, 20))
        d = ImageDraw.Draw(strip)
        pad = 4
        d.text((pad, 2), self.title, font=self._font, fill=(150, 200, 255))
        d.text((pad, 2 + (self.strip - 6) // 2), text, font=self._font,
               fill=(235, 235, 235))
        self._caption_cache = (key, strip)
        return strip
