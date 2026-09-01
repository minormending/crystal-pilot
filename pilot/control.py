"""Driving the game's UI: dialogue, yes/no boxes, the battle menu, move select.

Menus are driven the way a person drives them -- normalise the cursor to a known
corner with D-pad presses, then step to the wanted entry -- and the result is
confirmed by reading memory afterwards. That avoids depending on where the
cursor happened to be left by a previous battle.
"""
from __future__ import annotations

from .symbols import NAME_MENU_ITEMS, NAME_MENU_RIGHT, NAME_MENU_FIRST_PRESET

TEXT_EVENTS = ("text_prompt", "text_wait", "text_aorb")

# Routines that run immediately before the game asks "give it a nickname?".
# Catching the question needs advance warning, because by the time the YesNoBox
# is up it looks like any other yes/no -- and the default answer is YES.
NICKNAME_EVENTS = ("give_poke", "ball_nickname", "ball_nickname_box")

# wBattleMenuCursorPosition values, from BattleMenu in engine/battle/core.asm
FIGHT, PKMN, PACK, RUN = 1, 2, 3, 4


class Control:
    def __init__(self, session, reader):
        self.s = session
        self.r = reader
        self._nickname_armed = False
        self._name_menu_pending = False

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
        self.nickname_prompt()      # before the clear below discards the warning
        self.player_name_prompt()
        self.s.clear_events()
        while taps < max_taps and quiet < quiet_frames:
            # The NAME menu is the one prompt that cannot be tapped through and
            # then corrected, so it is checked before the tap, not after.
            if self.player_name_prompt() or self.name_menu_pending:
                taps += 1
                quiet = 0
                if self.name_menu_pending:
                    self.s.tick(8)      # still drawing; tapping would take NEW NAME
                continue
            self.s.tap("a", hold=4, gap=8)
            taps += 1
            # Checked before the clear: the question and its box can land in
            # the same window, and clearing would drop the box.
            if self.nickname_prompt():
                quiet = 0
                continue
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
            self.nickname_prompt()
        return taps

    def answer_yes_no(self, yes: bool = True, settle: int = 24,
                      tries: int = 14) -> bool:
        """YesNoBox: cursor 1 is YES, 2 is NO.

        Driven against the live cursor rather than by counting presses. The
        hook fires on entry, before the box is interactive, so a blind DOWN can
        be swallowed -- and then the A that follows answers YES. For a nickname
        prompt that means the letter grid opens and mashing A spells AAAAA, so
        the difference between "verified" and "probably fine" is the whole
        point of the method.
        """
        want = 1 if yes else 2
        self.s.tick(settle)
        for _ in range(tries):
            cur = self.s.rb("wMenuCursorY")
            if cur == want:
                self.s.tap("a")
                return True
            if cur == 0:                      # not drawn yet
                self.s.tick(6)
                continue
            self.s.tap("down" if cur < want else "up", hold=4, gap=6)
        return False

    # --- naming ------------------------------------------------------------
    def nickname_prompt(self, evs: set[str] | None = None) -> bool:
        """Answer a pending "give it a nickname?" with NO.

        Pokemon keep the names the game gives them. Arming happens on the
        routine that precedes the question; the answer is given when the box
        actually appears, which can be many frames later.

        Pass `evs` when the caller has already drained the event queue, since
        draining is destructive and the arming event would otherwise be lost.
        Returns True when it answered, so callers skip their own A tap.
        """
        def fired(*names: str) -> bool:
            if evs is None:
                return self.s.has_event(*names)
            return any(n in evs for n in names)

        if fired(*NICKNAME_EVENTS) and not self._nickname_armed:
            self._nickname_armed = True
            if evs is None:
                # A yes/no already in the queue belongs to an earlier prompt --
                # "do you want this one?", say -- and answering that one again
                # would use up the arming and let the real nickname box through
                # on its default of YES. The question always follows a PrintText
                # that waits for a button, so a later fire is guaranteed.
                self.s.clear_events()
        if not (self._nickname_armed and fired("yes_no")):
            return False
        self._nickname_armed = False
        self.answer_yes_no(False)
        if evs is None:
            # The fired yes_no would otherwise still be sitting in the queue
            # and answer the next prompt that comes along.
            self.s.clear_events()
        return True

    def player_name_prompt(self, evs: set[str] | None = None,
                           preset: int = 0) -> bool:
        """Take the intro's NAME menu, if it is on its way or already up.

        Returns True once a name has been picked. While the menu is pending but
        not yet drawn, callers must not tap A: the menu blocks on a choice, and
        a single stray A takes NEW NAME and drops the pilot into the letter
        grid, where mashing A spells AAAAA.
        """
        def fired(*names: str) -> bool:
            if evs is None:
                return self.s.has_event(*names)
            return any(n in evs for n in names)

        if fired("name_player"):
            self._name_menu_pending = True
        if not self._name_menu_pending or not self.name_menu_open():
            return False
        self._name_menu_pending = False
        picked = self.choose_player_name(preset)
        if evs is None:
            self.s.clear_events()
        return picked

    @property
    def name_menu_pending(self) -> bool:
        return self._name_menu_pending

    def name_menu_open(self) -> bool:
        """Is the intro's NAME menu on screen?

        Matched on the menu's own shape rather than the cursor: NamePlayer
        fires before the menu is drawn, and until it is, wMenuCursorY still
        holds whatever the gender prompt left there.
        """
        return (self.s.rb("wMenuDataItems") == NAME_MENU_ITEMS
                and self.s.rb("wMenuBorderRightCoord") == NAME_MENU_RIGHT
                and self.s.rb("wMenuBorderTopCoord") == 0)

    def choose_player_name(self, preset: int = 0, tries: int = 60) -> bool:
        """Pick one of the game's own names instead of typing one.

        Cursor 1 is NEW NAME, which opens the letter grid -- and an auto-pilot
        mashing A through a letter grid spells AAAAA. Cursor 2 and below are
        the presets, which NamePlayer stores directly with no naming screen at
        all. (Leaving the grid empty also works: the game falls back to
        CHRIS/KRIS. Picking a preset is the same outcome without the detour.)
        """
        want = NAME_MENU_FIRST_PRESET + max(0, preset)
        for _ in range(tries):
            if not self.name_menu_open():
                self.s.tick(8)
                continue
            cur = self.s.rb("wMenuCursorY")
            if cur == want:
                self.s.tap("a")
                self.s.tick(20)
                return True
            self.s.tap("down" if cur < want else "up", hold=4, gap=6)
        return False

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
