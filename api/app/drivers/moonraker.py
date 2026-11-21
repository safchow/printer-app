"""Moonraker (Klipper) driver: one websocket per printer.

Rules this driver enforces, whatever the UI does:
- one connection at a time, only to the configured host and port;
- when the printer can't be reached, wait `reconnect_seconds` (30 s by default)
  before the next single attempt;
- only methods in READ_ONLY_METHODS are sent, plus, when controls are enabled,
  CONTROL_METHODS; G-code goes out only if it matches ALLOWED_GCODE exactly;
- one control at a time, never retried; a print sends one HTTP upload of the
  app's copy, then one printer.print.start.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import websockets

from ..config import PrinterConfig
from .base import ControlError, ControlsDisabled, Fan, PrinterDriver, PrinterStatus, Temperature

log = logging.getLogger(__name__)

READ_ONLY_METHODS = frozenset({"printer.objects.subscribe"})
CONTROL_METHODS = frozenset({"printer.print.start", "printer.print.pause", "printer.print.resume",
                             "printer.print.cancel", "printer.gcode.script"})
# The only G-code this driver will ever send, whole-line matches.
ALLOWED_GCODE = tuple(re.compile(p) for p in (
    r"M220 S([2-9]0|1[0-5]0|160)",  # 20-160 in steps of 10
    r"M104 S(0|[3-9][0-9]|[12][0-9]{2}|300)",
    r"M140 S(0|[3-9][0-9]|100)",
    r"M106 S([0-9]|[1-9][0-9]|1[0-9]{2}|2[0-4][0-9]|25[0-5])",
    r"SET_FAN_SPEED FAN=[A-Za-z0-9_]{1,40} SPEED=(0|0\.[1-9]|1)",
    r"SET_PIN PIN=[A-Za-z0-9_]{1,40} VALUE=[01]",
    r"PAUSE|RESUME|CANCEL_PRINT",
    r'SDCARD_PRINT_FILE FILENAME="[^"\x00-\x1f\x7f]{1,255}"',  # path also checked by print_path_ok()
))
# Notices Moonraker pushes about files and jobs; logged and passed to notice listeners (file capture).
FILE_NOTICES = frozenset({"notify_filelist_changed", "notify_history_changed"})
PRINT_MACROS = {"printer.print.pause": "PAUSE", "printer.print.resume": "RESUME",
                "printer.print.cancel": "CANCEL_PRINT"}
COMMAND_TIMEOUT = 10.0
UPLOAD_TIMEOUT = httpx.Timeout(30.0, write=600.0, read=120.0)


def gcode_allowed(script: str) -> bool:
    return any(p.fullmatch(script) for p in ALLOWED_GCODE)


PRINT_SUFFIXES = (".gcode", ".3mf")


def print_path_ok(path: str) -> bool:
    """A file path inside the G-code folder that's safe to hand to printer.print.start."""
    if not path or len(path) > 255 or path.startswith("/") or not path.lower().endswith(PRINT_SUFFIXES):
        return False
    if any(ord(c) < 32 or c in '"\\\x7f' for c in path):
        return False
    return all(part not in ("", ".", "..") and not part.startswith(".") for part in path.split("/"))

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


