"""Moonraker (Klipper) driver: one websocket per printer, read-only.

Rules this driver enforces, whatever the UI does:
- one connection at a time, only to the configured host and port;
- when the printer can't be reached, wait `reconnect_seconds` (30 s by default)
  before the next single attempt;
- only methods in READ_ONLY_METHODS are ever sent.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import websockets

from ..config import PrinterConfig
from .base import Fan, PrinterDriver, PrinterStatus, Temperature

log = logging.getLogger(__name__)

READ_ONLY_METHODS = frozenset({"printer.objects.subscribe"})

BASE_OBJECTS = (
    "print_stats",
    "virtual_sdcard",
    "display_status",
    "extruder",
    "heater_bed",
    "gcode_move",
)

STATE_MAP = {
    "standby": "idle",
    "printing": "printing",
    "paused": "paused",
    "complete": "complete",
    "cancelled": "cancelled",
    "error": "error",
}


class MethodNotAllowed(RuntimeError):
    pass


def subscribed_objects(config: PrinterConfig) -> dict[str, None]:
    names = list(BASE_OBJECTS) + list(config.moonraker.fans.values())
    if config.moonraker.light:
        names.append(config.moonraker.light)
    return {name: None for name in dict.fromkeys(names)}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _percent(obj: dict[str, Any] | None) -> float | None:
    """Fans report `speed` (fan) or `value` (output_pin), both 0-1."""
    if not obj:
        return None
    raw = _num(obj.get("speed", obj.get("value")))
    return None if raw is None else round(raw * 100, 1)


def apply_objects(status: PrinterStatus, objects: dict[str, dict[str, Any]],
                  config: PrinterConfig, now: datetime | None = None) -> PrinterStatus:
    """Turn the merged Klipper object tree into our PrinterStatus."""
    now = now or datetime.now(timezone.utc)
    ps = objects.get("print_stats", {})
    sd = objects.get("virtual_sdcard", {})
    display = objects.get("display_status", {})
    info = ps.get("info") or {}

    state = STATE_MAP.get(ps.get("state", "standby"), "idle")
    active = state in ("printing", "paused")
    has_job = state in ("printing", "paused", "complete", "cancelled", "error")

    progress = _num(sd.get("progress"))
    if progress is None:
        progress = _num(display.get("progress"))
    print_duration = _num(ps.get("print_duration"))
    total_duration = _num(ps.get("total_duration"))

    started_at = eta_at = None
    if active and total_duration is not None:
        started_at = now - timedelta(seconds=total_duration)
    if state == "printing" and progress and progress > 0.001 and print_duration:
        remaining = print_duration / progress - print_duration
        eta_at = now + timedelta(seconds=remaining)

    extruder = objects.get("extruder", {})
    bed = objects.get("heater_bed", {})
    speed = _num(objects.get("gcode_move", {}).get("speed_factor"))

    light_on = None
    if config.moonraker.light and config.moonraker.light in objects:
        value = _num(objects[config.moonraker.light].get("value"))
        light_on = None if value is None else value > 0

    return status.model_copy(update={
        "state": state,
        "message": ps.get("message") or display.get("message") or None,
        "filename": ps.get("filename") or None if has_job else None,
        "progress": progress if has_job else None,
        "layer": info.get("current_layer") if has_job else None,
        "total_layers": info.get("total_layer") if has_job else None,
        "print_duration": print_duration if has_job else None,
        "total_duration": total_duration if has_job else None,
        "started_at": started_at,
        "eta_at": eta_at,
        "nozzle": Temperature(current=_num(extruder.get("temperature")), target=_num(extruder.get("target"))),
        "bed": Temperature(current=_num(bed.get("temperature")), target=_num(bed.get("target"))),
        "speed_percent": None if speed is None else round(speed * 100),
        "fans": [Fan(name=label, percent=_percent(objects.get(obj)))
                 for label, obj in config.moonraker.fans.items() if obj in objects],
        "light_on": light_on,
        "last_seen": now,
        "updated_at": now,
    })


def _deep_merge(into: dict[str, Any], new: dict[str, Any]) -> None:
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            _deep_merge(into[key], value)
        else:
            into[key] = value


class MoonrakerDriver(PrinterDriver):
    def __init__(self, config: PrinterConfig, reconnect_seconds: float = 30.0):
        super().__init__(config, reconnect_seconds)
        self.url = f"ws://{config.host}:{config.port}/websocket"
        self._ids = itertools.count(1)
        self._objects: dict[str, dict[str, Any]] = {}
        self.connection_attempts = 0

    async def _call(self, ws, method: str, params: dict[str, Any] | None = None) -> int:
        if method not in READ_ONLY_METHODS:
            raise MethodNotAllowed(f"{method} is not allowed: this driver is read-only")
        rid = next(self._ids)
        await ws.send(json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}, "id": rid}))
        return rid

    async def run(self) -> None:
        while True:
            self.connection_attempts += 1
            try:
                async with websockets.connect(
                    self.url, open_timeout=10, close_timeout=5,
                    ping_interval=20, ping_timeout=20, max_size=4 * 1024 * 1024,
                ) as ws:
                    await self._session(ws)
                reason = "connection closed"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network errors, refused, timeouts, bad replies
                reason = f"{type(exc).__name__}: {exc}"[:200]
            log.info("%s offline (%s); next try in %ss", self.config.id, reason, self.reconnect_seconds)
            await self._mark_offline(reason)
            await asyncio.sleep(self.reconnect_seconds)

    async def _subscribe(self, ws) -> int:
        return await self._call(ws, "printer.objects.subscribe", {"objects": subscribed_objects(self.config)})

    async def _session(self, ws) -> None:
        pending = await self._subscribe(ws)
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("id") == pending:
                if "error" in msg:
                    # Usually "Klippy not ready": drop and retry on the normal schedule.
                    raise RuntimeError(msg["error"].get("message", "subscribe failed"))
                self._objects = {}
                _deep_merge(self._objects, msg["result"].get("status", {}))
                await self._publish()
            elif msg.get("method") == "notify_status_update":
                _deep_merge(self._objects, msg["params"][0])
                await self._publish()
            elif msg.get("method") in ("notify_klippy_shutdown", "notify_klippy_disconnected"):
                self.status = self.status.model_copy(update={
                    "state": "error", "message": "Klipper is not ready",
                    "last_seen": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
                })
                await self._emit()
            elif msg.get("method") == "notify_klippy_ready":
                pending = await self._subscribe(ws)

    async def _publish(self) -> None:
        self.status = apply_objects(self.status, self._objects, self.config)
        await self._emit()

    async def _mark_offline(self, reason: str) -> None:
        now = datetime.now(timezone.utc)
        self.status = self.status.model_copy(update={
            "state": "offline", "message": reason, "updated_at": now,
            "started_at": None, "eta_at": None,
        })
        await self._emit()
