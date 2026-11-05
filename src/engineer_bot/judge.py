"""The judge: sanitize -> translate (AI or keyword) -> validate.

Two hard rules live here:

* The AI is a *translator*. It only ever produces a :class:`Command`. It can
  never make anything accepted.
* :func:`validate` is pure. Same command plus same state always gives the same
  answer, and it is the only function that may return ``ACCEPT``.
"""

from __future__ import annotations

import time
import unicodedata

from . import config
from .keyword_parser import parse_keyword
from .models import Command, JudgeResult, TranslationResult, Verb, WorldState

# --------------------------------------------------------------------------
# Sanitize
# --------------------------------------------------------------------------


def sanitize(text: str | None) -> tuple[str | None, str | None]:
    """Return ``(clean_text, error_message)``. Exactly one of them is None."""
    if text is None:
        return None, config.MSG_EMPTY
    # Drop control characters (including the ones a paste can smuggle in).
    cleaned = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return None, config.MSG_EMPTY
    if len(cleaned) > config.MAX_TEXT_LEN:
        return None, config.MSG_TOO_LONG
    return cleaned, None


# --------------------------------------------------------------------------
# Translate
# --------------------------------------------------------------------------


async def translate(
    text: str,
    *,
    ai=None,
) -> TranslationResult:
    """Turn player text into a :class:`Command`, with the keyword parser as safety net."""
    if ai is None or not getattr(ai, "available", False):
        started = time.perf_counter()
        command = parse_keyword(text)
        return TranslationResult(
            command=command,
            source="keyword",
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    result = await ai.translate(text)
    if result is not None:
        return result

    # AI failed: fall back, but keep the reason so the admin page can show it.
    started = time.perf_counter()
    command = parse_keyword(text)
    return TranslationResult(
        command=command,
        source="keyword",
        latency_ms=(time.perf_counter() - started) * 1000,
        error=getattr(ai, "last_error", None) or "ai_unavailable",
    )


# --------------------------------------------------------------------------
# Validate
# --------------------------------------------------------------------------


def _reject(state: WorldState, command: Command, message: str) -> JudgeResult:
    return JudgeResult(status="REJECT", message=message, state=state, command=command)


def _label(objek: str | None) -> str:
    if not objek:
        return "benda"
    return config.OBJECT_LABELS.get(objek, objek)


def _reachable(state: WorldState, objek: str) -> bool:
    return config.tile_of(objek) == state.tile


def validate(
    command: Command, state: WorldState, *, filter_seconds: int = config.FILTER_SECONDS
) -> JudgeResult:
    """Pure. The only place a submission can become ACCEPT."""
    verb = command.verb

    if verb is Verb.INVALID:
        return _reject(state, command, command.alasan or config.MSG_UNKNOWN_VERB)

    if verb is Verb.TUNGGU:
        return _wait(command, state, filter_seconds)

    handler = _HANDLERS.get(verb)
    if handler is None:  # pragma: no cover - every Verb member is mapped
        return _reject(state, command, config.MSG_UNKNOWN_VERB)
    return handler(command, state)


def _move(command: Command, state: WorldState) -> JudgeResult:
    if command.n is None:
        return _reject(state, command, config.MSG_NEED_STEPS)
    if not 1 <= command.n <= config.MOVE_MAX_STEPS:
        return _reject(state, command, config.MSG_NEED_STEPS)

    sign = 1 if command.verb is Verb.MAJU else -1
    dx, dy = config.HEADING_VECTORS[state.heading]
    nx = state.x + sign * dx * command.n
    ny = state.y + sign * dy * command.n
    if not (0 <= nx < config.GRID_W and 0 <= ny < config.GRID_H):
        return _reject(state, command, config.MSG_OUT_OF_BOUNDS)

    new = state.snapshot()
    new.x, new.y = nx, ny
    return JudgeResult(
        status="ACCEPT",
        message=f"{command.verb.value} {command.n} LANGKAH",
        state=new,
        command=command,
    )


def _turn(command: Command, state: WorldState) -> JudgeResult:
    if command.arah not in config.DIRECTIONS:
        return _reject(state, command, config.MSG_NEED_DIRECTION)
    if command.n not in config.TURN_DEGREES:
        return _reject(state, command, config.MSG_NEED_DEGREES)

    steps = command.n // 90
    if command.arah == "KIRI":
        steps = -steps
    idx = (config.HEADING_ORDER.index(state.heading) + steps) % len(config.HEADING_ORDER)

    new = state.snapshot()
    new.heading = config.HEADING_ORDER[idx]
    return JudgeResult(
        status="ACCEPT",
        message=f"BELOK {command.arah} {command.n}",
        state=new,
        command=command,
    )


def _take(command: Command, state: WorldState) -> JudgeResult:
    objek = command.objek
    if objek is None:
        return _reject(state, command, config.MSG_NEED_DIRECTION)
    if objek not in config.TAKEABLE:
        return _reject(state, command, config.MSG_NOT_TAKEABLE)
    if state.holding is not None:
        return _reject(state, command, config.MSG_HANDS_FULL)
    if not _reachable(state, objek):
        return _reject(state, command, config.MSG_OUT_OF_REACH.format(objek=_label(objek)))
    if (objek == "gelas" and state.has_cup) or (objek == "toples" and state.has_jar):
        return _reject(state, command, config.MSG_OUT_OF_REACH.format(objek=_label(objek)))

    new = state.snapshot()
    new.holding = objek
    if objek == "gelas":
        new.has_cup = True
    elif objek == "toples":
        new.has_jar = True
    return JudgeResult(
        status="ACCEPT", message=f"AMBIL {_label(objek).upper()}", state=new, command=command
    )


def _pour(command: Command, state: WorldState) -> JudgeResult:
    objek, target = command.objek, command.target
    if objek is None or target is None:
        return _reject(state, command, config.MSG_NEED_DIRECTION)
    if state.holding != objek:
        return _reject(state, command, config.MSG_NOT_HOLDING.format(objek=_label(objek)))
    if target != "corong":
        return _reject(state, command, config.MSG_WRONG_TARGET)
    if not _reachable(state, "corong"):
        return _reject(state, command, config.MSG_OUT_OF_REACH.format(objek=_label("corong")))

    new = state.snapshot()
    new.poured = True
    new.holding = None
    return JudgeResult(
        status="ACCEPT",
        message=f"TUANG {_label(objek).upper()} KE CORONG",
        state=new,
        command=command,
    )


def _wait(command: Command, state: WorldState, filter_seconds: int) -> JudgeResult:
    if command.n is None:
        return _reject(state, command, config.MSG_NEED_SECONDS)
    if not 1 <= command.n <= config.WAIT_MAX_SECONDS:
        return _reject(state, command, config.MSG_NEED_SECONDS)

    new = state.snapshot()
    if new.poured and not new.filtered and command.n >= filter_seconds:
        new.filtered = True
    return JudgeResult(
        status="ACCEPT", message=f"TUNGGU {command.n} DETIK", state=new, command=command
    )


def _add_tablet(command: Command, state: WorldState) -> JudgeResult:
    objek, target = command.objek, command.target
    if objek is None or target is None:
        return _reject(state, command, config.MSG_NEED_DIRECTION)
    if state.holding != "toples":
        return _reject(state, command, config.MSG_NOT_HOLDING.format(objek=_label("toples")))
    if objek != "tablet":
        return _reject(state, command, config.MSG_WRONG_TARGET)
    if state.tablet_added:
        return _reject(state, command, config.MSG_STEP_MISSING)
    if not _reachable(state, "tablet"):
        return _reject(state, command, config.MSG_OUT_OF_REACH.format(objek=_label("tablet")))
    if target != "toples":
        return _reject(state, command, config.MSG_WRONG_TARGET)

    new = state.snapshot()
    new.tablet_added = True
    return JudgeResult(status="ACCEPT", message="TAMBAH TABLET", state=new, command=command)


def _stir(command: Command, state: WorldState) -> JudgeResult:
    objek = command.objek
    if objek is None:
        return _reject(state, command, config.MSG_NEED_DIRECTION)
    if state.holding != "toples":
        return _reject(state, command, config.MSG_NOT_HOLDING.format(objek=_label("toples")))
    if objek != "sendok":
        return _reject(state, command, config.MSG_WRONG_TARGET)
    if not state.tablet_added:
        return _reject(state, command, config.MSG_STEP_MISSING)
    if not _reachable(state, "sendok"):
        return _reject(state, command, config.MSG_OUT_OF_REACH.format(objek=_label("sendok")))

    new = state.snapshot()
    new.stirred = True
    return JudgeResult(status="ACCEPT", message="ADUK DENGAN SENDOK", state=new, command=command)


def _place(command: Command, state: WorldState) -> JudgeResult:
    objek, target = command.objek, command.target
    if objek is None or target is None:
        return _reject(state, command, config.MSG_NEED_DIRECTION)
    if state.holding != "toples":
        return _reject(state, command, config.MSG_NOT_HOLDING.format(objek=_label("toples")))
    if objek != "toples":
        return _reject(state, command, config.MSG_WRONG_TARGET)
    if not (state.tablet_added and state.stirred):
        return _reject(state, command, config.MSG_STEP_MISSING)
    if target != "finish":
        return _reject(state, command, config.MSG_WRONG_TARGET)
    if not _reachable(state, "finish"):
        return _reject(state, command, config.MSG_OUT_OF_REACH.format(objek=_label("finish")))

    new = state.snapshot()
    new.finished = True
    new.holding = None
    return JudgeResult(
        status="ACCEPT", message="TARUH TOPLES DI AREA FINISH", state=new, command=command
    )


_HANDLERS = {
    Verb.MAJU: _move,
    Verb.MUNDUR: _move,
    Verb.BELOK: _turn,
    Verb.AMBIL: _take,
    Verb.TUANG: _pour,
    Verb.TAMBAH: _add_tablet,
    Verb.ADUK: _stir,
    Verb.TARUH: _place,
}
