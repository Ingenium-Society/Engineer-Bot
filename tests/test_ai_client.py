"""Translator client: any OpenAI-compatible endpoint, failure modes, cache.

Every test mocks the HTTP layer. Nothing here touches the network.
"""

from __future__ import annotations

import json

import httpx
import pytest

from engineer_bot import config, judge
from engineer_bot.ai_client import AIClient
from engineer_bot.models import Verb


def envelope(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


class Recorder:
    """Counts real (non warm-up) requests and lets a test script the replies."""

    def __init__(self, replies=None) -> None:
        self.calls: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.replies = replies or []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload = json.loads(request.content)
        if payload["messages"][-1]["content"] == "ping":
            return httpx.Response(200, json=envelope("{}"))
        self.calls.append(payload)
        if not self.replies:
            return httpx.Response(200, json=envelope('{"verb":"MAJU","n":2}'))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def build(
    recorder: Recorder,
    key: str = "test-key",
    base: str | None = None,
    model: str = "deepseek-flash",
    json_mode: bool = True,
) -> AIClient:
    settings = config.Settings(
        ai_api_key=key,
        ai_base_url=base or config.AI_BASE_URL,
        ai_model=model,
        ai_json_mode=json_mode,
        ai_timeout_seconds=3,
    )
    return AIClient(settings, transport=httpx.MockTransport(recorder))


async def test_good_json_is_parsed():
    recorder = Recorder()
    client = build(recorder)
    await client.startup()
    result = await client.translate("maju dua langkah")
    await client.aclose()

    assert result is not None
    assert result.source == "ai"
    assert result.command.verb is Verb.MAJU
    assert result.command.n == 2


async def test_request_shape_matches_the_docs():
    recorder = Recorder()
    client = build(recorder)
    await client.startup()
    await client.translate("maju 2")
    await client.aclose()

    payload = recorder.calls[0]
    assert payload["model"] == "deepseek-flash"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 80
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["messages"][0]["role"] == "system"
    assert '"""maju 2"""' in payload["messages"][-1]["content"]


async def test_unparseable_content_falls_back():
    recorder = Recorder([httpx.Response(200, json=envelope("maaf saya tidak bisa"))])
    client = build(recorder)
    await client.startup()
    assert await client.translate("maju 2") is None
    assert client.last_error == "schema_mismatch"
    await client.aclose()


async def test_unknown_verb_is_a_schema_mismatch():
    recorder = Recorder([httpx.Response(200, json=envelope('{"verb":"TERBANG","n":2}'))])
    client = build(recorder)
    await client.startup()
    assert await client.translate("terbang") is None
    assert client.last_error == "schema_mismatch"
    await client.aclose()


async def test_http_500():
    recorder = Recorder([httpx.Response(500, json={"error": "boom"})])
    client = build(recorder)
    await client.startup()
    assert await client.translate("maju 2") is None
    assert client.last_error == "http_500"
    await client.aclose()


async def test_timeout():
    recorder = Recorder([httpx.ReadTimeout("too slow")])
    client = build(recorder)
    await client.startup()
    assert await client.translate("maju 2") is None
    assert client.last_error == "timeout"
    await client.aclose()


async def test_blank_content():
    recorder = Recorder([httpx.Response(200, json=envelope("   "))])
    client = build(recorder)
    await client.startup()
    assert await client.translate("maju 2") is None
    assert client.last_error == "empty_content"
    await client.aclose()


async def test_fenced_json_is_tolerated():
    fenced = '```json\n{"verb":"AMBIL","objek":"gelas"}\n```'
    recorder = Recorder([httpx.Response(200, json=envelope(fenced))])
    client = build(recorder)
    await client.startup()
    result = await client.translate("ambil gelas")
    await client.aclose()
    assert result is not None
    assert result.command.objek == "gelas"


async def test_identical_text_is_cached():
    recorder = Recorder()
    client = build(recorder)
    await client.startup()
    await client.translate("maju 2")
    await client.translate("maju 2")
    await client.aclose()
    assert len(recorder.calls) == 1


async def test_missing_key_means_no_call():
    recorder = Recorder()
    client = build(recorder, key="")
    await client.startup()
    assert client.available is False
    assert await client.translate("maju 2") is None
    assert client.last_error == "not_configured"
    await client.aclose()


# ------------------------------------------------------------------ fallback


async def test_a_failed_call_returns_nothing_rather_than_guessing():
    """No rule-based fallback: the caller refuses the turn instead."""
    recorder = Recorder([httpx.ReadTimeout("too slow")])
    client = build(recorder)
    await client.startup()
    result = await judge.translate("maju dua langkah", ai=client)
    await client.aclose()

    assert result is None
    assert client.last_error == "timeout"


async def test_judge_uses_the_ai_when_it_works():
    recorder = Recorder()
    client = build(recorder)
    await client.startup()
    result = await judge.translate("maju dua langkah", ai=client)
    await client.aclose()
    assert result.source == "ai"


async def test_without_a_client_nothing_is_read_and_no_request_is_made():
    recorder = Recorder()
    client = build(recorder)
    await client.startup()
    result = await judge.translate("maju dua langkah", ai=None)
    await client.aclose()
    assert result is None
    assert recorder.calls == []


async def test_no_client_means_no_reading():
    assert await judge.translate("maju dua langkah", ai=None) is None


# ----------------------------------------------------------------- sanitize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  maju   2  ", ("maju 2", None)),
        ("", (None, config.MSG_EMPTY)),
        ("   ", (None, config.MSG_EMPTY)),
        ("x" * 81, (None, config.MSG_TOO_LONG)),
    ],
)
def test_sanitize(raw, expected):
    assert judge.sanitize(raw) == expected


