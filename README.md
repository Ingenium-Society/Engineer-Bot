# The Engineer-Bot

Server, projector and pages for a 9-minute STEM Fair game about **exact instructions**.
Two teams give Indonesian commands — spoken or typed — for a club member acting as a robot;
the robot only obeys what the judge accepts. Task: purify water on a 5×5 grid.

Play is **turn based**: team A goes first, then B, then A again. The server laptop also
drives the projector, which shows whose turn it is, the instruction as it is being typed,
a processing beat, and then the verdict.

The AI is a **translator**, not the judge. DeepSeek turns a sentence into strict JSON; a
pure Python validator decides ACCEPT or REJECT. The game runs with no internet at all.

```
server laptop ──┬── /screen   projector, F11 on the extended display
                └── /admin    operator, on the laptop's own display
team A laptop ───── /team/a
team B laptop ───── /team/b
                        └── all four pages ── WebSocket ──→ FastAPI ─→ judge ─→ logs/
```

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+. Nothing else.

```bash
uv sync                                   # creates .venv and installs from uv.lock
cp .env.example .env                      # then edit if you have an API key
uv run python -m engineer_bot             # starts the server on 0.0.0.0:8000
```

Add `--extra stt` to `uv sync` if you want server-side speech recognition
(`STT_MODE=server`). Without it the game still runs; the page falls back to the keyboard.

To start from nothing instead:

```bash
uv init --app --python 3.12 engineer-bot
cd engineer-bot
uv add fastapi "uvicorn[standard]" httpx pydantic python-dotenv python-multipart
uv add --dev pytest pytest-asyncio ruff
uv run uvicorn engineer_bot.main:app --host 0.0.0.0 --port 8000
```

On startup the server prints every URL you need:

```
======================================================================
  THE ENGINEER-BOT
======================================================================
  Proyektor: http://localhost:8000/screen     (layar penuh di proyektor)
  Operator : http://localhost:8000/admin      (PIN 1234)
  Tim A    : http://192.168.1.24:8000/team/a
  Tim B    : http://192.168.1.24:8000/team/b

  AI       : aktif (deepseek-flash)
  Suara    : browser (id-ID)
  HTTPS    : mati - mikrofon diblokir di http dari laptop lain
  Batas    : 14 instruksi / tim
======================================================================
```

Checks:

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
uv run python scripts/simulate.py          # full dry run, no AI, no browser
```

## Event day

**Three laptops, no more.** One runs everything; two play.

| Laptop | What it runs |
|---|---|
| **Server** | the app, the projector (`/screen` on the extended display) **and** the operator page (`/admin` on its own screen) |
| Team A | `/team/a` |
| Team B | `/team/b` |

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
6. Open `/admin?pin=1234` once **on the server laptop** — the PIN is then stored in a
   cookie for `/ws/admin`. The same browser also holds `/screen`; see below.

### The projector — one laptop, two windows

The server machine is the screen. There is no display laptop and no hardware to wire up:
`/screen` and `/admin` are two windows of the same browser, one per display.

1. **Connect the projector and extend.** System Settings → Displays → *Extend*, and arrange
   the two screens so the projector sits beside the laptop, not on top of it. Leave
   **Displays have separate Spaces** ticked (it is the default) — that is what keeps the
   laptop screen usable while the projector is fullscreen.
2. **Open the screen in its own window**, with no tabs and no address bar:

   ```bash
   open -na "Google Chrome" --args --app=http://localhost:8000/screen
   ```

   Any Chromium works — substitute `Thorium`, `Microsoft Edge`, `Brave` for the app name.
   Drag the window to the projector, then **⌃⌘F** to fill the display. (On Windows it is
   F11; on macOS F11 is *Show Desktop*, not fullscreen.)
3. **Open the operator page** in a normal window on the laptop's own display:
   `http://localhost:8000/admin?pin=1234`. Fullscreen it too if the laptop screen is small —
   with separate Spaces both stay visible at once.
4. Both use `localhost`, not the LAN IP: the browser trusts it, so no certificate warning
   on the machine that matters. The team laptops still use the LAN URL from the banner.

