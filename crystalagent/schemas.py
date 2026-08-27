"""Typed contracts at the machine boundaries: observe()/game_state()/route()
payloads, NDJSON request/reply envelopes, autopilot decisions, journal lines.

Strategy: functions KEEP returning plain dicts -- the models are built
internally so construction is validated and every consumer keeps its exact
JSON shape. A field type drift becomes a loud ValidationError at the
boundary instead of silent garbage downstream."""

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- game_state() -----------------------------------------------------------

class LocationState(_Strict):
    map_group: int
    map_number: int
    map: str
    x: int
    y: int


class PlayerState(_Strict):
    name: str
    rival: str
    money: int
    johto_badges: list[str]
    kanto_badges: list[str]


class PartyMoveGS(_Strict):
    name: str
    pp: int


class PartyMonGS(_Strict):
    species: int
    name: str
    egg: bool
    dvs: list[int]
    shiny: bool
    form: Optional[str] = None
    nickname: str
    level: int
    hp: int
    max_hp: int
    status: list[str]
    item: Optional[str] = None
    moves: list[PartyMoveGS]


class EnemyMon(_Strict):
    species: int
    name: str
    shiny: bool
    form: Optional[str] = None
    level: int
    hp: int
    max_hp: int


class EnemyObs(_Strict):
    species: int
    name: str
    level: int
    hp: int
    max_hp: int
    types: list[str] = []


class BattleState(_Strict):
    mode: Union[str, int]
    enemy: EnemyMon


class GameStateOut(_Strict):
    frame: int
    play_time: str
    location: LocationState
    player: PlayerState
    party: list[PartyMonGS]
    battle: Optional[BattleState]
    screen: Optional[list[str]] = None


# -- Driver.observe() -------------------------------------------------------

class PartyMoveObs(_Strict):
    name: str
    pp: int
    max_pp: int


class PartyMonObs(_Strict):
    species: str
    nick: str
    level: int
    hp: int
    max_hp: int
    status: Optional[str] = None
    moves: list[PartyMoveObs]
    egg: bool = False


class UIState(_Strict):
    textbox: bool
    battle: bool


class SpriteObs(_Strict):
    """A live wObjectStructs slot: walk-cell coords, SPRITEMOVEDATA_*
    movement type. Slot 0 is the player."""
    slot: int
    map_x: int
    map_y: int
    movement: int


class ObserveOut(_Strict):
    map: str
    group: int
    number: int
    x: int
    y: int
    tiles: dict[str, str]
    party: list[PartyMonObs]
    bag: dict[str, int]
    money: int
    badges: list[str]
    flags: dict[str, bool]
    npcs: list[list[int]]
    sprites: list[SpriteObs] = []   # live positions incl. player (slot 0)
    ui: UIState
    enemy: Optional[EnemyObs] = None   # present only while battling
    frame: int


# -- route steps ------------------------------------------------------------

class RouteStep(BaseModel):
    """walk legs carry {kind,map,x,y}; warp/connection legs carry from/to/dir
    plus edge detail -- those stay open via extra='allow'."""
    kind: Literal["walk", "warp", "connection"]
    model_config = ConfigDict(extra="allow")


# -- NDJSON envelopes -------------------------------------------------------

class ServeRequest(BaseModel):
    id: Optional[int] = None
    cmd: str
    args: dict = Field(default_factory=dict)


class ServeReply(BaseModel):
    id: Optional[int] = None
    ok: bool
    data: object = None
    error: Optional[str] = None


# -- autopilot decisions ----------------------------------------------------

class ActionSpec(_Strict):
    name: str
    kwargs: dict = {}


class SuccessSpec(_Strict):
    map: Optional[str] = None
    min_badges: Optional[int] = None
    flag: Optional[str] = None


class DecisionArgs(_Strict):
    action: ActionSpec
    goal: Optional[str] = None
    risky: bool = False
    success: SuccessSpec = Field(default_factory=SuccessSpec)


# -- journal lines ----------------------------------------------------------

class JournalCycle(_Strict):
    frame: int
    lead_lv: Optional[int] = None
    obs_digest: object
    action: dict
    goal: Optional[str] = None
    ok: bool
    used: Optional[int] = None
    error: Optional[str] = None
    why: Optional[list[str]] = None
    t: Optional[str] = None


# -- validators -------------------------------------------------------------

def validate_game_state(s: dict, include_screen: bool = False) -> dict:
    if include_screen:
        GameStateOut(**s)
    else:
        GameStateOut(**{k: v for k, v in s.items() if k != "screen"})
    return s


def validate_observe(obs: dict) -> dict:
    ObserveOut(**obs)
    return obs


def validate_route(steps: list) -> list:
    for step in steps:
        RouteStep(**step)
    return steps


def validate_decision(args: dict) -> dict:
    DecisionArgs(**args)
    return args


def validate_cycle_record(record: dict) -> dict:
    JournalCycle(**record)
    return record
