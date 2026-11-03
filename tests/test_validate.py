"""The validator: every verb, every reject path, and the state contract."""

from __future__ import annotations

import pytest

from engineer_bot import config, judge
from engineer_bot.models import Command, JudgeResult, Verb, WorldState


def at(x: int = 0, y: int = 0, heading: str = "N", **flags) -> WorldState:
    return WorldState(x=x, y=y, heading=heading, **flags)


def run(verb: Verb, state: WorldState | None = None, **fields) -> JudgeResult:
    return judge.validate(Command(verb=verb, **fields), state or WorldState())


# --------------------------------------------------------------------- move


def test_move_forward_two_tiles():
    result = run(Verb.MAJU, n=2)
    assert result.status == "ACCEPT"
    assert (result.state.x, result.state.y) == (0, 2)


def test_move_backward_from_start_leaves_the_grid():
    result = run(Verb.MUNDUR, n=1)
    assert result.status == "REJECT"
    assert result.message == config.MSG_OUT_OF_BOUNDS


def test_move_past_the_edge_is_rejected():
    result = run(Verb.MAJU, n=5)
    assert result.status == "REJECT"
    assert result.message == config.MSG_OUT_OF_BOUNDS


@pytest.mark.parametrize("steps", [0, 6, -1, 99])
def test_move_with_an_impossible_step_count(steps):
    result = run(Verb.MAJU, n=steps)
    assert result.status == "REJECT"
    assert result.message == config.MSG_NEED_STEPS


def test_move_without_a_number_asks_for_one():
    result = run(Verb.MAJU)
    assert result.status == "REJECT"
    assert result.message == config.MSG_NEED_STEPS


def test_move_respects_the_heading():
    result = run(Verb.MAJU, at(2, 2, heading="E"), n=2)
    assert result.status == "ACCEPT"
    assert (result.state.x, result.state.y) == (4, 2)


# --------------------------------------------------------------------- turn


@pytest.mark.parametrize(
    ("heading", "arah", "degrees", "expected"),
    [
        ("N", "KANAN", 90, "E"),
        ("N", "KIRI", 90, "W"),
        ("N", "KANAN", 180, "S"),
        ("E", "KIRI", 90, "N"),
        ("W", "KANAN", 180, "E"),
        ("S", "KANAN", 90, "W"),
    ],
)
def test_turn(heading, arah, degrees, expected):
    result = run(Verb.BELOK, at(2, 2, heading=heading), arah=arah, n=degrees)
    assert result.status == "ACCEPT"
    assert result.state.heading == expected


def test_turn_without_direction():
    result = run(Verb.BELOK, n=90)
    assert result.status == "REJECT"
    assert result.message == config.MSG_NEED_DIRECTION


def test_turn_without_degrees():
    result = run(Verb.BELOK, arah="KANAN")
    assert result.status == "REJECT"
    assert result.message == config.MSG_NEED_DEGREES


def test_turn_with_a_weird_angle():
    result = run(Verb.BELOK, arah="KANAN", n=45)
    assert result.status == "REJECT"
    assert result.message == config.MSG_NEED_DEGREES


# --------------------------------------------------------------------- take


def test_take_the_cup():
    result = run(Verb.AMBIL, at(0, 2), objek="gelas")
    assert result.status == "ACCEPT"
    assert result.state.holding == "gelas"
    assert result.state.has_cup is True


def test_take_the_jar():
    result = run(Verb.AMBIL, at(3, 2), objek="toples")
    assert result.status == "ACCEPT"
    assert result.state.has_jar is True


def test_take_from_the_wrong_tile():
    result = run(Verb.AMBIL, at(0, 0), objek="gelas")
    assert result.status == "REJECT"
    assert result.message == config.MSG_OUT_OF_REACH.format(objek="gelas")


def test_take_with_a_full_hand():
    result = run(Verb.AMBIL, at(3, 2, holding="gelas"), objek="toples")
    assert result.status == "REJECT"
    assert result.message == config.MSG_HANDS_FULL


