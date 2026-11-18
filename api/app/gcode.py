"""The G-code of the current print, fetched once per print for the 3D toolpath preview.

Rules (one download per print):
- one GET to the printer's own Moonraker, `/server/files/gcodes/<file>`, when a
  print is seen that isn't cached yet; never retried, even if it fails;
- one download at a time per printer, capped at MAX_BYTES;
- the file is kept on disk (data/gcode/<printer>/), with the print it belongs
  to, so a restart of the app doesn't fetch it again.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import re
import logging
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import PrinterConfig
from .drivers import PrinterStatus

log = logging.getLogger(__name__)

MAX_BYTES = 200 * 1024 * 1024
TIMEOUT = httpx.Timeout(30.0, read=60.0)
# Two sightings of the same file count as the same print if their start times are this close.
SAME_PRINT_SECONDS = 300
# Slicer comments that start a layer (PrusaSlicer/Orca/Creality Print, Cura), and header lines with the count.
LAYER_MARK = re.compile(rb"^;(?:LAYER_CHANGE|LAYER:-?\d+)\s*$", re.M)
TOTAL_MARK = re.compile(rb"^;\s*(?:total layer number|total layers count|LAYER_COUNT)\s*[:=]\s*(\d+)", re.M | re.I)


def layer_offsets(data: bytes) -> tuple[list[int], int | None]:
    """Byte offsets where each layer starts, and the layer count the header states (if any)."""
    offsets = [m.start() for m in LAYER_MARK.finditer(data)]
    m = TOTAL_MARK.search(data, 0, 64 * 1024)
    return offsets, int(m.group(1)) if m else None


def gcode_url(config: PrinterConfig, filename: str) -> str:
    path = "/".join(quote(part, safe="") for part in filename.split("/") if part not in ("", ".", ".."))
    return f"http://{config.host}:{config.port}/server/files/gcodes/{path}"


class GcodeCache:
    def __init__(self, data_dir: Path, configs: dict[str, PrinterConfig], max_bytes: int = MAX_BYTES,
                 library=None):
        self.root = data_dir / "gcode"
        self.configs = configs
        self.max_bytes = max_bytes
        self._tried: dict[str, tuple[str, float]] = {}  # printer -> (filename, started_at) of the running print
        self._tasks: dict[str, asyncio.Task] = {}
        self.downloads = 0
        # File capture (app/library.py), when on: a file it already holds isn't downloaded again,
        # and a file fetched here is kept there too.
        self.library = library
        # printer -> (filename, size, layer start offsets, total): built once per cached file.
        self._layers: dict[str, tuple[str, int, list[int], int]] = {}
        self._indexing: dict[str, asyncio.Task] = {}

    def _dir(self, printer_id: str) -> Path:
        return self.root / printer_id

    def meta(self, printer_id: str) -> dict | None:
        try:
            return json.loads((self._dir(printer_id) / "meta.json").read_text())
        except (OSError, ValueError):
            return None

    def path(self, printer_id: str) -> Path:
        return self._dir(printer_id) / "current.gcode"

    @staticmethod
    def _same(a: tuple[str, float], b: tuple[str, float]) -> bool:
        return a[0] == b[0] and abs(a[1] - b[1]) < SAME_PRINT_SECONDS

    def info(self, status: PrinterStatus) -> dict:
        """What the page needs to know: is there a file for the print on screen?"""
        meta = self.meta(status.id)
        if not status.filename:
            return {"state": "none"}
        if meta and meta.get("filename") == status.filename and "size" in meta and self.path(status.id).is_file():
            return {"state": "ready", "filename": meta["filename"], "size": meta["size"]}
        task = self._tasks.get(status.id)
        if task and not task.done():
            return {"state": "fetching", "filename": status.filename}
        if meta and meta.get("filename") == status.filename and meta.get("error"):
            return {"state": "failed", "filename": status.filename, "detail": meta["error"]}
        return {"state": "none", "filename": status.filename}

    def ready(self, status: PrinterStatus) -> bool:
        return self.info(status)["state"] == "ready"

    def layer_at(self, printer_id: str, filename: str | None, file_position: int | None) -> tuple[int, int] | None:
        """(current layer, total layers) worked out from the cached G-code and how far the printer has read it.

        For printers that don't report layers themselves: Klipper only knows them when the file calls
        SET_PRINT_STATS_INFO, and Creality Print doesn't. Reads nothing from the printer.
        """
        index = self._layers.get(printer_id)
        if not index or index[0] != filename or file_position is None:
            return None
        offsets, total = index[2], index[3]
        return min(bisect.bisect_right(offsets, file_position), total), total

    def _index(self, printer_id: str) -> None:
        meta = self.meta(printer_id) or {}
        path = self.path(printer_id)
        if "size" not in meta or not path.is_file():
            return
        cur = self._layers.get(printer_id)
        if cur and cur[0] == meta["filename"] and cur[1] == meta["size"]:
            return
        offsets, stated = layer_offsets(path.read_bytes())
        total = stated or len(offsets)
        if total:
            self._layers[printer_id] = (meta["filename"], meta["size"], offsets, total)
            log.info("%s: %d layers in %s (%d marks)", printer_id, total, meta["filename"], len(offsets))

    def _start_index(self, printer_id: str) -> None:
        task = self._indexing.get(printer_id)
        if task and not task.done():
            return
        self._indexing[printer_id] = asyncio.create_task(asyncio.to_thread(self._index, printer_id),
                                                         name=f"layers:{printer_id}")

    async def on_status(self, status: PrinterStatus) -> None:
        """Called on every status update; starts the one download for a new print."""
        if status.state in ("printing", "paused") and status.total_layers is None and self.ready(status):
            index = self._layers.get(status.id)
            if not index or index[0] != status.filename:
                self._start_index(status.id)
        if status.state not in ("printing", "paused"):
            if status.state not in ("offline", "connecting"):
                self._tried.pop(status.id, None)  # that print is over; the next one is new
            return
        if not status.filename or not status.started_at:
            return
        if status.id not in self.configs or status.type != "moonraker":
            return
        key = (status.filename, status.started_at.timestamp())
        tried = self._tried.get(status.id)
        if tried and (tried[0] == key[0] or self._same(tried, key)):
            return  # already tried for this print (it has been running ever since)
        meta = self.meta(status.id)
        if meta and self._same((meta.get("filename", ""), meta.get("started_at", 0.0)), key):
            self._tried[status.id] = key  # fetched (or failed) before a restart: don't ask again
            return
        task = self._tasks.get(status.id)
        if task and not task.done():
            return
        self._tried[status.id] = key
        self._tasks[status.id] = asyncio.create_task(self._fetch(status.id, status.filename, key[1]),
                                                     name=f"gcode:{status.id}")

    async def _fetch(self, printer_id: str, filename: str, started_at: float) -> None:
        config = self.configs[printer_id]
        folder = self._dir(printer_id)
        folder.mkdir(parents=True, exist_ok=True)
        part = folder / "download.part"
        meta = {"filename": filename, "started_at": started_at, "fetched_at": datetime.now().isoformat()}
        try:
            stored = await self.library.settled(printer_id, filename) if self.library else None
            if stored is not None and self.library.file_path(stored).is_file():
                await asyncio.to_thread(shutil.copyfile, self.library.file_path(stored), part)
                part.replace(self.path(printer_id))
                meta["size"] = self.path(printer_id).stat().st_size
                meta["from_library"] = stored.id
                self.library.link_print(printer_id, stored)
                (folder / "meta.json").write_text(json.dumps(meta))
                return
        except Exception:
            log.exception("%s: couldn't use the stored copy of %s", printer_id, filename)
        self.downloads += 1
        log.info("%s: fetching %s for the preview (once for this print)", printer_id, filename)
        try:
            size = 0
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream("GET", gcode_url(config, filename)) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"the printer answered {r.status_code}")
                    with part.open("wb") as f:
                        async for chunk in r.aiter_bytes(256 * 1024):
                            size += len(chunk)
                            if size > self.max_bytes:
                                raise RuntimeError(f"the file is over {self.max_bytes // (1024 * 1024)} MB")
                            f.write(chunk)
            part.replace(self.path(printer_id))
            meta["size"] = size
            if self.library:
                try:
                    stored = await asyncio.to_thread(self.library.ingest, self.path(printer_id), filename,
                                                     "print", printer_id)
                    self.library.link_print(printer_id, stored)
                except Exception:
                    log.exception("%s: couldn't keep %s in the library", printer_id, filename)
        except Exception as exc:  # never retried; the page shows why
            part.unlink(missing_ok=True)
            meta["error"] = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("%s: couldn't fetch %s: %s", printer_id, filename, meta["error"])
        (folder / "meta.json").write_text(json.dumps(meta))

    async def stop(self) -> None:
        for task in [*self._tasks.values(), *self._indexing.values()]:
            task.cancel()
        for task in [*self._tasks.values(), *self._indexing.values()]:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
