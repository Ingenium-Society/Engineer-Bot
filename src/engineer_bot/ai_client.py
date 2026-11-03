"""DeepSeek client.

Verified against the official docs (2026-11):

* endpoint ``POST https://api.deepseek.com/chat/completions``
* current model name ``deepseek-flash`` (``deepseek-chat`` is the legacy alias)
* JSON mode = ``response_format: {"type": "json_object"}``; the prompt must
  contain the word "JSON", and a failed parse comes back as blank content
* thinking mode is **on by default**, which is far too slow for a 3 s budget,
  so it is explicitly disabled

One client is created at startup and reused (keep-alive), because a fresh TLS
handshake per instruction would eat most of the timeout.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections import OrderedDict

import httpx
from pydantic import ValidationError

from . import config
from .models import Command, TranslationResult

CACHE_SIZE = 256
_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


class AIClient:
    """Thin, replaceable wrapper. Returns ``None`` on any failure so the caller
    can fall back to the keyword parser."""

    def __init__(
        self,
        settings: config.Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._cache: OrderedDict[str, Command] = OrderedDict()
        self.last_error: str | None = None
        self.last_latency_ms: float | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def available(self) -> bool:
        return bool(self.settings.deepseek_api_key)

    def _endpoint(self) -> str:
        base = self.settings.deepseek_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    async def startup(self) -> None:
        """Create the keep-alive client and warm the connection up."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.ai_timeout_seconds),
                headers={
                    "Authorization": f"Bearer {self.settings.deepseek_api_key}",
                    "Content-Type": "application/json",
                },
                transport=self._transport,
            )
        if not self.available:
            return
        with contextlib.suppress(Exception):  # warm-up is best effort, never fatal
            await self._client.post(
                self._endpoint(),
                json={
                    "model": self.settings.deepseek_model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "thinking": {"type": "disabled"},
                    "stream": False,
                },
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- translation -------------------------------------------------------

    def _payload(self, text: str) -> dict:
        return {
            "model": self.settings.deepseek_model,
            "messages": [
                {"role": "system", "content": config.SYSTEM_PROMPT},
                {"role": "user", "content": config.USER_PROMPT_TEMPLATE.format(text=text)},
            ],
            "temperature": 0,
            "max_tokens": 80,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
        }

    def _cache_get(self, key: str) -> Command | None:
        if key not in self._cache:
            return None
        self._cache.move_to_end(key)
        return self._cache[key]

    def _cache_put(self, key: str, command: Command) -> None:
        self._cache[key] = command
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)

    @staticmethod
    def _extract_content(body: dict) -> str:
        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return ""

    async def translate(self, text: str) -> TranslationResult | None:
        """Return a translated command, or ``None`` if the model let us down."""
        if not self.available or self._client is None:
            self.last_error = "no_api_key"
            return None

        key = " ".join(text.lower().split())
        cached = self._cache_get(key)
        if cached is not None:
            self.last_error = None
            return TranslationResult(command=cached, source="ai", latency_ms=0.0)

        started = time.perf_counter()
        try:
            response = await self._client.post(self._endpoint(), json=self._payload(text))
        except httpx.TimeoutException:
            self.last_error = "timeout"
            return None
        except httpx.HTTPError as exc:
            self.last_error = f"http_error:{type(exc).__name__}"
            return None

        latency = (time.perf_counter() - started) * 1000
        self.last_latency_ms = latency

        if response.status_code != 200:
            self.last_error = f"http_{response.status_code}"
            return None

        try:
            body = response.json()
        except json.JSONDecodeError:
            self.last_error = "bad_envelope"
            return None

        content = _FENCE.sub("", self._extract_content(body)).strip()
        if not content:
            # JSON mode replaces unparseable output with spaces.
            self.last_error = "empty_content"
            return None

        try:
            command = Command.model_validate_json(content)
        except ValidationError:
            self.last_error = "schema_mismatch"
            return None

        self.last_error = None
        self._cache_put(key, command)
        return TranslationResult(command=command, source="ai", latency_ms=latency)
