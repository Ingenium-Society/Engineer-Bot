"""Pydantic models shared by the judge, the engine and the transport layer."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import config

Status = Literal["ACCEPT", "REJECT"]
Phase = Literal["IDLE", "TYPING", "JUDGING", "LOCKED", "DONE"]
JudgeMode = Literal["ai", "keyword", "manual"]
Source = Literal["ai", "keyword", "manual"]


class Verb(StrEnum):
    MAJU = "MAJU"
    MUNDUR = "MUNDUR"
    BELOK = "BELOK"
    AMBIL = "AMBIL"
    TUANG = "TUANG"
    TUNGGU = "TUNGGU"
    TAMBAH = "TAMBAH"
    ADUK = "ADUK"
    TARUH = "TARUH"
    INVALID = "INVALID"


class Command(BaseModel):
    """The only shape a translator may produce. Never trusted, always validated."""

    verb: Verb
    n: int | None = None
    arah: str | None = None
    objek: str | None = None
    target: str | None = None
    alasan: str | None = None

    @field_validator("arah", mode="before")
    @classmethod
    def _upper_direction(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper() or None
        return value

    @field_validator("objek", "target", mode="before")
    @classmethod
    def _canonical(cls, value: object) -> object:
        if isinstance(value, str):
            return config.canonical_object(value) or value.strip().lower() or None
        return value

    @field_validator("n", mode="before")
    @classmethod
    def _coerce_n(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return int(stripped)
            except ValueError:
                return None
        return value


class WorldState(BaseModel):
    """Everything the validator needs. Snapshotted for the admin 'Ulang' button."""

    x: int = config.START_X
    y: int = config.START_Y
    heading: str = config.START_HEADING
    holding: str | None = None
    has_cup: bool = False
    poured: bool = False
    filtered: bool = False
    has_jar: bool = False
    tablet_added: bool = False
    stirred: bool = False
    finished: bool = False

    @property
    def tile(self) -> tuple[int, int]:
        return (self.x, self.y)

    @property
    def milestones(self) -> int:
        return sum(
            1
            for flag in (
                self.has_cup,
                self.poured,
                self.filtered,
                self.has_jar,
                self.tablet_added,
                self.stirred,
                self.finished,
            )
            if flag
        )

    def snapshot(self) -> WorldState:
        return self.model_copy(deep=True)


class JudgeResult(BaseModel):
    status: Status
    message: str
    state: WorldState
    command: Command | None = None


class TranslationResult(BaseModel):
    command: Command
    source: Source = "keyword"
    latency_ms: float = 0.0
    error: str | None = None


class HistoryEntry(BaseModel):
    text: str
    status: Status
    message: str
    player: int = 0


class TeamView(BaseModel):
    """What a team laptop is allowed to see."""

    team: str
    phase: Phase
    player: int
    max_players: int
    used: int
    remaining: int
    turn_seconds_left: float
    round_seconds_left: float
    last: HistoryEntry | None = None
    history: list[HistoryEntry] = Field(default_factory=list)
    winner: str | None = None
    round_over: bool = False
    paused: bool = False


class AdminTeamView(TeamView):
    state: WorldState
    milestones: int
    source: Source | None = None
    command: Command | None = None
    latency_ms: float | None = None
    ai_latency_ms: float | None = None
    fallback_used: bool = False
    locked_seconds: float = 0.0
    device: str = "mock"
    device_port: str = ""
    pending_text: str | None = None