The screen window never needs focus: it is read-only, needs no PIN, and cannot submit
anything. It reconnects on its own and shows the current state rather than replaying the
last verdict, so a reload mid-game is safe. The operator's `A` / `L` shortcuts work while
the admin window has focus, which it will, since that is the only window being clicked.

What the room sees: whose turn it is, the instruction **as it is typed**, a 0.7 s
processing beat, then a green `DITERIMA` flash or a red `DITOLAK` shake with the reason,
and the winner overlay at the end.

### Turn order

Team A opens, then B, then A again: **one instruction per turn, alternating**. Only the
team on turn can type or speak, and only that team's 20 s clock runs; the other page reads
*Menunggu giliran Tim B*. A rejection still costs the team its instruction **and** its
turn, which is the point: precision is the whole game.

> With 14 instructions each that is up to 28 turns inside 330 s — about 12 s per turn.
> If rehearsals feel rushed, lower `MAX_INSTRUCTIONS` to 10 or raise `PLAY_SECONDS`.
> Both are `.env` values, nothing else needs to change.

### HTTPS — required for the microphone

Browsers only hand out the microphone on a **secure origin**. `http://localhost` counts,
`http://192.168.x.x` does not, so the team laptops get `no-mic` until you serve TLS.
The keyboard works either way; only voice needs this.

```bash
brew install mkcert
mkcert -install                     # creates a local root CA and trusts it on THIS machine
mkdir -p certs
mkcert -cert-file certs/cert.pem -key-file certs/key.pem 192.168.1.24 localhost 127.0.0.1
```

Put the same IP you fixed in step 2 in that command — the certificate is bound to it.
Then in `.env`:

```
HTTPS_CERT=certs/cert.pem
HTTPS_KEY=certs/key.pem
```

Restart the server; the banner switches to `https://` and the pages open `wss://` sockets
by themselves. Finally, **trust the root CA on both team laptops**, or the page shows a
certificate warning and the mic stays blocked:

- macOS: `$(mkcert -CAROOT)/rootCA.pem` → double-click → Keychain → *Always Trust*.
- Windows: `rootCA.pem` → double-click → Install → *Local Machine* → *Trusted Root
  Certification Authorities*.

Fallback if you cannot install a CA on the school laptops: open
`chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add
`http://192.168.1.24:8000`, set it to *Enabled*, relaunch Chrome. It works, but it is a
per-laptop flag and the browser nags about it — use it as the backup, not the plan.

`certs/` is gitignored. Never commit a private key.

### Operator rhythm

| Step | Action |
|---|---|
| Start | **Mulai** — team A opens, 20 s per instruction |
| Each turn | the team on turn types or speaks → the judge proposes a verdict → **you confirm it** |
| Confirmed | the robot acts, then press **Selesai** — the floor moves to the other team |
| Wrong move | press **Ulang** — the state reverts, the instruction still counts, the turn passes |
| Nobody types in 20 s | the server rejects with *Waktu habis.* and passes the turn on its own |
| A team is stuck on screen | the column turns red after 30 s in LOCKED |
| Shortcuts | `A` / `L` = Selesai for team A / B, `Shift` + same key = Ulang (editable on the page) |

Only the team on turn has an enabled **Ketik untuk pemain** field and a live mic; the
other column is dimmed to `WAITING`.

### How a verdict is decided

There is one flow, with no mode to set:

```
speak or type → the model reads it → the validator checks the board
              → the column goes gold and shows what it wants to do and why:

                 MENUNGGU KONFIRMASI
                 TERIMA · MAJU 2 LANGKAH
                 AI baca: MAJU 2
                 "'langka' saya baca sebagai 'langkah'."
               → you press Terima or Tolak
```

The translation is a guess, so a person signs off on every instruction. The model is asked
to explain its reading in the same JSON (`alasan`, ≤ 12 words) — that line is what tells
you whether `maju dua langka` really meant two steps.

- **Terima** applies the proposal and the robot acts.
- **Tolak** rejects it with *Ditolak oleh operator.* — use it when the reading is wrong, or
  when the team deserves a rejection the parser missed.
- A proposal the validator already rejected cannot be accepted: there is no legal state to
  apply, so **Terima** on a REJECT still rejects, with the validator's reason.
