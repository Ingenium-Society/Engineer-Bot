"""Dry run: both teams play the optimal solution, no browser, no hardware.

    uv run python scripts/simulate.py

The translator is the model in the app; here a stub reads the ten fixed
phrases, so the dry run needs no endpoint. Useful at the rehearsal desk when
there is no audience to type for you.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from engineer_bot import config  # noqa: E402
from engineer_bot.game import Game  # noqa: E402
from engineer_bot.models import Command, TranslationResult  # noqa: E402

OPTIMAL = [
    "maju 2",
    "taruh pemurni di corong",
    "ambil botol",
    "tuang botol ke corong",
    "tunggu 5 detik",
    "ambil hasil",
]


#: What the stub reads. The real translator is the model; there is no
#: rule-based fallback any more, so the dry run has to bring its own.
READINGS: dict[str, Command] = {
    "maju 2": Command(verb="MAJU", n=2),
    "taruh pemurni di corong": Command(verb="TARUH", objek="pemurni", target="corong"),
    "ambil botol": Command(verb="AMBIL", objek="botol"),
    "tuang botol ke corong": Command(verb="TUANG", objek="botol", target="corong"),
    "tunggu 5 detik": Command(verb="TUNGGU", n=5),
    "ambil hasil": Command(verb="AMBIL", objek="hasil"),
}


class StubModel:
    available = True
    last_error = None

    async def translate(self, text: str) -> TranslationResult | None:
        command = READINGS.get(" ".join(text.lower().split()))
        if command is None:
            return None
        return TranslationResult(command=command, source="ai", latency_ms=1.0)


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
    settings = config.Settings(ai_api_key="")
    game = Game(settings, ai=StubModel(), clock=clock, log_dir=config.PROJECT_ROOT / "logs")

    async def always_accept(_pending):
        return "accept"

    # A dry run has no operator, so it plays the operator too.
    game.confirm = always_accept

    await game.start()
    print(f"Ronde dimulai. Giliran bergantian, {settings.max_instructions} instruksi per tim.")
    print("Juri otomatis menerima setiap usulan.\n")

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
