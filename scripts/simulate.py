"""Dry run: both teams play the optimal solution, no AI, no browser, no hardware.

    uv run python scripts/simulate.py

Useful at the rehearsal desk when there is no audience to type for you.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engineer_bot import config  # noqa: E402
from engineer_bot.game import Game  # noqa: E402

OPTIMAL = [
    "maju 2",
    "ambil gelas",
    "belok kanan 90",
    "maju 3",
    "tuang gelas ke corong",
    "tunggu 10 detik",
    "ambil toples",
    "tambah tablet ke toples",
    "aduk dengan sendok",
    "taruh toples di area finish",
]


class FakeClock:
    """Advances 1.5 s per instruction so the debounce and timers behave."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float = 1.5) -> None:
        self.now += seconds


async def main() -> int:
    clock = FakeClock()
    settings = config.Settings(deepseek_api_key="")
    game = Game(settings, clock=clock, log_dir=config.PROJECT_ROOT / "logs")
    game.judge_mode = "keyword"

    await game.start()
    print(f"Ronde dimulai. Giliran bergantian, {settings.max_instructions} instruksi per tim.\n")

    for index, text in enumerate(OPTIMAL):
        for team in ("a", "b"):
            if game.round_over:
                break
            before = len(game.teams[team].records)
            clock.advance()
            await game.submit(team, text)
            judged = len(game.teams[team].records) > before
            entry = game.teams[team].last if judged else None
            status = entry.status if entry else "-"
            message = entry.message if entry else "bukan gilirannya"
            print(f"  {index + 1:2}  {team.upper()} {text:<28} {status:<6} {message}")
            if game.round_over:
                break
            clock.advance()
            await game.done(team)

    if game.round_over:
        print("  (ronde selesai)")

    print()
    print(f"Pemenang: {game.winner or 'seri'}")
    print(f"Log: {game.last_log_path}")
    print()
    print(json.dumps(game.to_log("simulate"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
