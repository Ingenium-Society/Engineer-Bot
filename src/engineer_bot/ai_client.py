"""The translator client: any OpenAI-compatible chat endpoint.

Nothing here is tied to one provider. Point ``AI_BASE_URL`` at DeepSeek, OpenAI,
Ollama, llama.cpp, vLLM or LM Studio and it works, because:

* the request is plain ``POST {base}/chat/completions`` with the standard body
* ``Authorization`` is only sent when there is a key, so keyless local servers
  are not handed a bogus ``Bearer``
* the one provider-specific field (DeepSeek's ``thinking``) is only added when
  the host is actually DeepSeek
* the reply is read from ``choices[0].message.content``, tolerating both a
  string and the newer list-of-parts shape, and fenced or chatty JSON

One client is created at startup and reused (keep-alive), because a fresh TLS
handshake per instruction would eat most of the timeout.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections import OrderedDict
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from . import config
from .models import Command, TranslationResult

CACHE_SIZE = 256
_FENCE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


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
        """Configured well enough to try: a model, and either a key or a local host."""
        if not self.settings.ai_model:
            return False
        return self.settings.ai_configured

    def _endpoint(self) -> str:
        base = self.settings.ai_base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def _is_openrouter(self) -> bool:
        host = urlparse(self.settings.ai_base_url).hostname or ""
        return host.endswith("openrouter.ai")

    def _is_deepseek(self) -> bool:
        host = urlparse(self.settings.ai_base_url).hostname or ""
        return host.endswith("deepseek.com")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.ai_api_key:
            headers["Authorization"] = f"Bearer {self.settings.ai_api_key}"
        if self._is_openrouter():
            # Optional on OpenRouter, but it is how the app shows up in their
            # dashboard, and free-tier limits are per-app.
            headers["HTTP-Referer"] = "https://github.com/Ingenium-Society/Engineer-Bot"
            headers["X-Title"] = "The Engineer-Bot"
        return headers

    async def startup(self) -> None:
        """Create the keep-alive client and warm the connection up."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.ai_timeout_seconds),
                headers=self._headers(),
                transport=self._transport,
            )
        if not self.available:
            return
        with contextlib.suppress(Exception):  # warm-up is best effort, never fatal
            await self._client.post(
                self._endpoint(),
                json={
                    "model": self.settings.ai_model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                    "stream": False,
                    **({"thinking": {"type": "disabled"}} if self._is_deepseek() else {}),
                },
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- translation -------------------------------------------------------

    def _payload(self, text: str) -> dict:
        payload: dict = {
            "model": self.settings.ai_model,
            "messages": [
                {"role": "system", "content": config.SYSTEM_PROMPT},
                {"role": "user", "content": config.USER_PROMPT_TEMPLATE.format(text=text)},
            ],
            "temperature": 0,
            "max_tokens": 80,
            "stream": False,
        }
        if self.settings.ai_json_mode:
            payload["response_format"] = {"type": "json_object"}
        # DeepSeek thinks by default, which blows the 3 s budget. Only send the
        # flag where it is understood.
        if self._is_deepseek():
            payload["thinking"] = {"type": "disabled"}
        return payload

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
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        if isinstance(content, list):  # some servers answer with content parts
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content or ""

    @staticmethod
    def _parse(content: str) -> Command | None:
        """Accept a bare object, a fenced block, or an object inside prose."""
        raw = _FENCE.sub("", content).strip()
        if not raw:
            return None
        try:
            return Command.model_validate_json(raw)
        except ValidationError:
            match = _JSON_OBJECT.search(raw)
            if match is None:
                return None
            try:
                return Command.model_validate_json(match.group(0))
            except ValidationError:
                return None

    async def translate(self, text: str) -> TranslationResult | None:
        """Return a translated command, or ``None`` if the model let us down."""
        if not self.available or self._client is None:
            self.last_error = "not_configured"
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

        content = self._extract_content(body)
        if not content.strip():
            # JSON mode replaces unparseable output with blank content.
            self.last_error = "empty_content"
            return None

        command = self._parse(content)
        if command is None:
            self.last_error = "schema_mismatch"
            return None

        self.last_error = None
        self._cache_put(key, command)
        return TranslationResult(command=command, source="ai", latency_ms=latency)
