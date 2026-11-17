"""Owns one driver per printer, keeps their latest status, and fans it out to browsers."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from .config import PrinterConfig
from .db import Printer
from .drivers import MoonrakerDriver, PrinterDriver, PrinterStatus

log = logging.getLogger(__name__)

DRIVERS = {"moonraker": MoonrakerDriver}
PERSIST_EVERY = timedelta(seconds=60)


class UnsupportedPrinter(PrinterDriver):
    """Placeholder for printer types whose drivers aren't built yet. Never connects."""

    async def run(self) -> None:
        self.status = self.status.model_copy(update={
            "state": "offline", "message": f"{self.config.type} printers are not supported yet",
        })
        await self._emit()


class Hub:
    def __init__(self, printers: list[PrinterConfig], engine, reconnect_seconds: float = 30.0):
        self.engine = engine
        self.drivers: dict[str, PrinterDriver] = {}
        self._clients: set[asyncio.Queue] = set()
        self._persisted: dict[str, datetime] = {}
        for cfg in printers:
            driver_cls = DRIVERS.get(cfg.type, UnsupportedPrinter)
            driver = driver_cls(cfg, reconnect_seconds=reconnect_seconds)
            driver.on_status(self._on_status)
            self.drivers[cfg.id] = driver
        self._sync_db()

    def _sync_db(self) -> None:
        with Session(self.engine) as s:
            for pid, driver in self.drivers.items():
                cfg = driver.config
                row = s.get(Printer, pid)
                if row is None:
                    row = Printer(id=pid, name=cfg.name, type=cfg.type, address=f"{cfg.host}:{cfg.port}")
                row.name, row.type, row.address = cfg.name, cfg.type, f"{cfg.host}:{cfg.port}"
                row.settings = cfg.model_dump(mode="json", exclude={"id", "name", "type", "host", "port"})
                s.add(row)
                if row.last_seen:
                    seen = row.last_seen
                    if seen.tzinfo is None:
                        seen = seen.replace(tzinfo=timezone.utc)
                    driver.status = driver.status.model_copy(update={"last_seen": seen})
            s.commit()

    def _persist_last_seen(self, status: PrinterStatus, force: bool = False) -> None:
        if not status.last_seen:
            return
        prev = self._persisted.get(status.id)
        if not force and prev and status.last_seen - prev < PERSIST_EVERY:
            return
        with Session(self.engine) as s:
            row = s.get(Printer, status.id)
            if row:
                row.last_seen = status.last_seen.astimezone(timezone.utc)
                s.add(row)
                s.commit()
        self._persisted[status.id] = status.last_seen

    async def _on_status(self, status: PrinterStatus) -> None:
        self._persist_last_seen(status, force=status.state == "offline")
        self.broadcast({"type": "printer", "printer": status.model_dump(mode="json")})

    def statuses(self) -> list[PrinterStatus]:
        return [d.status for d in self.drivers.values()]

    def get(self, printer_id: str) -> PrinterDriver | None:
        return self.drivers.get(printer_id)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._clients.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._clients.discard(queue)

    def broadcast(self, message: dict) -> None:
        for queue in list(self._clients):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:  # a stuck browser shouldn't hold up the rest
                self._clients.discard(queue)

    def start(self) -> None:
        for driver in self.drivers.values():
            driver.start()

    async def stop(self) -> None:
        for driver in self.drivers.values():
            await driver.stop()
            self._persist_last_seen(driver.status, force=True)
