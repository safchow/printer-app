"""FastAPI app: REST + one WebSocket under /api, and the built web app at /."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import __doc__ as about
from .camera import CameraError, CameraRelay, SessionDescription
from .config import Settings, load_printers
from .db import make_engine, migrate
from .drivers import PrinterStatus
from .hub import Hub
from .relay import Go2rtc, decode_go2rtc_offer, encode_answer, fix_answer, fix_offer, pump

log = logging.getLogger(__name__)
VERSION = "0.1.0"
DEFAULT_STATIC = Path(__file__).resolve().parent.parent / "static"

CONTROLS = ("pause", "resume", "cancel", "speed", "fan", "temp", "light")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        migrate(settings.database_url)
        engine = make_engine(settings.database_url)
        printers = load_printers(settings.printers_file)
        if not printers:
            log.warning("no printers configured in %s", settings.printers_file)
        hub = Hub(printers, engine, reconnect_seconds=settings.reconnect_seconds)
        app.state.hub = hub
        app.state.camera = CameraRelay(min_interval=settings.camera_min_interval)
        app.state.go2rtc = (Go2rtc(settings.go2rtc_url, settings.camera_callback_url, settings.data_dir)
                            if settings.go2rtc_url else None)
        hub.start()
        try:
            yield
        finally:
            await hub.stop()
            engine.dispose()

    app = FastAPI(title="printer-api", version=VERSION, description=about, lifespan=lifespan)

    def hub_of() -> Hub:
        return app.state.hub

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": VERSION, "printers": len(hub_of().drivers)}

    @app.get("/api/config")
    def public_config() -> dict:
        return {"version": VERSION, "tz": settings.tz, "controls_enabled": False,
                "reconnect_seconds": settings.reconnect_seconds,
                "camera_mode": "relay" if settings.go2rtc_url else "direct"}

    @app.get("/api/printers", response_model=list[PrinterStatus])
    def list_printers() -> list[PrinterStatus]:
        return hub_of().statuses()

    @app.get("/api/printers/{printer_id}", response_model=PrinterStatus)
    def get_printer(printer_id: str) -> PrinterStatus:
        driver = hub_of().get(printer_id)
        if not driver:
            raise HTTPException(404, "no such printer")
        return driver.status

    # Controls are part of the API shape but stay off until the controls phase.
    # Nothing here reaches a printer.
    for action in CONTROLS:
        def make(action: str):
            async def control(printer_id: str) -> JSONResponse:
                if not hub_of().get(printer_id):
                    raise HTTPException(404, "no such printer")
                return JSONResponse({"detail": f"{action} is not enabled in this version"}, status_code=501)
            control.__name__ = f"control_{action}"
            return control
        app.post(f"/api/printers/{{printer_id}}/{action}", status_code=501)(make(action))

    @app.post("/api/printers/{printer_id}/camera/offer", response_model=SessionDescription)
    async def camera_offer(printer_id: str, offer: SessionDescription) -> SessionDescription:
        driver = hub_of().get(printer_id)
        if not driver:
            raise HTTPException(404, "no such printer")
        if driver.status.state in ("offline", "connecting"):
            raise HTTPException(409, "printer is offline")
        try:
            return await app.state.camera.offer(driver.config, offer)
        except CameraError as exc:
            raise HTTPException(exc.status, exc.detail) from exc

    # --- camera relay (go2rtc) ---

    @app.post("/api/internal/camera/{token}/{printer_id}", include_in_schema=False)
    async def relay_handshake(token: str, printer_id: str, request: Request) -> PlainTextResponse:
        """go2rtc's Creality source posts here instead of to the printer."""
        g: Go2rtc | None = app.state.go2rtc
        if g is None or not g.check_token(token):
            raise HTTPException(404)
        driver = hub_of().get(printer_id)
        if not driver:
            raise HTTPException(404, "no such printer")
        if driver.status.state in ("offline", "connecting"):
            raise HTTPException(409, "printer is offline")
        body = await request.body()
        try:
            if len(body) > 4 * 64 * 1024:
                raise CameraError(413, "offer too large")
            offer = decode_go2rtc_offer(body)
            sent = SessionDescription(type="offer", sdp=fix_offer(offer.sdp))
            answer = await app.state.camera.offer(driver.config, sent)
        except CameraError as exc:
            log.warning("camera relay handshake for %s: %s", printer_id, exc.detail)
            raise HTTPException(exc.status, exc.detail) from exc
        fixed = SessionDescription(type="answer", sdp=fix_answer(answer.sdp, sent.sdp))
        g.save_handshake(printer_id, offer.sdp, sent.sdp, answer.sdp, fixed.sdp)
        return PlainTextResponse(encode_answer(fixed))

    @app.websocket("/api/printers/{printer_id}/camera/ws")
    async def camera_ws(socket: WebSocket, printer_id: str) -> None:
        """Live video for one printer: go2rtc's MSE WebSocket, proxied."""
        await socket.accept()
        g: Go2rtc | None = app.state.go2rtc
        driver = hub_of().get(printer_id)
        problem = None
        if g is None:
            problem = "the camera relay is not set up on this server"
        elif not driver:
            problem = "no such printer"
        elif driver.config.camera != "creality_webrtc":
            problem = "this printer has no supported camera"
        elif driver.status.state in ("offline", "connecting"):
            problem = "printer is offline"
        if problem:
            await socket.send_json({"type": "error", "value": problem})
            await socket.close()
            return
        try:
            upstream = await websockets.connect(g.ws_url(printer_id), max_size=None, open_timeout=5)
        except (OSError, websockets.WebSocketException, TimeoutError) as exc:
            log.warning("camera relay unreachable: %r", exc)
            await socket.send_json({"type": "error", "value": "the camera relay isn't running"})
            await socket.close()
            return
        await pump(socket, upstream)
        with contextlib.suppress(Exception):
            await socket.close()

    @app.websocket("/api/ws")
    async def ws(socket: WebSocket) -> None:
        hub = hub_of()
        await socket.accept()
        queue = hub.subscribe()
        try:
            await socket.send_json({"type": "snapshot",
                                    "printers": [s.model_dump(mode="json") for s in hub.statuses()]})
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    if queue not in hub._clients:
                        break
                    message = {"type": "ping"}
                await socket.send_json(message)
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(queue)

    static_dir = settings.static_dir or DEFAULT_STATIC
    if (static_dir / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets", check_dir=False), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            candidate = (static_dir / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    return app
