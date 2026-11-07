"""Pydantic models shared by the judge, the engine and the transport layer."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import config

Status = Literal["ACCEPT", "REJECT"]
#: WAITING is the team whose turn it is not.
Phase = Literal["IDLE", "WAITING", "TYPING", "JUDGING", "LOCKED", "DONE"]
#: Where the *translation* came from.
Source = Literal["ai", "keyword", "manual"]
#: How the instruction reached the server. Independent of the translator.
InputSource = Literal["keyboard", "voice", "operator", "timeout"]
SttMode = Literal["browser", "server", "off"]
SttStatus = Literal["ok", "no-mic", "denied", "offline", "unsupported"]


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
            # No synonym table any more: the model is told the exact object
            # names, and anything else is a genuine misunderstanding the
            # validator should reject rather than quietly fix up.
            return value.strip().lower() or None
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

    @property
    def summary(self) -> str:
        """One line an operator can check at a glance: `BELOK KANAN 90`."""
        parts = [self.verb.value]
        if self.arah:
            parts.append(self.arah)
        if self.n is not None:
            parts.append(str(self.n))
        if self.objek:
            parts.append(self.objek)
        if self.target:
            parts.append(self.target)
        return " ".join(parts)


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
    turn: str | None = None
    #: True while a verdict is waiting for the operator to confirm it.
    pending: bool = False
    #: A transient message, e.g. "the model did not answer".
    notice: str | None = None
    stt_mode: SttMode = "browser"
    stt_lang: str = "id-ID"
    stt_tail_ms: int = 1000
    stt_max_ms: int = 6000
    stt_level: int = 6


class AdminTeamView(TeamView):
    state: WorldState
    milestones: int
    source: Source | None = None
    command: Command | None = None
    latency_ms: float | None = None
    ai_latency_ms: float | None = None
    fallback_used: bool = False
    locked_seconds: float = 0.0
    pending_text: str | None = None
    #: What the judge proposes, so the operator can confirm it knowingly.
    pending_status: Status | None = None
    pending_message: str | None = None
    pending_command: str | None = None
    pending_reason: str | None = None
    #: Why there is no proposal, when the model could not answer.
    pending_error: str | None = None
    stt_status: SttStatus | None = None
    stt_engine: str | None = None
    last_raw: str | None = None
    stt_ms: float | None = None
    input_source: InputSource | None = None
    draft: str = ""
