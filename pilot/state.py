"""Typed reads of live game state, via symbol-resolved WRAM addresses."""
from __future__ import annotations

from dataclasses import dataclass

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
    def balls(self) -> list[tuple[int, int]]:
        """The BALL pocket as [(item_id, quantity)].

        wNumBalls is the number of *kinds* of ball carried, not how many balls --
        the quantity is the second byte of each entry.
        """
        n = min(self.s.rb("wNumBalls"), 20)
        base = self.s.sym.addr("wBalls")
        out = []
        for i in range(n):
            item = self.s.rb(base + i * 2)
            if item in (0, 0xFF):
                break
            out.append((item, self.s.rb(base + i * 2 + 1)))
        return out

    def ball_count(self, item_id: int) -> int:
        return next((q for i, q in self.balls() if i == item_id), 0)

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
