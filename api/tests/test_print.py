"""Printing a stored file from the app, against the fake Moonraker. Never a real printer."""

import pytest
from fastapi.testclient import TestClient

import app.controls as controls
import app.library as library_mod
from app.config import Settings
from app.drivers.moonraker import gcode_allowed, print_path_ok
from app.library import upload_path
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


def test_print_uploads_our_copy_then_starts_it(client, fake_server):
    body = sliced("A")
    f = captured(client, fake_server, "drive/rail A.gcode", body)
    fake_server.run(fake_server.fake.delete("drive/rail A.gcode"))  # the printer's storage was cleared
    idle(client, fake_server)
    count = len(client.get("/api/files").json())

    r = print_file(client, f["id"])
    assert r.status_code == 200, r.text
    path = "printer-app_rail A.gcode"
    assert r.json()["command"] == f"upload {path}; printer.print.start {path}"
    assert fake_server.fake.uploads == [path] and fake_server.fake.files[path] == body
    assert fake_server.fake.started == [path]
    wait_for_sync(lambda: status(client)["state"] == "printing" and status(client)["filename"] == path)
    assert status(client)["progress"] < 0.01

    # The print is linked to the file we sent, and the upload isn't captured back as a new file.
    wait_for_sync(lambda: client.get(f"/api/files/{f['id']}").json()["prints"] == 1)
    assert len(client.get("/api/files").json()) == count
    assert not any(p.endswith(path.replace(" ", "%20")) for p in fake_server.fake.http_requests if "/gcodes/" in p)

    # Already printing: refused before anything reaches the printer.
    r = print_file(client, f["id"])
    assert r.status_code == 409 and "already printing" in r.json()["detail"]
    assert fake_server.fake.uploads == [path] and fake_server.fake.started == [path]

    log = client.get("/api/printers/k1c/actions").json()
    assert [(a["action"], a["result"]) for a in log[:2]] == [("print", "refused"), ("print", "sent")]
    assert log[1]["values"] == {"file_id": f["id"], "path": path}


def test_prints_the_exact_version_clicked(client, fake_server):
    old = captured(client, fake_server, "part.gcode", sliced("v1"))
    new = captured(client, fake_server, "part.gcode", sliced("version-2"))
    assert old["id"] != new["id"]
    idle(client, fake_server)
    assert print_file(client, old["id"]).status_code == 200
    assert fake_server.fake.files["printer-app_part.gcode"] == sliced("v1")
    wait_for_sync(lambda: client.get(f"/api/files/{old['id']}").json()["prints"] == 1)
    assert client.get(f"/api/files/{new['id']}").json()["prints"] == 0


def test_falls_back_to_sdcard_print_file(client, fake_server):
    f = captured(client, fake_server, "rail_B.gcode", sliced("B"))
    idle(client, fake_server)
    fake_server.fake.print_methods = False
    r = print_file(client, f["id"])
    assert r.status_code == 200 and r.json()["command"].endswith('SDCARD_PRINT_FILE FILENAME="printer-app_rail_B.gcode"')
    assert fake_server.fake.scripts == ['SDCARD_PRINT_FILE FILENAME="printer-app_rail_B.gcode"']
    wait_for_sync(lambda: status(client)["state"] == "printing")


def test_a_failed_upload_starts_nothing(client, fake_server):
    f = captured(client, fake_server, "rail_C.gcode", sliced("C"))
    idle(client, fake_server)
    fake_server.fake.upload_fails = True
    r = print_file(client, f["id"])
    assert r.status_code == 502 and r.json()["detail"] == "the printer refused the upload (HTTP 500: No space left on device)"
    assert fake_server.fake.started == [] and status(client)["state"] == "idle"
    assert client.get("/api/printers/k1c/actions").json()[0]["result"] == "failed"


def test_a_file_that_never_came_from_the_printer_can_be_printed(client, fake_server):
    lib = client.app.state.library
    src = lib.root / "upload.gcode"
    src.write_bytes(sliced("from-claude"))
    row = lib.ingest(src, "Bracket v2 ✓.gcode", "claude", None)
    idle(client, fake_server)
    assert print_file(client, row.id).status_code == 200
    assert fake_server.fake.started == ["printer-app_Bracket v2 _.gcode"]


def test_refusals(client, fake_server):
    assert print_file(client, 9999).status_code == 404
    assert client.post("/api/printers/nope/print", json={"file_id": 1}).status_code == 404
    f = captured(client, fake_server, "rail_D.gcode", sliced("D"))
    fake_server.offline(5)
    wait_for_sync(lambda: status(client)["state"] == "offline")
    r = print_file(client, f["id"])
    assert r.status_code == 409 and "offline" in r.json()["detail"]
    assert fake_server.fake.uploads == [] and fake_server.fake.started == []


def test_controls_off(tmp_path, printers_file, fake_server):
    with make_client(tmp_path, printers_file, controls_enabled=False) as c:
        assert c.post("/api/printers/k1c/print", json={"file_id": 1}).status_code == 501
    assert fake_server.fake.uploads == [] and fake_server.fake.started == []


def test_upload_names_and_paths():
    assert upload_path("rail A (v2).gcode") == "printer-app_rail A (v2).gcode"
    nasty = upload_path('../../a"b\nM112.GCODE')
    assert nasty.startswith("printer-app_") and nasty.endswith("M112.gcode") and "/" not in nasty
    assert print_path_ok(nasty) and '"' not in nasty and "\n" not in nasty
    assert upload_path(".hidden.3mf") == "printer-app_hidden.3mf"
    assert upload_path("noext") == "printer-app_noext.gcode"
    assert all(print_path_ok(upload_path(n)) for n in ("x" * 400 + ".gcode", "Körper.gcode", "...gcode"))
    for ok in ("a.gcode", "sub/Part 1 (v2).gcode", "x.3mf"):
        assert print_path_ok(ok), ok
    for bad in ("", "/etc/x.gcode", "../x.gcode", "a/../b.gcode", ".thumbs/x.gcode", "a.txt",
                'a".gcode', "a\nM112.gcode", "a\\b.gcode", "x" * 300 + ".gcode"):
        assert not print_path_ok(bad), bad
    assert gcode_allowed('SDCARD_PRINT_FILE FILENAME="sub/a b.gcode"')
    assert not gcode_allowed('SDCARD_PRINT_FILE FILENAME="a.gcode" X="y"')
    assert not gcode_allowed('SDCARD_PRINT_FILE FILENAME="a.gcode"\nM112')