- The 20 s turn clock stops while you decide; the 330 s round clock does not. Confirm
  briskly.

When the model is unreachable the keyword parser answers instead and the explanation line
is empty — same flow, less context. The `AI` row on the admin page tells you which one you
are getting.

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

## Voice input

The microphone is optional and never in the way. The text box works exactly as before,
and every failure ends with the player typing.

**Push-to-talk, never always-on.** Press the mic button — or the space bar when the text
box does not have focus. It stops on the final result or after about 6 seconds of silence,
and it closes the instant the turn ends. The room is full of people talking; a hot mic
would hear all of them.

```
press mic → Mendengarkan... (+ live interim text)
          → the transcript fills the text box
          → Kamu bilang: "…" · Terkirim dalam 2 detik · Ulangi   [countdown bar]
          → auto-submit after AUTO_SUBMIT_DELAY_MS
```

Any keystroke cancels the countdown so the player can fix a mishearing; **Enter** or
**Kirim** sends immediately; **Ulangi** records again. The 20 s turn timer keeps running
throughout, and the first submission that reaches the server ends the turn — typed or
spoken.

**Nothing is corrected silently.** The player sees exactly what the computer heard. A
mishearing is the teaching moment, not a bug to hide.

| `STT_MODE` | Engine | Needs internet on the team laptops |
|---|---|---|
| `browser` (default) | Web Speech API, `id-ID` | **yes** — Chrome and Edge send the audio to Google |
| `server` | `faster-whisper` on the server laptop | no |
| `off` | mic button hidden, app behaves exactly as before | — |

Each page reports its microphone state to the server — `ok`, `no-mic`, `denied`,
`offline`, `unsupported` — and the admin column shows it per team next to the last raw
transcript and the engine in use.

Voice text goes through the **same** pipeline as typed text: sanitize → translate →
validate. Nothing about speech can bypass the validator, and the 80-character limit
applies to the transcript.

### When a team's microphone fails

Use **Ketik untuk pemain** in that team's admin column. It counts as that team's
instruction, is logged with `source: "operator"`, and is ignored outside the input phase.
A voice failure never uses up a turn by itself.

### Server-side Whisper (phase 2)

For a venue with no internet, where the browser engine fails with `offline`:

```bash
uv sync --extra stt          # faster-whisper, CPU only
```

```
STT_MODE=server
WHISPER_MODEL=small
```

Download the model **before** the event, because the venue may have no connection:

```bash
uv run python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
```

It is cached in `~/.cache/huggingface/hub` (override with `HF_HOME`). The server loads it
once at startup, warms it with a short silent clip, and runs every decode in a
single-worker thread so the event loop never blocks. The uploaded clip is deleted
immediately after transcription; nothing is stored.

Measured on this laptop (CPU, int8, 1.4 s Indonesian clip):

| model | load + warm | transcribe |
|---|---|---|
| `tiny` | 3.0 s | 0.78 s |
| `base` | 3.0 s | 0.44 s |

`small` is the default per the brief. **If a 4 s clip takes more than about 3 s on the
venue machine, switch to `base`** — it is roughly twice as fast and still got
*"Maju dua langkah"* right in testing.

> The extra pins `av<19`: `faster-whisper` 1.2.1 calls `av.open(..., metadata_errors=…)`,
> which PyAV 19 removed. Without the pin every decode dies with a `TypeError`.

## Configuration

`.env` (all optional, defaults in `src/engineer_bot/config.py`):

| Key | Default | Meaning |
|---|---|---|
| `DEEPSEEK_API_KEY` | *(empty)* | empty = the keyword parser answers, no network |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | |
| `DEEPSEEK_MODEL` | `deepseek-flash` | `deepseek-chat` is the legacy alias |
| `AI_TIMEOUT_SECONDS` | `3` | then the keyword fallback takes over |
| `ADMIN_PIN` | `1234` | keeps the audience out of `/admin` |
| `MAX_INSTRUCTIONS` | `14` | per team, accepted and rejected both count |
| `TURN_SECONDS` | `20` | to type one instruction |
| `PLAY_SECONDS` | `330` | whole round |
| `FILTER_SECONDS` | `10` | `tunggu` must reach this to set `filtered` |
| `STT_MODE` | `browser` | `browser` \| `server` \| `off` |
| `STT_LANG` | `id-ID` | passed to the speech engine |
| `AUTO_SUBMIT_DELAY_MS` | `2000` | countdown before a transcript is sent |
| `WHISPER_MODEL` | `small` | `tiny` \| `base` \| `small` \| `medium` |
| `HTTPS_CERT` / `HTTPS_KEY` | *(empty)* | set both to serve TLS; see the HTTPS section |

