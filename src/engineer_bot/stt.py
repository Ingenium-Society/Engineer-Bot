"""Server-side speech to text (phase 2): faster-whisper on CPU.

The model is loaded once and warmed once at startup, and every transcription
runs in a single-worker thread so a two second decode never blocks the event
loop, a turn, or the serial writer.

Nothing here is required to run the game: without the ``stt`` extra the service
is unavailable and the page falls back to the keyboard.
"""

from __future__ import annotations

import asyncio
import io
import logging
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from . import config

log = logging.getLogger(__name__)


class SttError(Exception):
    """A failure the HTTP layer can turn into a status code and a reason."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class Transcriber(Protocol):
    available: bool

    def warmup(self) -> None: ...

    def transcribe(self, path: Path) -> str: ...


def _silence_wav(seconds: float = 0.4, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


class WhisperTranscriber:
    """faster-whisper, int8 on CPU. Imported lazily so the game runs without it."""

    available = True

    def __init__(self, model_name: str = config.WHISPER_MODEL, language: str = "id") -> None:
        self.model_name = model_name
        self.language = language
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel  # optional dependency

        log.info("loading whisper model %s", self.model_name)
        self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")

    def warmup(self) -> None:
        self.load()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(_silence_wav())
            path = Path(handle.name)
        try:
            self.transcribe(path)
        finally:
            path.unlink(missing_ok=True)

    def transcribe(self, path: Path) -> str:
        self.load()
        segments, _info = self._model.transcribe(
            str(path),
            language=self.language,
            beam_size=1,
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()


class NullTranscriber:
    """Stands in when the mode is off or the extra is not installed."""

    available = False

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def warmup(self) -> None:
        return None

    def transcribe(self, path: Path) -> str:
        raise SttError(503, self.reason)


def base_language(tag: str) -> str:
    """'id-ID' -> 'id', which is what whisper wants."""
    return (tag or "id").split("-")[0].lower()


def build_transcriber(settings: config.Settings) -> Transcriber:
    if settings.stt_mode != "server":
        return NullTranscriber("Mode suara server tidak aktif.")
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return NullTranscriber("faster-whisper belum terpasang (uv sync --extra stt).")
    return WhisperTranscriber(settings.whisper_model, base_language(settings.stt_lang))


def _suffix_for(content_type: str | None) -> str:
    base = (content_type or "").split(";")[0].strip().lower()
    if "ogg" in base:
        return ".ogg"
    if "wav" in base:
        return ".wav"
    if "mp4" in base or "mpeg" in base:
        return ".mp4"
    return ".webm"


class SttService:
    def __init__(
        self,
        transcriber: Transcriber,
        max_bytes: int = config.STT_MAX_UPLOAD_BYTES,
    ) -> None:
        self.transcriber = transcriber
        self.max_bytes = max_bytes
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="stt")

    @property
    def available(self) -> bool:
        return self.transcriber.available

    async def startup(self) -> None:
        if not self.available:
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self.transcriber.warmup)
            log.info("stt ready")
        except Exception as exc:  # noqa: BLE001 - a cold model must not stop the server
            log.warning("stt warmup failed: %s", exc)

    async def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def transcribe(self, data: bytes, content_type: str | None) -> tuple[str, float]:
        if not self.available:
            reason = getattr(self.transcriber, "reason", "Layanan suara tidak aktif.")
            raise SttError(503, reason)
        if not data:
            raise SttError(400, "Audio kosong.")
        if len(data) > self.max_bytes:
            raise SttError(413, "Audio terlalu besar.")

        base = (content_type or "").split(";")[0].strip().lower()
        if base and not (base.startswith("audio/") or base == "application/octet-stream"):
            raise SttError(415, "Format audio tidak didukung.")

        started = time.perf_counter()
        loop = asyncio.get_running_loop()
        with tempfile.NamedTemporaryFile(suffix=_suffix_for(base), delete=False) as handle:
            handle.write(data)
            path = Path(handle.name)
        try:
            text = await loop.run_in_executor(self._executor, self.transcriber.transcribe, path)
        except SttError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("stt failed: %s", exc)
            raise SttError(500, "Gagal memproses audio.") from exc
        finally:
            # The clip is never kept: it is the players' voices.
            await asyncio.to_thread(path.unlink, missing_ok=True)

        return (text or "").strip(), (time.perf_counter() - started) * 1000
