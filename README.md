# The Engineer-Bot

Server and pages for a 9-minute STEM Fair game about **exact instructions**. Two teams
type Indonesian commands for a club member acting as a robot; the robot only obeys what
the judge accepts. Task: purify water on a 5×5 grid.

The AI is a **translator**, not the judge. DeepSeek turns a sentence into strict JSON; a
pure Python validator decides ACCEPT or REJECT. The game runs with no internet at all.

```
browser (team A) ─┐
browser (team B) ─┼─ WebSocket ─→ FastAPI ─→ judge ─→ serial ─→ ESP32 A / B
operator (/admin) ┘                  │
                                     └─→ logs/round-YYYYmmdd-HHMMSS.json
```

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+. Nothing else.

```bash
uv sync                                   # creates .venv and installs from uv.lock
cp .env.example .env                      # then edit if you have an API key
uv run python -m engineer_bot             # starts the server on 0.0.0.0:8000
```

To start from nothing instead:

```bash
uv init --app --python 3.12 engineer-bot
cd engineer-bot
uv add fastapi "uvicorn[standard]" httpx pydantic python-dotenv pyserial
uv add --dev pytest pytest-asyncio ruff
uv run uvicorn engineer_bot.main:app --host 0.0.0.0 --port 8000
```

On startup the server prints every URL you need:

```
==============================================================
  THE ENGINEER-BOT
==============================================================
  Operator : http://192.168.1.24:8000/admin     (PIN 1234)
  Tim A    : http://192.168.1.24:8000/team/a
  Tim B    : http://192.168.1.24:8000/team/b
  Layar    : http://192.168.1.24:8000/screen

  AI       : aktif (deepseek-flash)
  Batas    : 14 instruksi / tim
==============================================================
```

Checks:

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
uv run python scripts/simulate.py          # full dry run, no AI, no browser
```

## Event day

1. **Same WiFi, no internet needed on the team laptops.** Everything (CSS, JS, fonts) is
   served by this machine. No CDN, no build step.
2. **Fix the server's IP** so the URLs do not change when it reconnects:
   macOS → System Settings → Network → Wi-Fi → Details → TCP/IP → *Manually*.
   If you cannot fix it, read the banner each time and write the URL on the whiteboard.
3. **Allow the port through the firewall** (macOS asks once; allow it). If a laptop cannot
   connect, run `nc -vz <server-ip> 8000` from it — refused/blocked is the firewall,
   not the app.
4. **Phone hotspot works** and is the most reliable option in a school hall: connect the
   server and both team laptops to the hotspot, read the IP from the banner.
5. Turn **automatic sleep off** (Energy Saver → *Prevent automatic sleeping*).
   A sleeping server drops every socket.
6. Open `/admin?pin=1234` once on the operator laptop — the PIN is then stored in a
   cookie for `/ws/admin`.

### Operator rhythm

| Step | Action |
|---|---|
| Start | **Mulai** — both teams get player 1 and 20 s per instruction |
| Each turn | the team types → the judge answers → the robot acts |
| Accepted | press **Selesai** to release the next turn |
| Wrong move | press **Ulang** — the state reverts, the instruction still counts |
| Nobody types in 20 s | the server rejects with *Waktu habis.* and advances on its own |
| A team is stuck on screen | the column turns red after 30 s in LOCKED |
| Shortcuts | `A` / `L` = Selesai for team A / B, `Shift` + same key = Ulang (editable on the page) |

Judge modes: **AI** (default, keyword fallback), **Kata kunci** (no network at all),
**Manual** (every submission waits for you to press *Paksa terima* / *Paksa tolak*).

### Measuring AI latency at the venue

The admin page shows the last DeepSeek latency and turns the dot red when the keyword
fallback was used. For a number you can quote in the report:

```bash
# ten real translations, median and max
for i in $(seq 10); do
  curl -s -o /dev/null -w "%{time_total}\n" \
    -H "Authorization: Bearer $DEEPSEEK_API_KEY" -H "Content-Type: application/json" \
    -d '{"model":"deepseek-flash","messages":[{"role":"user","content":"maju 2 langkah"}],"max_tokens":8,"thinking":{"type":"disabled"}}' \
    https://api.deepseek.com/chat/completions
