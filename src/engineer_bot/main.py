"""FastAPI app: HTTP routes, WebSockets and the one-second state broadcast."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .ai_client import AIClient
from .device import build_device
from .game import TEAM_IDS, Game

log = logging.getLogger(__name__)

STATIC_DIR = config.PROJECT_ROOT / "static"
COOKIE_NAME = "engineer_bot_pin"
TICK_SECONDS = 0.25
BROADCAST_SECONDS = 1.0


class Hub:
    """Fan-out to every connected page. One dead socket never stops the others."""

    def __init__(self) -> None:
        self.teams: dict[str, set[WebSocket]] = {team_id: set() for team_id in TEAM_IDS}
        self.admins: set[WebSocket] = set()
        self.screens: set[WebSocket] = set()

    @staticmethod
    async def _send(socket: WebSocket, payload: dict[str, Any]) -> bool:
        try:
            await socket.send_json(payload)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _fan(self, sockets: set[WebSocket], payload: dict[str, Any]) -> None:
        for socket in list(sockets):
            if not await self._send(socket, payload):
                sockets.discard(socket)

    async def broadcast(self, game: Game) -> None:
        for team_id in TEAM_IDS:
            await self._fan(
                self.teams[team_id], {"type": "state", **game.team_view(team_id).model_dump()}
            )
        if self.admins:
            await self._fan(self.admins, game.admin_view())
        if self.screens:
            await self._fan(self.screens, game.screen_view())


def create_app(settings: config.Settings | None = None) -> FastAPI:
    settings = settings or config.Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ai = AIClient(settings)
        await ai.startup()
        devices = {
            "a": build_device(settings.serial_port_a, settings.serial_baud, "A"),
            "b": build_device(settings.serial_port_b, settings.serial_baud, "B"),
        }
        hub = Hub()
        game = Game(
            settings,
            ai=ai,
            devices=devices,
            log_dir=config.PROJECT_ROOT / "logs",
        )

        async def notify() -> None:
            await hub.broadcast(game)

        game.notify = notify
        app.state.game = game
        app.state.hub = hub
        app.state.settings = settings

        async def ticker() -> None:
            last = 0.0
            while True:
                await asyncio.sleep(TICK_SECONDS)
                try:
                    changed = await game.tick()
                    now = time.monotonic()
                    if changed or now - last >= BROADCAST_SECONDS:
                        last = now
                        await hub.broadcast(game)
                except Exception:  # noqa: BLE001 - the ticker must outlive a bad frame
                    log.exception("ticker error")

        task = asyncio.create_task(ticker())
        try:
            yield
        finally:
            task.cancel()
            await ai.aclose()
            for device in devices.values():
                await device.close()

    app = FastAPI(title="The Engineer-Bot", lifespan=lifespan)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # -- pages ---------------------------------------------------------

    def page(name: str) -> FileResponse:
        return FileResponse(STATIC_DIR / name, media_type="text/html")

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse(
            """<!doctype html><html lang="id"><head><meta charset="utf-8">
<title>The Engineer-Bot</title>
<link rel="stylesheet" href="/static/style.css"></head>
<body class="hub"><main>
<h1>The Engineer-Bot</h1>
<ul class="hub-links">
  <li><a href="/team/a">Tim A</a></li>
  <li><a href="/team/b">Tim B</a></li>
  <li><a href="/admin">Operator</a></li>
  <li><a href="/screen">Layar</a></li>
</ul>
<p class="muted">Semua halaman harus dibuka dari laptop yang terhubung ke WiFi yang sama.</p>
</main></body></html>"""
        )

    @app.get("/team/a")
    async def team_a() -> FileResponse:
        return page("team.html")

    @app.get("/team/b")
    async def team_b() -> FileResponse:
        return page("team.html")

    @app.get("/screen")
    async def screen() -> FileResponse:
        return page("screen.html")

    @app.get("/admin")
    async def admin(request: Request, pin: str | None = None):
        cookie = request.cookies.get(COOKIE_NAME)
        if pin is not None and pin == settings.admin_pin:
            response = RedirectResponse("/admin", status_code=303)
            response.set_cookie(COOKIE_NAME, settings.admin_pin, httponly=True, samesite="lax")
            return response
        if cookie != settings.admin_pin:
            return HTMLResponse(
                """<!doctype html><html lang="id"><head><meta charset="utf-8">
<title>Operator</title><link rel="stylesheet" href="/static/style.css"></head>
<body class="gate"><main><h1>Operator</h1>
<form method="get" action="/admin">
  <label for="pin">PIN</label>
  <input id="pin" name="pin" type="password" inputmode="numeric" autofocus>
  <button type="submit">Masuk</button>
</form></main></body></html>""",
                status_code=401,
            )
        return page("admin.html")

    @app.get("/api/state")
    async def api_state() -> JSONResponse:
        return JSONResponse(app.state.game.admin_view())

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # -- websockets ----------------------------------------------------

    @app.websocket("/ws/team/{team_id}")
    async def ws_team(websocket: WebSocket, team_id: str) -> None:
        game: Game = app.state.game
        hub: Hub = app.state.hub
        if team_id not in TEAM_IDS:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        hub.teams[team_id].add(websocket)
        await websocket.send_json({"type": "state", **game.team_view(team_id).model_dump()})
        try:
            while True:
                message = await websocket.receive_json()
                if message.get("type") == "submit":
                    await game.submit(team_id, str(message.get("text", "")))
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            log.exception("team socket error")
        finally:
            hub.teams[team_id].discard(websocket)

    @app.websocket("/ws/admin")
    async def ws_admin(websocket: WebSocket) -> None:
        game: Game = app.state.game
        hub: Hub = app.state.hub
        pin = websocket.query_params.get("pin") or websocket.cookies.get(COOKIE_NAME)
        if pin != settings.admin_pin:
            await websocket.close(code=4403)
            return
        await websocket.accept()
        hub.admins.add(websocket)
        await websocket.send_json(game.admin_view())
        try:
            while True:
                message = await websocket.receive_json()
                await _handle_admin(game, message)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            log.exception("admin socket error")
        finally:
            hub.admins.discard(websocket)

    @app.websocket("/ws/screen")
    async def ws_screen(websocket: WebSocket) -> None:
        game: Game = app.state.game
        hub: Hub = app.state.hub
        await websocket.accept()
        hub.screens.add(websocket)
        await websocket.send_json(game.screen_view())
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.screens.discard(websocket)

    return app


async def _handle_admin(game: Game, message: dict[str, Any]) -> None:
    kind = message.get("type")
    team_id = message.get("team")
    if kind == "start":
        await game.start()
    elif kind == "pause":
        await game.pause()
    elif kind == "resume":
        await game.resume()
    elif kind == "reset":
        await game.reset()
    elif kind == "done" and team_id in TEAM_IDS:
        await game.done(team_id)
    elif kind == "redo" and team_id in TEAM_IDS:
        await game.redo(team_id)
    elif kind == "force" and team_id in TEAM_IDS:
        await game.force(team_id, str(message.get("decision", "")), message.get("text"))
    elif kind == "set_judge_mode" and message.get("mode") in ("ai", "keyword", "manual"):
        await game.set_judge_mode(message["mode"])


app = create_app()
