"""Driving the game's UI: dialogue, yes/no boxes, the battle menu, move select.

Menus are driven the way a person drives them -- normalise the cursor to a known
corner with D-pad presses, then step to the wanted entry -- and the result is
confirmed by reading memory afterwards. That avoids depending on where the
cursor happened to be left by a previous battle.
"""
from __future__ import annotations

TEXT_EVENTS = ("text_prompt", "text_wait", "text_aorb")

# wBattleMenuCursorPosition values, from BattleMenu in engine/battle/core.asm
FIGHT, PKMN, PACK, RUN = 1, 2, 3, 4


class Control:
    def __init__(self, session, reader):
        self.s = session
        self.r = reader

    # --- dialogue ----------------------------------------------------------
    def advance_text(self, max_taps: int = 400, quiet_frames: int = 120) -> int:
        """Tap A through dialogue until the game stops asking for input.

        The text hooks are edge-triggered on routine entry, so they signal
        "a textbox was just entered", not "a textbox is open". Pressing A exits
        the wait loop and the next box re-enters it -- so one fire == one box.
        We therefore tap unconditionally and use fires as the liveness signal,
        stopping once `quiet_frames` pass with no box appearing.
        """
        taps = 0
        quiet = 0
        per_tap = 12  # hold 4 + gap 8
        self.s.clear_events()
        while taps < max_taps and quiet < quiet_frames:
            self.s.tap("a", hold=4, gap=8)
            taps += 1
            if self.s.has_event(*TEXT_EVENTS):
                self.s.clear_events()
                quiet = 0
            else:
                quiet += per_tap
        return taps

    # --- map scripts -------------------------------------------------------
    def script_mode(self) -> int:
        """wScriptMode: 0 SCRIPT_OFF, 1 READ, 2 WAIT_MOVEMENT, 3 WAIT."""
        return self.s.rb("wScriptMode")

    def script_running(self) -> bool:
        return self.script_mode() != 0

    def run_scripts(self, max_taps: int = 800, settle: int = 45) -> int:
        """Mash A until no map script is executing.

        Unlike `advance_text`, this has a real level signal (wScriptMode) so it
        cannot bail out during a long pause inside a cutscene. It also stops the
        instant the script ends, which matters because one extra A while facing
        an NPC would start their dialogue all over again.
        """
        taps = 0
        while taps < max_taps:
            if not self.script_running():
                # Confirm it stays off -- scripts briefly drop to OFF between
                # chained segments.
                self.s.tick(settle)
                if not self.script_running():
                    return taps
            self.s.tap("a", hold=4, gap=8)
            taps += 1
        return taps

    def answer_yes_no(self, yes: bool = True) -> None:
        """YesNoBox: cursor starts on YES; DOWN once selects NO."""
        if not yes:
            self.s.tap("down")
        self.s.tap("a")

    # --- battle menu -------------------------------------------------------
    def _await_menu_cursor(self, tries: int = 25, settle: int = 40) -> bool:
        """Wait until a menu is actually drawn and interactive.

        The BattleMenu hook fires on routine entry, *before* the menu resets and
        redraws its cursor -- so a cursor read taken immediately still holds the
        previous turn's value and looks ready when it is not. Settling first
        means a non-zero cursor really does mean "menu is up", which matters
        because otherwise the directional presses and the confirm land on
        battle text and the turn silently falls back to whatever move the
        cursor was left on.
        """
        self.s.tick(settle)
        for _ in range(tries):
            if self.s.rb("wMenuCursorY") != 0:
                return True
            self.s.tick(4)
        return False

    def battle_menu_cell(self) -> tuple[int, int]:
        """Live battle-menu cursor as 1-based (x, y)."""
        return self.s.rb("wMenuCursorX"), self.s.rb("wMenuCursorY")

    def choose_battle_action(self, action: int) -> bool:
        """Put the battle menu cursor on `action` and confirm.

        The battle menu is a 2x2 grid driven by wMenuCursorX/wMenuCursorY (both
        1-based):
              (1,1) FIGHT   (2,1) PKMN
              (1,2) PACK    (2,2) RUN
        which is wBattleMenuCursorPosition = (y-1)*2 + x. We read the live
        cursor and step toward the target rather than counting presses from an
        assumed origin -- the cursor persists between turns, so an assumed
        origin is wrong exactly when it matters (fleeing, switching).
        """
        want_x = ((action - 1) % 2) + 1
        want_y = ((action - 1) // 2) + 1
        if not self._await_menu_cursor():
            # The BattleMenu hook fires on routine entry and its internal loop
            # can re-enter while text is still on screen. Pressing A then would
            # confirm whatever the cursor happens to be on, so refuse instead
            # and let the caller re-sync.
            return False
        for _ in range(8):
            x, y = self.battle_menu_cell()
            if (x, y) == (want_x, want_y):
                break
            if x != want_x:
                self.s.tap("right" if want_x > x else "left", hold=4, gap=6)
            elif y != want_y:
                self.s.tap("down" if want_y > y else "up", hold=4, gap=6)
        landed = self.battle_menu_cell() == (want_x, want_y)
        self.s.tap("a")
        return landed

    # --- move select -------------------------------------------------------
    def choose_move(self, index: int, n_moves: int = 4) -> bool:
        """Select move slot `index` (0-based) in MoveSelectionScreen.

        wMenuCursorY is 1-based and the move list *wraps*, so counting presses
        from an assumed start position silently picks the wrong move.
        """
        target = index + 1
        if not self._await_menu_cursor():
            return False
        for _ in range(n_moves + 2):
            if self.s.rb("wMenuCursorY") == target:
                break
            self.s.tap("down", hold=4, gap=6)
        landed = self.s.rb("wMenuCursorY") == target
        self.s.tap("a")
        return landed

    def current_move_num(self) -> int:
        """wCurMoveNum: the committed move slot, 0-based (it indexes wBattleMonPP)."""
        return self.s.rb("wCurMoveNum")

    # --- the pack ----------------------------------------------------------
    BALL_POCKET = 1        # wCurPocket: 0 ITEM, 1 BALL, 2 KEY ITEM, 3 TM/HM

    def throw_ball(self, ball_id: int) -> bool:
        """From the battle menu: PACK -> BALL pocket -> the ball -> USE.

        Selecting a ball opens a USE/QUIT submenu, so the throw needs a second
        confirm. Pocket and item are driven by reading wCurPocket/wCurItem
        rather than counting presses, because the pack remembers where it was
        left and the pocket switch swallows presses during its animation.
        """
        if not self.choose_battle_action(PACK):
            return False
        self.s.tick(60)
        if self.s.rb("wCurItem") in (0, 0xFF) and self.s.rb("wCurPocket") > 3:
            # The pack never opened, so the presses below would land in the
            # battle instead. Back out rather than flailing.
            self.close_menus(4)
            return False
        for _ in range(10):
            if self.s.rb("wCurPocket") == self.BALL_POCKET:
                break
            self.s.tap("right", hold=4, gap=8)
        if self.s.rb("wCurPocket") != self.BALL_POCKET:
            self.close_menus(4)
            return False
        for _ in range(12):
            if self.s.rb("wCurItem") == ball_id:
                break
            self.s.tap("down", hold=4, gap=8)
        if self.s.rb("wCurItem") != ball_id:
            self.close_menus(4)
            return False
        self.s.tap("a")
        self.s.tick(40)        # USE / QUIT submenu, cursor starts on USE
        self.s.tap("a")
        self.s.tick(40)
        return True

    # --- start menu / saving ----------------------------------------------
    def open_start_menu(self) -> None:
        self.s.tap("start", hold=8, gap=12)
        self.s.tick(20)

    def close_menus(self, times: int = 6) -> None:
        for _ in range(times):
            self.s.tap("b", hold=4, gap=6)
        self.s.tick(20)
