from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import PrintersFile, Settings, load_printers

PRINTER_DIR = Path(__file__).resolve().parents[2]


def test_defaults_are_safe(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here
    for var in ("BIND_ADDRESS", "PORT", "RECONNECT_SECONDS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.bind_address == "127.0.0.1"
    assert s.port == 8090
    assert s.reconnect_seconds == 30.0
    assert s.import_token == ""


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("BIND_ADDRESS", "100.64.0.1")
    monkeypatch.setenv("PORT", "9000")
    s = Settings()
    assert (s.bind_address, s.port) == ("100.64.0.1", 9000)


def test_example_printers_file_loads():
    printers = load_printers(PRINTER_DIR / "printers.example.yaml")
    assert [p.id for p in printers] == ["k1c"]
    k1c = printers[0]
    assert k1c.type == "moonraker" and k1c.port == 7125 and k1c.cfs
    assert k1c.moonraker.fans["Model fan"] == "fan_generic model_fan"
    assert k1c.moonraker.light == "output_pin LED"


def test_fake_and_dev_printers_files_load():
    assert load_printers(PRINTER_DIR / "printers.fake.yaml")[0].host == "fake-moonraker"
    assert load_printers(PRINTER_DIR / "printers.dev.yaml")[0].host == "127.0.0.1"


def test_directory_instead_of_file_means_no_printers(tmp_path):
    # Docker creates an empty directory when the bind-mounted file is missing.
    (tmp_path / "printers.yaml").mkdir()
    assert load_printers(tmp_path / "printers.yaml") == []


def test_missing_file_means_no_printers(tmp_path):
    assert load_printers(tmp_path / "nope.yaml") == []


@pytest.mark.parametrize("bad", [
    {"printers": [{"id": "Bad Id", "name": "x", "host": "h"}]},
    {"printers": [{"id": "a", "name": "x", "host": "h", "port": 70000}]},
    {"printers": [{"id": "a", "name": "x", "host": "h", "type": "makerbot"}]},
    {"printers": [{"id": "a", "name": "x", "host": "h"}, {"id": "a", "name": "y", "host": "h"}]},
])
def test_invalid_printers_rejected(bad):
    with pytest.raises(ValidationError):
        PrintersFile.model_validate(bad)
