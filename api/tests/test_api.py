import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

from .conftest import wait_for_sync


@pytest.fixture
def settings(tmp_path, printers_file):
    return Settings(data_dir=tmp_path / "data", printers_file=printers_file,
                    reconnect_seconds=0.3, static_dir=tmp_path / "no-static")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def state_of(client, pid="k1c"):
    return client.get(f"/api/printers/{pid}").json()["state"]


def test_health_and_config(client):
    assert client.get("/api/health").json()["printers"] == 1
    cfg = client.get("/api/config").json()
    assert cfg["controls_enabled"] is False


def test_printer_status(client):
    wait_for_sync(lambda: state_of(client) == "printing")
    printers = client.get("/api/printers").json()
    assert [p["id"] for p in printers] == ["k1c"]
    p = printers[0]
    assert p["filename"] == "phone_stand_v3.gcode"
    assert p["has_camera"] and p["cfs"]
    assert p["eta_at"] and p["started_at"]
    assert client.get("/api/printers/nope").status_code == 404


BODIES = {"pause": None, "resume": None, "cancel": None, "speed": {"percent": 100},
          "fan": {"name": "Model fan", "percent": 50}, "temp": {"heater": "nozzle", "target": 200},
          "light": {"on": True}}


@pytest.mark.parametrize("action", BODIES)
def test_controls_off_by_default(client, fake_server, action):
    wait_for_sync(lambda: state_of(client) == "printing")
    r = client.post(f"/api/printers/k1c/{action}", json=BODIES[action])
    assert r.status_code == 501
    assert client.post(f"/api/printers/nope/{action}", json=BODIES[action]).status_code == 404
    assert set(fake_server.fake.methods) == {"printer.objects.subscribe"}


def test_websocket_snapshot_and_updates(client):
    wait_for_sync(lambda: state_of(client) == "printing")
    with client.websocket_connect("/api/ws") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["printers"][0]["id"] == "k1c"
        update = ws.receive_json()
        assert update["type"] == "printer" and update["printer"]["id"] == "k1c"


def test_database_migrated_and_synced(client, settings):
    wait_for_sync(lambda: state_of(client) == "printing")
    con = sqlite3.connect(settings.data_dir / "printer.db")
    rows = con.execute("select id, name, type, address from printer").fetchall()
    version = con.execute("select version_num from alembic_version").fetchone()[0]
    con.close()
    assert rows[0][:3] == ("k1c", "K1C", "moonraker")
    assert version == "0003"


def test_last_seen_survives_restart(settings, fake_server):
    with TestClient(create_app(settings)) as c:
        wait_for_sync(lambda: state_of(c) == "printing")
        fake_server.offline(30)
        wait_for_sync(lambda: state_of(c) == "offline")
        seen = c.get("/api/printers/k1c").json()["last_seen"]
        assert seen
    with TestClient(create_app(settings)) as c:
        # Still offline: the remembered last-seen time is shown.
        wait_for_sync(lambda: state_of(c) == "offline")
        assert c.get("/api/printers/k1c").json()["last_seen"][:19] == seen[:19]


def test_serves_web_app(tmp_path, printers_file):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html>app</html>")
    (static / "manifest.webmanifest").write_text("{}")
    s = Settings(data_dir=tmp_path / "d", printers_file=printers_file, static_dir=static)
    with TestClient(create_app(s)) as c:
        assert c.get("/").text == "<html>app</html>"
        assert c.get("/printers/k1c").text == "<html>app</html>"  # client-side route
        assert c.get("/manifest.webmanifest").text == "{}"
        assert c.get("/api/nope").status_code == 404
        assert c.get("/../../etc/passwd").text == "<html>app</html>"


def test_no_printers_configured(tmp_path):
    s = Settings(data_dir=tmp_path / "d", printers_file=tmp_path / "missing.yaml")
    with TestClient(create_app(s)) as c:
        assert c.get("/api/printers").json() == []
