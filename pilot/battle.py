"""Battle engine: plays out one battle according to a policy.

Battle state (wEnemyMon*, wBattleMon*) is only valid once the game is actually
asking for a decision, so every read happens after the `BattleMenu` hook fires
rather than the moment wBattleMode flips -- reading earlier returns the previous
battle's leftovers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import items as I
from .control import FIGHT, PKMN, RUN, TEXT_EVENTS

# Hook event -> the decision name run() dispatches on. Kept as one explicit map
# so the two can never drift apart; they did, and a mismatched name meant the
# learn-move prompt fell through unhandled and got mashed through with A.
DECISION_EVENTS = {
    "battle_menu": "menu",
    "move_select": "move",
    "learn_move": "learn",
    "evolve": "evolve",
    "choose_mon": "replace",   # our mon fainted; the game wants the next one
    "lost_battle": "lost",
}


@dataclass
class BattlePolicy:
    flee_below: float = 0.35        # bail out under this HP fraction
    always_flee: bool = False       # leave every wild battle, whatever the HP
    allow_evolution: bool = True
    learn_new_moves: bool = False   # when all 4 slots are full
    switch_to_target: bool = True
    fight_if_cornered: bool = True   # if we cannot escape, win instead
    # Drink a Potion mid-fight at or under this HP fraction, if one is carried.
    #
    # Above `flee_below` on purpose. The order the two thresholds are checked
    # in is the feature: reaching for the bag at 45% and fleeing at 35% means a
    # fight that can still be won gets healed and won, while the flee is what
    # answers an empty bag. Setting this *below* flee_below would make it dead
    # code, because the flee fires first.
    heal_below: float = 0.45
    use_items: bool = True          # False for a run that must not spend items
    # A trainer battle cannot be fled, so the bag is the only answer there --
    # and running out of Potions mid-sweep is how a trainer sweep blacks out.
    # Bounded so a refused item cannot loop the turn counter away.
    max_heals: int = 6


@dataclass
class BattleOutcome:
    result: str = "unknown"   # won | fled | lost | ended | timeout
    turns: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.notes.append(msg)


class BattleEngine:
    def __init__(self, session, reader, control, gamedata, policy=None, log=print):
        self.s = session
        self.r = reader
        self.c = control
        self.gd = gamedata
        self.p = policy or BattlePolicy()
        self.log = log
        self._cornered = False
        self._heals_used = 0

    # --- move choice -------------------------------------------------------
    def rank_moves(self, battle) -> list[tuple[int, dict, float]]:
        """[(index, move_info, score)] best first, only moves with PP left."""
        ranked = []
        for i, (mid, pp) in enumerate(zip(battle.active_moves, battle.active_pp)):
            cur = pp & 0x3F
            if not mid or cur == 0:
                continue
            info = self.gd.move(mid)
            if self.gd.is_damaging(mid):
                # Expected damage proxy; fixed-damage moves get a modest score.
                power = info["power"] or 40
                score = power * max(info["accuracy"], 30) / 100.0
            else:
                score = 0.5   # status moves: only if nothing else is available
            ranked.append((i, info, score, cur))
        # Among moves of similar power, prefer the one with more PP left. That
        # spreads usage instead of draining the first slot to zero and forces
        # fewer trips to a Pokemon Center to restore PP.
        ranked.sort(key=lambda t: (round(t[2] / 5), t[3]), reverse=True)
        return [(i, info, score) for i, info, score, _pp in ranked]

    # --- the pump ----------------------------------------------------------
    def next_decision(self, max_frames: int = 6000) -> str:
        """Advance the battle until it wants a decision, tapping through text.

        Events are drained (never cleared) so nothing that fires between ticks
        is lost. If the game goes quiet without asking anything, we nudge with A
        -- that only happens when a textbox was entered before we started
        listening.
        """
        self.s.drain_events()
        quiet = 0
        spent = 0
        while spent < max_frames:
            self.s.tick(4)
            spent += 4
            evs = set(self.s.drain_events())
            # First, because a capture ends the battle with the nickname box
            # still on screen: check in_battle before this and the box is left
            # for the next A tap to answer with its default of YES.
            if self.c.nickname_prompt(evs):
                continue
            # **Before the "is it over?" check, not after.** Blacking out ends
            # the battle, so `wBattleMode` clears on the same tick `LostBattle`
            # fires -- and an "is it over?" test that runs first returns
            # "ended", which `run` maps to *won*. Measured: a Lv14 lead on 1HP
            # against a Lv60 enemy, the party wiped, `mon_fainted` and
            # `lost_battle` both fired, and the engine reported `won`.
            #
            # That is the worst available way to be wrong. The pilot cannot
            # tell winning from blacking out, `grind` counts the loss in
            # `stats["won"]`, and the only outward sign is that the next
            # `_ensure_grass` fails because a white-out has moved the player to
            # a Pokemon Center and healed the party -- so even the HP looks
            # like a win.
            if "lost_battle" in evs:
                return "lost"
            if not self.r.in_battle():
                return "ended"
            if "battle_menu" in evs:
                # Confirm the menu really opened before calling it a decision
                # point -- the hook can fire while battle text is still up.
                if self.c._await_menu_cursor():
                    return "menu"
                continue
            for key, decision in DECISION_EVENTS.items():
                if key in evs:
                    return decision
            if evs & set(TEXT_EVENTS):
                self.s.tap("a", hold=4, gap=4)
                spent += 8
                quiet = 0
            else:
                quiet += 4
                if quiet >= 90:
                    self.s.tap("a", hold=4, gap=4)
                    spent += 8
                    quiet = 0
        return "timeout"

    # --- the battle --------------------------------------------------------
    def run(self, target_slot: int | None = None, max_turns: int = 60,
            menu_open: bool = False) -> BattleOutcome:
        """Play out the current battle.

        `menu_open` says the battle menu is already up because the caller
        already pumped to it. That matters: the BattleMenu hook fires once per
        turn, so a second pump would never see it and would fall back to
        nudging with A -- which silently picks FIGHT instead of the action the
        policy asked for.
        """
        out = BattleOutcome()
        # Reset per battle: "cannot escape" and "how many Potions have gone
        # into this fight" are both facts about this fight.
        self._cornered = False
        self._heals_used = 0
        pending = "menu" if menu_open else None
        if not self.r.in_battle():
            out.result = "ended"
            return out
        while self.r.in_battle() and out.turns < max_turns:
            if pending is not None:
                what, pending = pending, None
            else:
                what = self.next_decision()
            if what == "ended":
                out.result = "won"
                break
            if what == "timeout":
                out.result = "timeout"
                out.note("battle stopped responding")
                break
            if what == "menu":
                out.turns += 1
                action = self._decide(target_slot, out)
                if action == "flee":
                    if self._try_flee(out):
                        out.result = "fled"
                        break
                    if self.p.fight_if_cornered:
                        # Escape failed. Carrying on trying is a livelock: the
                        # turns run out having done nothing while the wild mon
                        # keeps attacking. Win the fight instead.
                        self._cornered = True
                        out.note("could not escape; fighting it out instead")
                    continue
                if action == "heal":
                    self._heal_in_battle(out)
                    continue
                if action == "switch":
                    self._switch_to(target_slot, out)
                    continue
                self._attack(out)
            elif what == "move":
                self._pick_move(out)
            elif what == "learn":
                self._handle_learn_move(out)
            elif what == "evolve":
                self._handle_evolution(out)
            elif what == "replace":
                sent = self._send_next_mon(out)
                if sent == "wiped":
                    out.result = "lost"
                    break
                if sent == "stuck":
                    # Not a loss. The battle is still running and the pilot
                    # cannot drive it, which is what "gave up" means here.
                    out.result = "timeout"
                    break
            elif what == "lost":
                out.result = "lost"
                out.note("blacked out")
                break
        else:
            if out.turns >= max_turns:
                out.result = "timeout"
                out.note(f"exceeded {max_turns} turns")
        if out.result == "unknown":
            out.result = "won" if not self.r.in_battle() else "ended"
        # Post-battle: level-up prompts and evolutions happen after the fight.
        self._settle_post_battle(out)
        return out

    def _decide(self, target_slot: int | None, out: BattleOutcome) -> str:
        b = self.r.battle()
        if self._cornered:
            return "fight"
        if (self.p.switch_to_target and target_slot is not None
                and b.active_slot != target_slot):
            tgt = self.r.mon(target_slot)
            if not tgt.fainted:
                return "switch"
        if b.is_wild and self.p.always_flee:
            return "flee"
        frac = (b.active_hp / b.active_max_hp) if b.active_max_hp else 1.0
        # The bag before the flee, and above the flee threshold. A fight that
        # can still be won is worth a Potion; the flee is what answers an empty
        # bag. Checked the other way round, the flee fires first and the bag is
        # never opened -- which is what happened before this existed, and why a
        # trainer sweep with Potions in the bag still blacked out: a trainer
        # battle cannot be fled at all, so "flee" meant "keep fighting at 20%".
        if self._should_heal(frac):
            healer = self._battle_healer(b)
            if healer is not None:
                return "heal"
        if frac < self.p.flee_below and b.is_wild:
            out.note(f"fleeing at {b.active_hp}/{b.active_max_hp} HP")
            return "flee"
        return "fight"

    def _should_heal(self, frac: float) -> bool:
        return (self.p.use_items and self._heals_used < self.p.max_heals
                and frac <= self.p.heal_below)

    def _battle_healer(self, b) -> str | None:
        """The least wasteful HP item in the bag that works in a battle.

        Battle-usable is a different list from field-usable -- a Berry heals HP
        and is `ITEMMENU_NOUSE` outside a fight -- so the menu field that
        applies here is asked for by name rather than assumed to be the same.
        """
        missing = b.active_max_hp - b.active_hp
        if missing <= 0:
            return None
        amounts = I.healers(self.gd.root_str, in_battle=True)
        held = [(n, a) for n, a in amounts.items() if self.r.carrying(n) > 0]
        if not held:
            return None
        enough = [(n, a) for n, a in held if a is None or a >= missing]
        pool = enough or held
        if enough:
            return min(pool, key=lambda na: (na[1] is None, na[1] or 0, na[0]))[0]
        return max(pool, key=lambda na: (na[1] or 0, na[0]))[0]

    def _heal_in_battle(self, out: BattleOutcome) -> None:
        """Drink something, and report by HP rather than by the presses.

        The same standard the field pack holds itself to: at full HP the game
        takes every press, says the item would have no effect and spends
        nothing. The turn is consumed either way, so a heal that moved no HP is
        counted against the budget -- otherwise a refused item is an infinite
        turn.
        """
        b = self.r.battle()
        name = self._battle_healer(b)
        if name is None:
            return
        self._heals_used += 1
        before = b.active_hp
        self.c.use_item_in_battle(self.gd.item_id(name), b.active_slot)
        after = self.r.battle().active_hp
        if after > before:
            out.note(f"{name} mid-fight: {before} -> {after} HP")
        else:
            out.note(f"{name} moved no HP; not trying it again")
            # Stop reaching for the bag this battle. The alternative is
            # spending every remaining turn on an item the game will not accept.
            self._heals_used = self.p.max_heals

    def _attack(self, out: BattleOutcome) -> None:
        self.s.clear_events()
        if not self.c.choose_battle_action(FIGHT):
            out.note("battle menu was not ready; re-syncing")
            return
        ev = self.s.await_event("move_select", timeout=300)
        if ev is None:
            # No move menu appeared: usually every move is out of PP, in which
            # case the game forces Struggle on its own.
            out.note("move menu did not open (Struggle or forced move)")
            return
        self._pick_move(out)

    def _pick_move(self, out: BattleOutcome) -> None:
        b = self.r.battle()
        ranked = self.rank_moves(b)
        if not ranked:
            out.note("no moves with PP; letting the game force Struggle")
            self.s.tap("a")
            return
        idx, info, _score = ranked[0]
        n_moves = sum(1 for m in b.active_moves if m)
        if not self.c.choose_move(idx, n_moves=n_moves):
            out.note(f"could not put the cursor on {info['name']} (slot {idx})")
        elif self.c.current_move_num() != idx:
            out.note(f"used slot {self.c.current_move_num()}, wanted {idx} "
                     f"({info['name']})")

    def _try_flee(self, out: BattleOutcome, attempts: int = 4) -> bool:
        for _ in range(attempts):
            if not self.c.choose_battle_action(RUN):
                out.note("could not put the battle cursor on RUN")
            what = self.next_decision(max_frames=1200)
            if what == "ended" or not self.r.in_battle():
                return True
            if what != "menu":
                continue
        out.note("could not escape")
        return False

    def _switch_to(self, slot: int, out: BattleOutcome) -> None:
        """Send out the grind target via the in-battle PKMN menu.

        Driven against the live cursor, which is `wMenuCursorY` here -- 1-based,
        one row per party member plus CANCEL, and it **wraps**.

        It used to press UP six times to normalise and then DOWN `slot` times,
        which is the defect `choose_move` was written to avoid and which this
        repo's own mutation self-check tests for by name. Wrapping is what makes
        it wrong: six UP presses on a two-row list return to where they started,
        on a four-row list they land two rows off, so whether the normalisation
        works at all depends on the size of the party. With a full party and the
        cursor left on CANCEL by a previous turn, it sends out the wrong
        Pokemon -- and the only sign is a grind that trains something else.
        """
        if not self.c.choose_battle_action(PKMN):
            # Returning, not noting and carrying on. If the cursor never
            # reached PKMN then the presses below land in an unknown menu, and
            # the first two are A -- which from the battle menu picks FIGHT and
            # then a move. Losing a turn is much better than that.
            out.note("could not put the battle cursor on PKMN; skipping the switch")
            return
        self.s.tick(30)
        rows = self.r.party_count() + 1        # the party, plus CANCEL
        if not self.c.drive_menu_cursor(slot + 1, rows):
            out.note(f"could not reach party slot {slot + 1} in the PKMN list")
            self.c.close_menus()
            return
        self.s.tap("a")
        self.s.tick(20)
        # The submenu offers SWITCH / STATS / CANCEL, with SWITCH first.
        self.s.tap("a")
        self.s.tick(30)
        b = self.r.battle()
        if b.active_slot != slot:
            out.note(f"switch to slot {slot} did not take (active={b.active_slot})")
            self.c.close_menus()

    def _send_next_mon(self, out: BattleOutcome) -> str:
        """Choose a replacement after a faint.

        -> "sent" | "wiped" | "stuck". Three answers rather than a bool,
        because the two ways of failing are not the same thing and the caller
        turned both into a blackout. A party with nothing left standing really
        has lost. A party list that would not draw, or a cursor that would not
        reach the slot, is the *pilot* failing to drive the game -- and
        recording that as a loss writes a defeat into the log that never
        happened, which is the defect `next_decision` had when a win and a wipe
        shared a code path.

        The cursor is driven and checked rather than counted, for the reason
        `_switch_to` sets out at length: this list wraps, so a press that misses
        leaves the arrow somewhere unknown, and the next press is A. This
        function used to hold its own copy of that loop without the check at the
        end, so a cursor that never arrived still got an A -- sending out
        whatever happened to be highlighted.
        """
        party = self.r.party()
        healthy = [m for m in party if not m.fainted]
        if not healthy:
            out.note("whole party fainted")
            return "wiped"
        slot = healthy[0].slot
        out.note(f"sending out {healthy[0].species_name} (slot {slot + 1})")
        if not self.c._await_menu_cursor():
            out.note("the party list never drew; not pressing anything into it")
            return "stuck"
        # 1-based, and the forced-switch list is the party with no CANCEL row:
        # there is no declining this one.
        if not self.c.drive_menu_cursor(slot + 1, len(party)):
            out.note(f"could not reach party slot {slot + 1} in the switch list")
            return "stuck"
        self.s.tap("a")
        self.s.tick(40)
        # A forced switch may still offer a confirm; a second A is harmless once
        # the mon is already out.
        if self.r.battle().active_slot != slot:
            self.s.tap("a")
            self.s.tick(40)
        # The active slot is the evidence, not the presses. Saying "sent" when
        # the game is still asking would put the outer loop straight back here.
        if self.r.battle().active_slot != slot:
            out.note(f"slot {slot + 1} did not come out "
                     f"(active={self.r.battle().active_slot})")
            return "stuck"
        return "sent"

    # --- prompts -----------------------------------------------------------
    def _tap_until(self, *events: str, max_taps: int = 40) -> bool:
        """Tap A through text until one of `events` fires."""
        for _ in range(max_taps):
            if self.s.has_event(*events):
                return True
            self.s.tap("a", hold=4, gap=8)
        return self.s.has_event(*events)

    def _handle_learn_move(self, out: BattleOutcome) -> None:
        """Answer the level-up "learn a new move?" prompts.

        LearnMove prints "<mon> is trying to learn <move>" and waits for A
        *before* it reaches the YesNoBox in ForgetMove, so the prompt has to be
        reached by tapping through that text -- waiting passively for the
        YesNoBox just stalls until the timeout, after which generic text-mashing
        answers the prompt with A and forgets whatever move is listed first.

        When a move slot is free there is no prompt at all: the move is simply
        learned, and there is nothing to decide.
        """
        before = [list(m.moves) for m in self.r.party()]
        self.s.clear_events()
        if not self._tap_until("yes_no", max_taps=40):
            self.c.advance_text(max_taps=80, quiet_frames=80)
            return          # free slot: learned outright, no decision needed

        if self.p.learn_new_moves:
            self.s.clear_events()
            self.c.answer_yes_no(yes=True)       # make room
            self.s.tick(40)
            self.c.choose_move(0)                # forget the first slot
            self.c.advance_text(max_taps=120, quiet_frames=90)
            out.note("accepted a new move (replaced the first slot)")
            return

        self.s.clear_events()
        self.c.answer_yes_no(yes=False)          # do not make room
        if self._tap_until("yes_no", max_taps=30):
            self.s.clear_events()
            self.c.answer_yes_no(yes=True)       # yes, stop learning
        self.c.advance_text(max_taps=120, quiet_frames=90)
        self.s.tick(30)                          # let the change commit
        after = [list(m.moves) for m in self.r.party()]
        if after != before:
            out.note("a new move replaced an existing one despite declining")
        else:
            out.note("declined a new move to keep the moveset intact")

    def _handle_evolution(self, out: BattleOutcome) -> None:
        if self.p.allow_evolution:
            # Deliberately does NOT mash through text here: EvolveAfterBattle
            # runs after every battle, and blanket A-presses would also answer
            # the level-up learn-move prompts (accepting them and forgetting
            # whatever move is first). The settle loop advances text one box at
            # a time so those prompts stay interceptable.
            return
        before = [m.species for m in self.r.party()]
        # EvolutionAnimation checks `hJoyDown & PAD_B` at one specific frame, so
        # B has to be *held* across the animation -- tapping it almost always
        # misses that frame and the evolution goes through anyway.
        self.s.pyboy.button_press("b")
        try:
            self.s.tick(300)
        finally:
            self.s.pyboy.button_release("b")
        self.c.advance_text(max_taps=120, quiet_frames=90)
        after = [m.species for m in self.r.party()]
        if after != before:
            evolved = ", ".join(
                f"{self.gd.species_name(b)} -> {self.gd.species_name(a)}"
                for b, a in zip(before, after) if a != b
            )
            out.note(f"could not cancel the evolution ({evolved})")
        elif self.s.rb("wBattleMode") == 0:
            out.note("cancelled evolution")

    def _settle_post_battle(self, out: BattleOutcome, max_frames: int = 4000) -> None:
        """Clear XP/level-up text, learn-move prompts and evolutions after a fight."""
        spent = 0
        self.s.drain_events()
        while spent < max_frames:
            self.s.tick(4)
            spent += 4
            evs = set(self.s.drain_events())
            if self.c.nickname_prompt(evs):
                continue
            if "learn_move" in evs:
                self._handle_learn_move(out)
                continue
            if "evolve" in evs:
                species_before = [m.species for m in self.r.party()]
                self._handle_evolution(out)
                if self.p.allow_evolution:
                    self.s.tick(8)
                    changed = [
                        f"{self.gd.species_name(b)} -> {self.gd.species_name(a)}"
                        for b, a in zip(species_before,
                                        [m.species for m in self.r.party()])
                        if a != b
                    ]
                    if changed:
                        out.note("evolved: " + ", ".join(changed))
                continue
            if evs & set(TEXT_EVENTS):
                self.s.tap("a", hold=4, gap=4)
                spent += 8
                continue
            if self.r.in_battle():
                continue
            if not self.c.script_running():
                return
        out.note("post-battle cleanup hit its frame cap")
