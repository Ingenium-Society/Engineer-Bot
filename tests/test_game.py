"""Engine behaviour: phases, timers, budget, win and tie-break rules."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from engineer_bot import config
from engineer_bot.device import MockDevice
from engineer_bot.game import Game
from engineer_bot.models import WorldState

LOG_DIR = Path(__file__).resolve().parents[1] / ".pytest-logs"

OPTIMAL = [
    "maju 2",
    "ambil gelas",
    "belok kanan 90",
    "maju 3",
    "tuang gelas ke corong",
    "tunggu 10 detik",
    "ambil toples",
    "tambah tablet ke toples",
    "aduk dengan sendok",
    "taruh toples di area finish",
]


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
        settings = config.Settings(
            max_instructions=overrides.pop("max_instructions", 14),
            turn_seconds=overrides.pop("turn_seconds", 20.0),
            play_seconds=overrides.pop("play_seconds", 330.0),
            deepseek_api_key="",
        )
        devices = {"a": MockDevice("a"), "b": MockDevice("b")}
        return Game(settings, devices=devices, clock=clock, log_dir=logdir, **overrides)

    return factory


async def settle() -> None:
    """Let the short reject-hold task run."""
    await asyncio.sleep(0.05)


async def play(game: Game, team: str, steps: list[str], clock: FakeClock) -> None:
    for text in steps:
        clock.advance(1.5)  # clear the 1 s submit debounce
        await game.submit(team, text)
        if game.round_over:
            return
        assert game.teams[team].phase == "LOCKED", f"{text!r} was not accepted"
        await game.done(team)


# ------------------------------------------------------------------- start


async def test_start_puts_both_teams_in_typing(make_game):
    game = make_game()
    await game.start()
    for team in ("a", "b"):
        view = game.team_view(team)
        assert view.phase == "TYPING"
        assert view.player == 1
        assert view.used == 0
        assert view.remaining == 14


async def test_state_starts_idle(make_game):
    game = make_game()
    assert game.team_view("a").phase == "IDLE"
    assert game.team_view("a").turn_seconds_left == 0.0


# --------------------------------------------------------------- accept path


async def test_accept_locks_the_team_and_moves_the_robot(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2")
    team = game.teams["a"]
    assert team.phase == "LOCKED"
    assert team.used == 1
    assert team.state.tile == (0, 2)
    assert team.device.lines[-1] == "ACCEPT|maju 2"


async def test_done_starts_the_next_turn(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2")
    clock.advance(3)
    await game.done("a")
    view = game.team_view("a")
    assert view.phase == "TYPING"
    assert view.player == 2
    assert view.turn_seconds_left == pytest.approx(20.0)


async def test_done_is_ignored_when_not_locked(make_game):
    game = make_game()
    await game.start()
    await game.done("a")
    assert game.team_view("a").phase == "TYPING"


# --------------------------------------------------------------- reject path


async def test_reject_keeps_the_state_and_advances(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("a", "maju")
    team = game.teams["a"]
    assert team.used == 1
    assert team.state.tile == (0, 0)
    assert team.last is not None and team.last.message == config.MSG_NEED_STEPS
    assert team.device.lines[-1].startswith("REJECT|")
    await settle()
    assert team.phase == "TYPING"
    assert game.team_view("a").player == 2


async def test_rate_limit_ignores_a_second_submission(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("a", "maju")
    await settle()
    await game.submit("a", "maju 2")  # same fake clock instant
    assert game.teams["a"].used == 1
    clock.advance(1.5)
    await game.submit("a", "maju 2")
    assert game.teams["a"].used == 2


async def test_concurrent_submissions_are_serialised(make_game, clock):
    game = make_game()
    await game.start()
    await asyncio.gather(game.submit("a", "maju 2"), game.submit("a", "maju 2"))
    assert game.teams["a"].used == 1
    assert game.teams["a"].phase == "LOCKED"


async def test_submission_while_locked_is_ignored(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2")
    clock.advance(2)
    await game.submit("a", "maju 2")
    assert game.teams["a"].used == 1


# -------------------------------------------------------------------- timers


async def test_turn_timeout_counts_as_used(make_game, clock):
    game = make_game()
    await game.start()
    clock.advance(21)
    changed = await game.tick()
    assert changed is True
    team = game.teams["a"]
    assert team.used == 1
    assert team.last is not None and team.last.message == config.MSG_TIMEOUT
    assert team.phase == "TYPING"


async def test_play_timer_ends_the_round(make_game, clock):
    game = make_game()
    await game.start()
    clock.advance(331)
    await game.tick()
    assert game.round_over is True


async def test_pause_freezes_both_timers(make_game, clock):
    game = make_game()
    await game.start()
    clock.advance(10)
    await game.pause()
    clock.advance(100)
    await game.resume()
    view = game.team_view("a")
    assert view.round_seconds_left == pytest.approx(320.0)
    assert view.turn_seconds_left == pytest.approx(10.0)


async def test_paused_ticks_do_nothing(make_game, clock):
    game = make_game()
    await game.start()
    await game.pause()
    clock.advance(60)
    assert await game.tick() is False
    assert game.teams["a"].used == 0


# --------------------------------------------------------------- instruction budget


async def test_budget_is_enforced(make_game, clock):
    game = make_game(max_instructions=2)
    await game.start()
    await game.submit("a", "maju 1")
    await game.done("a")
    clock.advance(1.5)
    await game.submit("a", "maju 1")
    clock.advance(1.5)
    await game.done("a")
    assert game.teams["a"].phase == "DONE"
    assert game.teams["a"].used == 2


async def test_both_teams_exhausted_ends_the_round(make_game, clock):
    game = make_game(max_instructions=1)
    await game.start()
    await game.submit("a", "maju")
    await game.submit("b", "maju")
    await settle()
    assert game.teams["a"].phase == "DONE"
    assert game.teams["b"].phase == "DONE"
    assert game.round_over is True


# ----------------------------------------------------------------------- win


async def test_win_ends_the_round_for_both_teams(make_game, clock):
    game = make_game()
    await game.start()
    await play(game, "a", OPTIMAL, clock)
    assert game.round_over is True
    assert game.winner == "a"
    assert game.teams["a"].state.finished is True
    assert game.teams["b"].phase == "DONE"
    assert "WIN" in game.teams["a"].device.lines


async def test_winning_stops_further_submissions(make_game, clock):
    game = make_game()
    await game.start()
    await play(game, "a", OPTIMAL, clock)
    await game.submit("b", "maju 2")
    assert game.teams["b"].used == 0


# ---------------------------------------------------------------------- redo


async def test_redo_reverts_the_state_but_keeps_the_instruction(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2")
    assert game.teams["a"].state.tile == (0, 2)
    await game.redo("a")
    team = game.teams["a"]
    assert team.state.tile == (0, 0)
    assert team.used == 1
    assert team.phase == "TYPING"


async def test_redo_only_works_when_locked(make_game):
    game = make_game()
    await game.start()
    await game.redo("a")
    assert game.teams["a"].state.tile == (0, 0)


# ------------------------------------------------------------------ tie-break


async def test_more_milestones_wins(make_game):
    game = make_game()
    game.teams["a"].state = WorldState(has_cup=True, poured=True)
    game.teams["b"].state = WorldState(has_cup=True)
    assert game.decide_winner() == "a"


async def test_fewer_instructions_breaks_a_milestone_tie(make_game):
    game = make_game()
    game.teams["a"].state = WorldState(has_cup=True)
    game.teams["b"].state = WorldState(has_cup=True)
    game.teams["a"].used = 4
    game.teams["b"].used = 2
    assert game.decide_winner() == "b"


async def test_total_tie_is_a_draw(make_game):
    game = make_game()
    assert game.decide_winner() is None


async def test_finished_flag_wins_regardless_of_milestones(make_game):
    game = make_game()
    game.teams["a"].state = WorldState(finished=True)
    game.teams["b"].state = WorldState(has_cup=True, poured=True, filtered=True)
    assert game.decide_winner() == "a"


# -------------------------------------------------------------------- logging


async def test_round_log_is_written(make_game):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2")
    await game.done("a")
    clockless = game.clock
    clockless.now += 400  # type: ignore[attr-defined]
    await game.tick()
    assert game.last_log_path is not None
    assert game.last_log_path.exists()
    payload = game.to_log("time")
    assert payload["teams"]["a"]["submissions"][0]["text"] == "maju 2"
    assert payload["teams"]["a"]["submissions"][0]["status"] == "ACCEPT"


# --------------------------------------------------------------------- views


async def test_admin_view_reports_device_and_phase(make_game):
    game = make_game()
    await game.start()
    view = game.admin_view()
    assert view["teams"]["a"]["device"] == "mock"
    assert view["teams"]["a"]["phase"] == "TYPING"
    assert view["started"] is True
