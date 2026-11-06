"""Speech input: messy transcripts, operator fallback, and voice plumbing."""

from __future__ import annotations

import asyncio

import pytest

from engineer_bot import config, judge
from engineer_bot.keyword_parser import parse_keyword
from engineer_bot.models import Verb, WorldState

# Transcripts as a speech engine actually returns them: no punctuation, fillers
# everywhere, the odd misheard word.
SPEECH_VALID = [
    ("eh maju dua langkah", Verb.MAJU, {"n": 2}),
    ("hmm maju 3", Verb.MAJU, {"n": 3}),
    ("oke ya maju tiga", Verb.MAJU, {"n": 3}),
    ("maju dua langka", Verb.MAJU, {"n": 2}),
    ("jalan kedepan dua", Verb.MAJU, {"n": 2}),
    ("mundur satu langkah", Verb.MUNDUR, {"n": 1}),
    ("maju dua belas", Verb.MAJU, {"n": 12}),
    ("eh belok kekiri 90", Verb.BELOK, {"arah": "KIRI", "n": 90}),
    ("belok ke kanan sembilan puluh", Verb.BELOK, {"arah": "KANAN", "n": 90}),
    ("belok kanan sembilan puluh derajat", Verb.BELOK, {"arah": "KANAN", "n": 90}),
    ("nah ambil gelas nya", Verb.AMBIL, {"objek": "gelas"}),
    ("ambil gelasnya", Verb.AMBIL, {"objek": "gelas"}),
    ("ambilkan gelas", Verb.AMBIL, {"objek": "gelas"}),
    ("ya ambil toplesnya", Verb.AMBIL, {"objek": "toples"}),
    ("tuang gelas ke corong", Verb.TUANG, {"objek": "gelas", "target": "corong"}),
    ("tuangkan gelasnya ke saringan", Verb.TUANG, {"objek": "gelas", "target": "corong"}),
    ("tunggu sepuluh detik", Verb.TUNGGU, {"n": 10}),
    ("anu tunggu tiga puluh detik", Verb.TUNGGU, {"n": 30}),
    ("tambah tablet ke toples", Verb.TAMBAH, {"objek": "tablet", "target": "toples"}),
    ("masukkan tabletnya ke botol", Verb.TAMBAH, {"objek": "tablet", "target": "toples"}),
    ("aduk pakai sendok", Verb.ADUK, {"objek": "sendok"}),
    ("aduuk dengan sendok", Verb.ADUK, {"objek": "sendok"}),
    ("taruh toplesnya di area finis", Verb.TARUH, {"objek": "toples", "target": "finish"}),
    ("letakkan toples di garis finis", Verb.TARUH, {"objek": "toples", "target": "finish"}),
    ("oke maju 2 langkah", Verb.MAJU, {"n": 2}),
    ("maju dua langkah ya", Verb.MAJU, {"n": 2}),
]


@pytest.mark.parametrize(("text", "verb", "fields"), SPEECH_VALID)
def test_speech_transcripts_parse(text, verb, fields):
    command = parse_keyword(text)
    assert command.verb is verb, f"{text!r} -> {command.verb}"
    for key, value in fields.items():
        assert getattr(command, key) == value, f"{text!r} field {key}"


# Incomplete speech must stay incomplete: a missing number, direction or object
# is never guessed, however noisy the sentence is.
SPEECH_INCOMPLETE = [
    "maju",
    "eh maju",
    "hmm maju ya",
    "belok",
    "hmm belok kanan",
    "ambil",
    "eh ambil",
    "tuang gelas",
    "tuang ke corong",
    "tambah tablet",
    "taruh toples",
    "aduk",
]


@pytest.mark.parametrize("text", SPEECH_INCOMPLETE)
def test_speech_never_guesses_missing_fields(text):
    result = judge.validate(parse_keyword(text), WorldState())
    assert result.status == "REJECT"


def test_missing_step_count_still_asks_for_one():
    assert judge.validate(parse_keyword("eh maju"), WorldState()).message == config.MSG_NEED_STEPS


def test_missing_object_still_asks_for_one():
    result = judge.validate(parse_keyword("ambil"), WorldState())
    assert result.message == config.MSG_NEED_DIRECTION


@pytest.mark.parametrize("text", ["tolong dong", "eh hmm ya", "oke oke oke", "anu"])
def test_filler_only_is_not_a_command(text):
    assert parse_keyword(text).verb is Verb.INVALID


def test_truly_empty_text_is_empty_not_unknown():
    assert parse_keyword("").alasan == config.MSG_EMPTY


def test_system_prompt_covers_speech():
    assert "pengenalan suara" in config.SYSTEM_PROMPT
    assert "Jangan menebak angka" in config.SYSTEM_PROMPT


# --------------------------------------------------------------------- config


def test_stt_defaults():
    settings = config.Settings()
    assert settings.stt_mode == "browser"
    assert settings.stt_lang == "id-ID"
    assert settings.auto_submit_delay_ms == 2000
    assert settings.whisper_model == "base"
    assert settings.scheme == "http"


