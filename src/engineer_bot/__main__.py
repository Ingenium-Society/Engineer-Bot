"""`uv run python -m engineer_bot` — start the server and print the URLs.

The banner matters: on event day the operator needs the LAN address of this
laptop without opening a terminal command they will not remember.
"""

from __future__ import annotations

import argparse
import socket

import uvicorn

from . import config
from .main import app


def lan_addresses() -> list[str]:
    """Best-effort list of this machine's LAN IPv4 addresses."""
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.2)
        probe.connect(("8.8.8.8", 80))  # no packets are sent
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127.") and address not in found:
                found.append(address)
    except OSError:
        pass
    return found or ["127.0.0.1"]


def banner(host: str, port: int, settings: config.Settings) -> str:
    addresses = lan_addresses() if host in ("0.0.0.0", "") else [host]
    scheme = settings.scheme
    lan = f"{scheme}://{addresses[0]}:{port}"
    local = f"{scheme}://localhost:{port}"

    def row(label: str, value: str) -> str:
        return f"  {label:<9}: {value}"

    lines = ["", "=" * 70, "  THE ENGINEER-BOT", "=" * 70]
    # The two pages that live on this machine use localhost, which the browser
    # trusts; the team laptops need the LAN address.
    lines.append(row("Proyektor", f"{local}/screen     (F11 di layar proyektor)"))
    lines.append(row("Operator", f"{local}/admin      (PIN {settings.admin_pin})"))
    lines.append(row("Tim A", f"{lan}/team/a"))
    lines.append(row("Tim B", f"{lan}/team/b"))
    lines.append("")
    if settings.deepseek_api_key:
        lines.append(row("AI", f"aktif ({settings.deepseek_model})"))
    else:
        lines.append(row("AI", "mati - memakai parser kata kunci"))
    if settings.stt_mode == "off":
        lines.append(row("Suara", "mati (STT_MODE=off)"))
    else:
        lines.append(row("Suara", f"{settings.stt_mode} ({settings.stt_lang})"))
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
    if settings.https_cert and settings.https_key:
        tls = {"ssl_certfile": settings.https_cert, "ssl_keyfile": settings.https_key}
    uvicorn.run(app, host=args.host, port=args.port, log_level="info", **tls)


if __name__ == "__main__":
    main()
