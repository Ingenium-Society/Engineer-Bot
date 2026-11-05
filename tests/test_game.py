"""Engine behaviour: phases, turn order, timers, budget, win and tie-break rules."""

from __future__ import annotations

import asyncio

import pytest

from conftest import FakeClock
from engineer_bot import config
from engineer_bot.game import Game
from engineer_bot.models import WorldState

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


async def settle() -> None:
    """Let the short reject-hold task run."""
    await asyncio.sleep(0.05)


async def take_turn(game: Game, team: str, text: str, clock: FakeClock) -> None:
    clock.advance(1.5)  # clear the 1 s submit debounce
    await game.submit(team, text)
    if game.round_over:
        return
    assert game.teams[team].phase == "LOCKED", f"{text!r} was not accepted"
    clock.advance(1.5)
    await game.done(team)


async def play_alternating(game: Game, steps: list[str], clock: FakeClock) -> None:
    """Both teams play the same list, one instruction each per round."""
    for text in steps:
        for team in ("a", "b"):
            if game.round_over:
                return
            await take_turn(game, team, text, clock)


# ------------------------------------------------------------------- start


async def test_team_a_opens_and_team_b_waits(make_game):
    game = make_game()
    await game.start()
    assert game.turn == "a"
    assert game.team_view("a").phase == "TYPING"
    assert game.team_view("b").phase == "WAITING"
    assert game.team_view("a").player == 1
    assert game.team_view("b").used == 0


async def test_state_starts_idle(make_game):
    game = make_game()
    assert game.team_view("a").phase == "IDLE"
    assert game.team_view("a").turn_seconds_left == 0.0
    assert game.turn is None


async def test_only_the_team_on_turn_has_a_clock(make_game):
    game = make_game()
    await game.start()
    assert game.team_view("a").turn_seconds_left == pytest.approx(20.0)
    assert game.team_view("b").turn_seconds_left == 0.0


# --------------------------------------------------------------- accept path


async def test_accept_locks_the_team_and_moves_the_robot(make_game):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2")
    team = game.teams["a"]
    assert team.phase == "LOCKED"
    assert team.used == 1
    assert team.state.tile == (0, 2)
    assert team.last is not None and team.last.status == "ACCEPT"


