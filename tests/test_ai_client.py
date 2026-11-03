"""DeepSeek client: envelope handling, failure modes, cache and fallback.

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
        self.replies = replies or []

    def __call__(self, request: httpx.Request) -> httpx.Response:
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


def build(recorder: Recorder, key: str = "test-key") -> AIClient:
    settings = config.Settings(
        deepseek_api_key=key, deepseek_model="deepseek-flash", ai_timeout_seconds=3
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
    assert client.last_error == "no_api_key"
    await client.aclose()


# ------------------------------------------------------------------ fallback


async def test_judge_falls_back_to_keyword_when_the_ai_fails():
    recorder = Recorder([httpx.ReadTimeout("too slow")])
    client = build(recorder)
    await client.startup()
    result = await judge.translate("maju dua langkah", ai=client, mode="ai")
    await client.aclose()

    assert result.source == "keyword"
    assert result.command.verb is Verb.MAJU
    assert result.command.n == 2
    assert result.error == "timeout"


async def test_judge_uses_the_ai_when_it_works():
    recorder = Recorder()
    client = build(recorder)
    await client.startup()
    result = await judge.translate("maju dua langkah", ai=client, mode="ai")
    await client.aclose()
    assert result.source == "ai"


async def test_keyword_mode_skips_the_ai_entirely():
    recorder = Recorder()
    client = build(recorder)
    await client.startup()
    result = await judge.translate("maju dua langkah", ai=client, mode="keyword")
    await client.aclose()
    assert result.source == "keyword"
    assert recorder.calls == []


async def test_no_client_means_keyword():
    result = await judge.translate("maju dua langkah", ai=None, mode="ai")
    assert result.source == "keyword"
    assert result.command.verb is Verb.MAJU


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
