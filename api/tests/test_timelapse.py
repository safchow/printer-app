"""Time-lapses: one frame per layer, saved under TIMELAPSE_ROOT, read back by History."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import timelapse as tlmod
from app.config import PrinterConfig, Settings
from app.db import PrintJob, make_engine, migrate
from app.drivers import PrinterStatus
from app.main import create_app
from app.timelapse import MARKER, Timelapse, gaps, read_frames

JPEG = b"\xff\xd8" + b"x" * 2000


class FakeGo2rtc:
    url = "http://go2rtc.invalid:1984"

    def ws_url(self, printer_id: str) -> str:
        return f"ws://go2rtc.invalid/api/ws?name={printer_id}"


class FakeLibrary:
    def __init__(self, job_id: int | None):
        self.job_id = job_id

    def open_job_id(self, printer_id: str) -> int | None:
        return self.job_id


@pytest.fixture
def engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'printer.db'}"
    migrate(url)
    eng = make_engine(url)
    with Session(eng) as s:
        s.add(PrintJob(id=7, printer_id="k1c", filename="part.gcode", started_at=datetime.now(timezone.utc)))
        s.commit()
    yield eng
    eng.dispose()


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "tl"
    r.mkdir()
    (r / MARKER).touch()
    return r


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(tlmod, "MIN_GAP_SECONDS", 0.0)
    monkeypatch.setattr(tlmod, "RESET_PAUSE", 0.0)


def make_tl(engine, root, mp, job_id=7, frames=None):
    cfg = PrinterConfig(id="k1c", name="K1C", host="10.0.0.9", camera="creality_webrtc")
    tl = Timelapse(engine, root, FakeGo2rtc(), {"k1c": cfg}, FakeLibrary(job_id), min_free_bytes=0)
    calls = {"frames": 0, "drops": 0, "viewers": 0}
    answers = list(frames or [])

    async def fetch_frame(printer_id, timeout):
        calls["frames"] += 1
        return answers.pop(0) if answers else JPEG

    async def drop_stream(printer_id):
        calls["drops"] += 1

    async def open_viewer(self):
        if self.ws is None:
            calls["viewers"] += 1
            self.ws = object()
            self.drain = asyncio.get_running_loop().create_future()  # never done = still open
            self.fresh_viewer = True
        return True

    async def close_viewer(self):
        if self.drain is not None and not self.drain.done():
            self.drain.cancel()
        self.ws, self.drain = None, None

    tl.fetch_frame, tl.drop_stream = fetch_frame, drop_stream
    mp.setattr(tlmod.Recorder, "open_viewer", open_viewer)
    mp.setattr(tlmod.Recorder, "close_viewer", close_viewer)
    return tl, calls


def status(state="printing", layer=None, total=10, filename="part.gcode"):
    return PrinterStatus(id="k1c", name="K1C", type="moonraker", state=state, filename=filename,
                         layer=layer, total_layers=total, has_camera=True, camera="creality_webrtc")


async def settle():
    for _ in range(20):
        await asyncio.sleep(0.01)


def test_storage_needs_folder_marker_and_space(tmp_path, engine):
    tl = Timelapse(engine, tmp_path / "nope", FakeGo2rtc(), {}, None, min_free_bytes=0)
    assert "doesn't exist" in tl.storage_problem()
    (tmp_path / "nope").mkdir()
    assert MARKER in tl.storage_problem()  # an empty folder Docker made for an unmounted NAS
    (tmp_path / "nope" / MARKER).touch()
    assert tl.storage_problem() is None
    tl.min_free_bytes = 10**18
    assert "free" in tl.storage_problem()


def test_one_frame_per_layer_then_the_finished_part(engine, root, monkeypatch):
    async def go():
        tl, calls = make_tl(engine, root, monkeypatch)
        for layer in (1, 2, 2, 3, 5):
            await tl.on_status(status(layer=layer))
            await settle()
        await tl.on_status(status(state="complete", layer=None))
        await settle()
        await tl.stop()
        return calls

    calls = asyncio.run(go())
    folder = root / "k1c" / "7"
    frames = read_frames(folder)
    assert [f["layer"] for f in frames] == [1, 2, 3, 5, 10]  # repeated layer 2 isn't saved twice
    assert all((folder / f"{f['n']:06d}.jpg").read_bytes() == JPEG for f in frames)
    assert gaps(frames) == [[4, 4], [6, 9]]
    assert calls["viewers"] == 1  # one camera connection for the whole print
    with Session(engine) as s:
        job = s.get(PrintJob, 7)
        assert (job.timelapse_frames, job.timelapse_bytes) == (5, 5 * len(JPEG))


def test_no_frames_while_paused_or_without_storage(engine, root, monkeypatch):
    async def go():
        tl, calls = make_tl(engine, root, monkeypatch)
        await tl.on_status(status(state="paused", layer=1))
        await settle()
        (root / MARKER).unlink()
        await tl.on_status(status(layer=2))
        await settle()
        await tl.stop()
        return calls

    calls = asyncio.run(go())
    assert calls["frames"] == 0
    assert not (root / "k1c" / "7" / "frames.jsonl").exists()


def test_failed_frames_reset_the_stream(engine, root, monkeypatch):
    async def go():
        tl, calls = make_tl(engine, root, monkeypatch, frames=[None, None, JPEG])
        for layer in (1, 2, 3):
            await tl.on_status(status(layer=layer))
            await settle()
        await tl.stop()
        return calls

    calls = asyncio.run(go())
    assert calls["drops"] == 1 and calls["viewers"] == 2  # dropped and reopened after two misses
    assert [f["layer"] for f in read_frames(root / "k1c" / "7")] == [3]


def test_carries_on_after_a_restart(engine, root, monkeypatch):
    folder = root / "k1c" / "7"
    folder.mkdir(parents=True)
    (folder / "000001.jpg").write_bytes(JPEG)
    (folder / "frames.jsonl").write_text(json.dumps({"n": 1, "layer": 4, "total": 10, "bytes": len(JPEG)}) + "\nnot json\n")

    async def go():
        tl, _ = make_tl(engine, root, monkeypatch)
        await tl.on_status(status(layer=4))  # already have layer 4
        await settle()
        await tl.on_status(status(layer=6))
        await settle()
        await tl.stop()

    asyncio.run(go())
    assert [(f["n"], f["layer"]) for f in read_frames(folder)] == [(1, 4), (2, 6)]


def test_ignores_printers_without_a_relayed_camera(engine, root, monkeypatch):
    async def go():
        tl, calls = make_tl(engine, root, monkeypatch)
        tl.configs["k1c"] = PrinterConfig(id="k1c", name="K1C", host="10.0.0.9", camera="none")
        await tl.on_status(status(layer=1))
        await settle()
        await tl.stop()
        return calls, tl

    calls, tl = asyncio.run(go())
    assert calls["frames"] == 0 and not tl.recorders


# --- the API ---


def test_history_api_serves_frames(tmp_path, printers_file, fake_server):
    root = tmp_path / "tl"
    s = Settings(data_dir=tmp_path / "d", printers_file=printers_file, library_root=tmp_path / "lib",
                 timelapse_root=root, reconnect_seconds=0.3, static_dir=tmp_path / "none")
    app = create_app(s)
    with TestClient(app) as c:
        with Session(app.state.library.engine) as sess:
            sess.add(PrintJob(id=41, printer_id="k1c", filename="a.gcode", started_at=datetime.now(timezone.utc),
                              result="complete", timelapse_frames=2, timelapse_bytes=2 * len(JPEG)))
            sess.add(PrintJob(id=42, printer_id="k1c", filename="b.gcode", started_at=datetime.now(timezone.utc),
                              result="complete"))
            sess.commit()

        # Recorded, but the folder isn't there (NAS offline): History says so instead of failing.
        tl = c.get("/api/history/41/timelapse").json()
        assert tl["available"] is False and tl["frames"] == []

        folder = root / "k1c" / "41"
        folder.mkdir(parents=True)
        for n, layer in ((1, 1), (2, 3)):
            (folder / f"{n:06d}.jpg").write_bytes(JPEG)
            with (folder / "frames.jsonl").open("a") as fh:
                fh.write(json.dumps({"n": n, "layer": layer, "total": 3, "bytes": len(JPEG), "t": "2026-11-19T05:00:00+00:00"}) + "\n")

        entry = c.get("/api/history/41").json()
        assert entry["timelapse_frames"] == 2 and entry["filename"] == "a.gcode"
        tl = c.get("/api/history/41/timelapse").json()
        assert tl["available"] is True and [f["layer"] for f in tl["frames"]] == [1, 3]
        assert tl["gaps"] == [[2, 2]] and tl["total_layers"] == 3
        r = c.get("/api/history/41/timelapse/2.jpg")
        assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
        assert c.get("/api/history/41/timelapse/3.jpg").status_code == 404
        assert c.get("/api/history/41/timelapse/0.jpg").status_code == 404

        # A print with no time-lapse: empty, not an error.
        tl = c.get("/api/history/42/timelapse").json()
        assert tl["frames"] == [] and tl["available"] is True
        assert c.get("/api/history/999").status_code == 404
        assert any(j["id"] == 41 and j["timelapse_frames"] == 2 for j in c.get("/api/history").json())