**Moving the props** is a `config.py` edit only — `OBJECT_TILES` holds every coordinate
and `START_X` / `START_Y` / `START_HEADING` the start pose. No magic numbers elsewhere.

## Protocol

### WebSocket

`/ws/team/{a|b}` receives `{"type":"submit","text":"..."}` and pushes `state`. Two optional
extra fields carry the voice metadata; omitting them is a normal keyboard submission:

```jsonc
{"type":"submit","text":"maju 2","source":"voice","raw":"eh maju dua langka","stt_ms":640}
{"type":"stt_status","status":"denied","engine":"browser"}   // page -> server
{"type":"draft","text":"maju du"}                            // page -> server, mirrored to /screen
```

`/ws/admin` (PIN) sends `start`, `pause`, `resume`, `reset`, `done`, `redo`, `force`,
and `operator_submit` (`{"team":"a","text":"..."}`). `/ws/screen` is
read-only. State is pushed on every change and at least once a second, so a refreshed page
recovers by itself.

`screen_state` carries what the projector needs: `turn`, `turn_phase`, `turn_seconds_left`,
`draft`, both team summaries, and `last` — the most recent verdict with a monotonically
increasing `seq`. The page animates when `seq` changes, so a reconnect never replays an old
verdict.

### HTTP

`POST /api/stt/{a|b}` (phase 2) takes one multipart field, `audio`, and returns
`{"text":"…","ms":1200}`. 503 when the engine is not installed, 413 when the clip is over
4 MB, 415 for a non-audio content type. The clip is deleted after transcription.

## What is where

```
src/engineer_bot/
  config.py         every coordinate, limit, message and default
  models.py         Command, WorldState, JudgeResult, views
  judge.py          sanitize -> translate -> validate (validate is pure)
  keyword_parser.py offline Indonesian parser (fillers, mishearings)
  ai_client.py      DeepSeek, one keep-alive client, LRU cache, 3 s timeout
  game.py           turn order, phases, timers, budget, win and tie-break rules
  logbook.py        round log writer
  stt.py            phase 2 whisper transcriber + upload service
  main.py           FastAPI app, routes, sockets, 1 Hz broadcast
static/             team.html, admin.html, screen.html, app.js, voice.js, style.css
scripts/simulate.py full dry run with the keyword parser
tests/              251 tests, all offline
```

## Notes on the spec

Changes from the original brief, both requested after the first build:

- **Turn based.** Team A opens, then B, alternating one instruction per turn. Only the team
  on turn can submit and only its 20 s clock runs; the other sits in `WAITING`. A rejection
  costs the instruction *and* the turn.
- **No hardware.** The ESP32 / serial layer is gone: `device.py`, `pyserial`, the
  `SERIAL_*` settings and the `ACCEPT|…` line protocol were removed. The server laptop now
  drives the projector instead.
- **One judge flow, no modes.** The judge proposes and the operator accepts, always.
  The old `ai` / `keyword` / `manual` switch is gone: a mistranslation should never
  cost a team an instruction without a human looking at it, and an operator should
  never have to remember which mode is set. The model also explains its reading
  (`alasan`), which is what the operator actually judges.

Small additions, all to keep the robot's answers understandable:

- `Belok berapa derajat?` for `BELOK` without a usable angle, instead of
  *Arah atau benda belum jelas.*
- `Benda itu tidak bisa diambil.` for `AMBIL` on the funnel, spoon, tablet or finish pad.
- `Tidak terdengar. Coba lagi.` when the microphone opened but heard nothing — the brief
  lists the other voice statuses but not the silent case.

