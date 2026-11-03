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
    lines = ["", "=" * 62, "  THE ENGINEER-BOT", "=" * 62]
    for address in addresses:
        base = f"http://{address}:{port}"
        lines += [
            f"  Operator : {base}/admin     (PIN {settings.admin_pin})",
            f"  Tim A    : {base}/team/a",
            f"  Tim B    : {base}/team/b",
            f"  Layar    : {base}/screen",
            "",
        ]
    if settings.deepseek_api_key:
        lines.append(f"  AI       : aktif ({settings.deepseek_model})")
    else:
        lines.append("  AI       : mati - memakai parser kata kunci")
    lines.append(f"  Batas    : {settings.max_instructions} instruksi / tim")
    lines.append("=" * 62)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run The Engineer-Bot server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    settings = config.Settings.from_env()
    print(banner(args.host, args.port, settings), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