def test_https_scheme_follows_the_certificate():
    settings = config.Settings(https_cert="certs/cert.pem", https_key="certs/key.pem")
    assert settings.scheme == "https"


def test_invalid_stt_mode_falls_back(monkeypatch):
    monkeypatch.setenv("STT_MODE", "banana")
    assert config.Settings.from_env().stt_mode == "browser"


def test_stt_mode_can_be_off(monkeypatch):
    monkeypatch.setenv("STT_MODE", "off")
    assert config.Settings.from_env().stt_mode == "off"


# ----------------------------------------------------------------- submit flow


async def settle() -> None:
    await asyncio.sleep(0.05)


async def test_voice_submission_is_logged_with_its_raw_transcript(make_game):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2", source="voice", raw="eh maju dua langka", stt_ms=820.5)

    team = game.teams["a"]
    record = team.records[-1]
    assert record.input_source == "voice"
    assert record.raw == "eh maju dua langka"
    assert record.stt_ms == 820.5
    assert record.text == "maju 2"

    entry = game.to_log("test")["teams"]["a"]["submissions"][-1]
    assert entry["input_source"] == "voice"
    assert entry["raw"] == "eh maju dua langka"
    assert entry["stt_ms"] == 820.5


async def test_keyboard_submission_needs_no_extra_fields(make_game):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2")
    record = game.teams["a"].records[-1]
    assert record.input_source == "keyboard"
    assert record.raw is None
    assert record.stt_ms is None


async def test_voice_cannot_bypass_the_validator(make_game):
    game = make_game()
    await game.start()
    await game.submit("a", "maju", source="voice", raw="maju", stt_ms=100)
    team = game.teams["a"]
    assert team.last is not None and team.last.status == "REJECT"
    assert team.last.message == config.MSG_NEED_STEPS
    assert team.state.tile == (0, 0)


async def test_first_submission_ends_the_turn_from_any_source(make_game):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2", source="voice")
    await game.operator_submit("a", "maju 1")
    assert game.teams["a"].used == 1


# ------------------------------------------------------------- operator submit


async def test_operator_submit_counts_as_an_instruction(make_game):
    game = make_game()
    await game.start()
    await game.operator_submit("a", "maju 2")

    team = game.teams["a"]
    assert team.used == 1
    assert team.phase == "LOCKED"
    assert team.records[-1].input_source == "operator"
    assert game.to_log("test")["teams"]["a"]["submissions"][-1]["input_source"] == "operator"


async def test_operator_submit_is_judged_like_any_other(make_game):
    game = make_game()
    await game.start()
    await game.operator_submit("a", "maju")
    assert game.teams["a"].last is not None
    assert game.teams["a"].last.status == "REJECT"
    assert game.teams["a"].last.message == config.MSG_NEED_STEPS


async def test_operator_submit_is_ignored_before_the_round(make_game):
    game = make_game()
    await game.operator_submit("a", "maju 2")
    assert game.teams["a"].used == 0
    assert game.team_view("a").phase == "IDLE"


async def test_operator_submit_is_ignored_while_locked(make_game, clock):
    game = make_game()
    await game.start()
    await game.operator_submit("a", "maju 2")
    clock.advance(2)
    await game.operator_submit("a", "maju 1")
    assert game.teams["a"].used == 1


async def test_operator_submit_is_ignored_when_the_round_is_over(make_game, clock):
    game = make_game()
    await game.start()
    clock.advance(400)
    await game.tick()
    assert game.round_over is True
    before = game.teams["a"].used
    await game.operator_submit("a", "maju 2")
    assert game.teams["a"].used == before


# ------------------------------------------------------------------ stt status


async def test_stt_status_reaches_the_admin_view(make_game):
    game = make_game()
    await game.set_stt_status("a", "denied", "browser")
    view = game.admin_view()
    assert view["teams"]["a"]["stt_status"] == "denied"
    assert view["teams"]["a"]["stt_engine"] == "browser"


async def test_stt_status_rejects_unknown_values(make_game):
    game = make_game()
    await game.set_stt_status("a", "banana", "browser")
    assert game.teams["a"].stt_status is None


async def test_team_view_carries_the_stt_configuration(make_game):
    game = make_game(stt_mode="off", auto_submit_delay_ms=1500)
    view = game.team_view("a")
    assert view.stt_mode == "off"
    assert view.auto_submit_ms == 1500
    assert view.stt_lang == "id-ID"


async def test_last_raw_transcript_is_visible_to_the_operator(make_game):
    game = make_game()
    await game.start()
    await game.submit("a", "maju 2", source="voice", raw="eh maju dua", stt_ms=410)
    view = game.admin_view()
    assert view["teams"]["a"]["last_raw"] == "eh maju dua"
    assert view["teams"]["a"]["stt_ms"] == 410
    assert view["teams"]["a"]["input_source"] == "voice"


async def test_timeout_is_logged_as_its_own_source(make_game, clock):
    game = make_game()
    await game.start()
    clock.advance(21)
    await game.tick()
    assert game.teams["a"].records[-1].input_source == "timeout"