def test_take_something_that_cannot_be_held():
    result = run(Verb.AMBIL, at(3, 2), objek="corong")
    assert result.status == "REJECT"
    assert result.message == config.MSG_NOT_TAKEABLE


def test_take_without_an_object():
    result = run(Verb.AMBIL, at(0, 2))
    assert result.status == "REJECT"
    assert result.message == config.MSG_NEED_DIRECTION


def test_take_the_cup_twice():
    result = run(Verb.AMBIL, at(0, 2, has_cup=True, poured=True), objek="gelas")
    assert result.status == "REJECT"
    assert result.message == config.MSG_OUT_OF_REACH.format(objek="gelas")


# --------------------------------------------------------------------- pour


def test_pour_the_cup():
    state = at(3, 2, holding="gelas", has_cup=True)
    result = run(Verb.TUANG, state, objek="gelas", target="corong")
    assert result.status == "ACCEPT"
    assert result.state.poured is True
    assert result.state.holding is None


def test_pour_without_holding_anything():
    result = run(Verb.TUANG, at(3, 2), objek="gelas", target="corong")
    assert result.status == "REJECT"
    assert result.message == config.MSG_NOT_HOLDING.format(objek="gelas")


def test_pour_into_the_wrong_target():
    result = run(Verb.TUANG, at(3, 2, holding="gelas"), objek="gelas", target="toples")
    assert result.status == "REJECT"
    assert result.message == config.MSG_WRONG_TARGET


def test_pour_away_from_the_funnel():
    result = run(Verb.TUANG, at(0, 0, holding="gelas"), objek="gelas", target="corong")
    assert result.status == "REJECT"
    assert result.message == config.MSG_OUT_OF_REACH.format(objek="corong")


# --------------------------------------------------------------------- wait


def test_wait_long_enough_filters_the_water():
    state = at(3, 2, poured=True)
    result = run(Verb.TUNGGU, state, n=10)
    assert result.status == "ACCEPT"
    assert result.state.filtered is True


def test_short_wait_is_accepted_but_does_nothing():
    state = at(3, 2, poured=True)
    result = run(Verb.TUNGGU, state, n=5)
    assert result.status == "ACCEPT"
    assert result.state.filtered is False


def test_wait_before_pouring_does_nothing():
    result = run(Verb.TUNGGU, at(3, 2), n=30)
    assert result.status == "ACCEPT"
    assert result.state.filtered is False


def test_wait_without_a_number():
    result = run(Verb.TUNGGU, at(3, 2))
    assert result.status == "REJECT"
    assert result.message == config.MSG_NEED_SECONDS


def test_wait_too_long():
    result = run(Verb.TUNGGU, at(3, 2), n=31)
    assert result.status == "REJECT"
    assert result.message == config.MSG_NEED_SECONDS


# -------------------------------------------------------------------- tablet


def test_add_the_tablet():
    state = at(3, 2, holding="toples", has_jar=True, poured=True, filtered=True)
    result = run(Verb.TAMBAH, state, objek="tablet", target="toples")
    assert result.status == "ACCEPT"
    assert result.state.tablet_added is True


def test_add_tablet_without_the_jar():
    state = at(3, 2, poured=True, filtered=True)
    result = run(Verb.TAMBAH, state, objek="tablet", target="toples")
    assert result.status == "REJECT"
    assert result.message == config.MSG_NOT_HOLDING.format(objek="toples")


def test_add_the_wrong_object():
    state = at(3, 2, holding="toples")
    result = run(Verb.TAMBAH, state, objek="sendok", target="toples")
    assert result.status == "REJECT"
    assert result.message == config.MSG_WRONG_TARGET


def test_add_tablet_twice():
    state = at(3, 2, holding="toples", tablet_added=True)
    result = run(Verb.TAMBAH, state, objek="tablet", target="toples")
    assert result.status == "REJECT"
    assert result.message == config.MSG_STEP_MISSING


# ---------------------------------------------------------------------- stir


