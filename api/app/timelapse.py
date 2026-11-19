"""Time-lapses: one camera frame per layer of every print, kept on disk.

How it works:
- while a printer with a relayed camera (docs/camera-relay.md) is printing, a
  recorder holds ONE viewer connection to go2rtc, exactly like an open camera
  page, so the printer streams to go2rtc for the length of the print and no
  new handshake is needed per frame;
- on every layer change (the printer's own layer count, or the one the app
  works out from the G-code, app/gcode.py) it asks go2rtc for a still
  (/api/frame.jpeg) and saves it. Printers that report no layers get a frame
  every FALLBACK_SECONDS instead;
- frames go to TIMELAPSE_ROOT/<printer>/<print job id>/000001.jpg ..., with
  one line per frame in frames.jsonl (number, layer, time). The print_job row
  only keeps the count and size, so History never has to touch the disk;
- a frame that doesn't come within FRAME_TIMEOUT is skipped (a gap). After
  RESET_AFTER failures in a row the recorder drops its viewer and deletes the
  stream in go2rtc, so a stuck request can't keep the camera open, then
  starts again;
- nothing is written unless TIMELAPSE_ROOT holds the marker file MARKER
  (so an unmounted NAS never fills the system disk; Docker creates a missing
  bind-mount folder empty) and has at least TIMELAPSE_MIN_FREE_GB free;
- frames are kept until the history entry or the frames are deleted.

The recorder never talks to the printer itself: only to go2rtc, whose
connection to the printer goes through the app's rate-limited handshake.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import websockets
from sqlmodel import Session

from .config import PrinterConfig
from .db import PrintJob
from .drivers import PrinterStatus

log = logging.getLogger(__name__)

MARKER = ".timelapse-root"
FRAMES_FILE = "frames.jsonl"
FRAME_TIMEOUT = 10.0
FIRST_FRAME_TIMEOUT = 20.0  # the first frame waits for the camera handshake
MIN_GAP_SECONDS = 2.0  # at most one frame request this often
FALLBACK_SECONDS = 30.0
RESET_AFTER = 2
RESET_PAUSE = 5.0
MIN_JPEG_BYTES = 1000
# Codecs we ask go2rtc for on the viewer connection; the data itself is thrown away.
MSE_CODECS = "avc1.640029,avc1.64002A,avc1.640033,hvc1.1.6.L153.B0"


def frame_name(n: int) -> str:
    return f"{n:06d}.jpg"


def read_frames(folder: Path) -> list[dict[str, Any]]:
    """The frames recorded in a folder, in order. Bad lines are skipped."""
    out: list[dict[str, Any]] = []
    try:
        lines = (folder / FRAMES_FILE).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("n"), int):
            out.append(item)
    return out


def gaps(frames: list[dict[str, Any]]) -> list[list[int]]:
    """Layer ranges [from, to] missing between the first and last frame that has a layer."""
    layers = sorted({f["layer"] for f in frames if isinstance(f.get("layer"), int)})
    out = []
    for a, b in zip(layers, layers[1:]):
        if b - a > 1:
            out.append([a + 1, b - 1])
    return out


class Recorder:
    """Captures one print: holds the viewer connection and saves frames as layers change."""

    def __init__(self, tl: "Timelapse", printer_id: str, job_id: int):
        self.tl = tl
        self.printer_id = printer_id
        self.job_id = job_id
        self.folder = tl.root / printer_id / str(job_id)
        self.status: PrinterStatus | None = None
        self.wake = asyncio.Event()
        frames = read_frames(self.folder)  # carry on after an app restart
        self.count = max((f["n"] for f in frames), default=0)
        self.bytes = sum(int(f.get("bytes") or 0) for f in frames)
        self.last_layer: int | None = next((f.get("layer") for f in reversed(frames) if f.get("layer") is not None), None)
        self.last_capture = 0.0
        self.last_request = 0.0
        self.failures = 0
        self.ws = None
        self.drain: asyncio.Task | None = None
        self.fresh_viewer = False
        self.task = asyncio.create_task(self.run(), name=f"timelapse:{printer_id}:{job_id}")

    def update(self, status: PrinterStatus) -> None:
        self.status = status
        self.wake.set()

    # --- the loop ---------------------------------------------------------------

    def due(self) -> bool:
        st = self.status
        if st is None or st.state != "printing":
            return False
        if st.layer is not None:
            return st.layer != self.last_layer
        return time.monotonic() - self.last_capture >= FALLBACK_SECONDS

    async def run(self) -> None:
        try:
            while True:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.wake.wait(), timeout=FALLBACK_SECONDS)
                self.wake.clear()
                st = self.status
                if st is None:
                    continue
                if st.state != "printing":
                    await self.close_viewer()  # paused or offline: let the camera go
                    continue
                if not self.due():
                    continue
                wait = MIN_GAP_SECONDS - (time.monotonic() - self.last_request)
                if wait > 0:
                    await asyncio.sleep(wait)
                    if not self.due():
                        continue
                await self.capture()
        except asyncio.CancelledError:
            pass

    async def capture(self, final: bool = False) -> None:
        st = self.status
        layer = st.layer if st else None
        total = st.total_layers if st else None
        if final:  # the finished part: counted as the last layer
            layer = total or self.last_layer
        problem = self.tl.storage_problem()
        if problem:
            self.tl.warn_once(f"storage:{problem}", "time-lapse paused: %s", problem)
            self.last_capture = time.monotonic()
            self.last_layer = layer
            return
        if not await self.open_viewer():
            self.last_capture = time.monotonic()
            return
        self.last_request = time.monotonic()
        timeout = FIRST_FRAME_TIMEOUT if self.fresh_viewer else FRAME_TIMEOUT
        data = await self.tl.fetch_frame(self.printer_id, timeout)
        self.last_capture = time.monotonic()
        if data is None:
            self.failures += 1
            log.info("%s: no time-lapse frame for layer %s (%d in a row)", self.printer_id, layer, self.failures)
            if self.failures >= RESET_AFTER:
                await self.reset()
            return
        self.failures = 0
        self.fresh_viewer = False
        self.save(data, layer, total)

    def save(self, data: bytes, layer: int | None, total: int | None) -> None:
        n = self.count + 1
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self.folder / f".{frame_name(n)}.part"
            tmp.write_bytes(data)
            tmp.replace(self.folder / frame_name(n))
            line = {"n": n, "layer": layer, "total": total, "bytes": len(data),
                    "t": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            with (self.folder / FRAMES_FILE).open("a") as fh:
                fh.write(json.dumps(line) + "\n")
        except OSError as exc:
            self.tl.warn_once(f"write:{self.folder}", "time-lapse: can't write to %s (%s)", self.folder, exc)
            return
        self.count, self.bytes, self.last_layer = n, self.bytes + len(data), layer
        self.tl.record(self.job_id, self.count, self.bytes)

    # --- the viewer connection ------------------------------------------------------

    async def open_viewer(self) -> bool:
        if self.ws is not None and self.drain is not None and not self.drain.done():
            return True
        await self.close_viewer()
        g = self.tl.go2rtc
        try:
            self.ws = await websockets.connect(g.ws_url(self.printer_id), max_size=None, open_timeout=5)
            await self.ws.send(json.dumps({"type": "mse", "value": MSE_CODECS}))
        except (OSError, websockets.WebSocketException, TimeoutError) as exc:
            self.tl.warn_once("relay", "time-lapse: camera relay unreachable (%r)", exc)
            self.ws = None
            return False
        self.drain = asyncio.create_task(self._drain(self.ws), name=f"timelapse-viewer:{self.printer_id}")
        self.fresh_viewer = True
        return True

    @staticmethod
    async def _drain(ws) -> None:
        with contextlib.suppress(Exception):
            async for _ in ws:
                pass

    async def close_viewer(self) -> None:
        ws, drain = self.ws, self.drain
        self.ws, self.drain = None, None
        if ws is not None:
            with contextlib.suppress(Exception):
                await ws.close()
        if drain is not None and not drain.done():
            drain.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await drain

    async def reset(self) -> None:
        """Drop everything go2rtc holds for this camera, so a stuck request can't pin it open."""
        log.warning("%s: time-lapse frames keep failing; restarting the camera stream", self.printer_id)
        await self.close_viewer()
        await self.tl.drop_stream(self.printer_id)
        self.failures = 0
        await asyncio.sleep(RESET_PAUSE)

    async def stop(self) -> None:
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self.task
        await self.close_viewer()

    async def finish(self) -> None:
        """The print finished: one last frame of the finished part, if the camera is still open."""
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self.task
        try:
            if self.ws is not None and self.drain is not None and not self.drain.done():
                self.fresh_viewer = False
                await self.capture(final=True)
        finally:
            await self.close_viewer()


