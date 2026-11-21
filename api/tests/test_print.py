"""Printing a stored file from the app, against the fake Moonraker. Never a real printer."""

import pytest
from fastapi.testclient import TestClient

import app.controls as controls
import app.library as library_mod
from app.config import Settings
from app.drivers.moonraker import gcode_allowed, print_path_ok
from app.main import create_app

from .conftest import wait_for_sync
from .test_library import sliced


def make_client(tmp_path, printers_file, controls_enabled=True):
    s = Settings(data_dir=tmp_path / "data", printers_file=printers_file, reconnect_seconds=0.3,
                 static_dir=tmp_path / "none", library_root=tmp_path / "library",
                 controls_enabled=controls_enabled)
    return TestClient(create_app(s))


@pytest.fixture
def client(tmp_path, printers_file, monkeypatch):
    monkeypatch.setattr(controls, "MIN_GAP", 0.0)
    monkeypatch.setattr(library_mod, "QUIET_SECONDS", 0.2)
    monkeypatch.setattr(library_mod, "FALLBACK_SECONDS", 1.0)
    with make_client(tmp_path, printers_file) as c:
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        yield c


def status(c):
    return c.get("/api/printers/k1c").json()


def captured(c, fake_server, path: str, body: bytes) -> dict:
    fake_server.run(fake_server.fake.upload(path, body))
    name = path.rsplit("/", 1)[-1]
    wait_for_sync(lambda: any(f["filename"] == name and f["sha256"] for f in c.get("/api/files").json()
                              if f["size"] == len(body)))
    return next(f for f in c.get("/api/files").json() if f["filename"] == name and f["size"] == len(body))


def idle(c, fake_server):
    fake_server.scenario("idle")
    wait_for_sync(lambda: status(c)["state"] == "idle")


def print_file(c, file_id):
    return c.post("/api/printers/k1c/print", json={"file_id": file_id})


def test_print_a_captured_file(client, fake_server):
    f = captured(client, fake_server, "drive/rail_A.gcode", sliced("A"))
    assert f["printer_path"] == "drive/rail_A.gcode"
    idle(client, fake_server)

    r = print_file(client, f["id"])
    assert r.status_code == 200, r.text
    assert r.json()["command"] == "printer.print.start drive/rail_A.gcode"
    assert fake_server.fake.started == ["drive/rail_A.gcode"]
    wait_for_sync(lambda: status(client)["state"] == "printing" and status(client)["filename"] == "drive/rail_A.gcode")
    assert status(client)["progress"] == 0

    # Already printing: refused before anything reaches the printer.
    r = print_file(client, f["id"])
    assert r.status_code == 409 and "already printing" in r.json()["detail"]
    assert fake_server.fake.started == ["drive/rail_A.gcode"]

    log = client.get("/api/printers/k1c/actions").json()
    assert [(a["action"], a["result"]) for a in log[:2]] == [("print", "refused"), ("print", "sent")]
    assert log[1]["values"] == {"file_id": f["id"], "path": "drive/rail_A.gcode"}


def test_falls_back_to_sdcard_print_file(client, fake_server):
    f = captured(client, fake_server, "rail_B.gcode", sliced("B"))
    idle(client, fake_server)
    fake_server.fake.print_methods = False
    r = print_file(client, f["id"])
    assert r.status_code == 200 and r.json()["command"] == 'SDCARD_PRINT_FILE FILENAME="rail_B.gcode"'
    assert fake_server.fake.scripts == ['SDCARD_PRINT_FILE FILENAME="rail_B.gcode"']
    wait_for_sync(lambda: status(client)["state"] == "printing")


def test_file_gone_from_the_printer_is_reported(client, fake_server):
    f = captured(client, fake_server, "rail_C.gcode", sliced("C"))
    fake_server.run(fake_server.fake.delete("rail_C.gcode"))
    idle(client, fake_server)
    r = print_file(client, f["id"])
    assert r.status_code == 502 and "Unable to open file" in r.json()["detail"]
    assert status(client)["state"] == "idle"


def test_older_copy_under_a_reused_name_is_refused(client, fake_server):
    old = captured(client, fake_server, "part.gcode", sliced("v1"))
    new = captured(client, fake_server, "part.gcode", sliced("version-2"))
    assert old["id"] != new["id"]
    idle(client, fake_server)
    r = print_file(client, old["id"])
    assert r.status_code == 409 and "newer file with the same name" in r.json()["detail"]
    assert print_file(client, new["id"]).status_code == 200
    assert fake_server.fake.started == ["part.gcode"]


def test_refusals(client, fake_server):
    assert print_file(client, 9999).status_code == 404
    assert client.post("/api/printers/nope/print", json={"file_id": 1}).status_code == 404

    # A file that never came from this printer isn't on it.
    lib = client.app.state.library
    src = lib.root / "upload.gcode"
    src.write_bytes(sliced("claude"))
    row = lib.ingest(src, "upload.gcode", "claude", None)
    assert print_file(client, row.id).status_code == 409

    f = captured(client, fake_server, "rail_D.gcode", sliced("D"))
    fake_server.offline(5)
    wait_for_sync(lambda: status(client)["state"] == "offline")
    r = print_file(client, f["id"])
    assert r.status_code == 409 and "offline" in r.json()["detail"]
    assert fake_server.fake.started == []


def test_controls_off(tmp_path, printers_file, fake_server):
    with make_client(tmp_path, printers_file, controls_enabled=False) as c:
        assert c.post("/api/printers/k1c/print", json={"file_id": 1}).status_code == 501
    assert fake_server.fake.started == []


def test_print_paths_and_gcode():
    for ok in ("a.gcode", "sub/Part 1 (v2).gcode", "x.3mf", "Körper_PLA_1h.gcode"):
        assert print_path_ok(ok), ok
    for bad in ("", "/etc/x.gcode", "../x.gcode", "a/../b.gcode", ".thumbs/x.gcode", "a.txt",
                'a".gcode', "a\nM112.gcode", "a\\b.gcode", "x" * 300 + ".gcode"):
        assert not print_path_ok(bad), bad
    assert gcode_allowed('SDCARD_PRINT_FILE FILENAME="sub/a b.gcode"')
    assert not gcode_allowed('SDCARD_PRINT_FILE FILENAME="a.gcode" X="y"')
    assert not gcode_allowed('SDCARD_PRINT_FILE FILENAME="a.gcode"\nM112')
