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


def test_take_the_bottle():
    result = run(Verb.AMBIL, at(0, 2), objek="botol")
    assert result.status == "ACCEPT"
    assert result.state.holding == "botol"
    assert result.state.has_bottle is True


def test_take_the_result_after_the_wait():
    result = run(Verb.AMBIL, at(0, 2, poured=True, waited=True), objek="hasil")
    assert result.status == "ACCEPT"
    assert result.state.finished is True


def test_take_the_result_before_the_wait_is_too_early():
    result = run(Verb.AMBIL, at(0, 2, poured=True), objek="hasil")
    assert result.status == "REJECT"
    assert result.message == config.MSG_STEP_MISSING


def test_take_from_the_wrong_tile():
    result = run(Verb.AMBIL, objek="botol")
    assert result.message == config.MSG_OUT_OF_REACH.format(objek="botol")


def test_take_with_a_full_hand():
    result = run(Verb.AMBIL, at(0, 2, holding="botol"), objek="hasil")
    assert result.message == config.MSG_HANDS_FULL


def test_take_something_that_cannot_be_held():
    result = run(Verb.AMBIL, at(0, 2), objek="pemurni")
    assert result.message == config.MSG_NOT_TAKEABLE


def test_take_without_an_object():
    result = run(Verb.AMBIL, at(0, 2))
    assert result.message == config.MSG_NEED_DIRECTION


def test_take_the_bottle_twice():
    result = run(Verb.AMBIL, at(0, 2, has_bottle=True), objek="botol")
    assert result.status == "REJECT"


# -------------------------------------------------------------------- place


def test_place_the_purifier():
    """One vessel, so "taruh pemurni" needs no target."""
    result = run(Verb.TARUH, at(0, 2), objek="pemurni")
    assert result.status == "ACCEPT"
    assert result.state.pemurni_placed is True


def test_place_the_purifier_with_a_target_also_works():
    result = run(Verb.TARUH, at(0, 2), objek="pemurni", target="corong")
    assert result.status == "ACCEPT"


def test_place_somewhere_else():
    result = run(Verb.TARUH, at(0, 2), objek="pemurni", target="hasil")
    assert result.message == config.MSG_WRONG_TARGET


def test_place_the_purifier_twice():
    result = run(Verb.TARUH, at(0, 2, pemurni_placed=True), objek="pemurni", target="corong")
    assert result.message == config.MSG_STEP_MISSING


def test_place_away_from_the_vessel():
    result = run(Verb.TARUH, objek="pemurni")
    assert result.message == config.MSG_OUT_OF_REACH.format(objek="corong")


def test_place_without_an_object():
    result = run(Verb.TARUH, at(0, 2))
    assert result.message == config.MSG_NEED_DIRECTION


# --------------------------------------------------------------------- pour


def test_pour_the_bottle():
    result = run(
        Verb.TUANG, at(0, 2, holding="botol", pemurni_placed=True), objek="botol"
    )
    assert result.status == "ACCEPT"
    assert result.state.poured is True
    assert result.state.holding is None


def test_pour_before_the_purifier_is_placed():
    result = run(Verb.TUANG, at(0, 2, holding="botol"), objek="botol")
    assert result.status == "REJECT"
    assert result.message == config.MSG_STEP_MISSING


def test_pour_without_holding_anything():
    result = run(Verb.TUANG, at(0, 2, pemurni_placed=True), objek="botol")
    assert result.message == config.MSG_NOT_HOLDING.format(objek="botol")


def test_pour_into_the_wrong_target():
    result = run(
        Verb.TUANG,
        at(0, 2, holding="botol", pemurni_placed=True),
        objek="botol",
        target="hasil",
    )
    assert result.message == config.MSG_WRONG_TARGET


def test_pour_twice():
    result = run(
        Verb.TUANG,
        at(0, 2, holding="botol", pemurni_placed=True, poured=True),
        objek="botol",
        target="corong",
    )
    assert result.message == config.MSG_STEP_MISSING


# --------------------------------------------------------------------- wait


def test_wait_long_enough_lets_the_water_through():
    result = run(Verb.TUNGGU, at(0, 2, poured=True), n=config.FILTER_SECONDS)
    assert result.status == "ACCEPT"
    assert result.state.waited is True


def test_short_wait_is_accepted_but_does_nothing():
    result = run(Verb.TUNGGU, at(0, 2, poured=True), n=1)
    assert result.status == "ACCEPT"
    assert result.state.waited is False


def test_wait_before_pouring_is_too_early():
    result = run(Verb.TUNGGU, at(0, 2), n=config.FILTER_SECONDS)
    assert result.status == "REJECT"
    assert result.message == config.MSG_STEP_MISSING


# ------------------------------------------------------------------- invalid


def test_invalid_command_uses_its_own_reason():
    result = run(Verb.INVALID, alasan="dua aksi sekaligus")
    assert result.status == "REJECT"
    assert result.message == "dua aksi sekaligus"


def test_invalid_command_without_a_reason():
    result = run(Verb.INVALID)
    assert result.message == config.MSG_UNKNOWN_VERB


# ------------------------------------------------------- purity and snapshots


def test_validate_does_not_mutate_the_input_state():
    state = WorldState()
    run(Verb.MAJU, state, n=2)
    assert state.tile == (config.START_X, config.START_Y)


def test_snapshot_survives_later_changes():
    state = WorldState()
    snapshot = state.snapshot()
    state.pemurni_placed = True
    assert snapshot.pemurni_placed is False


# --------------------------------------------------- the six intended steps

SIX_BEATS = [
    (Verb.MAJU, {"n": 2}),
    (Verb.TARUH, {"objek": "pemurni"}),
    (Verb.AMBIL, {"objek": "botol"}),
    (Verb.TUANG, {"objek": "botol"}),
    (Verb.TUNGGU, {"n": config.FILTER_SECONDS}),
    (Verb.AMBIL, {"objek": "hasil"}),
]


def test_the_six_beats_finish_the_task():
    """maju, taruh pemurni, ambil botol, tuang, tunggu, ambil hasil."""
    state = WorldState()
    for verb, fields in SIX_BEATS:
        result = judge.validate(Command(verb=verb, **fields), state)
        assert result.status == "ACCEPT", (verb.value, result.message)
        state = result.state
    assert state.finished is True
    assert state.milestones == 5


# ------------------------------------------------------------------- summary


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (Command(verb="BELOK", arah="KANAN", n=90), "BELOK KANAN 90"),
        (Command(verb="MAJU", n=2), "MAJU 2"),
        (Command(verb="AMBIL", objek="gelas"), "AMBIL gelas"),
        (Command(verb="TUANG", objek="gelas", target="corong"), "TUANG gelas corong"),
        (Command(verb="INVALID", alasan="ngawur"), "INVALID"),
    ],
)
def test_command_summary_is_readable_at_a_glance(command, expected):
    assert command.summary == expected
