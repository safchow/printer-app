"""The print queue, against the fake Moonraker. Never a real printer."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session

import app.controls as controls
import app.library as library_mod
import app.queue as queue_mod
from app.db import QueueItem

from .conftest import wait_for_sync
from .test_library import sliced
from .test_print import captured, idle, make_client, status

PATH_A = "printer-app_part_A.gcode"


@pytest.fixture
def client(tmp_path, printers_file, monkeypatch):
    monkeypatch.setattr(controls, "MIN_GAP", 0.0)
    monkeypatch.setattr(library_mod, "QUIET_SECONDS", 0.2)
    monkeypatch.setattr(library_mod, "FALLBACK_SECONDS", 1.0)
    monkeypatch.setattr(queue_mod, "TICK_SECONDS", 0.1)
    with make_client(tmp_path, printers_file) as c:
        wait_for_sync(lambda: status(c)["state"] == "printing")
        yield c


def stored(c, fake_server, name="part A"):
    return captured(c, fake_server, f"queue/{name}.gcode", sliced(name[-1]))


def add(c, file_id, **kw):
    body = {"printer_id": "k1c", "file_id": file_id, "mode": "idle", **kw}
    r = c.post("/api/queue", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def queue(c):
    return c.get("/api/queue").json()[0]


def item(c, item_id):
    q = queue(c)
    return next(i for i in q["items"] + q["done"] if i["id"] == item_id)


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


def test_when_idle_starts_once_printer_is_free_and_plate_confirmed(client, fake_server):
    f = stored(client, fake_server)
    it = add(client, f["id"], plate_clear=True)  # printing now: the tick can't count
    wait_for_sync(lambda: item(client, it["id"])["note"] == "Waiting for the printer to finish")
    assert not queue(client)["plate_ok"]
    assert fake_server.fake.started == []

    idle(client, fake_server)
    wait_for_sync(lambda: item(client, it["id"])["note"] == "Clear the plate")
    assert fake_server.fake.started == []
    r = client.post("/api/queue/printers/k1c/plate")
    assert r.status_code == 200 and r.json()["plate_ok"]

    wait_for_sync(lambda: item(client, it["id"])["status"] == "started")
    assert fake_server.fake.started == [PATH_A] and fake_server.fake.uploads == [PATH_A]
    wait_for_sync(lambda: status(client)["state"] == "printing" and status(client)["filename"] == PATH_A)
    wait_for_sync(lambda: item(client, it["id"])["job_id"] is not None)
    actions = client.get("/api/printers/k1c/actions").json()
    assert actions[0]["action"] == "print" and actions[0]["device"] == "queue"


def test_plate_confirmation_expires_when_a_print_ends(client, fake_server):
    f = stored(client, fake_server)
    idle(client, fake_server)
    client.post("/api/queue/printers/k1c/pause", json={"paused": True})
    assert client.post("/api/queue/printers/k1c/plate").json()["plate_ok"]

    fake_server.scenario("printing")  # a print from the touchscreen
    wait_for_sync(lambda: status(client)["state"] == "printing")
    r = client.post("/api/queue/printers/k1c/plate")
    assert r.status_code == 409  # can't confirm with a print on the bed
    fake_server.scenario("complete")
    wait_for_sync(lambda: status(client)["state"] == "complete")
    assert not queue(client)["plate_ok"]

    it = add(client, f["id"])
    client.post("/api/queue/printers/k1c/pause", json={"paused": False})
    wait_for_sync(lambda: item(client, it["id"])["note"] == "Clear the plate")
    assert fake_server.fake.started == []


def test_busy_at_start_time_waits_then_misses(client, fake_server, monkeypatch):
    f = stored(client, fake_server)
    t0 = datetime.now(timezone.utc)
    it = add(client, f["id"], mode="at", start_at=iso(t0 + timedelta(minutes=1)), window_minutes=30, plate_clear=True)
    assert it["latest_at"] is not None
    wait_for_sync(lambda: item(client, it["id"])["note"] == "Scheduled")

    monkeypatch.setattr(queue_mod, "now", lambda: t0 + timedelta(minutes=10))
    wait_for_sync(lambda: item(client, it["id"])["note"] == "Waiting for the printer to finish")
    monkeypatch.setattr(queue_mod, "now", lambda: t0 + timedelta(minutes=40))
    wait_for_sync(lambda: item(client, it["id"])["status"] == "missed")
    assert "busy" in item(client, it["id"])["note"]

    idle(client, fake_server)  # free now, but too late: it must not start
    client.post("/api/queue/printers/k1c/plate")
    assert item(client, it["id"])["status"] == "missed"
    assert fake_server.fake.started == []


def test_start_time_in_the_past_is_refused(client, fake_server):
    f = stored(client, fake_server)
    r = client.post("/api/queue", json={"printer_id": "k1c", "file_id": f["id"], "mode": "at",
                                        "start_at": iso(datetime.now(timezone.utc) - timedelta(hours=1))})
    assert r.status_code == 422


def test_failed_start_is_not_retried_and_pauses_the_queue(client, fake_server):
    f = stored(client, fake_server)
    g = stored(client, fake_server, "part B")
    a = add(client, f["id"], plate_clear=True)
    b = add(client, g["id"])
    lib = client.app.state.library
    lib.file_path(lib.get(f["id"])).unlink()  # our copy vanished: the start can only fail
    idle(client, fake_server)
    client.post("/api/queue/printers/k1c/plate")
    wait_for_sync(lambda: item(client, a["id"])["status"] == "failed")
    q = queue(client)
    assert q["paused"] and "failed" in q["note"]
    assert item(client, b["id"])["status"] == "scheduled"
    assert fake_server.fake.uploads == [] and fake_server.fake.started == []


def test_after_previous_waits_for_a_successful_finish(client, fake_server):
    f = stored(client, fake_server)
    g = stored(client, fake_server, "part B")
    a = add(client, f["id"], plate_clear=True)
    b = add(client, g["id"], mode="after")
    assert b["after_id"] == a["id"]
    idle(client, fake_server)
    client.post("/api/queue/printers/k1c/plate")
    wait_for_sync(lambda: item(client, a["id"])["status"] == "started")
    wait_for_sync(lambda: item(client, a["id"])["job_id"] is not None)
    assert item(client, b["id"])["note"] == "Waiting for the print above to finish"

    fake_server.scenario("cancelled")
    wait_for_sync(lambda: item(client, b["id"])["note"] == "Held: the print above ended as cancelled")
    client.post("/api/queue/printers/k1c/plate")
    assert fake_server.fake.started == [PATH_A]


def test_after_previous_runs_when_the_print_above_completes(client, fake_server):
    f = stored(client, fake_server)
    g = stored(client, fake_server, "part B")
    a = add(client, f["id"], plate_clear=True)
    b = add(client, g["id"], mode="after", window_minutes=None)
    idle(client, fake_server)
    client.post("/api/queue/printers/k1c/plate")
    wait_for_sync(lambda: item(client, a["id"])["job_id"] is not None)
    fake_server.scenario("complete")
    wait_for_sync(lambda: item(client, b["id"])["note"] == "Clear the plate")  # the part is still on the bed
    client.post("/api/queue/printers/k1c/plate")
    wait_for_sync(lambda: item(client, b["id"])["status"] == "started")
    assert fake_server.fake.started == [PATH_A, "printer-app_part_B.gcode"]


def test_edit_move_cancel(client, fake_server):
    f = stored(client, fake_server)
    a = add(client, f["id"])
    b = add(client, f["id"], mode="after")
    c = add(client, f["id"])
    assert [i["id"] for i in queue(client)["items"]] == [a["id"], b["id"], c["id"]]

    r = client.post(f"/api/queue/{c['id']}/move", json={"to": 0})
    assert [i["id"] for i in r.json()] == [c["id"], a["id"], b["id"]]
    assert item(client, b["id"])["after_id"] == a["id"]
    client.post(f"/api/queue/{b['id']}/move", json={"to": 1})
    assert item(client, b["id"])["after_id"] == c["id"]

    start = datetime.now(timezone.utc) + timedelta(hours=6)
    r = client.patch(f"/api/queue/{a['id']}", json={"mode": "at", "start_at": iso(start), "any_time": True})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "at" and r.json()["window_minutes"] is None

    r = client.post(f"/api/queue/{c['id']}/cancel")
    assert r.json()["status"] == "cancelled"
    assert item(client, b["id"])["after_id"] is None  # now at the top, nothing above it
    assert client.post(f"/api/queue/{c['id']}/cancel").status_code == 409
    assert [i["id"] for i in queue(client)["done"]] == [c["id"]]


def test_restart_mid_start_needs_a_person(tmp_path, printers_file, fake_server, monkeypatch):
    monkeypatch.setattr(queue_mod, "TICK_SECONDS", 0.1)
    with make_client(tmp_path, printers_file) as c:
        f = stored(c, fake_server)
        it = add(c, f["id"])
        with Session(c.app.state.queue.engine) as s:
            row = s.get(QueueItem, it["id"])
            row.status = "starting"
            s.add(row)
            s.commit()
    with make_client(tmp_path, printers_file) as c:
        assert item(c, it["id"])["status"] == "needs_check"
        assert queue(c)["paused"]
    assert fake_server.fake.started == []


def test_other_web_pages_cant_queue_prints(client, fake_server):
    f = stored(client, fake_server)
    r = client.post("/api/queue", json={"printer_id": "k1c", "file_id": f["id"], "mode": "idle"},
                    headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert queue(client)["items"] == []
