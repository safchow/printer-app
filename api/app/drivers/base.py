"""The one interface every printer type implements, and the status it reports."""

from __future__ import annotations

import abc
import asyncio
from datetime import datetime
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from ..config import PrinterConfig

State = Literal[
    "connecting",  # never reached yet since startup
    "offline",  # was reachable, now isn't (or never answered)
    "idle",
    "printing",
    "paused",
    "complete",
    "cancelled",
    "error",
]


class Temperature(BaseModel):
    current: float | None = None
    target: float | None = None


class Fan(BaseModel):
    name: str
    percent: float | None = None


class PrinterStatus(BaseModel):
    id: str
    name: str
    type: str
    state: State = "connecting"
    message: str | None = None
    filename: str | None = None
    progress: float | None = Field(None, description="file progress, 0-1")
    layer: int | None = None
    total_layers: int | None = None
    print_duration: float | None = Field(None, description="seconds spent printing")
    total_duration: float | None = Field(None, description="seconds since the job started")
    started_at: datetime | None = None
    eta_at: datetime | None = None
    nozzle: Temperature = Field(default_factory=Temperature)
    bed: Temperature = Field(default_factory=Temperature)
    speed_percent: float | None = None
    fans: list[Fan] = Field(default_factory=list)
    light_on: bool | None = None
    has_camera: bool = False
    camera: str = "none"
    cfs: bool = False
    last_seen: datetime | None = None
    updated_at: datetime | None = None
    reconnect_seconds: float = 30.0


StatusListener = Callable[[PrinterStatus], Awaitable[None]]


class ControlsDisabled(RuntimeError):
    """Raised by every control when controls are off for this printer (CONTROLS_ENABLED)."""


class ControlError(RuntimeError):
    """A control that couldn't be done. `status` is the HTTP status to report."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class PrinterDriver(abc.ABC):
    def __init__(self, config: PrinterConfig, reconnect_seconds: float = 30.0):
        self.config = config
        self.reconnect_seconds = reconnect_seconds
        self.status = PrinterStatus(
            id=config.id,
            name=config.name,
            type=config.type,
            has_camera=config.camera != "none",
            camera=config.camera,
            cfs=config.cfs,
            reconnect_seconds=reconnect_seconds,
        )
        self._listeners: list[StatusListener] = []
        self._task: asyncio.Task | None = None

    def on_status(self, listener: StatusListener) -> None:
        self._listeners.append(listener)

    async def _emit(self) -> None:
        for listener in list(self._listeners):
            await listener(self.status)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name=f"driver:{self.config.id}")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    @abc.abstractmethod
    async def run(self) -> None:
        """Keep one connection to the printer alive and update self.status."""

    # Controls. Each returns the command it sent, for the action log. The
    # checks every printer type shares (state, limits, rate) are in
    # app/controls.py; a driver only builds and sends its own commands.
    controls_enabled = False

    async def pause(self) -> str:
        raise ControlsDisabled("controls are not supported for this printer type")

    async def resume(self) -> str:
        raise ControlsDisabled("controls are not supported for this printer type")

    async def cancel(self) -> str:
        raise ControlsDisabled("controls are not supported for this printer type")

    async def set_speed(self, percent: int) -> str:
        raise ControlsDisabled("controls are not supported for this printer type")

    async def set_fan(self, name: str, percent: int) -> str:
        raise ControlsDisabled("controls are not supported for this printer type")

    async def set_temp(self, heater: str, target: int) -> str:
        raise ControlsDisabled("controls are not supported for this printer type")

    async def set_light(self, on: bool) -> str:
        raise ControlsDisabled("controls are not supported for this printer type")