def test_sanitize_strips_control_characters():
    clean, error = judge.sanitize("maju\x00 2\x07")
    assert error is None
    assert clean == "maju 2"


def test_sanitize_none():
    assert judge.sanitize(None) == (None, config.MSG_EMPTY)


async def test_the_model_explanation_survives_the_json_round_trip():
    payload = '{"verb":"MAJU","n":2,"alasan":"langka dibaca langkah"}'
    recorder = Recorder([httpx.Response(200, json=envelope(payload))])
    client = build(recorder)
    await client.startup()
    result = await client.translate("maju dua langka")
    await client.aclose()
    assert result is not None
    assert result.command.alasan == "langka dibaca langkah"


# ------------------------------------------------------- provider agnostic


async def test_a_local_endpoint_needs_no_key():
    recorder = Recorder()
    client = build(recorder, key="", base="http://127.0.0.1:11434/v1")
    await client.startup()
    assert client.available is True
    result = await client.translate("maju 2")
    await client.aclose()
    assert result is not None
    assert len(recorder.calls) == 1


async def test_a_remote_endpoint_still_needs_a_key():
    recorder = Recorder()
    client = build(recorder, key="", base="https://api.openai.com/v1")
    await client.startup()
    assert client.available is False
    assert await client.translate("maju 2") is None
    assert recorder.calls == []
    await client.aclose()


async def test_no_authorization_header_without_a_key():
    recorder = Recorder()
    client = build(recorder, key="", base="http://192.168.1.9:8080/v1")
    await client.startup()
    await client.translate("maju 2")
    await client.aclose()
    assert "authorization" not in {k.lower() for k in recorder.requests[-1].headers}


async def test_a_key_is_sent_as_a_bearer_token():
    recorder = Recorder()
    client = build(recorder, key="sk-test", base="https://api.openai.com/v1")
    await client.startup()
    await client.translate("maju 2")
    await client.aclose()
    assert recorder.requests[-1].headers["authorization"] == "Bearer sk-test"


async def test_deepseek_gets_the_thinking_flag_and_others_do_not():
    recorder = Recorder()
    client = build(recorder, base="https://api.deepseek.com")
    await client.startup()
    await client.translate("maju 2")
    await client.aclose()
    assert recorder.calls[-1]["thinking"] == {"type": "disabled"}

    recorder = Recorder()
    client = build(recorder, base="http://127.0.0.1:1234/v1", key="")
    await client.startup()
    await client.translate("maju 2")
    await client.aclose()
    assert "thinking" not in recorder.calls[-1]


async def test_the_json_mode_field_can_be_turned_off():
    recorder = Recorder()
    client = build(recorder, base="http://127.0.0.1:8080/v1", key="", json_mode=False)
    await client.startup()
    await client.translate("maju 2")
    await client.aclose()
    assert "response_format" not in recorder.calls[-1]


@pytest.mark.parametrize(
    "base",
    [
        "https://api.deepseek.com",
        "https://api.openai.com/v1",
        "http://127.0.0.1:11434/v1",
        "http://127.0.0.1:8080/v1/",
    ],
)
async def test_every_base_url_resolves_to_chat_completions(base):
    recorder = Recorder()
    client = build(recorder, base=base)
    await client.startup()
    await client.translate("maju 2")
    await client.aclose()
    assert str(recorder.requests[-1].url).endswith("/chat/completions")
    assert "/chat/completions/chat/completions" not in str(recorder.requests[-1].url)


@pytest.mark.parametrize(
    "content",
    [
        '{"verb":"MAJU","n":2}',
        '```json\n{"verb":"MAJU","n":2}\n```',
        'Tentu: {"verb":"MAJU","n":2} semoga membantu.',
    ],
)
async def test_fenced_and_chatty_json_both_parse(content):
    recorder = Recorder([httpx.Response(200, json=envelope(content))])
    client = build(recorder)
    await client.startup()
    result = await client.translate("maju dua langkah")
    await client.aclose()
    assert result is not None
    assert result.command.verb is Verb.MAJU


async def test_content_returned_as_parts_is_joined():
    body = {
        "choices": [{"message": {"content": [{"type": "text", "text": '{"verb":"MAJU","n":3}'}]}}]
    }
    recorder = Recorder([httpx.Response(200, json=body)])
    client = build(recorder)
    await client.startup()
    result = await client.translate("maju tiga langkah")
    await client.aclose()
    assert result is not None
    assert result.command.n == 3