`Terima` cannot invent a legal move: if the validator rejects the command there is no new
state to apply, so it stays rejected with the validator's reason. Confirmation lets the
operator catch a bad *translation*, not overrule the board.

A team page may only claim `source: "voice"` or `"keyboard"`; anything else is treated as
keyboard. `"operator"` is reserved for the admin socket, so a player cannot log a
submission as if the operator had typed it.

## Manual test checklist

`pytest` covers the parser, the engine and the upload route with a fake transcriber. These
need a real browser, a real microphone and a real network — run them on the actual event
laptops, once, before the day.

| # | Test | Expected |
|---|---|---|
| 1 | Open `/screen` on the server laptop, fullscreen it (⌃⌘F), start a round | the projector shows `GILIRAN TIM A`, team B reads `Menunggu giliran Tim A` |
| 2 | Type into team A's box on the other laptop | the projector mirrors the text **as it is typed** |
| 3 | Submit an accepted instruction | the admin column goes gold with `TERIMA · …`, the projector says `MENUNGGU JURI…`, and **nothing moves** until you press Terima |
| 4 | Press **Tolak** on a proposal you know is right | the team gets *Ditolak oleh operator.* and loses the turn |
| 5 | Submit `maju` (no number) | the proposal is `TOLAK · Berapa langkah?`; **Terima** still rejects |
| 6 | Do nothing for 20 s | *Waktu habis.*, the turn passes, the projector says the same |
| 7 | Reload `/screen` mid-round | it shows the current state and does **not** replay the last verdict |
| 8 | Open `/team/a` over **https** on a second laptop, press the mic | the browser asks for microphone permission once |
| 9 | Say "maju dua langkah" | `Mendengarkan...` → transcript appears → countdown → sent by itself |
| 10 | Same on **Chrome** and on **Edge** | identical behaviour; both use the same engine |
| 11 | Type a character during the countdown | countdown disappears, nothing is sent, **Kirim** still works |
| 12 | Press **Ulangi** | records again, replaces the transcript |
| 13 | Deny the permission prompt | `Mikrofon tidak tersedia, silakan ketik`, mic disabled, typing still works |
| 14 | Unplug a USB microphone mid-turn, then press the mic | `no-mic`, keyboard unaffected, **no instruction used up** |
| 15 | Unplug the internet, press the mic (browser mode) | `offline`, keyboard unaffected — then repeat with `STT_MODE=server` |
| 16 | Open `/team/a` over **http** from another laptop | mic refuses to work; the keyboard still does (this is why HTTPS matters) |
| 17 | Open the admin page during all of the above | the team's `Suara` column matches what the page shows |
| 18 | `STT_MODE=off`, reload | no mic button at all, status reads *Ketik satu instruksi* |
| 19 | Let the turn timer run out while listening | mic closes, `Waktu habis.`, nothing submitted |

## Troubleshooting

| Symptom | Cause |
|---|---|
| Team page says *Menunggu ronde dimulai* | nobody pressed **Mulai** |
| Page never updates | server asleep, or the socket died — reload; state comes back |
| Every instruction falls back to keyword | no key, no internet, or a 3 s timeout; check the admin dot |
| `ADMIN_PIN` rejected | PIN is read at startup; restart after editing `.env` |
| Projector shows nothing | the page is not on `/screen`, or the laptop mirrored instead of extended |
| Projector frozen on the last verdict | the socket dropped — reload; it reconnects and shows the current state |
| Projector shows a verdict for the wrong team | impossible by construction: `last.team` comes from the verdict itself, not from `turn` |
| `uv run` cannot find the package | run `uv sync` in the project root |
| Mic button greyed out | the turn is not in `TYPING`, or the page reported `denied` / `offline` |
| Mic works on the server laptop only | the page is on `http://<ip>` — see the HTTPS section |
| `Mikrofon tidak tersedia` on a laptop with a mic | Web Speech needs internet in `browser` mode; use `server` mode |
| `503` from `/api/stt/a` | `uv sync --extra stt` was not run, or `STT_MODE` is not `server` |
| Whisper raises `metadata_errors` | PyAV 19 installed; the extra pins `av<19` — re-run `uv sync --extra stt` |
