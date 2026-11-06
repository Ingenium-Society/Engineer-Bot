"""Phase 2 speech to text: the service contract and the upload route.

No model is ever loaded here — the transcriber is faked, so the suite stays
offline and takes milliseconds.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engineer_bot import config
from engineer_bot.main import create_app
from engineer_bot.stt import (
    NullTranscriber,
    SttError,
    SttService,
    base_language,
    build_transcriber,
)


class FakeTranscriber:
    available = True

    def __init__(self, text: str = "maju dua langkah") -> None:
        self.text = text
        self.paths: list[Path] = []
        self.warmed = False

    def warmup(self) -> None:
        self.warmed = True

    def transcribe(self, path: Path) -> str:
        assert path.exists(), "the clip must be on disk while transcribing"
        self.paths.append(path)
        return self.text


class BoomTranscriber(FakeTranscriber):
    def transcribe(self, path: Path) -> str:
        self.paths.append(path)
        raise RuntimeError("model exploded")


def service(**kwargs) -> SttService:
    return SttService(FakeTranscriber(**kwargs))


async def test_good_clip_is_transcribed():
    transcriber = FakeTranscriber("maju dua langkah")
    stt = SttService(transcriber)
    text, ms = await stt.transcribe(b"fake-opus-bytes", "audio/webm;codecs=opus")
    assert text == "maju dua langkah"
    assert ms >= 0
    await stt.close()


async def test_the_clip_is_deleted_afterwards():
    transcriber = FakeTranscriber()
    stt = SttService(transcriber)
    await stt.transcribe(b"fake-opus-bytes", "audio/webm")
    assert transcriber.paths, "the transcriber was never called"
    assert not transcriber.paths[0].exists(), "the audio was left on disk"
    await stt.close()


async def test_empty_result_is_not_an_error():
    stt = SttService(FakeTranscriber("   "))
    text, _ms = await stt.transcribe(b"x", "audio/webm")
    assert text == ""
    await stt.close()


async def test_oversized_upload_is_refused_before_transcribing():
    transcriber = FakeTranscriber()
    stt = SttService(transcriber, max_bytes=10)
    with pytest.raises(SttError) as caught:
        await stt.transcribe(b"0123456789abcdef", "audio/webm")
    assert caught.value.status_code == 413
    assert transcriber.paths == []
    await stt.close()


async def test_wrong_content_type_is_refused():
    stt = SttService(FakeTranscriber())
    with pytest.raises(SttError) as caught:
        await stt.transcribe(b"hello", "text/plain")
    assert caught.value.status_code == 415
    await stt.close()


async def test_empty_upload_is_refused():
    stt = SttService(FakeTranscriber())
    with pytest.raises(SttError) as caught:
        await stt.transcribe(b"", "audio/webm")
    assert caught.value.status_code == 400
    await stt.close()


async def test_unavailable_service_says_so():
    stt = SttService(NullTranscriber("faster-whisper belum terpasang."))
    with pytest.raises(SttError) as caught:
        await stt.transcribe(b"x", "audio/webm")
    assert caught.value.status_code == 503
    assert "faster-whisper" in caught.value.message
    await stt.close()


async def test_a_broken_model_is_a_500_and_the_file_is_still_removed():
    transcriber = BoomTranscriber()
    stt = SttService(transcriber)
    with pytest.raises(SttError) as caught:
        await stt.transcribe(b"x", "audio/webm")
    assert caught.value.status_code == 500
    assert not transcriber.paths[0].exists()
    await stt.close()


async def test_warmup_is_only_called_when_available():
    transcriber = FakeTranscriber()
    stt = SttService(transcriber)
    await stt.startup()
    assert transcriber.warmed is True
    await stt.close()

    null = SttService(NullTranscriber("off"))
    await null.startup()
    assert null.available is False
    await null.close()


# --------------------------------------------------------------------- config


def test_base_language():
    assert base_language("id-ID") == "id"
    assert base_language("EN") == "en"
    assert base_language("") == "id"


def test_transcriber_is_null_when_the_mode_is_off():
    assert isinstance(build_transcriber(config.Settings(stt_mode="off")), NullTranscriber)
    assert isinstance(build_transcriber(config.Settings(stt_mode="browser")), NullTranscriber)


# ---------------------------------------------------------------------- route


def make_client() -> TestClient:
    # stt_mode stays "browser" so the lifespan never tries to load a model.
    return TestClient(create_app(config.Settings(stt_mode="browser", ai_api_key="")))


def test_upload_route_returns_the_transcript():
    app = create_app(config.Settings(stt_mode="browser", ai_api_key=""))
    with TestClient(app) as client:
        app.state.stt = SttService(FakeTranscriber("ambil gelas"))
        response = client.post(
            "/api/stt/a",
            files={"audio": ("clip.webm", b"fake-bytes", "audio/webm")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "ambil gelas"
    assert body["ms"] >= 0


def test_upload_route_reports_a_missing_engine():
    app = create_app(config.Settings(stt_mode="browser", ai_api_key=""))
    with TestClient(app) as client:
        response = client.post(
            "/api/stt/a",
            files={"audio": ("clip.webm", b"fake-bytes", "audio/webm")},
        )
    assert response.status_code == 503
    assert "error" in response.json()


def test_upload_route_rejects_an_unknown_team():
    app = create_app(config.Settings(stt_mode="browser", ai_api_key=""))
    with TestClient(app) as client:
        app.state.stt = SttService(FakeTranscriber())
        response = client.post(
            "/api/stt/z",
            files={"audio": ("clip.webm", b"fake-bytes", "audio/webm")},
        )
    assert response.status_code == 404
