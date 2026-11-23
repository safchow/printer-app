"""Print queue: start stored files later, one at a time, only when the printer is free.

Each printer has an ordered list of items. Only the item at the head of the list
is ever started. An item's start rule is one of:

- ``at``    : at a set time. If the printer is busy then, it waits up to its
              window (``window_minutes``; none = any time), then becomes
              ``missed``. A queued print never starts later than you allowed.
- ``idle``  : as soon as the printer is free.
- ``after`` : after the item above it finished *successfully*, plus an optional
              gap; its window counts from then. If that print was cancelled or
              failed, the item holds until you change it.

Before an item starts, every one of these must hold:

1. the queue for that printer isn't paused, and controls are on;
2. the printer's live state (the app's one Moonraker subscription, kept alive by
   websocket pings) is idle, complete or cancelled;
3. the plate was confirmed clear, and no print has started or ended on that
   printer since (prints from Creality Print or the touchscreen count too);
4. the stored file still exists.

Waiting sends nothing to the printer. Starting is the Print button's own path
(upload the app's copy, then one start command), tried ONCE per item: if it
fails the item is ``failed`` and the printer's queue pauses until you look. An
item the app was in the middle of starting when it stopped becomes
``needs_check`` on the next start-up and is never retried by itself, since the
printer may already be printing it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Literal

from pydantic import BaseModel, Field as PField, model_validator
from sqlalchemy import or_
from sqlmodel import Session, select

from .controls import OFFLINE, PRINT_STATES, READY_STATES
from .db import PrintJob, QueueItem, QueueState, StoredFile
from .library import upload_path
from .drivers.base import ControlError, ControlsDisabled, PrinterStatus

log = logging.getLogger(__name__)

TICK_SECONDS = 15.0
ACTIVE = ("scheduled", "starting")
DONE = ("started", "missed", "failed", "cancelled", "needs_check")
WINDOWS = (30, 120, 360)  # the dialog's choices; any value from 5 min to 7 days is accepted
DONE_SHOWN = 20

Mode = Literal["at", "idle", "after"]
# (printer id, stored file id, device) -> the command sent. Raises ControlError / ControlsDisabled.
StartFn = Callable[[str, int, str], Awaitable[str]]


def now() -> datetime:
    return datetime.now(timezone.utc)


def utc(d: datetime | None) -> datetime | None:
    if d is None:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


class QueueError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class ItemBody(BaseModel):
    printer_id: str = PField(max_length=60)
    file_id: int
    mode: Mode
    start_at: datetime | None = None
    window_minutes: int | None = PField(120, ge=5, le=7 * 24 * 60)
    gap_minutes: int = PField(0, ge=0, le=24 * 60)
    plate_clear: bool = False

    @model_validator(mode="after")
    def _start_needed(self):
        if self.mode == "at" and self.start_at is None:
            raise ValueError("start_at is needed for an 'at' item")
        return self


class EditBody(BaseModel):
    mode: Mode | None = None
    start_at: datetime | None = None
    window_minutes: int | None = PField(None, ge=5, le=7 * 24 * 60)
    any_time: bool = False  # window_minutes=None can't say "no window", so this does
    gap_minutes: int | None = PField(None, ge=0, le=24 * 60)


class PrintQueue:
    def __init__(self, engine, drivers: dict, start: StartFn, controls_enabled: bool,
                 tick_seconds: float | None = None):
        self.engine = engine
        self.drivers = drivers  # printer id -> driver (has .status, .config)
        self.start_print = start
        self.controls_enabled = controls_enabled
        self.tick_seconds = TICK_SECONDS if tick_seconds is None else tick_seconds
        self._last_state: dict[str, str] = {}
        self._print_ended: dict[str, datetime] = {}  # seen live, in case the history row is late
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._busy = asyncio.Lock()

    # --- lifecycle -------------------------------------------------------------------------

    def start(self) -> None:
        self.recover()
        self._task = asyncio.create_task(self._loop(), name="print-queue")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def recover(self) -> None:
        """After a restart: half-started items need a person; nothing is retried by itself."""
        with Session(self.engine) as s:
            for item in s.exec(select(QueueItem).where(QueueItem.status == "starting")).all():
                item.status, item.finished_at = "needs_check", now()
                item.note = "The app stopped while starting this. Check the printer before running it again."
                s.add(item)
                self._pause(s, item.printer_id, "Paused after a restart in the middle of a start.")
                log.warning("queue item %s was mid-start at shutdown: marked needs_check", item.id)
            s.commit()

    async def on_status(self, status: PrinterStatus) -> None:
        prev = self._last_state.get(status.id)
        self._last_state[status.id] = status.state
        if status.state in OFFLINE:
            return
        if prev in PRINT_STATES and status.state not in PRINT_STATES:
            self._print_ended[status.id] = now()
        if prev != status.state:
            self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), self.tick_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            try:
                await self.tick()
            except Exception:
                log.exception("print queue tick failed")

    # --- the runner --------------------------------------------------------------------------

    async def tick(self) -> None:
        async with self._busy:
            for printer_id in list(self.drivers):
                await self._tick_printer(printer_id)

    async def _tick_printer(self, printer_id: str) -> None:
        t = now()
        with Session(self.engine) as s:
            items = self._active(s, printer_id)
            self._link_jobs(s, printer_id)
            # Windows pass for every item, not just the head.
            for item in items:
                _, until = self._due(s, item)
                if until is not None and t > until and item.status == "scheduled":
                    item.status, item.finished_at = "missed", t
                    item.note = self._missed_reason(printer_id)
                    s.add(item)
                    log.info("queue item %s missed its window", item.id)
            s.commit()
            items = [i for i in items if i.status == "scheduled"]
            if not items:
                return
            head = items[0]
            reason = self._blocker(s, head, t)
            if reason:
                if head.note != reason:
                    head.note = reason
                    s.add(head)
                    s.commit()
                return
            head.status, head.note, head.started_at = "starting", "Starting…", t
            s.add(head)
            s.commit()
            item_id, file_id = head.id, head.file_id

        log.info("queue: starting item %s (file %s) on %s", item_id, file_id, printer_id)
        result: tuple[str, str | None]
        try:
            command = await self.start_print(printer_id, file_id, "queue")
            result = ("started", command)
        except ControlsDisabled as exc:
            result = ("back", str(exc))
        except ControlError as exc:
            # 409/429: the printer or another command got there first; nothing was sent. Try later.
            result = ("back", exc.detail) if exc.status in (409, 429) else ("failed", exc.detail)
        except Exception as exc:  # never let one item stop the runner
            log.exception("queue item %s failed to start", item_id)
            result = ("failed", str(exc) or type(exc).__name__)

        with Session(self.engine) as s:
            item = s.get(QueueItem, item_id)
            kind, text = result
            if kind == "started":
                item.status, item.note, item.finished_at = "started", None, now()
                item.command = text
            elif kind == "back":
                item.status, item.started_at, item.note = "scheduled", None, f"Waiting: {text}"
            else:
                item.status, item.finished_at, item.note = "failed", now(), f"Not started: {text}"
                self._pause(s, printer_id, "Paused because a queued print failed to start.")
            s.add(item)
            s.commit()

    def _blocker(self, s: Session, item: QueueItem, t: datetime) -> str | None:
        """Why the head item can't start right now, or None when it can."""
        state = self._state(s, item.printer_id)
        if state.paused:
            return "Queue paused"
        if not self.controls_enabled:
            return "Controls are off on this server (CONTROLS_ENABLED)"
        due, _ = self._due(s, item)
        if due is None:
            return self._hold_reason(s, item)
        if t < due:
            return "Scheduled"
        driver = self.drivers.get(item.printer_id)
        if not driver:
            return "This printer isn't set up any more"
        st = driver.status.state
        if st in OFFLINE:
            return "Waiting for the printer to come online"
        if st in PRINT_STATES:
            return "Waiting for the printer to finish"
        if st not in READY_STATES:
            return "Waiting: the printer reports an error"
        if not self.plate_ok(s, item.printer_id):
            return "Clear the plate"
        if not s.get(StoredFile, item.file_id):
            return "The file was deleted"
        return None

    def _due(self, s: Session, item: QueueItem) -> tuple[datetime | None, datetime | None]:
        """(earliest start, latest start). earliest None = held; latest None = no limit."""
        window = timedelta(minutes=item.window_minutes) if item.window_minutes else None
        if item.mode == "at":
            due = utc(item.start_at)
            return due, (due + window if window else None)
        if item.mode == "idle":
            return utc(item.created_at), None
        prev = s.get(QueueItem, item.after_id) if item.after_id else None
        if prev is None:
            return utc(item.created_at), None  # nothing above it: like "when idle"
        if prev.status != "started":
            return None, None
        job = s.get(PrintJob, prev.job_id) if prev.job_id else None
        if not job or job.ended_at is None or job.result != "complete":
            return None, None
        due = utc(job.ended_at) + timedelta(minutes=item.gap_minutes)
        return due, (due + window if window else None)

    def _hold_reason(self, s: Session, item: QueueItem) -> str:
        prev = s.get(QueueItem, item.after_id) if item.after_id else None
        if prev is None:
            return "Waiting"
        if prev.status in ("scheduled", "starting"):
            return "Waiting for the print above"
        if prev.status != "started":
            return f"Held: the item above was {prev.status.replace('_', ' ')}"
        job = s.get(PrintJob, prev.job_id) if prev.job_id else None
        if job and job.ended_at is not None and job.result != "complete":
            return f"Held: the print above ended as {job.result}"
        return "Waiting for the print above to finish"

    def _missed_reason(self, printer_id: str) -> str:
        driver = self.drivers.get(printer_id)
        st = driver.status.state if driver else "unknown"
        if st in PRINT_STATES:
            return "Missed: the printer was busy for the whole window"
        if st in OFFLINE:
            return "Missed: the printer was offline"
        return "Missed: it couldn't start inside its window"

    def _link_jobs(self, s: Session, printer_id: str) -> None:
        """Point started items at the history row of the print they started."""
        rows = s.exec(select(QueueItem).where(QueueItem.printer_id == printer_id, QueueItem.status == "started",
                                              QueueItem.job_id == None)).all()  # noqa: E711
        for item in rows:
            f = s.get(StoredFile, item.file_id)
            if not f:
                continue
            # The printer's own start time can be a little before ours (it counts heating).
            since = utc(item.started_at) - timedelta(minutes=15)
            taken = select(QueueItem.job_id).where(QueueItem.job_id != None)  # noqa: E711
            job = s.exec(select(PrintJob).where(PrintJob.printer_id == printer_id,
                                                PrintJob.filename == upload_path(f.filename),
                                                PrintJob.started_at >= since, PrintJob.id.not_in(taken))
                         .order_by(PrintJob.id.desc())).first()
            if job:
                item.job_id = job.id
                s.add(item)
        s.commit()

    # --- plate -----------------------------------------------------------------------------

    def plate_ok(self, s: Session, printer_id: str) -> bool:
        confirmed = utc(self._state(s, printer_id).plate_confirmed_at)
        if confirmed is None:
            return False
        ended = self._print_ended.get(printer_id)
        if ended and ended > confirmed:
            return False
        later = s.exec(select(PrintJob.id).where(PrintJob.printer_id == printer_id, or_(
            PrintJob.started_at > confirmed, PrintJob.ended_at > confirmed))).first()
        return later is None

    def confirm_plate(self, printer_id: str) -> None:
        driver = self._driver(printer_id)
        if driver.status.state in PRINT_STATES:
            raise QueueError(409, "a print is still on the bed; confirm after it ends")
        with Session(self.engine) as s:
            st = self._state(s, printer_id)
            st.plate_confirmed_at = now()
            s.add(st)
            s.commit()
        self._wake.set()

    # --- changes from the API --------------------------------------------------------------------

    def add(self, body: ItemBody) -> dict:
        self._driver(body.printer_id)
        with Session(self.engine) as s:
            if not s.get(StoredFile, body.file_id):
                raise QueueError(404, "no such file")
            if body.mode == "at" and utc(body.start_at) < now() - timedelta(minutes=1):
                raise QueueError(422, "that start time has already passed")
            items = self._active(s, body.printer_id)
            item = QueueItem(printer_id=body.printer_id, file_id=body.file_id, mode=body.mode,
                             start_at=utc(body.start_at) if body.mode == "at" else None,
                             window_minutes=body.window_minutes, gap_minutes=body.gap_minutes,
                             position=(items[-1].position + 1) if items else 0, status="scheduled",
                             created_at=now())
            s.add(item)
            s.commit()
            self._relink(s, body.printer_id)
            s.refresh(item)
            item_id = item.id
        if body.plate_clear:
            driver = self._driver(body.printer_id)
            if driver.status.state not in PRINT_STATES:
                self.confirm_plate(body.printer_id)
        self._wake.set()
        return self.item(item_id)

    def edit(self, item_id: int, body: EditBody) -> dict:
        with Session(self.engine) as s:
            item = self._editable(s, item_id)
            mode = body.mode or item.mode
            start_at = utc(body.start_at) if body.start_at else utc(item.start_at)
            if mode == "at":
                if start_at is None:
                    raise QueueError(422, "start_at is needed for an 'at' item")
                if body.start_at and start_at < now() - timedelta(minutes=1):
                    raise QueueError(422, "that start time has already passed")
            item.mode, item.start_at = mode, start_at if mode == "at" else None
            if body.any_time:
                item.window_minutes = None
            elif body.window_minutes is not None:
                item.window_minutes = body.window_minutes
            if body.gap_minutes is not None:
                item.gap_minutes = body.gap_minutes
            item.note = None
            s.add(item)
            s.commit()
        self._wake.set()
        return self.item(item_id)

    def cancel(self, item_id: int) -> dict:
        with Session(self.engine) as s:
            item = self._editable(s, item_id)
            item.status, item.finished_at, item.note = "cancelled", now(), None
            s.add(item)
            s.commit()
            self._relink(s, item.printer_id)
        self._wake.set()
        return self.item(item_id)

    def move(self, item_id: int, to: int) -> list[dict]:
        with Session(self.engine) as s:
            item = self._editable(s, item_id)
            items = [i for i in self._active(s, item.printer_id) if i.id != item.id]
            to = max(0, min(to, len(items)))
            items.insert(to, item)
            for n, i in enumerate(items):
                i.position = n
                s.add(i)
            s.commit()
            self._relink(s, item.printer_id)
            printer_id = item.printer_id
        self._wake.set()
        return self.listing(printer_id)["items"]

    def set_paused(self, printer_id: str, paused: bool) -> None:
        self._driver(printer_id)
        with Session(self.engine) as s:
            st = self._state(s, printer_id)
            st.paused, st.note = paused, (st.note if paused else None)
            s.add(st)
            s.commit()
        self._wake.set()

    # --- reading -------------------------------------------------------------------------------

    def item(self, item_id: int) -> dict:
        with Session(self.engine) as s:
            item = s.get(QueueItem, item_id)
            if not item:
                raise QueueError(404, "no such queue item")
            return self._public(s, item)

    def listing(self, printer_id: str) -> dict:
        driver = self._driver(printer_id)
        with Session(self.engine) as s:
            st = self._state(s, printer_id)
            items = [self._public(s, i) for i in self._active(s, printer_id)]
            done = s.exec(select(QueueItem).where(QueueItem.printer_id == printer_id, QueueItem.status.in_(DONE))
                          .order_by(QueueItem.finished_at.desc()).limit(DONE_SHOWN)).all()
            return {
                "printer_id": printer_id,
                "name": driver.config.name,
                "state": driver.status.state,
                "paused": st.paused,
                "note": st.note,
                "plate_ok": self.plate_ok(s, printer_id),
                "plate_confirmed_at": utc(st.plate_confirmed_at),
                "items": items,
                "done": [self._public(s, i) for i in done],
            }

    def everything(self) -> list[dict]:
        return [self.listing(pid) for pid in self.drivers]

    def _public(self, s: Session, item: QueueItem) -> dict:
        f = s.get(StoredFile, item.file_id)
        due, until = self._due(s, item) if item.status == "scheduled" else (None, None)
        return {
            "id": item.id, "printer_id": item.printer_id, "file_id": item.file_id,
            "filename": f.filename if f else None,
            "meta": f.meta if f else {},
            "mode": item.mode, "start_at": utc(item.start_at), "window_minutes": item.window_minutes,
            "gap_minutes": item.gap_minutes, "after_id": item.after_id, "position": item.position,
            "status": item.status, "note": item.note, "command": item.command, "job_id": item.job_id,
            "due_at": due, "latest_at": until,
            "created_at": utc(item.created_at), "started_at": utc(item.started_at),
            "finished_at": utc(item.finished_at),
        }

    # --- helpers ---------------------------------------------------------------------------------

    def _driver(self, printer_id: str):
        driver = self.drivers.get(printer_id)
        if not driver:
            raise QueueError(404, "no such printer")
        return driver

    @staticmethod
    def _active(s: Session, printer_id: str) -> list[QueueItem]:
        return list(s.exec(select(QueueItem).where(QueueItem.printer_id == printer_id,
                                                   QueueItem.status.in_(ACTIVE))
                           .order_by(QueueItem.position, QueueItem.id)).all())

    @staticmethod
    def _editable(s: Session, item_id: int) -> QueueItem:
        item = s.get(QueueItem, item_id)
        if not item:
            raise QueueError(404, "no such queue item")
        if item.status != "scheduled":
            raise QueueError(409, f"this item is {item.status.replace('_', ' ')} and can't be changed")
        return item

    def _relink(self, s: Session, printer_id: str) -> None:
        """Each 'after' item follows the item directly above it in the active list.

        The head of the list stays linked to the item that ran before it, if it
        ran and it's still the latest, so 'after previous' waits for that print.
        """
        items = self._active(s, printer_id)
        for n, item in enumerate(items):
            if item.mode != "after":
                continue
            if n > 0:
                item.after_id = items[n - 1].id
            else:
                last = s.exec(select(QueueItem).where(QueueItem.printer_id == printer_id,
                                                      QueueItem.status == "started")
                              .order_by(QueueItem.started_at.desc())).first()
                keep = item.after_id is not None and last is not None and item.after_id == last.id
                item.after_id = last.id if keep else None
            s.add(item)
        s.commit()

    @staticmethod
    def _state(s: Session, printer_id: str) -> QueueState:
        st = s.get(QueueState, printer_id)
        if st is None:
            st = QueueState(printer_id=printer_id, paused=False)
            s.add(st)
            s.commit()
            s.refresh(st)
        return st

    @staticmethod
    def _pause(s: Session, printer_id: str, note: str) -> None:
        st = s.get(QueueState, printer_id) or QueueState(printer_id=printer_id)
        st.paused, st.note = True, note
        s.add(st)
