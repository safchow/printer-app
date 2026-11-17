"""Printer controls: the checks every printer type shares, then the driver, then the log.

Order for every press:
1. controls on (CONTROLS_ENABLED) and the printer known;
2. values within limits;
3. the printer's live state allows it (no command while offline);
4. at most one command per printer at a time, and one per MIN_GAP seconds;
5. the driver builds and sends its one command (never retried);
6. the press is written to action_log, whatever happened.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session, select

from .db import ActionLog
from .drivers import PrinterDriver
from .drivers.base import ControlError, ControlsDisabled

log = logging.getLogger(__name__)

MIN_GAP = 1.0
SPEEDS = range(20, 161, 10)
PRINT_STATES = ("printing", "paused")
OFFLINE = ("offline", "connecting")
LIMITS = {"nozzle": 300, "bed": 100}
MIN_HEAT = 30


def check(driver: PrinterDriver, action: str, values: dict[str, Any]) -> None:
    """Raise ControlError if this press can't be sent right now."""
    state = driver.status.state
    if state in OFFLINE:
        raise ControlError(409, "the printer is offline")
    if action == "pause" and state != "printing":
        raise ControlError(409, "nothing is printing")
    if action == "resume" and state != "paused":
        raise ControlError(409, "the print isn't paused")
    if action == "cancel" and state not in PRINT_STATES:
        raise ControlError(409, "nothing is printing")
    if action == "speed":
        if state not in PRINT_STATES:
            raise ControlError(409, "speed can only be changed during a print")
        if values["percent"] not in SPEEDS:
            raise ControlError(422, "speed must be 20 to 160% in steps of 10")
    if action == "fan":
        if values["percent"] not in range(0, 101, 10):
            raise ControlError(422, "fan speed must be 0 to 100% in steps of 10")
        if values["name"] not in driver.config.moonraker.fans:
            raise ControlError(404, f"no fan called {values['name']!r}")
    if action == "temp":
        heater, target = values["heater"], values["target"]
        if heater not in LIMITS:
            raise ControlError(404, f"no heater called {heater!r}")
        if target != 0 and not MIN_HEAT <= target <= LIMITS[heater]:
            raise ControlError(422, f"{heater} target must be 0 or {MIN_HEAT} to {LIMITS[heater]} °C")


class Controls:
    def __init__(self, engine, enabled: bool, min_gap: float | None = None):
        self.engine = engine
        self.enabled = enabled
        self.min_gap = MIN_GAP if min_gap is None else min_gap
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def run(self, driver: PrinterDriver, action: str, values: dict[str, Any], device: str | None) -> str:
        command = None
        try:
            if not self.enabled:
                raise ControlsDisabled("controls are turned off on this server (CONTROLS_ENABLED)")
            check(driver, action, values)
            lock = self._locks.setdefault(driver.config.id, asyncio.Lock())
            if lock.locked():
                raise ControlError(429, "another command for this printer is still running")
            async with lock:
                wait = self.min_gap - (time.monotonic() - self._last.get(driver.config.id, -1e9))
                if wait > 0:
                    raise ControlError(429, "one command per second, please")
                self._last[driver.config.id] = time.monotonic()
                command = await self._send(driver, action, values)
        except ControlsDisabled as exc:
            self._log(driver, action, values, None, "refused", str(exc), device)
            raise
        except ControlError as exc:
            result = "refused" if exc.status in (404, 409, 422, 429, 501) else "failed"
            self._log(driver, action, values, command, result, exc.detail, device)
            raise
        self._log(driver, action, values, command, "sent", None, device)
        return command

    @staticmethod
    async def _send(driver: PrinterDriver, action: str, v: dict[str, Any]) -> str:
        match action:
            case "pause":
                return await driver.pause()
            case "resume":
                return await driver.resume()
            case "cancel":
                return await driver.cancel()
            case "speed":
                return await driver.set_speed(v["percent"])
            case "fan":
                return await driver.set_fan(v["name"], v["percent"])
            case "temp":
                return await driver.set_temp(v["heater"], v["target"])
            case "light":
                return await driver.set_light(v["on"])
        raise ControlError(404, f"unknown action {action}")

    def _log(self, driver, action, values, command, result, detail, device) -> None:
        try:
            with Session(self.engine) as s:
                s.add(ActionLog(printer_id=driver.config.id, action=action, values=values, command=command,
                                result=result, detail=detail, device=device, at=datetime.now(timezone.utc)))
                s.commit()
        except Exception:  # the log must never stop a control from being reported
            log.exception("couldn't write the action log")

    def recent(self, printer_id: str, limit: int = 20) -> list[ActionLog]:
        with Session(self.engine) as s:
            rows = s.exec(select(ActionLog).where(ActionLog.printer_id == printer_id)
                          .order_by(ActionLog.id.desc()).limit(limit)).all()
        for r in rows:
            if r.at.tzinfo is None:
                r.at = r.at.replace(tzinfo=timezone.utc)
        return list(rows)
