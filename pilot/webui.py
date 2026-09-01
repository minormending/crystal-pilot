"""A phone-friendly web front end for the pilot.

The Mac runs the emulator; the phone is a thin remote. That split matters: the
whole point of the pilot is running headless at tens of thousands of frames a
second, which a phone cannot do, and the browser needs no install or port.

Threading: PyBoy is not thread-safe, so the emulator is touched by exactly one
thread. HTTP handlers only read a published snapshot and push commands onto a
queue; the main loop drains that queue, runs the task, and republishes. Frames
are encoded to PNG by the same loop and handed over as bytes.

The server binds to the LAN so a phone can reach it, so every request carries a
token generated at startup. This is a convenience for a home network, not a
hardened service -- do not expose it to the internet.
"""
from __future__ import annotations

import json
import io
import queue
import secrets
import socket
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .session import Budget
from .tasks.base import TaskResult
from .wild import species_on

WEB_ROOT = Path(__file__).resolve().parent / "web"
BUTTONS = ("up", "down", "left", "right", "a", "b", "start", "select")
# A confirm or a menu step wants a short press; walking wants a long one. The
# ceiling is here so a client cannot pin a button down indefinitely -- the press
# blocks the pilot's loop while it runs.
TAP_FRAMES = 8
WALK_FRAMES = 16
MAX_PRESS_FRAMES = 40

# Tapping the screen. The overworld is drawn in 16x16 tiles, so the 160x144
# screen is 10x9 of them, and the player is always the one at (4, 4) -- verified
# on three maps at three different positions, because the camera keeps them
# centred rather than clamping at map edges.
SCREEN_TILES_X, SCREEN_TILES_Y = 10, 9
PLAYER_TILE_X, PLAYER_TILE_Y = 4, 4
# Menus are drawn on the 8x8 text grid, two rows per entry, with the first entry
# two rows below the window's top edge.
TEXT_ROWS = 18
MENU_FIRST_ROW = 2
MENU_ROW_STRIDE = 2


@dataclass
class Status:
    """What the phone is shown. Rebuilt by the main loop, read by handlers."""

    where: str = ""
    on_grass: bool = False
    party: list = field(default_factory=list)
    species: list = field(default_factory=list)
    trainers: int = 0
    balls: list = field(default_factory=list)
    busy: bool = False
    task: str = ""
    progress: str = ""
    result: dict | None = None
    frame: int = 0

    def to_json(self) -> dict:
        return {
            "where": self.where, "onGrass": self.on_grass, "party": self.party,
            "species": self.species, "trainers": self.trainers,
            "balls": self.balls, "busy": self.busy, "task": self.task,
            "progress": self.progress, "result": self.result,
            "frame": self.frame,
        }