done | sort -n | awk '{a[NR]=$1} END {print "median", a[int(NR/2)+1], "max", a[NR]}'
```

Run it **from the venue network**, not from home — that is the number that matters.
Every judged submission also records `latency_ms` in the round log, so the real
distribution is in `logs/round-*.json` afterwards.

## Configuration

`.env` (all optional, defaults in `src/engineer_bot/config.py`):

| Key | Default | Meaning |
|---|---|---|
| `DEEPSEEK_API_KEY` | *(empty)* | empty = keyword mode, no network |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | |
| `DEEPSEEK_MODEL` | `deepseek-flash` | `deepseek-chat` is the legacy alias |
| `AI_TIMEOUT_SECONDS` | `3` | then the keyword fallback takes over |
| `ADMIN_PIN` | `1234` | keeps the audience out of `/admin` |
| `MAX_INSTRUCTIONS` | `14` | per team, accepted and rejected both count |
| `TURN_SECONDS` | `20` | to type one instruction |
| `PLAY_SECONDS` | `330` | whole round |
| `FILTER_SECONDS` | `10` | `tunggu` must reach this to set `filtered` |
| `SERIAL_PORT_A` / `_B` | *(empty)* | e.g. `/dev/cu.usbserial-0001`; empty = mock |
| `SERIAL_BAUD` | `115200` | |

**Moving the props** is a `config.py` edit only — `OBJECT_TILES` holds every coordinate
and `START_X` / `START_Y` / `START_HEADING` the start pose. No magic numbers elsewhere.

## Protocol

### Device (server → ESP32, one line each, `\n`, 115200 baud)

```
ACCEPT|MAJU 2 LANGKAH
REJECT|Berapa langkah?
WIN
RESET
```

ASCII-printable, truncated to 40 characters. A dead port never blocks a turn: the write
runs in a thread, the failure is logged, and the admin page shows `error`.

### WebSocket

`/ws/team/{a|b}` receives `{"type":"submit","text":"..."}` and pushes `state`.
`/ws/admin` (PIN) sends `start`, `pause`, `resume`, `reset`, `done`, `redo`,
`force`, `set_judge_mode`. `/ws/screen` is read-only. State is pushed on every change and
at least once a second, so a refreshed page recovers by itself.

## What is where

```
src/engineer_bot/
  config.py         every coordinate, limit, message and default
  models.py         Command, WorldState, JudgeResult, views
  judge.py          sanitize -> translate -> validate (validate is pure)
  keyword_parser.py offline Indonesian parser
  ai_client.py      DeepSeek, one keep-alive client, LRU cache, 3 s timeout
  game.py           phases, timers, budget, win and tie-break rules
  device.py         SerialDevice / MockDevice
  logbook.py        round log writer
  main.py           FastAPI app, routes, sockets, 1 Hz broadcast
static/             team.html, admin.html, screen.html, app.js, style.css
scripts/simulate.py full dry run with the keyword parser
tests/              165 tests, all offline
```

## Notes on the spec

Two small additions, both to keep the robot's answers understandable:

- `Belok berapa derajat?` for `BELOK` without a usable angle, instead of
  *Arah atau benda belum jelas.*
- `Benda itu tidak bisa diambil.` for `AMBIL` on the funnel, spoon, tablet or finish pad.

`Paksa terima` cannot invent a legal move: if the validator rejects the command there is
no new state to apply, so it stays rejected with the validator's reason. Manual mode
still lets the operator approve anything the validator accepts.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Team page says *Menunggu ronde dimulai* | nobody pressed **Mulai** |
| Page never updates | server asleep, or the socket died — reload; state comes back |
| Every instruction falls back to keyword | no key, no internet, or a 3 s timeout; check the admin dot |
| `ADMIN_PIN` rejected | PIN is read at startup; restart after editing `.env` |
| Serial device shows `mock` | port empty or already open (close the Arduino monitor) |
| `uv run` cannot find the package | run `uv sync` in the project root |
