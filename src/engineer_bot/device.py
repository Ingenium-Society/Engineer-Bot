"""Output devices: the two ESP32 screens, or a mock when no hardware is wired.

The protocol is one line of text per message at 115200 baud:

    ACCEPT|MAJU 2 LANGKAH
    REJECT|Berapa langkah?
    WIN
    RESET

Serial writes happen in a worker thread so a slow or dead port can never block
the event loop or a turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from typing import Protocol

from . import config

log = logging.getLogger(__name__)


def sanitize_device_text(text: str, limit: int = config.DEVICE_TEXT_MAX) -> str:
    """ASCII-printable, single line, at most ``limit`` characters."""
    cleaned = "".join(char for char in text if 32 <= ord(char) < 127 or char == " ")
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]


def format_line(kind: str, text: str = "") -> str:
    if kind not in config.DEVICE_PREFIXES:
        raise ValueError(f"unknown device message: {kind}")
    if not text:
        return kind
    return f"{kind}|{sanitize_device_text(text)}"


class Device(Protocol):
    name: str
    status: str
    port: str
    last_line: str

    async def send(self, line: str) -> None: ...

    async def close(self) -> None: ...


class MockDevice:
    """Logs to the console and keeps the last lines for the admin page."""

    def __init__(self, name: str = "mock", port: str = "") -> None:
        self.name = name
        self.port = port
        self.status = "mock"
        self.last_line = ""
        self.lines: deque[str] = deque(maxlen=20)

    async def send(self, line: str) -> None:
        self.last_line = line
        self.lines.append(line)
        log.info("[%s] %s", self.name, line)

    async def close(self) -> None:
        return None


class SerialDevice:
    """Real hardware. Every failure is swallowed and logged: the game goes on."""

    def __init__(self, port: str, baud: int, name: str = "serial") -> None:
        import serial  # imported lazily so the module works without pyserial

        self.name = name
        self.port = port
        self.status = "connected"
        self.last_line = ""
        self._serial = serial.Serial(port, baud, timeout=0, write_timeout=1)
        self._lock = asyncio.Lock()

    def _write(self, line: str) -> None:
        self._serial.write((line + "\n").encode("utf-8"))
        self._serial.flush()

    async def send(self, line: str) -> None:
        self.last_line = line
        async with self._lock:
            try:
                await asyncio.to_thread(self._write, line)
                self.status = "connected"
            except Exception as exc:  # noqa: BLE001 - hardware must never crash a round
                self.status = "error"
                log.warning("serial write failed on %s: %s", self.port, exc)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(self._serial.close)


def build_device(port: str, baud: int, name: str) -> Device:
    """Serial when a port is configured and opens, mock otherwise."""
    if not port:
        return MockDevice(name=f"{name}-mock")
    try:
        return SerialDevice(port, baud, name=name)
    except Exception as exc:  # noqa: BLE001
        log.warning("cannot open serial port %s (%s); using mock", port, exc)
        device = MockDevice(name=f"{name}-mock", port=port)
        device.status = "error"
        return device
