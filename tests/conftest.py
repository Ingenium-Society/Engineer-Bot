"""Shared fixtures. The clock is fake so no test ever sleeps on a timer."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineer_bot import config
from engineer_bot.game import Game
from engineer_bot.models import Command, TranslationResult

LOG_DIR = Path(__file__).resolve().parents[1] / ".pytest-logs"


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def logdir():
    """A scratch directory inside the project.

    pytest's own tmp_path lands in /var/folders, which the sandbox broker will
    not create; keeping the scratch dir local keeps the suite runnable anywhere.
    """
    if not LOG_DIR.exists():
        LOG_DIR.mkdir(parents=True)
    for stale in LOG_DIR.glob("*.json"):
        stale.unlink()
    yield LOG_DIR
    for stale in LOG_DIR.glob("*.json"):
        stale.unlink()


#: What the "model" reads. The app has no rule-based fallback any more, so a
#: test that expects a verdict has to say what the model said.
READINGS: dict[str, Command] = {
    "maju": Command(verb="MAJU"),
    "maju 1": Command(verb="MAJU", n=1),
    "maju 2": Command(verb="MAJU", n=2),
    "maju 3": Command(verb="MAJU", n=3),
    "maju dua langkah": Command(verb="MAJU", n=2),
    "maju dua langkah!": Command(verb="MAJU", n=2),
    "maju dua langka": Command(verb="MAJU", n=2),
    "eh maju dua langka": Command(verb="MAJU", n=2),
    "eh maju dua": Command(verb="MAJU", n=2),
    "belok kanan 90": Command(verb="BELOK", arah="KANAN", n=90),
    "taruh pemurni di corong": Command(verb="TARUH", objek="pemurni", target="corong"),
    "ambil botol": Command(verb="AMBIL", objek="botol"),
    "tuang botol ke corong": Command(verb="TUANG", objek="botol", target="corong"),
    "tunggu 5 detik": Command(verb="TUNGGU", n=5),
    "ambil hasil": Command(verb="AMBIL", objek="hasil"),
}


class StubAI:
    """A model that only knows the phrases a test needs."""

    available = True
    last_error = None

    def __init__(self, readings: dict[str, Command] | None = None) -> None:
        self.readings = READINGS if readings is None else readings
        self.calls: list[str] = []

    async def translate(self, text: str) -> TranslationResult | None:
        self.calls.append(text)
        command = self.readings.get(" ".join(text.lower().split()))
        if command is None:
            return None
        return TranslationResult(command=command, source="ai", latency_ms=1.0)


@pytest.fixture
def make_game(logdir, clock, monkeypatch):
    monkeypatch.setattr(config, "REJECT_HOLD_SECONDS", 0.01)

    async def auto_confirm(_pending):
        return "accept"

    def factory(**overrides):
        # The engine always waits for a human verdict. Tests that are not about
        # confirmation get an automatic "Terima"; the rest pass auto_confirm=False.
        auto = overrides.pop("auto_confirm", True)
        ai = overrides.pop("ai", None)
        settings = config.Settings(
            max_instructions=overrides.pop("max_instructions", 14),
            turn_seconds=overrides.pop("turn_seconds", 20.0),
            play_seconds=overrides.pop("play_seconds", 330.0),
            ai_api_key="",
            **overrides,
        )
        game = Game(settings, ai=ai if ai is not None else StubAI(), clock=clock, log_dir=logdir)
        if auto:
            game.confirm = auto_confirm
        return game

    return factory
