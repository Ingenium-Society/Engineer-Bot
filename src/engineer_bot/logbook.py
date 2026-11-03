"""Round log writer.

One JSON file per round, used for the club's project report. Writing happens
once, at the end of the round, and a failure here must never break the game.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def round_filename(moment: datetime | None = None) -> str:
    moment = moment or datetime.now()
    return f"round-{moment:%Y%m%d-%H%M%S}.json"


def write_round(directory: Path | str, payload: dict[str, Any]) -> Path | None:
    directory = Path(directory)
    try:
        if not directory.exists():
            directory.mkdir(parents=True)
        path = directory / round_filename()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("round log written to %s", path)
        return path
    except OSError as exc:
        log.error("could not write round log: %s", exc)
        return None
