"""Typed reads of live game state, via symbol-resolved WRAM addresses."""
from __future__ import annotations

from dataclasses import dataclass

from . import items as I
from . import symbols as S
from .gamedata import GameData
from .session import Session


@dataclass
class Mon:
    slot: int              # 0-based party index
    species: int
    species_name: str
    level: int
    hp: int
    max_hp: int
    status: int
    moves: list[int]
    pp: list[int]

    @property
    def fainted(self) -> bool:
        return self.hp == 0

    @property
    def hp_frac(self) -> float:
        return (self.hp / self.max_hp) if self.max_hp else 0.0

    @property
    def status_name(self) -> str:
        if self.status & S.STATUS_BITS["SLP"]:
            return "SLP"
        for name in ("FRZ", "PAR", "BRN", "PSN"):
            if self.status & S.STATUS_BITS[name]:
                return name
        return "OK"

    def usable_moves(self, gd: GameData) -> list[tuple[int, int, dict]]:
        """[(move_index, current_pp, move_info)] for moves with PP left."""
        out = []
        for i, (mid, pp) in enumerate(zip(self.moves, self.pp)):
            cur = pp & 0x3F
            if mid and cur > 0:
                out.append((i, cur, gd.move(mid)))
        return out

    def describe(self, gd: GameData) -> str:
        mv = ", ".join(
            f"{gd.move_name(m)}({p & 0x3F})" for m, p in zip(self.moves, self.pp) if m
        )
        return (f"{self.species_name} Lv{self.level} "
                f"{self.hp}/{self.max_hp}HP {self.status_name} [{mv}]")


@dataclass
class BattleState:
    mode: int
    enemy_species: int
    enemy_name: str
    enemy_level: int
    enemy_hp: int
    enemy_max_hp: int
    active_slot: int       # wCurPartyMon, 0-based
    active_species: int
    active_level: int
    active_hp: int
    active_max_hp: int
    active_moves: list[int]
    active_pp: list[int]
    enemy_dvs: tuple[int, int] = (0, 0)

    @property
    def enemy_shiny(self) -> bool:
        return S.dvs_are_shiny(*self.enemy_dvs)

    @property
    def ready(self) -> bool:
        """True once the battle structs are populated and safe to read."""
        return bool(self.enemy_max_hp and self.active_max_hp)

    @property
    def in_battle(self) -> bool:
        return self.mode != S.BATTLE_NONE

    @property
    def is_wild(self) -> bool:
        return self.mode == S.BATTLE_WILD

    @property
    def is_trainer(self) -> bool:
        return self.mode == S.BATTLE_TRAINER


@dataclass
class Location:
    group: int
    number: int
    x: int
    y: int

    @property
    def key(self) -> tuple[int, int]:
        return (self.group, self.number)

    def __str__(self) -> str:
        return f"map {self.group}.{self.number} @ ({self.x},{self.y})"


