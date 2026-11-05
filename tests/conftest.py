"""Shared fixtures. The clock is fake so no test ever sleeps on a timer."""

from __future__ import annotations

from pathlib import Path

import pytest

from engineer_bot import config
from engineer_bot.game import Game

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


@pytest.fixture
def make_game(logdir, clock, monkeypatch):
    monkeypatch.setattr(config, "REJECT_HOLD_SECONDS", 0.01)

    def factory(**overrides):
        ai = overrides.pop("ai", None)
        settings = config.Settings(
            max_instructions=overrides.pop("max_instructions", 14),
            turn_seconds=overrides.pop("turn_seconds", 20.0),
            play_seconds=overrides.pop("play_seconds", 330.0),
            deepseek_api_key="",
            **overrides,
        )
        return Game(settings, ai=ai, clock=clock, log_dir=logdir)

    return factory