class WebPilot:
    """Runs the emulator loop and serves the phone UI."""

    def __init__(self, pilot, source, host: str = "0.0.0.0", port: int = 8080,
                 token: str | None = None, timeout: float = 900.0,
                 allow_input: bool = True, log=print):
        self.p = pilot
        self.source = source
        self.host = host
        self.port = port
        self.token = token or secrets.token_urlsafe(9)
        self.timeout = timeout
        self.allow_input = allow_input
        self.log = log

        self._commands: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._status = Status()
        self._png = b""
        self._running = True
        self._httpd = None

    # --- published state ---------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return self._status.to_json()

    def screen_png(self) -> bytes:
        with self._lock:
            return self._png

    def submit(self, command: dict) -> None:
        self._commands.put(command)

    # --- the loop ----------------------------------------------------------
    def run(self) -> None:
        self._serve_forever_in_background()
        self._refresh_status()
        self._refresh_frame(force=True)
        url = self.url()
        self._say("")
        self._say("  Open this on your phone, on the same wifi:")
        self._say("")
        self._say(f"      {url}")
        self._say("")
        lines = qr_lines(url)
        if lines:
            self._say("  ...or point your camera at this:")
            self._say("")
            for line in lines:
                self._say("      " + line)
            self._say("")
        else:
            self._say("  (pip install qrcode for a scannable code)")
            self._say("")
        self._say("  The token in the URL is the only thing protecting this,")
        self._say("  so keep it on your own network. Ctrl-C to stop.")
        self._say("")
        ticks = 0
        try:
            while self._running:
                try:
                    cmd = self._commands.get_nowait()
                except queue.Empty:
                    cmd = None
                if cmd is not None:
                    # A command is whatever JSON a client sent. Nothing in it is
                    # worth ending the session over: an unparseable field used to
                    # raise out of here, past the loop, into the finally below --
                    # which stops the emulator and writes the save. A typo in a
                    # curl command would end a running grind, and the HTTP layer
                    # had already answered {"ok": true}.
                    try:
                        self._handle(cmd)
                    except Exception as e:  # noqa: BLE001
                        self._finish({"ok": False,
                                      "message": f"could not do that: "
                                                 f"{type(e).__name__}: {e}"})
                    continue
                self.p.session.pyboy.tick(1, True)
                ticks += 1
                # ~10 Hz is plenty for watching, and PNG encoding is not free.
                if ticks % 6 == 0:
                    self._refresh_frame()
                if ticks % 60 == 0:
                    self._refresh_status()
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _say(self, line: str) -> None:
        """Print and flush.

        stdout is block-buffered whenever it is not a terminal, so without this
        the connection URL -- the one line you actually need -- can sit in the
        buffer indefinitely.
        """
        self.log(line)
        try:
            sys.stdout.flush()
        except Exception:
            pass

    def stop(self) -> None:
        self._running = False
        if self._httpd is not None:
            threading.Thread(target=self._httpd.shutdown, daemon=True).start()
        self.log("stopping; writing the .sav")
        self.p.stop(save_sram=True)

    # --- commands ----------------------------------------------------------
    def _handle(self, cmd: dict) -> None:
        kind = cmd.get("kind")
        if kind == "input":
            self._press(cmd.get("button", ""), cmd.get("frames", TAP_FRAMES))
        elif kind == "save":
            self._run_named("save", self._do_save)
        elif kind in ("grind", "hunt", "catch", "trainers", "tap"):
            self._run_task(kind, cmd)
        else:
            # Say so rather than doing nothing: silently ignoring it would leave
            # the previous task's result on screen, which reads as a reply.
            self._finish({"ok": False, "message": f"unknown task {kind!r}"})
        self._refresh_status()
        self._refresh_frame(force=True)

    def _press(self, button: str, frames: int = TAP_FRAMES) -> None:
        """Press a button for `frames` frames.

        The length matters, and used to be fixed at six. Gen 2 turns you before
        it walks you, and six frames are spent entirely on the turn -- so the
        first press after any change of direction moved nowhere, and walking a
        tile took two presses. A walk-length press does both, and the client
        repeats while a finger stays down.
        """
        if not self.allow_input or button not in BUTTONS:
            return
        try:
            frames = int(frames)
        except (TypeError, ValueError):
            frames = TAP_FRAMES
        frames = max(1, min(MAX_PRESS_FRAMES, frames))
        self.p.session.tap(button, hold=frames, gap=2)

    # --- tapping the screen ---------------------------------------------------
    def _window_open(self) -> bool:
        """Is a menu or textbox actually on screen right now?

        wWindowStackSize is pushed and popped by the game's own window code, so
        unlike the menu cursor it cannot be left over from a menu that closed
        minutes ago. That distinction matters here: mistaking the overworld for
        a menu means sending DOWN to move a cursor that is not there, which
        walks the player instead -- into grass, and into a wild battle.
        """
        return self.p.session.rb("wWindowStackSize") > 0

    def _tap_row(self, cmd: dict) -> int | None:
        """The tapped row on the 8px text grid, for menus.

        Not derived from the 16px tile: that halves the resolution, and menus
        are laid out in text rows. Two in five of the game's menus start on an
        odd row, and on those the rounding put the cursor one entry above the
        one that was tapped, for every entry but the first.
        """
        try:
            fy = float(cmd.get("y", -1))
        except (TypeError, ValueError):
            return None
        if not (0.0 <= fy <= 1.0):
            return None
        return min(TEXT_ROWS - 1, int(fy * TEXT_ROWS))

    def _tap_target(self, cmd: dict) -> tuple[int, int] | None:
        """The tapped screen tile, from fractions of the screen's width/height.

        Fractions rather than pixels because the phone scales the picture to fit
        its own screen, and only the browser knows by how much.
        """
        try:
            fx, fy = float(cmd.get("x", -1)), float(cmd.get("y", -1))
        except (TypeError, ValueError):
            return None
        if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
            return None
        return (min(SCREEN_TILES_X - 1, int(fx * SCREEN_TILES_X)),
                min(SCREEN_TILES_Y - 1, int(fy * SCREEN_TILES_Y)))

    def _walk_to_tap(self, goal: tuple[int, int]) -> TaskResult:
        res = TaskResult()
        gx, gy = goal
        if not self.p.collision.calibrated:
            self.p.calibrate()
        before = self.p.reader.location()
        step = self.p.nav.walk_to(gx, gy)
        now = self.p.reader.location()
        res.stats = {"from": f"({before.x},{before.y})", "to": f"({now.x},{now.y})",
                     "wanted": f"({gx},{gy})"}
        if (now.x, now.y) == (gx, gy):
            res.status = "completed"
            res.message = f"walked to ({gx},{gy})"
        elif getattr(step, "battle", False) or self.p.reader.in_battle():
            res.status = "blocked"
            res.message = f"a wild battle interrupted the walk at ({now.x},{now.y})"
        else:
            res.status = "blocked"
            res.message = (f"could not reach ({gx},{gy}); stopped at "
                           f"({now.x},{now.y}). Ledges are one-way, and some "
                           f"tiles are only reachable the long way round")
        return res

    def _move_cursor_to_row(self, row: int, entries: int) -> TaskResult:
        """Step an open menu's cursor to `row`, without confirming anything.

        Deliberately never presses A. If the row arithmetic is off the cursor
        lands next to what you meant and you can see that; pressing A on the
        wrong entry could use an item or throw away a Pokemon.
        """
        res = TaskResult()
        s = self.p.session
        for _ in range(entries + 2):
            if not self._window_open():
                res.status = "blocked"
                res.message = "the menu closed"
                return res
            cur = s.rb("wMenuCursorY")
            if cur == row:
                res.status = "completed"
                res.message = f"cursor on entry {row}"
                return res
            s.tap("down" if cur < row else "up", hold=4, gap=6)
        res.status = "blocked"
        res.message = "the cursor would not settle on that entry"
        return res

    def _do_save(self):
        ok, why = self.p.settle_for_save()
        if not ok:
            return {"ok": False, "message": why}
        if self.p.save():
            return {"ok": True, "message": "saved"}
        return {"ok": False, "message": "the game did not commit the save"}

    def _run_named(self, title: str, fn):
        self._begin(title)
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "message": f"{type(e).__name__}: {e}"}
        self._finish(result)

    def _run_task(self, kind: str, cmd: dict) -> None:
        # Planning reads fields straight out of the request, so it is as capable
        # of raising on a bad one as the task itself -- and it sat outside the
        # guard below, which is how an unparseable slot number reached the run
        # loop and stopped the session.
        try:
            title, call = self._plan(kind, cmd)
        except Exception as e:  # noqa: BLE001
            self._finish({"ok": False,
                          "message": f"could not read that request: "
                                     f"{type(e).__name__}: {e}"})
            return
        if call is None:
            self._finish({"ok": False, "message": title})
            return
        self._begin(title)
        # The pilot's own log lines become the progress text on the phone, and
        # each one is a chance to refresh the picture too.
        previous = self.p.log

        def relay(*args, **kwargs):
            self._progress(" ".join(str(a) for a in args))
            self._refresh_frame(force=True)
            return previous(*args, **kwargs)

        self.p.log = relay
        self.p.session.set_budget(Budget(max_frames=60 * 60 * 60 * 12,
                                         max_wall_seconds=self.timeout))
        try:
            res = call()
            result = {"ok": bool(getattr(res, "ok", False)),
                      "message": getattr(res, "message", str(res)),
                      "stats": getattr(res, "stats", {}) or {},
                      "notes": (getattr(res, "notes", []) or [])[:6]}
            if getattr(res, "saved", False):
                self.p.session.flush_sram()
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "message": f"{type(e).__name__}: {e}"}
        finally:
            self.p.log = previous
        self._finish(result)

    def _plan(self, kind: str, cmd: dict):
        """-> (title, callable) or (reason, None) if the request makes no sense."""
        if kind == "grind":
            slot = int(cmd.get("slot", 0))
            level = int(cmd.get("level", 0))
            party = self.p.reader.party()
            if not (0 <= slot < len(party)):
                return "that party slot is empty", None
            if not (1 <= level <= 100):
                return "pick a level between 1 and 100", None
            if party[slot].level >= level:
                return f"already Lv{party[slot].level}", None
            return (f"grind {party[slot].species_name} to Lv{level}",
                    lambda: self.p.grind(slot=slot, to_level=level))
        if kind == "tap":
            tile = self._tap_target(cmd)
            if tile is None:
                return "that tap was off the screen", None
            tx, ty = tile
            if self._window_open():
                # A menu is up, so the tap means "put the cursor there" rather
                # than "walk there" -- there is no map on screen to walk on.
                cursor = self.p.session.rb("wMenuCursorY")
                if cursor == 0:
                    return "that is a textbox, not a menu -- press A", None
                entries = max(1, self.p.session.rb("wMenuDataItems"))
                top = self.p.session.rb("wMenuBorderTopCoord")
                text_row = self._tap_row(cmd)
                if text_row is None:
                    return "that tap was off the screen", None
                row = (text_row - top - MENU_FIRST_ROW) // MENU_ROW_STRIDE + 1
                row = max(1, min(entries, row))
                return (f"cursor to entry {row}",
                        lambda: self._move_cursor_to_row(row, entries))
            if self.p.reader.in_battle():
                return "that is a battle, not the map", None
            loc = self.p.reader.location()
            goal = (loc.x + tx - PLAYER_TILE_X, loc.y + ty - PLAYER_TILE_Y)
            if goal == (loc.x, loc.y):
                return "you are already standing there", None
            if min(goal) < 0:
                return "that is off the edge of the map", None
            return (f"walk to ({goal[0]},{goal[1]})",
                    lambda: self._walk_to_tap(goal))
        if kind in ("hunt", "catch"):
            shiny = bool(cmd.get("shiny"))
            species = cmd.get("species") or None
            if not species and not shiny:
                return "choose something to look for", None
            if kind == "catch" and not self.p.reader.balls():
                return "no Poke Balls in the bag", None
            name = "a shiny" if shiny and not species else species
            if kind == "hunt":
                return (f"hunt {name}",
                        lambda: self.p.hunt(species=species, shiny=shiny))
            return (f"catch {name}",
                    lambda: self.p.catch(species=species, shiny=shiny))
        if kind == "trainers":
            if not self._trainer_count():
                return "no trainers on this map", None
            return "battle every trainer here", lambda: self.p.trainers()
        return f"unknown task {kind!r}", None

    # --- status ------------------------------------------------------------
    def _begin(self, title: str) -> None:
        with self._lock:
            self._status.busy = True
            self._status.task = title
            self._status.progress = "starting"
            self._status.result = None
        self._say(f"  {title}")

    def _progress(self, text: str) -> None:
        with self._lock:
            self._status.progress = text[:120]

    def _finish(self, result: dict) -> None:
        with self._lock:
            self._status.busy = False
            self._status.progress = ""
            self._status.result = result
        self._say(f"  {result.get('message', '')}")

    def _trainer_count(self) -> int:
        loc = self.p.reader.location()
        const = self.p.gamedata.map_name(loc.group, loc.number)
        return len(self.p.world.trainers.get(const, []))

    def _refresh_status(self) -> None:
        r, gd = self.p.reader, self.p.gamedata
        try:
            loc = r.location()
            const = gd.map_name(loc.group, loc.number)
            party = [{"slot": m.slot, "name": m.species_name, "level": m.level,
                      "hp": m.hp, "maxHp": m.max_hp, "status": m.status_name}
                     for m in r.party()]
            balls = [{"name": gd.item_name(i).replace("_", " ").title(),
                      "count": q} for i, q in r.balls()]
            info = {
                "where": gd.map_pretty(loc.group, loc.number),
                "on_grass": r.on_grass(),
                "party": party,
                "species": species_on(self.source, const),
                "trainers": len(self.p.world.trainers.get(const, [])),
                "balls": balls,
                "frame": self.p.session.frame,
            }
        except Exception:
            return
        with self._lock:
            for key, value in info.items():
                setattr(self._status, key, value)

    def _refresh_frame(self, force: bool = False) -> None:
        try:
            img = self.p.session.pyboy.screen.image
            if img is None:
                return
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            data = buf.getvalue()
        except Exception:
            return
        with self._lock:
            self._png = data

    # --- http --------------------------------------------------------------
    def url(self) -> str:
        return f"http://{local_ip()}:{self.port}/?t={self.token}"

    def _serve_forever_in_background(self) -> None:
        app = self
        handler = make_handler(app)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()