class GameStateReader:
    def __init__(self, session: Session, gamedata: GameData):
        self.s = session
        self.gd = gamedata

    # --- names ---------------------------------------------------------------
    def player_name(self) -> str:
        return S.decode_text(self.s.rbytes("wPlayerName", S.PLAYER_NAME_LENGTH))

    def nickname(self, slot: int) -> str:
        """What this party member is called.

        Equal to the species name unless something named it -- which is how a
        run that mashed A through a "give it a nickname?" prompt gives itself
        away.
        """
        base = self.s.sym.addr("wPartyMonNicknames") + slot * S.MON_NAME_LENGTH
        return S.decode_text(self.s.rbytes(base, S.MON_NAME_LENGTH))

    # --- party -------------------------------------------------------------
    def party_count(self) -> int:
        return min(self.s.rb("wPartyCount"), S.MAX_PARTY)

    def mon(self, slot: int) -> Mon:
        base = self.s.sym.addr("wPartyMon1") + slot * S.PARTY_STRUCT_LEN
        species = self.s.rb(base + S.MON_SPECIES)
        return Mon(
            slot=slot,
            species=species,
            species_name=self.gd.species_name(species),
            level=self.s.rb(base + S.MON_LEVEL),
            hp=self.s.rw(base + S.MON_HP),
            max_hp=self.s.rw(base + S.MON_MAXHP),
            status=self.s.rb(base + S.MON_STATUS),
            moves=self.s.rbytes(base + S.MON_MOVES, S.NUM_MOVES),
            pp=self.s.rbytes(base + S.MON_PP, S.NUM_MOVES),
        )

    def party(self) -> list[Mon]:
        return [self.mon(i) for i in range(self.party_count())]

    def find_in_party(self, species_id: int) -> Mon | None:
        for m in self.party():
            if m.species == species_id:
                return m
        return None

    def party_healthy(self) -> bool:
        return any(not m.fainted for m in self.party())

    # --- battle ------------------------------------------------------------
    def battle(self) -> BattleState:
        s = self.s
        enemy_species = s.rb("wEnemyMonSpecies")
        return BattleState(
            mode=s.rb("wBattleMode"),
            enemy_species=enemy_species,
            enemy_name=self.gd.species_name(enemy_species),
            enemy_level=s.rb("wEnemyMonLevel"),
            enemy_hp=s.rw("wEnemyMonHP"),
            enemy_max_hp=s.rw("wEnemyMonMaxHP"),
            active_slot=s.rb("wCurPartyMon"),
            active_species=s.rb("wBattleMonSpecies"),
            active_level=s.rb("wBattleMonLevel"),
            active_hp=s.rw("wBattleMonHP"),
            active_max_hp=s.rw("wBattleMonMaxHP"),
            active_moves=s.rbytes("wBattleMonMoves", S.NUM_MOVES),
            active_pp=s.rbytes("wBattleMonPP", S.NUM_MOVES),
            enemy_dvs=tuple(s.rbytes("wEnemyMonDVs", 2)),
        )

    def in_battle(self) -> bool:
        return self.s.rb("wBattleMode") != S.BATTLE_NONE

    # --- overworld ---------------------------------------------------------
    def location(self) -> Location:
        s = self.s
        return Location(
            group=s.rb("wMapGroup"),
            number=s.rb("wMapNumber"),
            x=s.rb("wXCoord"),
            y=s.rb("wYCoord"),
        )

    # --- bag ---------------------------------------------------------------
    def pocket(self, which: int) -> list[tuple[int, int]]:
        """One pack pocket as [(item_id, quantity)].

        Every pocket has the same shape -- a count of *kinds* followed by
        (item, quantity) pairs -- so there is one reader rather than one per
        pocket. The count is kinds, not units: five Poke Balls and three Great
        Balls is a count of two.

        The per-pocket limit is the game's own (`MAX_ITEMS` 20, `MAX_BALLS` 12),
        and it is a real bound rather than a defensive one. Reading twenty
        entries out of the ball pocket walks eight entries past the end of
        `wBalls` and reports whatever `wNumKeyItems` and the key items after it
        happen to hold as balls -- which is worse than reporting nothing,
        because the numbers look plausible.
        """
        count_sym, list_sym = I.POCKET_SYMBOLS[which]
        limit = I.POCKET_LIMITS[which]
        n = min(self.s.rb(count_sym), limit)
        base = self.s.sym.addr(list_sym)
        out = []
        for i in range(n):
            item = self.s.rb(base + i * 2)
            if item in (0, 0xFF):
                break
            out.append((item, self.s.rb(base + i * 2 + 1)))
        return out

    def balls(self) -> list[tuple[int, int]]:
        """The BALL pocket as [(item_id, quantity)]."""
        return self.pocket(I.BALL_POCKET)

    def items(self) -> list[tuple[int, int]]:
        """The ITEM pocket as [(item_id, quantity)].

        The pocket next to the one this pilot has always read. Everything that
        heals, cures or is worth picking up off a route lives here, so without
        it a Potion in the bag is invisible and the only answer to a hurt party
        is the walk to a Pokemon Center.
        """
        return self.pocket(I.ITEM_POCKET)

    def ball_count(self, item_id: int) -> int:
        return next((q for i, q in self.balls() if i == item_id), 0)

    def item_count(self, item_id: int) -> int:
        return next((q for i, q in self.items() if i == item_id), 0)

    def carrying(self, name: str) -> int:
        """How many of an item named by its constant, in whichever pocket holds it.

        Saves every caller from knowing which pocket a name lives in, which is
        the one detail about the bag that is genuinely arbitrary: POKE_BALL is
        in the ball pocket and BERRY_JUICE is not, and nothing about either name
        says so.
        """
        iid = self.gd.item_id(name)
        which = I.attributes(self.gd.root_str).get(name, {}).get(
            "pocket", I.ITEM_POCKET)
        if which not in I.POCKET_SYMBOLS:
            return 0
        return next((q for i, q in self.pocket(which) if i == iid), 0)

    # --- event flags -------------------------------------------------------
    def event_done(self, name: str) -> bool | None:
        """Has this EVENT_* flag been set? None when it cannot be told.

        Bit-indexed into wEventFlags: byte `index // 8`, bit `index % 8`.

        This is the thing the mobile port could not do, and said so honestly --
        over there an item ball that has already been taken is still in the
        object list, so the only way to find out whether anything is left is to
        walk over and press A. Here the disassembly names the flag each object
        carries, so the question can be answered before the walk.

        `None` rather than False for a flag this build does not name, because
        "already taken" and "cannot tell" lead to different decisions: the
        first skips the walk and the second has to make it.
        """
        index = self.gd.events.get(name)
        if index is None:
            return None
        byte = self.s.rb(self.s.sym.addr("wEventFlags") + index // 8)
        return bool(byte & (1 << (index % 8)))

    def money(self) -> int:
        """How much the player is carrying.

        Three bytes, big-endian, plain binary -- not the packed BCD that Gen 1
        used. `bigdt MAX_MONEY` in engine/events/money.asm is the tell, and
        MAX_MONEY is 999,999, which needs 20 bits and so cannot be BCD in three
        bytes. Decoding it as BCD reads ¥1,000 as ¥232.
        """
        hi, mid, lo = self.s.rbytes("wMoney", 3)
        return (hi << 16) | (mid << 8) | lo

    def tile_collision(self) -> int:
        """Collision value of the tile the player stands on (wPlayerTileCollision)."""
        return self.s.rb("wPlayerTileCollision")

    def on_grass(self) -> bool:
        """True on an encounter tile -- the same values CheckGrassCollision uses."""
        return self.tile_collision() in S.GRASS_COLLISION

    def facing(self) -> int:
        return self.s.rb("wPlayerDirection")

    def summary(self) -> str:
        if self.in_battle():
            b = self.battle()
            kind = "wild" if b.is_wild else "trainer"
            return (f"[{kind} battle] enemy {b.enemy_name} Lv{b.enemy_level} "
                    f"{b.enemy_hp}/{b.enemy_max_hp} | me Lv{b.active_level} "
                    f"{b.active_hp}/{b.active_max_hp}")
        party = ", ".join(f"{m.species_name} Lv{m.level}" for m in self.party())
        return f"[overworld] {self.location()} | party: {party or 'empty'}"
