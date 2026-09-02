"""The Pilot facade: assembles every layer and exposes the tasks."""
from __future__ import annotations

import os
from pathlib import Path

from .backup import BackupManager, GameSaver
from .collision import CollisionMap
from .control import Control
from .gamedata import GameData
from .nav import Navigator
from .recorder import Recorder
from .timeline import CheckpointWriter, Timeline
from .session import Budget, Session
from .state import GameStateReader
from .tasks.bootstrap import Bootstrap
from .tasks.grind import GrindTask
from .tasks.catch import CatchTask
from .tasks.moment import CaptureTask, FightTask, HealTask
from .tasks.hunt import HuntTask
from .tasks.trainers import TrainerSweepTask
from .travel import Traveler
from .world import World

# Where the pokecrystal disassembly lives. Set POKECRYSTAL_DIR to point
# elsewhere; everything else is derived from it.
DEFAULT_SOURCE = Path(
    os.environ.get("POKECRYSTAL_DIR", Path.home() / "projects" / "pokecrystal")
)


class Pilot:
    def __init__(self, rom: str | Path, source: str | Path = DEFAULT_SOURCE,
                 sav: str | Path | None = None, sym: str | Path | None = None,
                 backup_dir: str | Path | None = None, window: str = "null",
                 speed: int = 0, timeout_seconds: float = 900.0,
                 max_frames: int | None = None, log=print):
        self.log = log
        rom = Path(rom)
        # A generous default frame cap: at ~30k fps headless this is minutes of
        # wall clock, and the wall-clock limit is the one that really bites.
        budget = Budget(
            max_frames=max_frames or 60 * 60 * 60 * 12,   # ~12 in-game hours
            max_wall_seconds=timeout_seconds,
        )
        self.session = Session(rom, sym=sym, sav=sav, window=window,
                               speed=speed, budget=budget)
        self.gamedata = GameData(source)
        self.world = World(self.gamedata, source)
        self.reader = GameStateReader(self.session, self.gamedata)
        self.control = Control(self.session, self.reader)
        self.collision = CollisionMap(self.session, self.reader, source)
        self.nav = Navigator(self.session, self.reader, collision=self.collision)
        self.traveler = Traveler(self.session, self.reader, self.control,
                                 self.nav, self.world, self.gamedata, log=log)
        self.saver = GameSaver(self.session, self.reader, self.control, log=log)
        self._caption_slot = None
        self.backups = BackupManager(
            backup_dir or (rom.parent / "pilot-backups"),
            sav_path=self.session.sav_path, log=log,
        )

    def calibrate(self, attempts: int = 4) -> bool:
        """Verify the collision decode against the running game.

        Retried because a map that is still loading has not published a
        meaningful wPlayerTileCollision yet. Movement falls back to exploratory
        walking if this never succeeds, so it is checked rather than assumed.
        """
        for i in range(attempts):
            self.nav.settle()
            if self.collision.calibrate():
                return True
            self.session.tick(60)
            if i:
                # Nudge: a step guarantees the map/collision state is current.
                self.nav.step("down")
        self.log("warning: could not verify the collision map; "
                 "navigation will fall back to exploratory walking")
        return False

    def continue_game(self, timeout: int = 6000) -> bool:
        """Boot and load the existing save, stopping when the world is live.

        Waiting on party data would be wrong -- the CONTINUE screen already has
        the save loaded into WRAM, so the pilot would start pressing buttons
        while still in the menus.
        """
        self.session.tick(2500)
        ok = self.session.await_world(timeout=timeout, tap_a_every=16)
        if not ok:
            self.log("could not reach the overworld from the title screen")
            return False
        self.control.run_scripts()
        self.nav.settle()
        self.calibrate()
        return True

    # --- recording ---------------------------------------------------------
    def caption(self) -> str:
        """One line of live state for the video's caption strip."""
        r = self.reader
        try:
            loc = r.location()
            if loc.key == (0, 0):
                return ""      # title screen / intro: no map to name yet
            where = self.gamedata.map_pretty(loc.group, loc.number)
            if r.in_battle():
                b = r.battle()
                # wBattleMon/wEnemyMon are only meaningful once the battle has
                # finished setting up; sampled mid-transition they read as
                # zeroes. Fall through to the party line rather than captioning
                # a frame "me Lv0 0/0HP".
                if b.active_max_hp and b.enemy_max_hp:
                    kind = "wild" if b.is_wild else "trainer"
                    return (f"{kind}: {b.enemy_name} Lv{b.enemy_level}  |  "
                            f"me Lv{b.active_level} "
                            f"{b.active_hp}/{b.active_max_hp}HP")
                party = r.party()
                lead = party[0] if party else None
                tail = (f"  |  {lead.species_name} Lv{lead.level} "
                        f"{lead.hp}/{lead.max_hp}HP" if lead else "")
                return f"battle starting{tail}"
            party = r.party()
            if party:
                m = party[0] if self._caption_slot is None else r.mon(self._caption_slot)
                return (f"{where}  |  {m.species_name} Lv{m.level} "
                        f"{m.hp}/{m.max_hp}HP")
            return where
        except Exception:
            return ""

    def start_recording(self, path, speed: float = 30.0, fps: int = 30,
                        scale: int = 3, hud: bool = True, title: str = "",
                        crf: int = 23):
        rec = Recorder(path, speed=speed, fps=fps, scale=scale, hud=hud,
                       title=title, caption_fn=self.caption, crf=crf,
                       log=self.log)
        self.session.attach_recorder(rec)
        self.log(f"recording to {rec.path} at {rec.actual_speed:.0f}x "
                 f"(1 frame every {rec.every})")
        return rec

    def checkpoint_summary(self) -> dict:
        """What the timeline index records about this moment."""
        r = self.reader
        level = None
        savable = True
        try:
            party = r.party()
            if party:
                m = party[0] if self._caption_slot is None else r.mon(self._caption_slot)
                level = m.level
            # Only a battle is a genuine blocker: the game refuses to save
            # during one, and no amount of waiting changes that. A running map
            # script is temporary -- settle_for_save runs it to completion --
            # so it must not be counted here or almost every point would be
            # marked unsavable.
            savable = not r.in_battle()
        except Exception:
            pass
        return {"text": self.caption(), "level": level, "savable": savable}

    def start_checkpoints(self, directory, every_frames: int, task: str = ""):
        video = None
        rec = self.session.recorder
        if rec is not None:
            video = {"path": str(rec.path), "fps": rec.fps, "every": rec.every,
                     "speed": rec.actual_speed}
        writer = CheckpointWriter(
            directory, every_frames=every_frames,
            summary_fn=self.checkpoint_summary, task=task, video=video,
            rom=str(self.session.rom), log=self.log,
        )
        self.session.attach_checkpoints(writer)
        writer.capture(self.session, self.session.frame)   # always have a start
        self.log(f"checkpointing to {writer.dir} every {every_frames} frames "
                 f"({every_frames / 60:.0f}s of game time)")
        return writer

    def stop_checkpoints(self):
        writer = self.session.detach_checkpoints()
        if writer is None:
            return None
        writer.capture(self.session, self.session.frame)   # and an end
        writer.close()
        self.log(writer.describe())
        return writer

    def resume_from(self, timeline: Timeline, checkpoint) -> None:
        """Load a checkpoint's exact machine state into this session."""
        self.session.restore(timeline.load_blob(checkpoint))
        self.collision._calibrated = False
        self.calibrate()

    def stop_recording(self):
        rec = self.session.detach_recorder()
        if rec is None:
            return None
        rec.close()
        self.log(rec.describe())
        return rec

    # --- tasks -------------------------------------------------------------
    def grind(self, **kwargs):
        if not self.collision.calibrated:
            self.calibrate()
        self._caption_slot = kwargs.get("slot")
        task = GrindTask(self.session, self.reader, self.control, self.nav,
                         self.world, self.gamedata, self.traveler, self.saver,
                         self.backups, log=self.log)
        result = task.run(**kwargs)
        if result.saved:
            self.session.flush_sram()
        return result

    def hunt(self, **kwargs):
        if not self.collision.calibrated:
            self.calibrate()
        task = HuntTask(self.session, self.reader, self.control, self.nav,
                        self.world, self.gamedata, self.traveler, self.saver,
                        self.backups, log=self.log)
        return task.run(**kwargs)

    def catch(self, **kwargs):
        if not self.collision.calibrated:
            self.calibrate()
        task = CatchTask(self.session, self.reader, self.control, self.nav,
                         self.world, self.gamedata, self.traveler, self.saver,
                         self.backups, log=self.log)
        result = task.run(**kwargs)
        if result.saved:
            self.session.flush_sram()
        return result

    # --- acting on the situation you are already in -------------------------
    # Deliberately no calibrate() on the two battle ones: they never walk, so a
    # collision decode is not needed and asking for one inside a battle would
    # be sampling a map that is not on screen.
    def battle(self, **kwargs):
        task = FightTask(self.session, self.reader, self.control, self.nav,
                         self.world, self.gamedata, self.traveler, self.saver,
                         self.backups, log=self.log)
        return task.run(**kwargs)

    def capture(self, **kwargs):
        task = CaptureTask(self.session, self.reader, self.control, self.nav,
                           self.world, self.gamedata, self.traveler, self.saver,
                           self.backups, log=self.log)
        result = task.run(**kwargs)
        if result.saved:
            self.session.flush_sram()
        return result

    def heal(self, **kwargs):
        if not self.collision.calibrated:
            self.calibrate()
        task = HealTask(self.session, self.reader, self.control, self.nav,
                        self.world, self.gamedata, self.traveler, self.saver,
                        self.backups, log=self.log)
        return task.run(**kwargs)

    def trainers(self, **kwargs):
        if not self.collision.calibrated:
            self.calibrate()
        task = TrainerSweepTask(self.session, self.reader, self.control, self.nav,
                                self.world, self.gamedata, self.traveler,
                                self.saver, self.backups, log=self.log)
        result = task.run(**kwargs)
        if result.saved:
            self.session.flush_sram()
        return result

    def bootstrap(self, starter: str = "cyndaquil", to_route: bool = True):
        b = Bootstrap(self.session, self.reader, self.control, self.nav,
                      log=self.log)
        b.run_intro()
        b.walk_to_lab()
        b.get_starter(starter)
        if to_route:
            self.to_first_route()
        return self.reader.summary()

    def to_first_route(self) -> bool:
        if not self.collision.calibrated:
            self.calibrate()
        """Leave Elm's lab and get onto Route 29's grass."""
        for _ in range(4):
            if self.nav.take_warp(4, 11, push="down"):
                break
            self.control.run_scripts()
            self.nav.settle()
        if self.traveler.current_const() == "NEW_BARK_TOWN":
            self.nav.cross_edge("west")
        ok = self.traveler.current_const() == "ROUTE_29"
        if ok:
            self.nav.find_grass()
        return ok

    # --- info --------------------------------------------------------------
    def status(self) -> str:
        r = self.reader
        loc = r.location()
        where = self.gamedata.map_pretty(loc.group, loc.number)
        lines = [f"location : {where} ({loc.x},{loc.y})"
                 + ("  [on grass]" if r.on_grass() else "")]
        if r.in_battle():
            lines.append(f"battle   : {r.summary()}")
        party = r.party()
        if party:
            lines.append("party    :")
            for m in party:
                # 1-based to match the --slot flag.
                lines.append(f"  slot {m.slot + 1}: {m.describe(self.gamedata)}")
        else:
            lines.append("party    : (empty)")
        pc = self.world.nearest_pokecenter(
            self.gamedata.map_name(loc.group, loc.number))
        if pc:
            lines.append(f"nearest Center: {pc[-1][1]} ({len(pc)} hops)")
        return "\n".join(lines)

    def settle_for_save(self, rounds: int = 12) -> tuple[bool, str]:
        """Get the game into a state where an in-game save is possible.

        A checkpoint is an arbitrary frame: the player may be mid-step, a map
        script may be running, or the map may still be loading. START is ignored
        in all of those, so saving straight after a resume fails for reasons that
        have nothing to do with the save menu itself.
        """
        self.nav.settle()
        for _ in range(rounds):
            if self.reader.in_battle():
                return False, "that point is mid-battle, and the game cannot save then"
            if self.control.script_running():
                self.control.run_scripts()
                continue
            if not self.session.world_loaded():
                self.session.tick(60)
                continue
            # Let any in-progress step finish so the player is tile-aligned.
            self.session.tick(30)
            if not self.control.script_running() and not self.reader.in_battle():
                return True, ""
        if self.reader.in_battle():
            return False, "that point is mid-battle, and the game cannot save then"
        return False, "the game never settled into a controllable overworld"

    def save(self) -> bool:
        ok = self.saver.save_in_game()
        if ok:
            self.session.flush_sram()
        return ok

    def stop(self, save_sram: bool = True) -> None:
        self.session.stop(save_sram=save_sram)
