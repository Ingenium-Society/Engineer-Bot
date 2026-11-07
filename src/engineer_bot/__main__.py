"""`uv run python -m engineer_bot` — start the server and print the URLs.

The banner matters: on event day the operator needs the LAN address of this
laptop without opening a terminal command they will not remember.
"""

from __future__ import annotations

import argparse
import socket
import threading
from urllib.parse import urlparse

import uvicorn

from . import config
from .main import app


def _hostname_addresses(timeout: float = 0.5) -> list[str]:
    """Hostname lookup in a bounded thread: a slow resolver must not stall startup."""
    found: list[str] = []

    def lookup() -> None:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = info[4][0]
                if not address.startswith("127."):
                    found.append(address)
        except OSError:
            pass

    thread = threading.Thread(target=lookup, daemon=True)
    thread.start()
    thread.join(timeout)
    return found


def lan_addresses() -> list[str]:
    """Best-effort LAN IPv4 of this machine.

    The UDP connect is instant and needs no DNS, so it answers in practice; the
    hostname lookup is only a fallback for a machine with no default route.
    """
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("8.8.8.8", 80))  # no packets are sent
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    if not found:
        found = _hostname_addresses()
    return found or ["127.0.0.1"]


def banner(host: str, port: int, settings: config.Settings) -> str:
    addresses = lan_addresses() if host in ("0.0.0.0", "") else [host]
    scheme = settings.scheme
    local = f"{scheme}://localhost:{port}"
    # Tunnel mode: the team laptops use the public URL, not this machine's IP.
    team_base = settings.public_url or f"{scheme}://{addresses[0]}:{port}"

    def row(label: str, value: str) -> str:
        return f"  {label:<9}: {value}"

    lines = ["", "=" * 70, "  THE ENGINEER-BOT", "=" * 70]
    lines.append(row("Proyektor", f"{local}/screen     (layar penuh di proyektor)"))
    lines.append(row("Operator", f"{local}/admin      (PIN {settings.admin_pin})"))
    lines.append(row("Tim A", f"{team_base}/team/a"))
    lines.append(row("Tim B", f"{team_base}/team/b"))
    lines.append("")
    if settings.ai_configured:
        host_name = urlparse(settings.ai_base_url).hostname or settings.ai_base_url
        lines.append(row("AI", f"{settings.ai_model} @ {host_name}"))
    else:
        lines.append(row("AI", "mati - memakai parser kata kunci"))
    if settings.stt_mode == "off":
        lines.append(row("Suara", "mati (STT_MODE=off)"))
    else:
        lines.append(row("Suara", f"{settings.stt_mode} ({settings.stt_lang})"))
    if settings.tunnel:
        lines.append(row("Jaringan", f"tunnel -> {settings.public_url}"))
        lines.append(row("HTTPS", "ditangani tunnel (TLS lokal mati)"))
        if settings.https_cert or settings.https_key:
            lines.append("             (HTTPS_CERT/HTTPS_KEY diabaikan selama PUBLIC_URL diisi)")
    else:
        lines.append(row("Jaringan", f"lokal {addresses[0]}"))
        if scheme == "https":
            lines.append(row("HTTPS", "aktif - percayakan sertifikat di laptop tim"))
        else:
            lines.append(row("HTTPS", "mati - mikrofon diblokir di http dari laptop lain"))
    lines.append(row("Batas", f"{settings.max_instructions} instruksi / tim"))
    lines.append("=" * 70)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run The Engineer-Bot server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    settings = config.Settings.from_env()
    print(banner(args.host, args.port, settings), flush=True)

    tls: dict[str, str] = {}
    proxy: dict[str, object] = {}
    if settings.tunnel:
        # The tunnel terminates TLS and forwards over http, so trust its headers.
        proxy = {"proxy_headers": True, "forwarded_allow_ips": "*"}
    elif settings.https_cert and settings.https_key:
        tls = {"ssl_certfile": settings.https_cert, "ssl_keyfile": settings.https_key}
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", **tls, **proxy)


if __name__ == "__main__":
    main()