async def test_done_hands_the_turn_to_the_other_team(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2")
    clock.advance(3)
    await game.done("a")

    assert game.turn == "b"
    assert game.team_view("b").phase == "TYPING"
    assert game.team_view("a").phase == "WAITING"
    assert game.team_view("b").player == 1
    assert game.team_view("b").turn_seconds_left == pytest.approx(20.0)


async def test_done_is_ignored_when_not_locked(make_game):
    game = make_game()
    await game.start()
    await game.done("a")
    assert game.turn == "a"
    assert game.team_view("a").phase == "TYPING"


async def test_the_waiting_team_cannot_submit(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("b", "maju 2")
    assert game.teams["b"].used == 0
    assert game.teams["b"].phase == "WAITING"


# --------------------------------------------------------------- reject path


async def test_reject_keeps_the_state_and_hands_over(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("a", "maju")
    team = game.teams["a"]
    assert team.used == 1
    assert team.state.tile == (0, 0)
    assert team.last is not None and team.last.message == config.MSG_NEED_STEPS
    await settle()
    assert team.phase == "WAITING"
    assert game.turn == "b"


async def test_rate_limit_ignores_a_second_submission(make_game, clock):
    game = make_game(max_instructions=5)
    await game.start()
    game.teams["b"].phase = "DONE"  # B is out, so A keeps the floor
    await game.submit("a", "maju 2")
    await game.redo("a")  # straight back to A, same clock instant
    assert game.turn == "a"

    await game.submit("a", "maju 2")
    assert game.teams["a"].used == 1  # debounced

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


async def test_turn_timeout_counts_as_used_and_passes_the_turn(make_game, clock):
    game = make_game()
    await game.start()
    clock.advance(21)
    changed = await game.tick()
    assert changed is True
    team = game.teams["a"]
    assert team.used == 1
    assert team.last is not None and team.last.message == config.MSG_TIMEOUT
    assert team.phase == "WAITING"
    assert game.turn == "b"


async def test_the_waiting_team_never_times_out(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2")
    await game.done("a")
    clock.advance(21)
    await game.tick()
    assert game.teams["b"].used == 1  # B timed out, A was never touched
    assert game.teams["a"].used == 1


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


# --------------------------------------------------------------- budget


async def test_budget_is_enforced_and_the_turn_moves_on(make_game, clock):
    game = make_game(max_instructions=2)
    await game.start()
    await take_turn(game, "a", "maju 1", clock)
    await take_turn(game, "b", "maju 1", clock)
    await take_turn(game, "a", "maju 1", clock)

    assert game.teams["a"].used == 2
    assert game.teams["a"].phase == "DONE"
    assert game.turn == "b"


async def test_a_finished_team_is_skipped(make_game, clock):
    game = make_game(max_instructions=1)
    await game.start()
    await take_turn(game, "a", "maju 1", clock)
    # A is done, so B keeps the floor until it is done too.
    assert game.teams["a"].phase == "DONE"
    assert game.turn == "b"
    await take_turn(game, "b", "maju 1", clock)
    assert game.round_over is True


async def test_both_teams_exhausted_ends_the_round(make_game, clock):
    game = make_game(max_instructions=1)
    await game.start()
    await game.submit("a", "maju")
    await settle()
    await game.submit("b", "maju")
    await settle()
    assert game.teams["a"].phase == "DONE"
    assert game.teams["b"].phase == "DONE"
    assert game.round_over is True


# ----------------------------------------------------------------------- win


async def test_win_ends_the_round_for_both_teams(make_game, clock):
    game = make_game()
    await game.start()
    await play_alternating(game, OPTIMAL, clock)
    assert game.round_over is True
    assert game.winner == "a"
    assert game.teams["a"].state.finished is True
    assert game.teams["b"].phase == "DONE"
    assert game.turn is None


async def test_winning_stops_further_submissions(make_game, clock):
    game = make_game()
    await game.start()
    await play_alternating(game, OPTIMAL, clock)
    before = game.teams["b"].used
    await game.submit("b", "maju 2")
    assert game.teams["b"].used == before


# ---------------------------------------------------------------------- redo


async def test_redo_reverts_the_state_and_hands_over(make_game, clock):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2")
    assert game.teams["a"].state.tile == (0, 2)
    await game.redo("a")
    team = game.teams["a"]
    assert team.state.tile == (0, 0)
    assert team.used == 1
    assert team.phase == "WAITING"
    assert game.turn == "b"


async def test_redo_only_works_when_locked(make_game):
    game = make_game()
    await game.start()
    await game.redo("a")
    assert game.teams["a"].state.tile == (0, 0)
    assert game.turn == "a"


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
    game.clock.now += 400  # type: ignore[attr-defined]
    await game.tick()
    assert game.last_log_path is not None
    assert game.last_log_path.exists()
    payload = game.to_log("time")
    assert payload["teams"]["a"]["submissions"][0]["text"] == "maju 2"
    assert payload["teams"]["a"]["submissions"][0]["status"] == "ACCEPT"


# --------------------------------------------------------------------- views


async def test_admin_view_reports_the_turn(make_game):
    game = make_game()
    await game.start()
    view = game.admin_view()
    assert view["turn"] == "a"
    assert view["teams"]["a"]["phase"] == "TYPING"
    assert view["teams"]["b"]["phase"] == "WAITING"
    assert view["started"] is True


async def test_screen_view_carries_the_draft_and_the_verdict_sequence(make_game):
    game = make_game()
    await game.start()
    await game.set_draft("a", "maju du")
    view = game.screen_view()
    assert view["turn"] == "a"
    assert view["draft"] == "maju du"
    assert view["seq"] == 0

    await game.submit("a", "maju 2")
    view = game.screen_view()
    assert view["seq"] == 1
    assert view["last"]["status"] == "ACCEPT"
    assert view["last"]["text"] == "maju 2"


async def test_draft_is_ignored_for_the_waiting_team(make_game):
    game = make_game()
    await game.start()
    await game.set_draft("b", "maju 2")
    assert game.teams["b"].draft == ""
    assert game.screen_view()["draft"] == ""


async def test_draft_is_capped_and_collapsed(make_game):
    game = make_game()
    await game.start()
    await game.set_draft("a", "  maju    dua   " + "x" * 200)
    draft = game.teams["a"].draft
    assert len(draft) == config.DRAFT_MAX
    assert "  " not in draft


async def test_submitting_clears_the_draft(make_game, clock):
    game = make_game()
    await game.start()
    await game.set_draft("a", "maju 2")
    await game.submit("a", "maju 2")
    assert game.teams["a"].draft == ""
