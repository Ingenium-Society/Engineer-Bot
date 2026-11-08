"""The judge: sanitize -> translate (model only) -> validate.

Two hard rules live here:

* The model is a *translator*. It only ever produces a :class:`Command`. It can
  never make anything accepted, and there is no rule-based fallback: if the
  model cannot read the sentence, nobody did.
* :func:`validate` is pure. Same command plus same state always gives the same
  answer, and it is the only function that may return ``ACCEPT``.
"""

from __future__ import annotations

import unicodedata

from . import config
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
) -> TranslationResult | None:
    """Ask the model to read the sentence. ``None`` means nobody read it.

    There is deliberately no rule-based fallback: a verdict comes from the model
    or from the operator, never from a keyword table that guesses.
    """
    if ai is None or not getattr(ai, "available", False):
        return None
    return await ai.translate(text)


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
    """AMBIL botol, or AMBIL hasil once the water has settled."""
    objek = command.objek
    if objek is None:
        return _reject(state, command, config.MSG_NEED_DIRECTION)
    if objek not in config.TAKEABLE:
        return _reject(state, command, config.MSG_NOT_TAKEABLE)
    if state.holding is not None:
        return _reject(state, command, config.MSG_HANDS_FULL)
    if not _reachable(state, objek):
        return _reject(state, command, config.MSG_OUT_OF_REACH.format(objek=_label(objek)))
    if objek == "botol" and state.has_bottle:
        return _reject(state, command, config.MSG_OUT_OF_REACH.format(objek=_label(objek)))
    if objek == "hasil" and not state.waited:
        # Nothing to pick up until the water has gone through.
        return _reject(state, command, config.MSG_STEP_MISSING)

    new = state.snapshot()
    new.holding = objek
    if objek == "botol":
        new.has_bottle = True
        message = "AMBIL BOTOL"
    else:
        new.finished = True
        message = "AMBIL AIR BERSIH"
    return JudgeResult(status="ACCEPT", message=message, state=new, command=command)


def _place(command: Command, state: WorldState) -> JudgeResult:
    """TARUH pemurni di corong — set down, not carried: it is already there."""
    objek, target = command.objek, command.target
    if objek is None or target is None:
        return _reject(state, command, config.MSG_NEED_DIRECTION)
    if objek != "pemurni" or target != "corong":
        return _reject(state, command, config.MSG_WRONG_TARGET)
    if state.pemurni_placed:
        return _reject(state, command, config.MSG_STEP_MISSING)
    if not _reachable(state, "corong"):
        return _reject(state, command, config.MSG_OUT_OF_REACH.format(objek=_label("corong")))

    new = state.snapshot()
    new.pemurni_placed = True
    return JudgeResult(
        status="ACCEPT", message="TARUH PEMURNI DI CORONG", state=new, command=command
    )


def _pour(command: Command, state: WorldState) -> JudgeResult:
    objek, target = command.objek, command.target
    if objek is None or target is None:
        return _reject(state, command, config.MSG_NEED_DIRECTION)
    if state.holding != "botol":
        return _reject(state, command, config.MSG_NOT_HOLDING.format(objek=_label("botol")))
    if objek != "botol" or target != "corong":
        return _reject(state, command, config.MSG_WRONG_TARGET)
    if not state.pemurni_placed:
        # Pouring before the purifier is in place is the classic mistake.
        return _reject(state, command, config.MSG_STEP_MISSING)
    if state.poured:
        return _reject(state, command, config.MSG_STEP_MISSING)
    if not _reachable(state, "corong"):
        return _reject(state, command, config.MSG_OUT_OF_REACH.format(objek=_label("corong")))

    new = state.snapshot()
    new.poured = True
    new.holding = None
    return JudgeResult(status="ACCEPT", message="TUANG BOTOL KE CORONG", state=new, command=command)


def _wait(command: Command, state: WorldState, filter_seconds: int) -> JudgeResult:
    if command.n is None:
        return _reject(state, command, config.MSG_NEED_SECONDS)
    if not 1 <= command.n <= config.WAIT_MAX_SECONDS:
        return _reject(state, command, config.MSG_NEED_SECONDS)
    if not state.poured:
        return _reject(state, command, config.MSG_STEP_MISSING)

    new = state.snapshot()
    if not new.waited and command.n >= filter_seconds:
        new.waited = True
    return JudgeResult(
        status="ACCEPT", message=f"TUNGGU {command.n} DETIK", state=new, command=command
    )


_HANDLERS = {
    Verb.MAJU: _move,
    Verb.MUNDUR: _move,
    Verb.BELOK: _turn,
    Verb.AMBIL: _take,
    Verb.TARUH: _place,
    Verb.TUANG: _pour,
}
