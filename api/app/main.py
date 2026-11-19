"""FastAPI app: REST + one WebSocket under /api, and the built web app at /."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __doc__ as about
from pydantic import BaseModel, Field

from .camera import CameraError, CameraRelay, SessionDescription
from .config import Settings, load_printers
from .db import make_engine, migrate
from .drivers import PrinterStatus
from .controls import Controls
from .drivers.base import ControlError, ControlsDisabled
from .gcode import GcodeCache
from .library import Library
from .themes import ThemeStore
from .hub import Hub
from .timelapse import Timelapse
from .relay import Go2rtc, decode_go2rtc_offer, encode_answer, fix_answer, fix_offer, pump, Stills

log = logging.getLogger(__name__)
VERSION = "0.1.0"
DEFAULT_STATIC = Path(__file__).resolve().parent.parent / "static"

class SpeedBody(BaseModel):
    percent: int

class FanBody(BaseModel):
    name: str = Field(max_length=60)
    percent: int

class TempBody(BaseModel):
    heater: str = Field(max_length=20)
    target: int

class LightBody(BaseModel):
    on: bool

class FileBody(BaseModel):
    label: str | None = Field(None, max_length=60)


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
        for driver in hub.drivers.values():
            driver.controls_enabled = settings.controls_enabled
        app.state.controls = Controls(engine, settings.controls_enabled)
        configs = {d.config.id: d.config for d in hub.drivers.values()}
        library = None
        if settings.capture_files:
            try:
                library = Library(engine, settings.library_root, configs)
            except OSError as exc:  # e.g. a library folder the app can't write to: run without capture
                log.error("file capture is off: can't use %s (%s)", settings.library_root, exc)
        app.state.library = library
        app.state.gcode = GcodeCache(settings.data_dir, configs, library=library)
        for driver in hub.drivers.values():
            if library:
                driver.on_notice(library.on_notice)
                driver.on_status(library.on_status)
            if settings.gcode_preview:
                driver.on_status(app.state.gcode.on_status)
                driver.layer_lookup = app.state.gcode.layer_at
        app.state.camera = CameraRelay(min_interval=settings.camera_min_interval)
        app.state.go2rtc = (Go2rtc(settings.go2rtc_url, settings.camera_callback_url, settings.data_dir)
                            if settings.go2rtc_url else None)
        app.state.stills = Stills(app.state.go2rtc) if app.state.go2rtc else None
        app.state.timelapse = None
        if settings.timelapse and library:
            tl = Timelapse(engine, settings.timelapse_root, app.state.go2rtc, configs, library,
                           min_free_bytes=int(settings.timelapse_min_free_gb * 1024**3))
            app.state.timelapse = tl
            problem = tl.storage_problem()
            if problem:
                log.warning("time-lapses won't be saved until this is fixed: %s", problem)
            if not app.state.go2rtc:
                log.warning("time-lapses need the camera relay (GO2RTC_URL)")
            for driver in hub.drivers.values():
                driver.on_status(tl.on_status)  # after the library's, which opens the print's history row
        hub.start()
        try:
            yield
        finally:
            await hub.stop()
            await app.state.gcode.stop()
            if app.state.stills:
                await app.state.stills.stop()
            if app.state.timelapse:
                await app.state.timelapse.stop()
            if app.state.library:
                await app.state.library.stop()
            engine.dispose()

    app = FastAPI(title="printer-api", version=VERSION, description=about, lifespan=lifespan)

    def hub_of() -> Hub:
        return app.state.hub

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": VERSION, "printers": len(hub_of().drivers)}

    @app.get("/api/config")
    def public_config() -> dict:
        return {"version": VERSION, "tz": settings.tz, "controls_enabled": settings.controls_enabled,
                "reconnect_seconds": settings.reconnect_seconds,
                "camera_mode": "relay" if settings.go2rtc_url else "direct",
                "capture_files": bool(getattr(app.state, "library", None))}

    themes = ThemeStore(settings.themes_file or settings.data_dir / "themes.yaml")

    @app.get("/api/themes")
    def list_themes() -> dict:
        """Base themes merged with the user's themes file. Reloaded when either changes."""
        return themes.get().public()

    @app.get("/api/printers", response_model=list[PrinterStatus])
    def list_printers() -> list[PrinterStatus]:
        return hub_of().statuses()

    @app.get("/api/printers/{printer_id}", response_model=PrinterStatus)
    def get_printer(printer_id: str) -> PrinterStatus:
        driver = hub_of().get(printer_id)
        if not driver:
            raise HTTPException(404, "no such printer")
        return driver.status

    # --- the current print's G-code, for the 3D preview (app/gcode.py has the rules) ---

    @app.get("/api/printers/{printer_id}/gcode/info")
    def gcode_info(printer_id: str) -> dict:
        driver = hub_of().get(printer_id)
        if not driver:
            raise HTTPException(404, "no such printer")
        if not settings.gcode_preview:
            return {"state": "off"}
        return app.state.gcode.info(driver.status)

    @app.get("/api/printers/{printer_id}/gcode")
    def gcode_file(printer_id: str) -> FileResponse:
        driver = hub_of().get(printer_id)
        if not driver:
            raise HTTPException(404, "no such printer")
        if not settings.gcode_preview or not app.state.gcode.ready(driver.status):
            raise HTTPException(404, "no G-code for the current print")
        return FileResponse(app.state.gcode.path(printer_id), media_type="text/plain",
                            headers={"Cache-Control": "no-store"})

    # --- captured files and print history (app/library.py has the rules) ---

    def library_of() -> Library:
        if not app.state.library:
            raise HTTPException(404, "file capture is turned off (CAPTURE_FILES)")
        return app.state.library

    @app.get("/api/files")
    def list_files(label: str | None = None, inbox: bool = False) -> list[dict]:
        return library_of().list(label=label, inbox=inbox)

    @app.get("/api/files/{file_id}")
    def file_detail(file_id: int) -> dict:
        d = library_of().detail(file_id)
        if not d:
            raise HTTPException(404, "no such file")
        return d

    @app.get("/api/files/{file_id}/raw")
    def file_raw(file_id: int) -> FileResponse:
        lib = library_of()
        f = lib.get(file_id)
        if not f or not lib.file_path(f).is_file():
            raise HTTPException(404, "no such file")
        return FileResponse(lib.file_path(f), media_type="text/plain", filename=f.filename,
                            content_disposition_type="inline")

    @app.get("/api/files/{file_id}/thumbnail")
    def file_thumb(file_id: int) -> FileResponse:
        lib = library_of()
        f = lib.get(file_id)
        if not f or not lib.thumb_path(f).is_file():
            raise HTTPException(404, "no thumbnail")
        return FileResponse(lib.thumb_path(f), media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    @app.patch("/api/files/{file_id}")
    def file_update(file_id: int, body: FileBody) -> dict:
        d = library_of().set_label(file_id, body.label)
        if not d:
            raise HTTPException(404, "no such file")
        return d

    @app.delete("/api/files/{file_id}")
    def file_delete(file_id: int) -> dict:
        if not library_of().delete(file_id):
            raise HTTPException(404, "no such file")
        return {"ok": True}

    @app.get("/api/labels")
    def labels() -> list[dict]:
        return library_of().labels()

    @app.get("/api/history")
    def history(limit: int = 200) -> list[dict]:
        return library_of().history(limit=max(1, min(limit, 1000)))

    def job_of(job_id: int) -> dict:
        job = library_of().job(job_id)
        if not job:
            raise HTTPException(404, "no such print")
        return job

    @app.get("/api/history/{job_id}")
    def history_entry(job_id: int) -> dict:
        return job_of(job_id)

    @app.get("/api/history/{job_id}/timelapse")
    def timelapse_frames(job_id: int) -> dict:
        job = job_of(job_id)
        tl = app.state.timelapse
        if tl is None:
            return {"frames": [], "gaps": [], "total_layers": None, "recording": False,
                    "available": not job.get("timelapse_frames")}
        return tl.frames(job)

    @app.get("/api/history/{job_id}/timelapse/{n}.jpg")
    def timelapse_frame(job_id: int, n: int) -> FileResponse:
        job = job_of(job_id)
        tl = app.state.timelapse
        path = tl.frame_path(job, n) if tl else None
        if path is None:
            raise HTTPException(404, "no such frame")
        # A saved frame never changes.
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=31536000, immutable"})

    # --- controls (app/controls.py has the rules) ---

    async def control(printer_id: str, action: str, values: dict, request: Request) -> dict:
        driver = hub_of().get(printer_id)
        if not driver:
            raise HTTPException(404, "no such printer")
        device = request.client.host if request.client else None
        try:
            command = await app.state.controls.run(driver, action, values, device)
        except ControlsDisabled as exc:
            raise HTTPException(501, str(exc)) from exc
        except ControlError as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        return {"ok": True, "command": command}

    @app.post("/api/printers/{printer_id}/pause")
    async def control_pause(printer_id: str, request: Request) -> dict:
        return await control(printer_id, "pause", {}, request)

    @app.post("/api/printers/{printer_id}/resume")
    async def control_resume(printer_id: str, request: Request) -> dict:
        return await control(printer_id, "resume", {}, request)

    @app.post("/api/printers/{printer_id}/cancel")
    async def control_cancel(printer_id: str, request: Request) -> dict:
        return await control(printer_id, "cancel", {}, request)

    @app.post("/api/printers/{printer_id}/speed")
    async def control_speed(printer_id: str, body: SpeedBody, request: Request) -> dict:
        return await control(printer_id, "speed", body.model_dump(), request)

    @app.post("/api/printers/{printer_id}/fan")
    async def control_fan(printer_id: str, body: FanBody, request: Request) -> dict:
        return await control(printer_id, "fan", body.model_dump(), request)

    @app.post("/api/printers/{printer_id}/temp")
    async def control_temp(printer_id: str, body: TempBody, request: Request) -> dict:
        return await control(printer_id, "temp", body.model_dump(), request)

    @app.post("/api/printers/{printer_id}/light")
    async def control_light(printer_id: str, body: LightBody, request: Request) -> dict:
        return await control(printer_id, "light", body.model_dump(), request)

    @app.get("/api/printers/{printer_id}/actions")
    def printer_actions(printer_id: str, limit: int = 20) -> list[dict]:
        if not hub_of().get(printer_id):
            raise HTTPException(404, "no such printer")
        rows = app.state.controls.recent(printer_id, max(1, min(limit, 500)))
        return [r.model_dump(mode="json") for r in rows]

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

    @app.get("/api/printers/{printer_id}/camera/still.jpg")
    async def camera_still(printer_id: str) -> Response:
        """One still from the camera relay, for pages that can't play the live video."""
        stills: Stills | None = app.state.stills
        driver = hub_of().get(printer_id)
        if stills is None:
            raise HTTPException(404, "the camera relay is not set up on this server")
        if not driver:
            raise HTTPException(404, "no such printer")
        if driver.config.camera != "creality_webrtc":
            raise HTTPException(404, "this printer has no supported camera")
        if driver.status.state in ("offline", "connecting"):
            raise HTTPException(409, "printer is offline")
        data = await stills.get(printer_id)
        if data is None:
            raise HTTPException(504, "no picture came from the camera")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

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
