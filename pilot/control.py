"""Driving the game's UI: dialogue, yes/no boxes, the battle menu, move select.

Menus are driven the way a person drives them -- normalise the cursor to a known
corner with D-pad presses, then step to the wanted entry -- and the result is
confirmed by reading memory afterwards. That avoids depending on where the
cursor happened to be left by a previous battle.
"""
from __future__ import annotations

from . import symbols as S
from .symbols import NAME_MENU_FIRST_PRESET, NAME_MENU_ITEMS, NAME_MENU_RIGHT

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

    # --- which box is on screen -------------------------------------------
    def window_open(self) -> bool:
        """Is any window on the stack?

        Necessary and nowhere near sufficient -- see `is_box`. Used on its own
        only to decide whether there is anything left to back out of.
        """
        return self.s.rb("wWindowStackSize") > 0

    def box_shape(self) -> tuple[int, int]:
        """(rows, top screen row) of the box currently loaded."""
        return self.s.rb("wMenuDataItems"), self.s.rb("wMenuBorderTopCoord")

    def is_box(self, shape: tuple[int | None, int | None]) -> bool:
        """Is the box on screen the one this shape describes?

        The cursor keeps its previous value between boxes, so both "a window is
        open" and "the cursor is somewhere" are true of the wrong box. Shape is
        what tells them apart, and it is the same lesson `_handle_learn_move`
        already carries -- arriving here for the three boxes between the START
        menu and a healed Pokemon.

        Either half may be `None` for "do not care", which exactly one box
        needs: the party list is `(None, 0)` because its row count is not a
        constant. Measured on the same one-Pokemon party, the field pack's
        party list reports four rows and the battle pack's reports two -- two
        different menu headers for the same question -- so matching the count
        would have made this work in the field and silently fail in a battle,
        which is where a heal actually matters.
        """
        if not self.window_open():
            return False
        rows, top = self.box_shape()
        want_rows, want_top = shape
        return ((want_rows is None or rows == want_rows)
                and (want_top is None or top == want_top))

    def await_box(self, shape: tuple[int | None, int | None], tries: int = 30,
                  settle: int = 30) -> bool:
        """Tick until `shape` is the box on screen *and has stopped changing*.

        Two rules, and both were paid for.

        Press, then wait for what you expected -- rather than pressing and
        looking once. A box's shape is written as it is drawn, so a single
        sample taken during the draw reads the previous box and reports the
        wrong one.

        And then: **a box that is on screen is not yet a box that takes input.**
        Returning on the first matching frame is what the first version did, and
        it made the pack fail in the most confusing way available -- every press
        "landed", every box "appeared", and the Potion was never used.
        `backup.py` already carries this warning about the save confirm ("any A
        pressed before that is swallowed"); it is the same fact about other
        boxes.

        So the shape has to still be there after `settle` frames. The default is
        thirty, and that number is measured rather than padded: the *field*
        pack's party list takes input after ten, and the *battle* pack's needs
        thirty. Twelve was enough to make healing work on the map and not in a
        fight, which is the half where it matters. Thirty frames is half a
        second of emulated time and the pilot runs at several hundred times
        real speed, so the honest reading is that this costs nothing.
        """
        for _ in range(tries):
            if self.is_box(shape):
                self.s.tick(settle)
                return self.is_box(shape)
            self.s.tick(6)
        return self.is_box(shape)

    # --- menu rows ---------------------------------------------------------
    def menu_row_count(self, limit: int = 12) -> int:
        """How many rows the open menu has, by stepping until the cursor wraps.

        Returns 0 if the player turns out to be walking, which means the menu
        was never open and these DOWN presses are moving through the world --
        in grass that starts a wild battle, and then nothing else here applies.
        """
        loc0 = self.r.location()
        seen: list[int] = []
        for _ in range(limit):
            cur = self.s.rb("wMenuCursorY")
            if cur in seen:
                break
            seen.append(cur)
            self.s.tap("down", hold=4, gap=6)
            now = self.r.location()
            if (now.x, now.y) != (loc0.x, loc0.y):
                return 0
        return max(seen) if seen else 0

    def drive_menu_cursor(self, target: int, count: int) -> bool:
        """Step the cursor to row `target`, confirming nothing."""
        for _ in range(count + 2):
            if self.s.rb("wMenuCursorY") == target:
                return True
            self.s.tap("down", hold=4, gap=6)
        return self.s.rb("wMenuCursorY") == target

    # --- the pack ----------------------------------------------------------
    BALL_POCKET = 1        # wCurPocket: 0 ITEM, 1 BALL, 2 KEY ITEM, 3 TM/HM
    ITEM_POCKET = 0

    def _pack_moved(self, button: str, read: str, tries: int = 10) -> int:
        """Press `button` and wait for `read` to actually change.

        The pocket switch swallows presses during its animation, so pressing
        and then looking reads the value from before the press and presses
        again -- which is how a walk to the ITEM pocket sails past it. Press,
        then wait for the thing to move.
        """
        before = self.s.rb(read)
        self.s.tap(button, hold=4, gap=8)
        for _ in range(tries):
            self.s.tick(6)
            now = self.s.rb(read)
            if now != before:
                return now
        return self.s.rb(read)

    def open_pack(self, tries: int = 8) -> bool:
        """Open the pack from the START menu, by trying rows and checking.

        The row is not fixed and cannot be counted to. The START menu grows --
        no POKeDEX or POKeGEAR early on -- so PACK sits at a different index
        depending on how far the game has got. `backup.py` learned the same
        thing about SAVE, and solved it there by knowing that SAVE is always
        third from the end; PACK has no such anchor, so this drives to a row,
        presses A, and asks whether the pack's own box is what appeared.
        """
        for row in range(1, tries + 1):
            self.close_menus()
            self.open_start_menu()
            count = self.menu_row_count()
            if not count:
                return False
            if row > count:
                return False
            if not self.drive_menu_cursor(row, count):
                continue
            self.s.tap("a")
            self.s.tick(30)
            if self.await_box(S.BOX_PACK, tries=12):
                return True
        return False

    def reach_pocket(self, pocket: int, tries: int = 8) -> bool:
        """Walk the open pack to `pocket`.

        A walk rather than an assumption: the pack remembers which pocket it
        was left in, so where it opens depends on what happened last time.
        """
        if self.s.rb("wCurPocket") == pocket:
            return True
        for _ in range(tries):
            if self._pack_moved("right", "wCurPocket") == pocket:
                return True
        return self.s.rb("wCurPocket") == pocket

    def reach_item(self, item_id: int, tries: int = 24) -> bool:
        """Walk the open pocket's cursor onto `item_id`.

        DOWN past the last entry lands on CANCEL and *stays* there -- the list
        does not wrap -- so an overshoot is walked back once rather than
        pressed through, and a second miss is reported instead of flailing.
        """
        if self.s.rb("wCurItem") == item_id:
            return True
        for _ in range(tries):
            cur = self.s.rb("wCurItem")
            if cur == S.CANCEL_ITEM:
                self.s.tap("up", hold=4, gap=8)
                self.s.tick(8)
                return self.s.rb("wCurItem") == item_id
            if self._pack_moved("down", "wCurItem") == item_id:
                return True
        return self.s.rb("wCurItem") == item_id

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
        if not self.reach_pocket(self.BALL_POCKET):
            self.close_menus(4)
            return False
        if not self.reach_item(ball_id, tries=12):
            self.close_menus(4)
            return False
        self.s.tap("a")
        self.s.tick(40)        # USE / QUIT submenu, cursor starts on USE
        self.s.tap("a")
        self.s.tick(40)
        return True

    def use_item_in_battle(self, item_id: int, on: int = 0) -> bool:
        """From the battle menu: PACK -> ITEM pocket -> item -> USE -> whom.

        The same walk as `throw_ball` down to the pocket, then two boxes more.
        A ball is thrown at whatever is on the field and needs no target; a
        Potion has to be given to somebody, so there is a USE/QUIT confirm and
        then a party list.

        The battle pack takes the ordinary six-frame tap throughout -- measured,
        and worth stating because the *field* pack does not (see
        `FIELD_PACK_HOLD`). Two packs, two answers, and assuming they were one
        is what made the first version of this press at a box for eight frames
        and report success.

        Whether the party list appears depends on the item: a Full Heal on a
        one-Pokemon party still asks, but an X Attack does not. So it is waited
        for and skipped rather than required.
        """
        if not self.choose_battle_action(PACK):
            return False
        self.s.tick(60)
        if not self.reach_pocket(self.ITEM_POCKET):
            self.close_menus()
            return False
        if not self.reach_item(item_id):
            self.close_menus()
            return False
        self.s.tap("a")
        # USE / QUIT. Skipping this is what the first version did, and then
        # every press "landed" while the item was never used: the pack was
        # sitting on an unconfirmed submenu waiting for exactly this A.
        if not self.await_box(S.BOX_BATTLE_ITEM, tries=20):
            self.close_menus()
            return False
        if not self.drive_menu_cursor(S.PACK_USE_ROW, 2):
            self.close_menus()
            return False
        self.s.tap("a")
        if self.await_box(S.BOX_PARTY_PICK, tries=20):
            if not self.drive_menu_cursor(on + 1, S.MAX_PARTY):
                self.close_menus()
                return False
            self.s.tap("a")
        self.s.tick(40)
        return True

    def use_item_on(self, item_id: int, on: int = 0) -> bool:
        """From the overworld: START -> PACK -> ITEM pocket -> item -> USE -> whom.

        Four boxes deep, every one of them confirmed by its shape before
        anything is pressed into it. This reports only whether the presses
        landed; whether the item *did* anything is a question about HP and the
        status byte, and `travel.py` asks it -- because measured at full HP the
        game takes every press, says the item would have no effect, spends
        nothing, and drops back to the pack. A press-counting version calls
        that a heal.
        """
        if not self.open_pack():
            self.close_menus()
            return False
        if not self.reach_pocket(self.ITEM_POCKET):
            self.close_menus()
            return False
        if not self.reach_item(item_id):
            self.close_menus()
            return False
        self._pack_confirm()
        if not self.await_box(S.BOX_ITEM_USE, tries=14):
            self.close_menus()
            return False
        if not self.drive_menu_cursor(S.PACK_USE_ROW, 4):
            self.close_menus()
            return False
        self._pack_confirm()
        if not self.await_box(S.BOX_PARTY_PICK, tries=20):
            self.close_menus()
            return False
        if not self.drive_menu_cursor(on + 1, S.MAX_PARTY):
            self.close_menus()
            return False
        self._pack_confirm()
        self.past_the_message()
        self.close_menus()
        return True

    def _pack_confirm(self) -> None:
        """One A press inside the field pack, held long enough to land.

        See `FIELD_PACK_HOLD`: the ordinary six-frame tap is swallowed here.
        """
        self.s.tap("a", hold=S.FIELD_PACK_HOLD, gap=S.FIELD_PACK_GAP)

    def past_the_message(self, taps: int = 20) -> bool:
        """Tap through the message a use leaves, and *only* the message.

        Not `advance_text`, which taps A for as long as the game keeps asking --
        and after a heal the pack is still open and still asking. With two
        Potions in the bag that press lands on the next item and uses it, which
        is the stray-press failure `_watch_throw` has warned about since it was
        written. So the stopping condition is a shape rather than a flag: stop
        as soon as the box on screen is the pack or the party list again,
        because those are boxes to back out of and not text to advance.
        """
        for _ in range(taps):
            if not self.window_open() and not self.script_running():
                return True
            if self.is_box(S.BOX_PACK) or self.is_box(S.BOX_PARTY_PICK):
                return True
            self.s.tap("a", hold=4, gap=8)
        return False

    # --- the counter -------------------------------------------------------
    def buy_from_clerk(self, item_id: int, count: int = 1) -> bool:
        """Buy `count` of an item from the clerk already being talked to.

        Five boxes, each confirmed by its shape. The caller is responsible for
        standing in front of a clerk and pressing A; this drives what appears.

        One press at a time rather than a jump to a quantity, deliberately. The
        quantity box takes UP to raise the count and it *wraps* at ninety-nine,
        so an overshoot does not stop at the top -- it buys ninety-nine of
        something. Counting presses up from one is the only version where being
        wrong costs one item rather than the whole wallet.
        """
        if not self.await_box(S.BOX_SHOP_MENU, tries=30):
            return False
        if not self.drive_menu_cursor(1, 3):        # BUY
            return False
        self.s.tap("a")
        if not self.await_box(S.BOX_SHOP_LIST, tries=30):
            return False
        if not self.reach_item(item_id):
            return False
        self.s.tap("a")
        if not self.await_box(S.BOX_SHOP_HOW_MANY, tries=30):
            return False
        for _ in range(max(0, count - 1)):
            self.s.tap("up", hold=4, gap=8)
            self.s.tick(6)
        self.s.tap("a")
        # "That'll be N. OK?" is a real YesNoBox, so the hook-backed answer
        # applies and the cursor is driven rather than assumed.
        if not self.await_box(S.BOX_SHOP_CONFIRM, tries=30):
            return False
        if not self.answer_yes_no(True):
            return False
        self.advance_text(max_taps=30, quiet_frames=60)
        return True

    # --- start menu / saving ----------------------------------------------
    def open_start_menu(self) -> None:
        self.s.tap("start", hold=8, gap=12)
        self.s.tick(20)

    def close_menus(self, times: int = 16) -> bool:
        """Press B until nothing is open, rather than a fixed number of times.

        Six was not even the wrong number: a box swallows presses while it
        animates, so the count that closes three levels of menu is not three,
        or six, or any number. The old version pressed six times and hoped,
        which fails in both directions -- it leaves a box open when presses were
        eaten, and when they were not, the spare presses land in the world. In
        grass that starts a wild battle, which is how "the menus are closed now"
        became "the pilot is in a fight it did not ask for".

        Press, look, stop when it is shut.

        The cap is generous on purpose, and that is the point of checking rather
        than counting: it costs nothing when one press is enough, and a cap that
        is too small silently leaves a menu open. Measured after a heal --
        party list inside the pack inside the START menu -- it takes ten
        presses, so eight was still not enough.

        A few frames after each press, so the next one is not aimed at a box
        that is still animating, which is what eats them in the first place.

        **The signal does not cover the battle's own screens.** Measured:
        standing in MoveSelectionScreen, `wWindowStackSize` reads 0 -- the move
        list is not a window on that stack -- so this returns at once having
        pressed nothing, and the caller is still in move select believing it is
        back at the battle menu. Use `back_out` there. Everything reached
        through the pack, the START menu or a textbox is on the stack and is
        this method's business.
        """
        for _ in range(times):
            if not self.window_open():
                self.s.tick(20)
                return True
            self.s.tap("b", hold=4, gap=6)
            self.s.tick(8)
        self.s.tick(20)
        return not self.window_open()

    def back_out(self, times: int = 6) -> None:
        """Press B a fixed number of times, for screens the stack cannot see.

        The counting version, kept deliberately and used only where checking is
        impossible: inside a battle, `wWindowStackSize` reads 0 for move select
        and for the battle menu itself, so `close_menus` cannot tell "backed out
        far enough" from "nothing was ever open". Somewhere has to press and
        hope, and it is better that it be one named method than the default.
        """
        for _ in range(times):
            self.s.tap("b", hold=4, gap=6)
            self.s.tick(8)