class Timelapse:
    def __init__(self, engine, root: Path, go2rtc, configs: dict[str, PrinterConfig], library,
                 min_free_bytes: int = 20 * 1024**3, client: httpx.AsyncClient | None = None):
        self.engine = engine
        self.root = Path(root)
        self.go2rtc = go2rtc
        self.configs = configs
        self.library = library
        self.min_free_bytes = min_free_bytes
        self.client = client or httpx.AsyncClient()
        self.recorders: dict[str, Recorder] = {}
        self._finishing: set[asyncio.Task] = set()
        self._warned: set[str] = set()

    # --- state ------------------------------------------------------------------

    def warn_once(self, key: str, msg: str, *args) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.warning(msg, *args)

    def storage_problem(self) -> str | None:
        """Why frames can't be written right now, or None."""
        if not self.root.is_dir():
            return f"{self.root} doesn't exist"
        if not (self.root / MARKER).exists():
            return f"{self.root} has no {MARKER} file (is the NAS mounted?)"
        try:
            free = shutil.disk_usage(self.root).free
        except OSError as exc:
            return f"can't check free space on {self.root} ({exc})"
        if free < self.min_free_bytes:
            return f"under {self.min_free_bytes // 1024**3} GB free on {self.root}"
        return None

    def can_record(self, printer_id: str) -> bool:
        cfg = self.configs.get(printer_id)
        return bool(self.go2rtc and cfg and cfg.camera == "creality_webrtc")

    # --- listening to printers ----------------------------------------------------

    async def on_status(self, status: PrinterStatus) -> None:
        pid = status.id
        if not self.can_record(pid):
            return
        rec = self.recorders.get(pid)
        if status.state in ("offline", "connecting"):
            if rec:
                rec.update(status)  # the print may carry on; the recorder lets the camera go
            return
        if status.state not in ("printing", "paused") or not status.filename:
            if rec:
                self.recorders.pop(pid, None)
                if status.state == "complete":
                    # Not awaited: the driver waits on this listener.
                    task = asyncio.create_task(rec.finish(), name=f"timelapse-finish:{pid}")
                    self._finishing.add(task)
                    task.add_done_callback(self._finishing.discard)
                else:
                    await rec.stop()
            return
        job_id = self.library.open_job_id(pid) if self.library else None
        if job_id is None:
            return
        if rec and rec.job_id != job_id:
            self.recorders.pop(pid, None)
            await rec.stop()
            rec = None
        if rec is None:
            rec = self.recorders[pid] = Recorder(self, pid, job_id)
            log.info("%s: recording a time-lapse for print %s", pid, job_id)
        rec.update(status)

    def record(self, job_id: int, frames: int, size: int) -> None:
        with Session(self.engine) as s:
            job = s.get(PrintJob, job_id)
            if job:
                job.timelapse_frames, job.timelapse_bytes = frames, size
                s.add(job)
                s.commit()

    # --- go2rtc -----------------------------------------------------------------

    async def fetch_frame(self, printer_id: str, timeout: float) -> bytes | None:
        url = f"{self.go2rtc.url}/api/frame.jpeg"
        try:
            r = await self.client.get(url, params={"src": printer_id}, timeout=timeout)
        except (httpx.HTTPError, OSError) as exc:
            log.debug("frame for %s: %r", printer_id, exc)
            return None
        data = r.content
        if r.status_code != 200 or len(data) < MIN_JPEG_BYTES or not data.startswith(b"\xff\xd8"):
            return None
        return data

    async def drop_stream(self, printer_id: str) -> None:
        with contextlib.suppress(httpx.HTTPError, OSError):
            await self.client.delete(f"{self.go2rtc.url}/api/streams", params={"src": printer_id}, timeout=5)

    # --- reading back -------------------------------------------------------------

    def folder(self, job: dict) -> Path:
        return self.root / job["printer_id"] / str(job["id"])

    def frames(self, job: dict) -> dict:
        """What History needs to play a print's time-lapse."""
        out: dict[str, Any] = {"frames": [], "gaps": [], "total_layers": None,
                               "recording": job["printer_id"] in self.recorders
                               and self.recorders[job["printer_id"]].job_id == job["id"],
                               "available": True}
        if not job.get("timelapse_frames"):
            return out
        folder = self.folder(job)
        if not folder.is_dir():
            out["available"] = False  # recorded, but the NAS isn't there right now
            return out
        frames = read_frames(folder)
        out["frames"] = [{"n": f["n"], "layer": f.get("layer"), "t": f.get("t")} for f in frames]
        out["gaps"] = gaps(frames)
        out["total_layers"] = next((f.get("total") for f in reversed(frames) if f.get("total")), None)
        return out

    def frame_path(self, job: dict, n: int) -> Path | None:
        if n < 1:
            return None
        p = self.folder(job) / frame_name(n)
        return p if p.is_file() else None

    async def stop(self) -> None:
        recs = list(self.recorders.values())
        self.recorders.clear()
        for rec in recs:
            await rec.stop()
        for task in list(self._finishing):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.client.aclose()
