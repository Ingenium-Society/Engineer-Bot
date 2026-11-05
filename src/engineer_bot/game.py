"""The game engine: phases, timers, turn flow, win rules.

One :class:`Game` owns two :class:`TeamState` objects. Everything time-related
is read from an injectable monotonic clock so tests never sleep.

Play is **turn based**: team A starts, then B, then A again. Only the team whose
turn it is can type, and only that team has a running 20 s clock. Everything
else waits in ``WAITING``.

Phase machine per team::

    IDLE -> WAITING -> TYPING -> JUDGING -> LOCKED -> (operator) -> WAITING
                        \\-> (rejected) -> WAITING after a short hold
                                          -> DONE when the budget is spent
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, judge
from .logbook import write_round
from .models import (
    AdminTeamView,
    Command,
    HistoryEntry,
    InputSource,
    JudgeMode,
    JudgeResult,
    Phase,
    Source,
    SttStatus,
    TeamView,
    TranslationResult,
    WorldState,
)

log = logging.getLogger(__name__)

TEAM_IDS = ("a", "b")
TEAM_LABELS = {"a": "TIM A", "b": "TIM B"}


@dataclass
class Submission:
    """One line of the round log."""

    at: str
    player: int
    text: str
    source: Source | None
    command: dict[str, Any] | None
    status: str
    message: str
    latency_ms: float | None
    input_source: InputSource = "keyboard"
    raw: str | None = None
    stt_ms: float | None = None
    engine: str | None = None
    stt_status: str | None = None


@dataclass
class Pending:
    """A submission waiting for the operator in manual mode."""

    text: str
    command: Command | None
    proposed: JudgeResult
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: str | None = None


@dataclass
class TeamState:
    id: str
    phase: Phase = "IDLE"
    used: int = 0
    state: WorldState = field(default_factory=WorldState)
    snapshot: WorldState | None = None
    history: list[HistoryEntry] = field(default_factory=list)
    records: list[Submission] = field(default_factory=list)
    last: HistoryEntry | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    turn_deadline: float = 0.0
    last_submit_at: float = -1e9
    source: Source | None = None
    command: Command | None = None
    latency_ms: float | None = None
    ai_latency_ms: float | None = None
    fallback_used: bool = False
    locked_since: float | None = None
    pending_text: str | None = None
    pending: Pending | None = None
    forced: str | None = None
    generation: int = 0
    input_source: InputSource | None = None
    last_raw: str | None = None
    stt_ms: float | None = None
    stt_status: SttStatus | None = None
    stt_engine: str | None = None
    #: Live text on the team's page, mirrored to the projector while they type.
    draft: str = ""

    @property
    def label(self) -> str:
        return TEAM_LABELS[self.id]

    def player_number(self, maximum: int) -> int:
        if self.phase in ("LOCKED", "JUDGING", "DONE"):
            return max(1, min(self.used, maximum))
        return min(self.used + 1, maximum)


class Game:
    def __init__(
        self,
        settings: config.Settings,
        *,
        ai: Any = None,
        notify: Callable[[], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        log_dir: Path | str | None = None,
    ) -> None:
        self.settings = settings
        self.ai = ai
        self.clock = clock
        self.judge_mode: JudgeMode = "ai"
        self.notify = notify
        self.log_dir = Path(log_dir) if log_dir else config.PROJECT_ROOT / "logs"

        self.teams: dict[str, TeamState] = {team_id: TeamState(id=team_id) for team_id in TEAM_IDS}

        self.started = False
        self.paused = False
        self.round_over = False
        self.winner: str | None = None
        self.finished_at: float | None = None
        #: Whose turn it is. Turn based play starts with team A.
        self.turn: str | None = None
        #: Bumped once per judged instruction so the projector can animate
        #: exactly one verdict instead of re-playing the current state.
        self.seq = 0
        #: The most recent verdict, with the team that produced it. Kept apart
        #: from `turn`, because a rejection hands the floor over immediately.
        self.last_event: dict[str, Any] | None = None
        self._round_deadline = 0.0
        self._pause_started = 0.0
        self._running = asyncio.Event()
        self._running.set()
        self._tasks: set[asyncio.Task] = set()
        self.last_log_path: Path | None = None

    # -- helpers -----------------------------------------------------------

    async def _notify(self) -> None:
        if self.notify is None:
            return
        try:
            await self.notify()
        except Exception as exc:  # noqa: BLE001 - a dead socket must not stop the game
            log.warning("broadcast failed: %s", exc)

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _resolve_pending(self, team: TeamState, decision: str) -> None:
        if team.pending is not None and not team.pending.event.is_set():
            team.pending.decision = decision
            team.pending.event.set()

    # -- round control -----------------------------------------------------

    async def start(self) -> None:
        now = self.clock()
        self.started = True
        self.paused = False
        self._running.set()
        self.round_over = False
        self.winner = None
        self.finished_at = None
        self.seq = 0
        self.last_event = None
        self._round_deadline = now + self.settings.play_seconds
        for team in self.teams.values():
            team.generation += 1
            team.used = 0
            team.state = WorldState()
            team.snapshot = None
            team.history.clear()
            team.records.clear()
            team.last = None
            team.source = None
            team.command = None
            team.latency_ms = None
            team.ai_latency_ms = None
            team.fallback_used = False
            team.locked_since = None
            team.pending_text = None
            team.pending = None
            team.forced = None
            team.input_source = None
            team.last_raw = None
            team.stt_ms = None
            team.draft = ""
            team.phase = "WAITING"
            team.turn_deadline = 0.0
        # Team A always opens the round.
        self.turn = "a"
        opener = self.teams["a"]
        opener.phase = "TYPING"
        opener.turn_deadline = now + self.settings.turn_seconds
        await self._notify()

    async def reset(self) -> None:
        self.started = False
        self.paused = False
        self._running.set()
        self.round_over = False
        self.winner = None
        self.finished_at = None
        self.turn = None
        self.seq = 0
        self.last_event = None
        self._round_deadline = 0.0
        for team in self.teams.values():
            team.generation += 1
            self._resolve_pending(team, "reject")
            team.pending = None
            team.phase = "IDLE"
            team.used = 0
            team.state = WorldState()
            team.snapshot = None
            team.history.clear()
            team.records.clear()
            team.last = None
            team.turn_deadline = 0.0
            team.locked_since = None
            team.pending_text = None
            team.forced = None
            team.input_source = None
            team.last_raw = None
            team.stt_ms = None
            team.draft = ""
        await self._notify()

    async def pause(self) -> None:
        if not self.started or self.paused or self.round_over:
            return
        self.paused = True
        self._running.clear()
        self._pause_started = self.clock()
        await self._notify()

    async def resume(self) -> None:
        if not self.started or not self.paused:
            return
        delta = self.clock() - self._pause_started
        self._round_deadline += delta
        for team in self.teams.values():
            if team.turn_deadline:
                team.turn_deadline += delta
        self.paused = False
        self._running.set()
        await self._notify()

    async def set_judge_mode(self, mode: JudgeMode) -> None:
        self.judge_mode = mode
        await self._notify()

    # -- timing ------------------------------------------------------------

    async def tick(self) -> bool:
        """Called a few times per second by the server. Returns True on change."""
        if not self.started or self.paused or self.round_over:
            return False
        now = self.clock()
        changed = False
        for team in self.teams.values():
            if team.phase == "TYPING" and now >= team.turn_deadline:
                await self._timeout(team)
                changed = True
        if not self.round_over and now >= self._round_deadline:
            await self._finish("time")
            changed = True
        return changed

    async def _timeout(self, team: TeamState) -> None:
        team.generation += 1
        team.used += 1
        entry = HistoryEntry(
            text="",
            status="REJECT",
            message=config.MSG_TIMEOUT,
            player=team.player_number(self.settings.max_instructions),
        )
        team.history.append(entry)
        team.last = entry
        team.source = None
        team.command = None
        team.draft = ""
        team.records.append(
            Submission(
                at=datetime.now().isoformat(timespec="seconds"),
                player=entry.player,
                text="",
                source=None,
                command=None,
                status="REJECT",
                message=config.MSG_TIMEOUT,
                latency_ms=None,
                input_source="timeout",
            )
        )
        self.seq += 1
        self.last_event = {
            "seq": self.seq,
            "team": team.id,
            "text": "",
            "status": "REJECT",
            "message": config.MSG_TIMEOUT,
        }
        await self._advance_turn(team)

    # -- turn flow ---------------------------------------------------------

    async def submit(
        self,
        team_id: str,
        text: str,
        *,
        source: InputSource = "keyboard",
        raw: str | None = None,
        stt_ms: float | None = None,
    ) -> None:
        """The single door into the judge. Voice and keyboard share it exactly.

        Only the team whose turn it is may use it; the other team is WAITING.
        """
        team = self.teams.get(team_id)
        if team is None:
            return
        if not self.started or self.round_over or self.paused:
            return
        if self.turn != team_id or team.phase != "TYPING":
            return
        now = self.clock()
        if now - team.last_submit_at < config.SUBMIT_RATE_LIMIT_SECONDS:
            return
        async with team.lock:
            # Re-check inside the lock: two quick submissions must not both run.
            if team.phase != "TYPING" or self.round_over or self.turn != team_id:
                return
            now = self.clock()
            if now - team.last_submit_at < config.SUBMIT_RATE_LIMIT_SECONDS:
                return
            team.last_submit_at = now
            team.generation += 1
            generation = team.generation
            team.used += 1
            team.phase = "JUDGING"
            team.pending_text = text
            team.input_source = source
            team.last_raw = raw
            team.stt_ms = stt_ms
            await self._notify()
            await self._judge_and_apply(team, text, generation, source, raw, stt_ms)

    async def set_draft(self, team_id: str, text: str) -> None:
        """Mirror the team's live typing to the projector.

        Only the team on turn, only while typing, and capped — this arrives on
        every keystroke, so it must stay cheap and must never leak into the judge.
        """
        team = self.teams.get(team_id)
        if team is None or team.phase != "TYPING" or self.turn != team_id:
            return
        draft = " ".join((text or "").split())[: config.DRAFT_MAX]
        if draft == team.draft:
            return
        team.draft = draft
        await self._notify()

    async def operator_submit(self, team_id: str, text: str) -> None:
        """The operator types for a team whose microphone died.

        Counts as that team's instruction, is logged with source "operator", and
        is ignored outside that team's input phase (``submit`` enforces it).
        """
        team = self.teams.get(team_id)
        if team is None or team.phase != "TYPING" or self.turn != team_id:
            return
        await self.submit(team_id, text, source="operator")

    async def set_stt_status(self, team_id: str, status: str, engine: str | None = None) -> None:
        """Remember what the team page's microphone is doing, for the admin page."""
        team = self.teams.get(team_id)
        if team is None or status not in config.STT_STATUSES:
            return
        if team.stt_status == status and (engine is None or team.stt_engine == engine):
            return
        team.stt_status = status  # type: ignore[assignment]
        if engine:
            team.stt_engine = engine
        await self._notify()

    async def _judge_and_apply(
        self,
        team: TeamState,
        text: str,
        generation: int,
        source: InputSource = "keyboard",
        raw: str | None = None,
        stt_ms: float | None = None,
    ) -> None:
        clean, error = judge.sanitize(text)
        translation: TranslationResult | None = None

        if error is not None:
            result = JudgeResult(status="REJECT", message=error, state=team.state)
        else:
            assert clean is not None
            translation = await judge.translate(clean, ai=self.ai, mode=self.judge_mode)
            result = judge.validate(
                translation.command,
                team.state,
                filter_seconds=self.settings.filter_seconds,
            )

            if team.forced in ("accept", "reject"):
                decision = team.forced
                team.forced = None
                # A forced accept cannot invent a legal state transition, so an
                # invalid command stays rejected even when the operator insists.
                if decision == "accept" and result.status == "ACCEPT":
                    result = JudgeResult(
                        status="ACCEPT",
                        message=result.message,
                        state=result.state,
                        command=result.command,
                    )
                else:
                    result = JudgeResult(
                        status="REJECT",
                        message=(
                            result.message
                            if result.status == "REJECT"
                            else config.MSG_OPERATOR_REJECT
                        ),
                        state=team.state,
                        command=result.command,
                    )
            elif self.judge_mode == "manual":
                result = await self._await_operator(team, clean, translation, result)

        if team.generation != generation:
            return
        await self._apply(team, result, text, translation, generation, source, raw, stt_ms)

    async def _await_operator(
        self,
        team: TeamState,
        clean: str,
        translation: TranslationResult,
        proposed: JudgeResult,
    ) -> JudgeResult:
        pending = Pending(text=clean, command=translation.command, proposed=proposed)
        team.pending = pending
        await self._notify()
        await pending.event.wait()
        team.pending = None
        if pending.decision == "accept" and proposed.status == "ACCEPT":
            return proposed
        message = proposed.message if proposed.status == "REJECT" else config.MSG_OPERATOR_REJECT
        return JudgeResult(status="REJECT", message=message, state=team.state)

    async def _apply(
        self,
        team: TeamState,
        result: JudgeResult,
        text: str,
        translation: TranslationResult | None,
        generation: int,
        source: InputSource = "keyboard",
        raw: str | None = None,
        stt_ms: float | None = None,
    ) -> None:
        if team.generation != generation:
            return

        team.source = translation.source if translation else None
        team.command = translation.command if translation else None
        team.latency_ms = translation.latency_ms if translation else None
        if translation is not None and translation.source == "ai":
            team.ai_latency_ms = translation.latency_ms
        team.fallback_used = bool(
            translation is not None
            and translation.source == "keyword"
            and self.ai is not None
            and getattr(self.ai, "available", False)
            and self.judge_mode == "ai"
        )

        entry = HistoryEntry(
            text=text,
            status=result.status,
            message=result.message,
            player=team.player_number(self.settings.max_instructions),
        )
        team.history.append(entry)
        team.last = entry
        team.draft = ""
        self.seq += 1
        self.last_event = {
            "seq": self.seq,
            "team": team.id,
            "text": text,
            "status": result.status,
            "message": result.message,
        }
        team.records.append(
            Submission(
                at=datetime.now().isoformat(timespec="seconds"),
                player=entry.player,
                text=text,
                source=team.source,
                command=team.command.model_dump() if team.command else None,
                status=result.status,
                message=result.message,
                latency_ms=team.latency_ms,
                input_source=source,
                raw=raw,
                stt_ms=stt_ms,
                engine=team.stt_engine,
                stt_status=team.stt_status,
            )
        )

        if result.status == "ACCEPT":
            team.snapshot = team.state.snapshot()
            team.state = result.state
            team.phase = "LOCKED"
            team.locked_since = self.clock()
            await self._notify()
            if team.state.finished:
                await self._finish("win", winner=team.id)
            return

        await self._notify()
        self._spawn(self._after_reject(team, generation))

    async def _after_reject(self, team: TeamState, generation: int) -> None:
        await asyncio.sleep(config.REJECT_HOLD_SECONDS)
        await self._running.wait()  # a pause during the hold must not burn the next turn
        if team.generation != generation or self.round_over:
            return
        await self._advance_turn(team)

    async def _advance_turn(self, current: TeamState) -> None:
        """Close `current`'s turn and hand the floor to the other team.

        A team that has spent its 14 instructions is DONE and is skipped; if both
        are DONE the round ends. Nothing here touches the world state.
        """
        current.pending_text = None
        current.locked_since = None
        current.turn_deadline = 0.0
        if self.round_over:
            current.phase = "DONE"
            await self._notify()
            return

        if current.used >= self.settings.max_instructions:
            current.phase = "DONE"
        else:
            current.phase = "WAITING"

        if all(team.phase == "DONE" for team in self.teams.values()):
            await self._finish("exhausted")
            return

        other = self.teams[self._other(current.id)]
        if other.phase == "DONE":
            # The other team is out of instructions: this one keeps playing.
            other = current
        self.turn = other.id
        other.phase = "TYPING"
        other.turn_deadline = self.clock() + self.settings.turn_seconds
        await self._notify()

    @staticmethod
    def _other(team_id: str) -> str:
        return "b" if team_id == "a" else "a"

    # -- operator actions --------------------------------------------------

    async def done(self, team_id: str) -> None:
        team = self.teams.get(team_id)
        if team is None:
            return
        async with team.lock:
            if team.phase != "LOCKED" or self.round_over:
                return
            if team.state.finished:
                await self._finish("win", winner=team.id)
                return
            await self._advance_turn(team)

    async def redo(self, team_id: str) -> None:
        team = self.teams.get(team_id)
        if team is None:
            return
        async with team.lock:
            if team.phase != "LOCKED" or self.round_over:
                return
            if team.snapshot is not None:
                team.state = team.snapshot.snapshot()
            await self._advance_turn(team)

    async def force(self, team_id: str, decision: str, text: str | None = None) -> None:
        team = self.teams.get(team_id)
        if team is None or decision not in ("accept", "reject"):
            return
        if team.pending is not None and not team.pending.event.is_set():
            self._resolve_pending(team, decision)
            return
        if text and team.phase == "TYPING" and self.turn == team_id and not self.round_over:
            team.forced = decision
            await self.submit(team_id, text)

    # -- round end ---------------------------------------------------------

    def decide_winner(self) -> str | None:
        a, b = self.teams["a"], self.teams["b"]
        if a.state.finished and not b.state.finished:
            return "a"
        if b.state.finished and not a.state.finished:
            return "b"
        if a.state.milestones != b.state.milestones:
            return "a" if a.state.milestones > b.state.milestones else "b"
        if a.used != b.used:
            return "a" if a.used < b.used else "b"
        return None

    async def _finish(self, reason: str, winner: str | None = None) -> None:
        if self.round_over:
            return
        self.round_over = True
        self.finished_at = self.clock()
        self.turn = None
        for team in self.teams.values():
            self._resolve_pending(team, "reject")
            team.pending = None
            team.phase = "DONE"
            team.turn_deadline = 0.0
        self.winner = winner or self.decide_winner()
        self.last_log_path = write_round(self.log_dir, self.to_log(reason))
        await self._notify()

    # -- views -------------------------------------------------------------

    def _turn_left(self, team: TeamState) -> float:
        if team.phase != "TYPING" or self.paused:
            return 0.0
        return max(0.0, team.turn_deadline - self.clock())

    def _round_left(self) -> float:
        if not self.started or self.round_over:
            return 0.0
        return max(0.0, self._round_deadline - self.clock())

    def team_view(self, team_id: str) -> TeamView:
        team = self.teams[team_id]
        return TeamView(
            team=team_id,
            phase=team.phase,
            player=team.player_number(self.settings.max_instructions),
            max_players=self.settings.max_instructions,
            used=team.used,
            remaining=max(0, self.settings.max_instructions - team.used),
            turn_seconds_left=round(self._turn_left(team), 1),
            round_seconds_left=round(self._round_left(), 1),
            last=team.last,
            history=team.history[-20:],
            winner=self.winner,
            round_over=self.round_over,
            paused=self.paused,
            turn=self.turn,
            stt_mode=self.settings.stt_mode,  # type: ignore[arg-type]
            stt_lang=self.settings.stt_lang,
            auto_submit_ms=self.settings.auto_submit_delay_ms,
        )

    def admin_team_view(self, team_id: str) -> AdminTeamView:
        team = self.teams[team_id]
        base = self.team_view(team_id)
        locked_seconds = (
            self.clock() - team.locked_since
            if team.phase == "LOCKED" and team.locked_since is not None
            else 0.0
        )
        return AdminTeamView(
            **base.model_dump(),
            state=team.state,
            milestones=team.state.milestones,
            source=team.source,
            command=team.command,
            latency_ms=team.latency_ms,
            ai_latency_ms=team.ai_latency_ms,
            fallback_used=team.fallback_used,
            locked_seconds=round(locked_seconds, 1),
            pending_text=team.pending.text if team.pending is not None else None,
            stt_status=team.stt_status,
            stt_engine=team.stt_engine,
            last_raw=team.last_raw,
            stt_ms=team.stt_ms,
            input_source=team.input_source,
            draft=team.draft,
        )

    def admin_view(self) -> dict[str, Any]:
        return {
            "type": "admin_state",
            "started": self.started,
            "paused": self.paused,
            "round_over": self.round_over,
            "winner": self.winner,
            "turn": self.turn,
            "judge_mode": self.judge_mode,
            "ai_available": bool(self.ai is not None and getattr(self.ai, "available", False)),
            "ai_model": self.settings.deepseek_model if self.ai is not None else None,
            "round_seconds_left": round(self._round_left(), 1),
            "teams": {team_id: self.admin_team_view(team_id).model_dump() for team_id in TEAM_IDS},
        }

    def screen_view(self) -> dict[str, Any]:
        """Everything the projector needs, including the live draft and the
        sequence number that tells it a *new* verdict just happened."""
        active = self.teams[self.turn] if self.turn else None
        return {
            "type": "screen_state",
            "started": self.started,
            "round_over": self.round_over,
            "winner": self.winner,
            "paused": self.paused,
            "round_seconds_left": round(self._round_left(), 1),
            "turn": self.turn,
            "turn_phase": active.phase if active else "IDLE",
            "turn_seconds_left": round(self._turn_left(active), 1) if active else 0.0,
            "draft": active.draft if active else "",
            "seq": self.seq,
            "last": self.last_event,
            "teams": {
                team_id: {
                    "label": self.teams[team_id].label,
                    "milestones": self.teams[team_id].state.milestones,
                    "used": self.teams[team_id].used,
                    "remaining": max(0, self.settings.max_instructions - self.teams[team_id].used),
                    "phase": self.teams[team_id].phase,
                    "last": (
                        self.teams[team_id].last.model_dump() if self.teams[team_id].last else None
                    ),
                }
                for team_id in TEAM_IDS
            },
        }

    # -- log ---------------------------------------------------------------

    def _outcome(self, team_id: str) -> str:
        if self.winner == team_id:
            return "win"
        return "lose" if self.winner else "draw"

    def to_log(self, reason: str = "") -> dict[str, Any]:
        return {
            "round": {
                "started": self.started,
                "reason": reason,
                "winner": self.winner,
                "judge_mode": self.judge_mode,
                "settings": {
                    "max_instructions": self.settings.max_instructions,
                    "turn_seconds": self.settings.turn_seconds,
                    "play_seconds": self.settings.play_seconds,
                    "filter_seconds": self.settings.filter_seconds,
                    "deepseek_model": self.settings.deepseek_model,
                },
            },
            "teams": {
                team_id: {
                    "used": team.used,
                    "milestones": team.state.milestones,
                    "flags": team.state.model_dump(),
                    "outcome": self._outcome(team_id),
                    "submissions": [
                        {
                            "at": record.at,
                            "player": record.player,
                            "text": record.text,
                            "source": record.source,
                            "command": record.command,
                            "status": record.status,
                            "message": record.message,
                            "latency_ms": (
                                round(record.latency_ms, 1) if record.latency_ms else None
                            ),
                            "input_source": record.input_source,
                            "raw": record.raw,
                            "stt_ms": round(record.stt_ms, 1) if record.stt_ms else None,
                            "engine": record.engine,
                            "stt_status": record.stt_status,
                        }
                        for record in team.records
                    ],
                }
                for team_id, team in self.teams.items()
            },
        }
