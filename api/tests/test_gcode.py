"""The current print's G-code: fetched once per print from the fake Moonraker, then served."""

import pytest
from fastapi.testclient import TestClient

from app.config import PrinterConfig, Settings
from app.gcode import gcode_url
from app.main import create_app

from .conftest import wait_for_sync


def make(tmp_path, printers_file, **kw):
    return Settings(data_dir=tmp_path / "data", printers_file=printers_file, reconnect_seconds=0.3,
                    static_dir=tmp_path / "none", **kw)


def info(c):
    return c.get("/api/printers/k1c/gcode/info").json()


def test_fetched_once_and_served(tmp_path, printers_file, fake_server):
    with TestClient(create_app(make(tmp_path, printers_file))) as c:
        wait_for_sync(lambda: info(c)["state"] == "ready")
        r = c.get("/api/printers/k1c/gcode")
        assert r.status_code == 200 and ";LAYER_CHANGE" in r.text
        assert info(c)["size"] == len(r.content)
        # Pausing, resuming and many status updates later: still one download.
        fake_server.scenario("paused")
        fake_server.scenario("printing")
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        assert fake_server.fake.gcode_downloads == 1
    # A restart of the app mid-print doesn't fetch it again.
    with TestClient(create_app(make(tmp_path, printers_file))) as c:
        wait_for_sync(lambda: info(c)["state"] == "ready")
        assert fake_server.fake.gcode_downloads == 1


def test_new_print_fetches_again(tmp_path, printers_file, fake_server):
    with TestClient(create_app(make(tmp_path, printers_file))) as c:
        wait_for_sync(lambda: info(c)["state"] == "ready")
        fake_server.scenario("complete")
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "complete")
        fake_server.fake.start_progress = 0.01  # a new job, started just now
        fake_server.scenario("printing")
        wait_for_sync(lambda: fake_server.fake.gcode_downloads == 2)


def test_failure_is_not_retried(tmp_path, printers_file, fake_server):
    fake_server.fake.gcode_missing = True
    with TestClient(create_app(make(tmp_path, printers_file))) as c:
        wait_for_sync(lambda: info(c)["state"] == "failed")
        assert "404" in info(c)["detail"]
        assert c.get("/api/printers/k1c/gcode").status_code == 404
        fake_server.scenario("paused")
        fake_server.scenario("printing")
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        assert fake_server.fake.gcode_downloads == 1


def test_nothing_fetched_when_idle_or_off(tmp_path, printers_file, fake_server):
    fake_server.scenario("idle")
    with TestClient(create_app(make(tmp_path, printers_file))) as c:
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "idle")
        assert info(c)["state"] == "none"
    fake_server.scenario("printing")
    with TestClient(create_app(make(tmp_path / "off", printers_file, gcode_preview=False))) as c:
        wait_for_sync(lambda: c.get("/api/printers/k1c").json()["state"] == "printing")
        assert info(c)["state"] == "off"
    assert fake_server.fake.gcode_downloads == 0


def test_url_stays_under_gcodes():
    cfg = PrinterConfig(id="k1c", name="K1C", host="10.0.0.5")
    assert gcode_url(cfg, "a b.gcode") == "http://10.0.0.5:7125/server/files/gcodes/a%20b.gcode"
    assert gcode_url(cfg, "../../etc/passwd") == "http://10.0.0.5:7125/server/files/gcodes/etc/passwd"
    assert gcode_url(cfg, "sub/x?.gcode") == "http://10.0.0.5:7125/server/files/gcodes/sub/x%3F.gcode"