def objects_file_position(objects: dict[str, dict[str, Any]]) -> int | None:
    pos = _num(objects.get("virtual_sdcard", {}).get("file_position"))
    return None if pos is None else int(pos)


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
        self.controls_enabled = False
        self._ws = None
        self._waiters: dict[int, asyncio.Future] = {}
        self._control_lock = asyncio.Lock()

    def _check(self, method: str, params: dict[str, Any] | None) -> None:
        if method in READ_ONLY_METHODS:
            return
        if method not in CONTROL_METHODS:
            raise MethodNotAllowed(f"{method} is not allowed")
        if not self.controls_enabled:
            raise MethodNotAllowed(f"{method} is not allowed: controls are off")
        if method == "printer.gcode.script" and not gcode_allowed(str((params or {}).get("script", ""))):
            raise MethodNotAllowed(f"G-code {params!r} is not allowed")
        if method == "printer.print.start" and not print_path_ok(str((params or {}).get("filename", ""))):
            raise MethodNotAllowed(f"print file {params!r} is not allowed")

    async def _call(self, ws, method: str, params: dict[str, Any] | None = None) -> int:
        self._check(method, params)
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
        self._ws = ws
        try:
            await self._read(ws)
        finally:
            self._ws = None
            for fut in self._waiters.values():
                if not fut.done():
                    fut.set_exception(ControlError(502, "the connection to the printer dropped"))
            self._waiters.clear()

    async def _read(self, ws) -> None:
        pending = await self._subscribe(ws)
        async for raw in ws:
            msg = json.loads(raw)
            waiter = self._waiters.pop(msg.get("id"), None) if "id" in msg else None
            if waiter is not None:
                if not waiter.done():
                    waiter.set_result(msg)
            elif msg.get("id") == pending:
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
            elif msg.get("method") in FILE_NOTICES:
                # Passive: Moonraker pushes these on the connection we already hold.
                log.info("%s: %s %s", self.config.id, msg["method"], json.dumps(msg.get("params"))[:500])
                await self._notice(msg["method"], msg.get("params"))

    async def _publish(self) -> None:
        status = apply_objects(self.status, self._objects, self.config)
        if status.total_layers is None and status.filename and self.layer_lookup:
            pos = objects_file_position(self._objects)
            found = self.layer_lookup(self.config.id, status.filename, pos)
            if found:
                status = status.model_copy(update={"layer": found[0], "total_layers": found[1]})
        self.status = status
        await self._emit()

    async def _mark_offline(self, reason: str) -> None:
        now = datetime.now(timezone.utc)
        self.status = self.status.model_copy(update={
            "state": "offline", "message": reason, "updated_at": now,
            "started_at": None, "eta_at": None,
        })
        await self._emit()

    # --- controls -----------------------------------------------------------------

    def _require_enabled(self) -> None:
        if not self.controls_enabled:
            raise ControlsDisabled("controls are off (CONTROLS_ENABLED)")

    async def _command(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one control and wait for its reply. Never retried."""
        self._require_enabled()
        ws = self._ws
        if ws is None:
            raise ControlError(503, "not connected to the printer")
        async with self._control_lock:
            rid = await self._call(ws, method, params)
            fut = asyncio.get_running_loop().create_future()
            self._waiters[rid] = fut
            try:
                return await asyncio.wait_for(fut, COMMAND_TIMEOUT)
            except TimeoutError as exc:
                raise ControlError(504, "the printer didn't answer in time") from exc
            finally:
                self._waiters.pop(rid, None)

    async def _ok(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg = await self._command(method, params)
        if "error" in msg:
            err = msg["error"] or {}
            raise ControlError(502, f"the printer refused: {err.get('message', 'unknown error')}")

    async def _gcode(self, script: str) -> str:
        await self._ok("printer.gcode.script", {"script": script})
        return script

    async def _print_action(self, method: str) -> str:
        self._require_enabled()
        msg = await self._command(method)
        if "error" in msg and (msg["error"] or {}).get("code") == -32601:
            # This Moonraker build lacks the method, so nothing happened: use the Klipper macro.
            return await self._gcode(PRINT_MACROS[method])
        if "error" in msg:
            raise ControlError(502, f"the printer refused: {msg['error'].get('message', 'unknown error')}")
        return method

    async def upload_file(self, local: Path, path: str) -> str:
        """One HTTP upload of a file into the G-code folder (POST /server/files/upload). Never retried."""
        self._require_enabled()
        if not print_path_ok(path):
            raise ControlError(422, "that file name can't be sent to the printer")
        folder, _, name = path.rpartition("/")
        url = f"http://{self.config.host}:{self.config.port}/server/files/upload"
        try:
            async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT) as client:
                with local.open("rb") as fh:
                    r = await client.post(url, data={"root": "gcodes", "path": folder},
                                          files={"file": (name, fh, "application/octet-stream")})
        except httpx.HTTPError as exc:
            raise ControlError(502, f"couldn't upload the file to the printer ({type(exc).__name__})") from exc
        if r.status_code not in (200, 201):
            raise ControlError(502, f"the printer refused the upload (HTTP {r.status_code})")
        return f"upload {path}"

    async def start_print(self, path: str) -> str:
        self._require_enabled()
        if not print_path_ok(path):
            raise ControlError(422, "that file name can't be sent to the printer")
        msg = await self._command("printer.print.start", {"filename": path})
        if "error" in msg and (msg["error"] or {}).get("code") == -32601:
            # No printer.print.start on this Moonraker build: Klipper's own command does the same.
            return await self._gcode(f'SDCARD_PRINT_FILE FILENAME="{path}"')
        if "error" in msg:
            raise ControlError(502, f"the printer refused: {(msg['error'] or {}).get('message', 'unknown error')}")
        return f"printer.print.start {path}"

    async def pause(self) -> str:
        return await self._print_action("printer.print.pause")

    async def resume(self) -> str:
        return await self._print_action("printer.print.resume")

    async def cancel(self) -> str:
        return await self._print_action("printer.print.cancel")

    async def set_speed(self, percent: int) -> str:
        self._require_enabled()
        return await self._gcode(f"M220 S{percent}")

    async def set_fan(self, name: str, percent: int) -> str:
        self._require_enabled()
        obj = self.config.moonraker.fans.get(name)
        if obj is None:
            raise ControlError(404, f"no fan called {name!r}")
        kind, _, short = obj.partition(" ")
        if kind == "fan_generic" and short:
            speed = "0" if percent == 0 else "1" if percent == 100 else f"0.{percent // 10}"
            return await self._gcode(f"SET_FAN_SPEED FAN={short} SPEED={speed}")
        if obj == "fan":
            return await self._gcode(f"M106 S{round(percent * 255 / 100)}")
        raise ControlError(501, f"{obj} can't be set from the app")

    async def set_temp(self, heater: str, target: int) -> str:
        self._require_enabled()
        code = {"nozzle": "M104", "bed": "M140"}.get(heater)
        if code is None:
            raise ControlError(404, f"no heater called {heater!r}")
        return await self._gcode(f"{code} S{target}")

    async def set_light(self, on: bool) -> str:
        self._require_enabled()
        obj = self.config.moonraker.light or ""
        kind, _, pin = obj.partition(" ")
        if kind != "output_pin" or not pin:
            raise ControlError(501, "this printer has no light the app can switch")
        return await self._gcode(f"SET_PIN PIN={pin} VALUE={1 if on else 0}")
