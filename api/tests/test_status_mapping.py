from datetime import datetime, timedelta, timezone

from app.config import PrinterConfig
from app.drivers.base import PrinterStatus
from app.drivers.moonraker import apply_objects, subscribed_objects

CFG = PrinterConfig(id="k1c", name="K1C", host="x")
NOW = datetime(2026, 11, 17, 4, 0, tzinfo=timezone.utc)


def blank() -> PrinterStatus:
    return PrinterStatus(id="k1c", name="K1C", type="moonraker")


def printing_objects(**over):
    objs = {
        "print_stats": {"state": "printing", "filename": "part.gcode", "print_duration": 3000.0,
                        "total_duration": 3100.0, "message": "", "info": {"current_layer": 148, "total_layer": 240}},
        "virtual_sdcard": {"progress": 0.5},
        "extruder": {"temperature": 219.8, "target": 220.0},
        "heater_bed": {"temperature": 60.1, "target": 60.0},
        "gcode_move": {"speed_factor": 1.3},
        "fan_generic model_fan": {"speed": 1.0},
        "fan_generic side_fan": {"speed": 0.4},
        "output_pin LED": {"value": 1.0},
    }
    objs.update(over)
    return objs


def test_printing():
    s = apply_objects(blank(), printing_objects(), CFG, now=NOW)
    assert s.state == "printing"
    assert s.filename == "part.gcode"
    assert s.progress == 0.5
    assert (s.layer, s.total_layers) == (148, 240)
    assert s.started_at == NOW - timedelta(seconds=3100)
    assert s.eta_at == NOW + timedelta(seconds=3000)  # same time again at 50%
    assert s.nozzle.current == 219.8 and s.bed.target == 60.0
    assert s.speed_percent == 130
    assert [(f.name, f.percent) for f in s.fans] == [("Model fan", 100.0), ("Auxiliary fan", 40.0)]
    assert s.light_on is True
    assert s.last_seen == NOW


def test_standby_is_idle_without_job():
    objs = printing_objects(print_stats={"state": "standby", "filename": "", "print_duration": 0,
                                         "total_duration": 0, "info": {}})
    s = apply_objects(blank(), objs, CFG, now=NOW)
    assert s.state == "idle"
    assert s.filename is None and s.progress is None and s.layer is None
    assert s.started_at is None and s.eta_at is None


def test_paused_has_no_eta():
    objs = printing_objects()
    objs["print_stats"]["state"] = "paused"
    s = apply_objects(blank(), objs, CFG, now=NOW)
    assert s.state == "paused" and s.eta_at is None and s.started_at is not None


def test_falls_back_to_display_status_progress():
    objs = printing_objects()
    del objs["virtual_sdcard"]
    objs["display_status"] = {"progress": 0.25}
    assert apply_objects(blank(), objs, CFG, now=NOW).progress == 0.25


def test_missing_objects_are_tolerated():
    s = apply_objects(blank(), {"print_stats": {"state": "printing"}}, CFG, now=NOW)
    assert s.state == "printing" and s.fans == [] and s.light_on is None and s.nozzle.current is None


def test_fan_object_speed():
    cfg = PrinterConfig(id="p", name="P", host="x", moonraker={"fans": {"Part": "fan"}, "light": None})
    s = apply_objects(blank(), {"fan": {"speed": 0.55}}, cfg, now=NOW)
    assert s.fans[0].percent == 55.0


def test_subscription_lists_configured_objects():
    objs = subscribed_objects(CFG)
    assert {"print_stats", "virtual_sdcard", "extruder", "heater_bed", "gcode_move"} <= set(objs)
    assert "fan_generic model_fan" in objs and "output_pin LED" in objs
    assert all(v is None for v in objs.values())
