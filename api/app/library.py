"""File capture: keep a copy of every print file that reaches the printer, plus print history.

Rules:
- the printer announces new files on the websocket we already hold
  (notify_filelist_changed). A file is downloaded ONCE, when it has finished
  arriving: QUIET_SECONDS after the modify_file notice that carries its full
  size (the K1C sends create_file at ~100 KB, can then go silent for 10+ s
  while still writing, and sends modify_file when done). A download whose size
  doesn't match the notice is thrown away, never stored. If no modify_file
  comes, FALLBACK_SECONDS after the last notice the file is fetched and kept
  only if it matches the size create_file gave (plain Moonraker uploads
  announce finished files that way). Thumbnails under .thumbs/ are ignored;
- files are stored once by content (sha256) under LIBRARY_ROOT/files/;
  every copy is kept until it is deleted in the app; a file deleted on the
  printer stays here;
- new files have no label (the Inbox) until the user gives them one;
- each print the app watches becomes a print_job row. The app starts a print
  only when asked in the UI (POST /api/printers/{id}/print, app/controls.py),
  and only of a file already on that printer: nothing is uploaded.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session, select

from . import gcode_meta
from .config import PrinterConfig
from .db import Label, PrintJob, StoredFile
from .drivers import PrinterStatus
from .drivers.base import ControlError
from .gcode import MAX_BYTES, TIMEOUT, gcode_url

log = logging.getLogger(__name__)

QUIET_SECONDS = 3.0
FALLBACK_SECONDS = 120.0
# A print is only taken as over without an end state (complete/cancelled/error) after the
# printer has looked idle this long: right after a reconnect it briefly reports no print.
IDLE_END_SECONDS = 60.0
# A print seen again with the same file and a start time this close is the same print.
SAME_PRINT_SECONDS = 300.0
SUFFIXES = (".gcode", ".3mf")
ACTIVE = ("printing", "paused")
ENDED = ("complete", "cancelled", "error")


def wanted(item: dict[str, Any]) -> str | None:
    """The G-code-folder path of a print file worth capturing, else None."""
    if item.get("root", "gcodes") != "gcodes":
        return None
    path = str(item.get("path") or "")
    parts = path.split("/")
    if not path or any(p.startswith(".") or p in ("", "..") for p in parts):
        return None  # .thumbs/ and other hidden folders
    return path if path.lower().endswith(SUFFIXES) else None


def now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Library:
    def __init__(self, engine, root: Path, configs: dict[str, PrinterConfig],
                 quiet_seconds: float | None = None, max_bytes: int = MAX_BYTES):
        self.engine = engine
        self.root = root
        self.configs = configs
        self.quiet = QUIET_SECONDS if quiet_seconds is None else quiet_seconds
        self._expect: dict[tuple[str, str], tuple[int | None, bool]] = {}  # (size, finished) per arriving file
        self.max_bytes = max_bytes
        self.downloads = 0
        self._pending: dict[tuple[str, str], asyncio.Task] = {}
        self._jobs: dict[str, int] = {}  # printer -> open print_job id
        self._seen: dict[str, tuple[bool, str | None]] = {}
        self._idle: dict[str, asyncio.Task] = {}  # printer -> pending "it ended unseen" close
        (root / "files").mkdir(parents=True, exist_ok=True)
        (root / "thumbs").mkdir(parents=True, exist_ok=True)

    # --- storing ---------------------------------------------------------------------

    def file_path(self, f: StoredFile) -> Path:
        return self.root / f.path

    def thumb_path(self, f: StoredFile) -> Path:
        return self.root / "thumbs" / f"{f.sha256}.png"

    def ingest(self, src: Path, filename: str, source: str, printer_id: str | None,
               move: bool = False) -> StoredFile:
        """Store a file once by content; a file seen before only gets its name and time updated."""
        h = hashlib.sha256()
        with src.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        sha = h.hexdigest()
        name = filename.rsplit("/", 1)[-1]
        with Session(self.engine) as s:
            row = s.exec(select(StoredFile).where(StoredFile.sha256 == sha)).first()
            printer_path = filename.lstrip("/") if printer_id else None
            if row:
                row.filename, row.last_seen = name, now()
                if printer_path:
                    row.printer_id, row.printer_path = printer_id, printer_path
                s.add(row)
                s.commit()
                s.refresh(row)
                if move:
                    src.unlink(missing_ok=True)
                return row
            suffix = Path(name).suffix.lower() or ".gcode"
            rel = f"files/{sha}{suffix}"
            dest = self.root / rel
            if move:
                shutil.move(str(src), dest)
            else:
                shutil.copyfile(src, dest)
            try:
                meta, thumb = gcode_meta.parse(dest)
            except Exception:  # a file we can't read is still kept
                log.exception("couldn't read the header of %s", name)
                meta, thumb = {}, None
            if thumb:
                (self.root / "thumbs" / f"{sha}.png").write_bytes(thumb)
                meta["thumbnail"] = True
            row = StoredFile(sha256=sha, filename=name, size=dest.stat().st_size, path=rel, source=source,
                             printer_id=printer_id, printer_path=printer_path, meta=meta,
                             first_seen=now(), last_seen=now())
            s.add(row)
            s.commit()
            s.refresh(row)
            log.info("stored %s (%s, %d bytes) from %s", name, sha[:12], row.size, source)
            return row

    def latest_named(self, filename: str) -> StoredFile | None:
        name = filename.rsplit("/", 1)[-1]
        with Session(self.engine) as s:
            return s.exec(select(StoredFile).where(StoredFile.filename == name)
                          .order_by(StoredFile.last_seen.desc())).first()

    # --- capture from the printer's notices -------------------------------------------

    async def on_notice(self, printer_id: str, method: str, params: Any) -> None:
        if method != "notify_filelist_changed" or printer_id not in self.configs:
            return
        info = params[0] if isinstance(params, list) and params else {}
        if not isinstance(info, dict):
            return
        action = info.get("action")
        path = wanted(info.get("item") or {})
        if action == "move_file":
            src = wanted(info.get("source_item") or {})
            if src:
                self._cancel(printer_id, src)
        if not path:
            if action == "create_file":
                self._thumb_activity(printer_id, info.get("item") or {})
            return
        if action == "delete_file":
            self._cancel(printer_id, path)  # our copy, if any, stays
            self._expect.pop((printer_id, path), None)
            return
        if action in ("create_file", "modify_file", "move_file"):
            size = (info.get("item") or {}).get("size")
            size = int(size) if isinstance(size, (int, float)) and size > 0 else None
            # modify_file means the write finished; a move is a finished file put in place.
            finished = action in ("modify_file", "move_file")
            self._expect[(printer_id, path)] = (size, finished)
            self._restart(printer_id, path)

    def _restart(self, printer_id: str, path: str) -> None:
        self._cancel(printer_id, path)
        _, finished = self._expect.get((printer_id, path), (None, False))
        delay = self.quiet if finished else FALLBACK_SECONDS
        self._pending[(printer_id, path)] = asyncio.create_task(
            self._after_quiet(printer_id, path, delay), name=f"capture:{printer_id}:{path}")

    def _thumb_activity(self, printer_id: str, item: dict[str, Any]) -> None:
        """A slicer's thumbnail (<dir>/.thumbs/<stem>-WxH.png) arriving means its file is still arriving."""
        if item.get("root", "gcodes") != "gcodes":
            return
        m = re.fullmatch(r"(?:(.*)/)?\.thumbs/(.+)-\d+x\d+\.png", str(item.get("path") or ""))
        if not m:
            return
        folder, stem = m.group(1) or "", m.group(2)
        for pid, path in list(self._pending):
            if pid != printer_id:
                continue
            head, _, name = path.rpartition("/")
            if head == folder and name.rsplit(".", 1)[0] == stem and not self._expect.get((pid, path), (None, False))[1]:
                self._restart(printer_id, path)

    def _cancel(self, printer_id: str, path: str) -> None:
        task = self._pending.pop((printer_id, path), None)
        if task and not task.done():
            task.cancel()

    async def _after_quiet(self, printer_id: str, path: str, delay: float) -> None:
        await asyncio.sleep(delay)
        size, _ = self._expect.get((printer_id, path), (None, False))
        try:
            await self.download(printer_id, path, source="printer", expected_size=size)
        finally:
            if self._pending.get((printer_id, path)) is asyncio.current_task():
                self._pending.pop((printer_id, path), None)
                self._expect.pop((printer_id, path), None)

    async def settled(self, printer_id: str, filename: str, timeout: float = 300.0) -> StoredFile | None:
        """The stored copy of `filename`, after any capture of it still in progress finishes."""
        task = self._pending.get((printer_id, filename))
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        return self.latest_named(filename)

    async def download(self, printer_id: str, path: str, source: str,
                       expected_size: int | None = None) -> StoredFile | None:
        """One GET of a file from the printer's G-code folder, never retried. A copy whose
        size isn't the size the printer announced is thrown away (it was still arriving)."""
        config = self.configs[printer_id]
        self.downloads += 1
        log.info("%s: capturing %s (one download)", printer_id, path)
        tmp = Path(tempfile.mkstemp(dir=self.root, prefix=".download-")[1])
        try:
            size = 0
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async with client.stream("GET", gcode_url(config, path)) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"the printer answered {r.status_code}")
                    with tmp.open("wb") as f:
                        async for chunk in r.aiter_bytes(256 * 1024):
                            size += len(chunk)
                            if size > self.max_bytes:
                                raise RuntimeError(f"the file is over {self.max_bytes // (1024 * 1024)} MB")
                            f.write(chunk)
            if expected_size is not None and size != expected_size:
                raise RuntimeError(f"got {size} of {expected_size} bytes (still arriving?); not kept")
            row = await asyncio.to_thread(self.ingest, tmp, path, source, printer_id, True)
            self._link_open_job(printer_id, row)
            return row
        except Exception as exc:
            log.warning("%s: couldn't capture %s: %s", printer_id, path, exc)
            return None
        finally:
            tmp.unlink(missing_ok=True)

    # --- history ----------------------------------------------------------------------

    def _open_job(self, s: Session, printer_id: str) -> PrintJob | None:
        jid = self._jobs.get(printer_id)
        job = s.get(PrintJob, jid) if jid else None
        if job is None:  # after a restart, pick up a job left open
            job = s.exec(select(PrintJob).where(PrintJob.printer_id == printer_id, PrintJob.ended_at == None)  # noqa: E711
                         .order_by(PrintJob.id.desc())).first()
        return job if job and job.ended_at is None else None

    def _same_print(self, s: Session, status: PrinterStatus) -> PrintJob | None:
        """The newest job for this printer and file that started within SAME_PRINT_SECONDS of this
        print and wasn't seen to finish: the same print, seen again (e.g. after a restart)."""
        job = s.exec(select(PrintJob).where(PrintJob.printer_id == status.id)
                     .order_by(PrintJob.id.desc())).first()
        if not job or job.filename != status.filename or job.result not in ("printing", "unknown"):
            return None
        started = _utc(status.started_at) or now()
        return job if abs((_utc(job.started_at) - started).total_seconds()) < SAME_PRINT_SECONDS else None

    def _cancel_idle(self, printer_id: str) -> None:
        task = self._idle.pop(printer_id, None)
        if task and not task.done():
            task.cancel()

    async def on_status(self, status: PrinterStatus) -> None:
        if status.state in ("offline", "connecting"):
            return  # a dropped connection doesn't end a print
        # Fast path for the many updates during a print: nothing to write.
        key = (status.state in ACTIVE, status.filename)
        if self._seen.get(status.id) == key:
            return
        self._seen[status.id] = key
        with Session(self.engine) as s:
            job = self._open_job(s, status.id)
            if status.state in ACTIVE and status.filename:
                self._cancel_idle(status.id)
                if job and job.filename == status.filename:
                    self._jobs[status.id] = job.id
                    return
                if job:  # a different file is printing: the old job's end was missed
                    job.ended_at, job.result = now(), "unknown"
                    s.add(job)
                    s.commit()
                again = self._same_print(s, status)
                if again:  # closed too early (an idle blip, a restart): carry on with it
                    again.ended_at, again.result, again.print_duration = None, "printing", None
                    s.add(again)
                    s.commit()
                    self._jobs[status.id] = again.id
                    log.info("%s: print continues: %s", status.id, status.filename)
                    return
                f = self.latest_named(status.filename)
                job = PrintJob(printer_id=status.id, filename=status.filename, file_id=f.id if f else None,
                               started_at=status.started_at or now(), result="printing")
                s.add(job)
                s.commit()
                s.refresh(job)
                self._jobs[status.id] = job.id
                log.info("%s: print started: %s", status.id, status.filename)
            elif job and status.state in ENDED:
                self._cancel_idle(status.id)
                self._close(s, job, status.state, status.print_duration)
            elif job and status.id not in self._idle:
                # Idle without an end state: only believe it if it lasts.
                self._idle[status.id] = asyncio.create_task(
                    self._close_if_still_idle(status.id, now()), name=f"idle:{status.id}")

    def _close(self, s: Session, job: PrintJob, result: str, print_duration: float | None,
               ended_at: datetime | None = None) -> None:
        job.result = result
        job.ended_at = ended_at or now()
        if print_duration:
            job.print_duration = print_duration
        s.add(job)
        s.commit()
        self._jobs.pop(job.printer_id, None)
        log.info("%s: print ended (%s): %s", job.printer_id, result, job.filename)

    async def _close_if_still_idle(self, printer_id: str, since: datetime) -> None:
        await asyncio.sleep(IDLE_END_SECONDS)
        if self._idle.get(printer_id) is asyncio.current_task():
            self._idle.pop(printer_id, None)
        with Session(self.engine) as s:
            job = self._open_job(s, printer_id)
            if job:
                self._close(s, job, "unknown", None, ended_at=since)

    def _link_open_job(self, printer_id: str, f: StoredFile) -> None:
        with Session(self.engine) as s:
            job = self._open_job(s, printer_id)
            # The newest capture of the printing file wins (a re-sent file may share the name).
            if job and job.filename.rsplit("/", 1)[-1] == f.filename and job.file_id != f.id:
                job.file_id = f.id
                s.add(job)
                s.commit()

    def link_print(self, printer_id: str, f: StoredFile) -> None:
        self._link_open_job(printer_id, f)

    # --- the app's side: list, label, delete ---------------------------------------------

    def summary(self, s: Session, f: StoredFile) -> dict:
        jobs = s.exec(select(PrintJob).where(PrintJob.file_id == f.id).order_by(PrintJob.started_at.desc())).all()
        label = s.get(Label, f.label_id) if f.label_id else None
        return {
            "id": f.id, "sha256": f.sha256, "filename": f.filename, "size": f.size, "source": f.source,
            "printer_id": f.printer_id, "printer_path": self._printer_path(s, f) if f.printer_id else None,
            "label": label.name if label else None, "meta": f.meta,
            "thumbnail": bool(f.meta.get("thumbnail")),
            "first_seen": _utc(f.first_seen), "last_seen": _utc(f.last_seen),
            "prints": len(jobs), "last_printed": _utc(jobs[0].started_at) if jobs else None,
        }

    def list(self, label: str | None = None, inbox: bool = False) -> list[dict]:
        with Session(self.engine) as s:
            q = select(StoredFile)
            if inbox:
                q = q.where(StoredFile.label_id == None)  # noqa: E711
            elif label:
                lab = s.exec(select(Label).where(Label.name == label)).first()
                if not lab:
                    return []
                q = q.where(StoredFile.label_id == lab.id)
            rows = s.exec(q.order_by(StoredFile.last_seen.desc())).all()
            return [self.summary(s, f) for f in rows]

    def get(self, file_id: int) -> StoredFile | None:
        with Session(self.engine) as s:
            return s.get(StoredFile, file_id)

    def detail(self, file_id: int) -> dict | None:
        with Session(self.engine) as s:
            f = s.get(StoredFile, file_id)
            if not f:
                return None
            out = self.summary(s, f)
            jobs = s.exec(select(PrintJob).where(PrintJob.file_id == f.id).order_by(PrintJob.started_at.desc())).all()
            out["jobs"] = [self.job_dict(j) for j in jobs]
            return out

    # --- printing a stored file ------------------------------------------------------------

    @staticmethod
    def _printer_path(s: Session, f: StoredFile) -> str:
        """Where this file sits on its printer: as captured, else its latest print's name, else its name."""
        if f.printer_path:
            return f.printer_path
        job = s.exec(select(PrintJob).where(PrintJob.file_id == f.id, PrintJob.printer_id == f.printer_id)
                     .order_by(PrintJob.started_at.desc())).first()
        return job.filename if job else f.filename

    def print_path(self, file_id: int, printer_id: str) -> str:
        """The printer-side path to start a print of this stored file with, or ControlError.

        The app only starts files that came from that printer (they're already
        in its G-code folder; nothing is uploaded), and only the newest copy
        seen under that name, so a print never starts a different file that
        was later sent under the same name."""
        with Session(self.engine) as s:
            f = s.get(StoredFile, file_id)
            if not f:
                raise ControlError(404, "no such file")
            if f.printer_id != printer_id:
                raise ControlError(409, "this file wasn't sent to that printer, so it isn't on it")
            path = self._printer_path(s, f)
            name = path.rsplit("/", 1)[-1]
            same = s.exec(select(StoredFile).where(StoredFile.printer_id == printer_id, StoredFile.id != f.id,
                                                   StoredFile.last_seen > f.last_seen)).all()
            if any(self._printer_path(s, o) == path or (not o.printer_path and o.filename == name) for o in same):
                raise ControlError(409, "a newer file with the same name was sent to the printer after this one, "
                                        "so the printer's copy isn't this file any more")
            return path

    def set_label(self, file_id: int, name: str | None) -> dict | None:
        name = (name or "").strip()[:60] or None
        with Session(self.engine) as s:
            f = s.get(StoredFile, file_id)
            if not f:
                return None
            if name is None:
                f.label_id = None
            else:
                lab = s.exec(select(Label).where(Label.name == name)).first()
                if not lab:
                    lab = Label(name=name)
                    s.add(lab)
                    s.commit()
                    s.refresh(lab)
                f.label_id = lab.id
            s.add(f)
            s.commit()
            s.refresh(f)
            return self.summary(s, f)

    def delete(self, file_id: int) -> bool:
        with Session(self.engine) as s:
            f = s.get(StoredFile, file_id)
            if not f:
                return False
            for job in s.exec(select(PrintJob).where(PrintJob.file_id == f.id)).all():
                job.file_id = None  # history keeps the name
                s.add(job)
            paths = [self.file_path(f), self.thumb_path(f)]
            s.delete(f)
            s.commit()
        for p in paths:
            p.unlink(missing_ok=True)
        return True

    def labels(self) -> list[dict]:
        with Session(self.engine) as s:
            out = []
            for lab in s.exec(select(Label).order_by(Label.name)).all():
                n = len(s.exec(select(StoredFile.id).where(StoredFile.label_id == lab.id)).all())
                out.append({"name": lab.name, "files": n})
            inbox = len(s.exec(select(StoredFile.id).where(StoredFile.label_id == None)).all())  # noqa: E711
            return [{"name": None, "files": inbox}] + out

    @staticmethod
    def job_dict(j: PrintJob) -> dict:
        return {"id": j.id, "printer_id": j.printer_id, "filename": j.filename, "file_id": j.file_id,
                "started_at": _utc(j.started_at), "ended_at": _utc(j.ended_at), "result": j.result,
                "print_duration": j.print_duration, "timelapse_frames": j.timelapse_frames,
                "timelapse_bytes": j.timelapse_bytes}

    def open_job_id(self, printer_id: str) -> int | None:
        """The id of the print this printer is running, as far as history knows."""
        jid = self._jobs.get(printer_id)
        if jid is not None:
            return jid
        with Session(self.engine) as s:
            job = self._open_job(s, printer_id)
            return job.id if job else None

    def job(self, job_id: int) -> dict | None:
        with Session(self.engine) as s:
            j = s.get(PrintJob, job_id)
            return self.job_dict(j) if j else None

    def history(self, limit: int = 200) -> list[dict]:
        with Session(self.engine) as s:
            jobs = s.exec(select(PrintJob).order_by(PrintJob.started_at.desc()).limit(limit)).all()
            return [self.job_dict(j) for j in jobs]

    async def stop(self) -> None:
        tasks = list(self._pending.values()) + list(self._idle.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