def test_stir_after_the_tablet():
    state = at(3, 2, holding="toples", tablet_added=True)
    result = run(Verb.ADUK, state, objek="sendok")
    assert result.status == "ACCEPT"
    assert result.state.stirred is True


def test_stir_before_the_tablet():
    state = at(3, 2, holding="toples")
    result = run(Verb.ADUK, state, objek="sendok")
    assert result.status == "REJECT"
    assert result.message == config.MSG_STEP_MISSING


def test_stir_with_the_wrong_tool():
    state = at(3, 2, holding="toples", tablet_added=True)
    result = run(Verb.ADUK, state, objek="gelas")
    assert result.status == "REJECT"
    assert result.message == config.MSG_WRONG_TARGET


# --------------------------------------------------------------------- place


def test_place_the_jar_and_finish():
    state = at(3, 2, holding="toples", has_jar=True, tablet_added=True, stirred=True)
    result = run(Verb.TARUH, state, objek="toples", target="finish")
    assert result.status == "ACCEPT"
    assert result.state.finished is True
    assert result.state.holding is None


def test_place_without_stirring():
    state = at(3, 2, holding="toples", tablet_added=True)
    result = run(Verb.TARUH, state, objek="toples", target="finish")
    assert result.status == "REJECT"
    assert result.message == config.MSG_STEP_MISSING


def test_place_somewhere_else():
    state = at(3, 2, holding="toples", tablet_added=True, stirred=True)
    result = run(Verb.TARUH, state, objek="toples", target="corong")
    assert result.status == "REJECT"
    assert result.message == config.MSG_WRONG_TARGET


# ------------------------------------------------------------------- invalid


def test_invalid_command_uses_its_own_reason():
    result = run(Verb.INVALID, alasan=config.MSG_TOO_GENERAL)
    assert result.status == "REJECT"
    assert result.message == config.MSG_TOO_GENERAL


def test_invalid_command_without_a_reason():
    result = run(Verb.INVALID)
    assert result.status == "REJECT"
    assert result.message == config.MSG_UNKNOWN_VERB


# ------------------------------------------------------- purity and snapshots


def test_validate_does_not_mutate_the_input_state():
    state = at(0, 0)
    run(Verb.MAJU, state, n=2)
    assert (state.x, state.y) == (0, 0)


def test_snapshot_survives_later_changes():
    state = at(0, 2, holding="gelas")
    snapshot = state.snapshot()
    moved = run(Verb.MAJU, state, n=2).state
    assert moved.tile == (0, 4)
    assert snapshot.tile == (0, 2)
    assert snapshot.holding == "gelas"


# --------------------------------------------------- the intended 10 steps


OPTIMAL = [
    (Verb.MAJU, {"n": 2}),
    (Verb.AMBIL, {"objek": "gelas"}),
    (Verb.BELOK, {"arah": "KANAN", "n": 90}),
    (Verb.MAJU, {"n": 3}),
    (Verb.TUANG, {"objek": "gelas", "target": "corong"}),
    (Verb.TUNGGU, {"n": 10}),
    (Verb.AMBIL, {"objek": "toples"}),
    (Verb.TAMBAH, {"objek": "tablet", "target": "toples"}),
    (Verb.ADUK, {"objek": "sendok"}),
    (Verb.TARUH, {"objek": "toples", "target": "finish"}),
]

EXPECTED_FLAGS = [
    "has_cup",
    "poured",
    "filtered",
    "has_jar",
    "tablet_added",
    "stirred",
    "finished",
]


def test_optimal_solution_reaches_finished_in_ten_steps():
    state = WorldState()
    seen: list[str] = []
    for index, (verb, fields) in enumerate(OPTIMAL, start=1):
        result = judge.validate(Command(verb=verb, **fields), state)
        assert result.status == "ACCEPT", f"step {index} ({verb}) was rejected"
        state = result.state
        for flag in EXPECTED_FLAGS:
            if getattr(state, flag) and flag not in seen:
                seen.append(flag)
    assert state.finished is True
    assert state.milestones == 7
    assert seen == EXPECTED_FLAGS