def qr_lines(url: str) -> list[str]:
    """The URL as a scannable terminal QR code, or [] if qrcode is absent.

    Typing http://192.168.1.x:8080/?t=... on a phone keypad is the worst part of
    connecting, and pointing a camera at the terminal removes it entirely. The
    dependency is optional so a missing package degrades to just the URL.
    """
    try:
        import io as _io

        import qrcode
    except ImportError:
        return []
    try:
        qr = qrcode.QRCode(border=1,
                           error_correction=qrcode.constants.ERROR_CORRECT_L)
        qr.add_data(url)
        qr.make(fit=True)
        buf = _io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        return buf.getvalue().splitlines()
    except Exception:
        return []


def local_ip() -> str:
    """Best guess at the address a phone on the same wifi should use."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))     # no packets sent; just picks the route
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def make_handler(app: WebPilot):
    class Handler(BaseHTTPRequestHandler):
        server_version = "crystal-pilot"

        def log_message(self, *args):        # keep the console for the pilot
            pass

        # -- helpers --
        def _authorised(self) -> bool:
            from urllib.parse import parse_qs, urlparse
            token = parse_qs(urlparse(self.path).query).get("t", [None])[0]
            if token is None:
                token = (self.headers.get("X-Pilot-Token") or "").strip()
            return secrets.compare_digest(token or "", app.token)

        def _send(self, code, body=b"", ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        # -- routes --
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if not self._authorised():
                self._send(403, b"bad or missing token", "text/plain")
                return
            if path == "/":
                page = (WEB_ROOT / "index.html").read_bytes()
                self._send(200, page, "text/html; charset=utf-8")
            elif path == "/screen.png":
                png = app.screen_png()
                self._send(200 if png else 503, png, "image/png")
            elif path == "/api/state":
                self._json(app.snapshot())
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if not self._authorised():
                self._send(403, b"bad or missing token", "text/plain")
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json({"ok": False, "message": "bad JSON"}, 400)
                return
            if path == "/api/task":
                state = app.snapshot()
                if state["busy"]:
                    self._json({"ok": False, "message": "already working"}, 409)
                    return
                app.submit(body)
                self._json({"ok": True})
            elif path == "/api/input":
                app.submit({"kind": "input", "button": body.get("button"),
                            "frames": body.get("frames", TAP_FRAMES)})
                self._json({"ok": True})
            elif path == "/api/save":
                app.submit({"kind": "save"})
                self._json({"ok": True})
            else:
                self._send(404, b"not found", "text/plain")

    return Handler
