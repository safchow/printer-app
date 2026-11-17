"""File capture: keep a copy of every print file that reaches the printer, plus print history.

Rules (saf, 2026-11-17):
- the printer announces new files on the websocket we already hold
  (notify_filelist_changed). A file is downloaded ONCE, after it has been quiet
  for QUIET_SECONDS (the first notice comes while it is still arriving); never
  retried; thumbnails under .thumbs/ are ignored;
- files are stored once by content (sha256) under LIBRARY_ROOT/files/;
  every copy is kept until it is deleted in the app; a file deleted on the
  printer stays here;
- new files have no label (the Inbox) until saf gives them one;
- each print the app watches becomes a print_job row; the app never starts one.
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
from .gcode import MAX_BYTES, TIMEOUT, gcode_url

log = logging.getLogger(__name__)

QUIET_SECONDS = 10.0
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
        self.max_bytes = max_bytes
        self.downloads = 0
        self._pending: dict[tuple[str, str], asyncio.Task] = {}
        self._jobs: dict[str, int] = {}  # printer -> open print_job id
        self._seen: dict[str, tuple[bool, str | None]] = {}
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
            if row:
                row.filename, row.last_seen = name, now()
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
                             printer_id=printer_id, meta=meta, first_seen=now(), last_seen=now())
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
            return
        if action in ("create_file", "modify_file", "move_file"):
            # Restart the quiet timer: the file is still arriving while notices keep coming.
            self._restart(printer_id, path)

    def _restart(self, printer_id: str, path: str) -> None:
        self._cancel(printer_id, path)
        self._pending[(printer_id, path)] = asyncio.create_task(
            self._after_quiet(printer_id, path), name=f"capture:{printer_id}:{path}")

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
            if head == folder and name.rsplit(".", 1)[0] == stem:
                self._restart(printer_id, path)

    def _cancel(self, printer_id: str, path: str) -> None:
        task = self._pending.pop((printer_id, path), None)
        if task and not task.done():
            task.cancel()

    async def _after_quiet(self, printer_id: str, path: str) -> None:
        await asyncio.sleep(self.quiet)
        try:
            await self.download(printer_id, path, source="printer")
        finally:
            if self._pending.get((printer_id, path)) is asyncio.current_task():
                self._pending.pop((printer_id, path), None)

    async def settled(self, printer_id: str, filename: str, timeout: float = 300.0) -> StoredFile | None:
        """The stored copy of `filename`, after any capture of it still in progress finishes."""
        task = self._pending.get((printer_id, filename))
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                pass
        return self.latest_named(filename)

    async def download(self, printer_id: str, path: str, source: str) -> StoredFile | None:
        """One GET of a file from the printer's G-code folder, never retried."""
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
                if job and job.filename == status.filename:
                    self._jobs[status.id] = job.id
                    return
                if job:  # a different file is printing: the old job's end was missed
                    job.ended_at, job.result = now(), "unknown"
                    s.add(job)
                f = self.latest_named(status.filename)
                job = PrintJob(printer_id=status.id, filename=status.filename, file_id=f.id if f else None,
                               started_at=status.started_at or now(), result="printing")
                s.add(job)
                s.commit()
                s.refresh(job)
                self._jobs[status.id] = job.id
                log.info("%s: print started: %s", status.id, status.filename)
            elif job:
                if status.state in ENDED:
                    job.result = status.state
                else:  # idle without seeing the end state
                    job.result = "unknown"
                job.ended_at = now()
                job.print_duration = status.print_duration
                s.add(job)
                s.commit()
                self._jobs.pop(status.id, None)
                log.info("%s: print ended (%s): %s", status.id, job.result, job.filename)

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
            "printer_id": f.printer_id, "label": label.name if label else None, "meta": f.meta,
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
                "print_duration": j.print_duration}

    def history(self, limit: int = 200) -> list[dict]:
        with Session(self.engine) as s:
            jobs = s.exec(select(PrintJob).order_by(PrintJob.started_at.desc()).limit(limit)).all()
            return [self.job_dict(j) for j in jobs]

    async def stop(self) -> None:
        tasks = list(self._pending.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
