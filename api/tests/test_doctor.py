from app import doctor


def test_validate_only_touches_nothing(monkeypatch, printers_file, fake_server, capsys):
    monkeypatch.setenv("PRINTERS_FILE", str(printers_file))
    assert doctor.main([]) == 0
    assert "k1c: K1C (moonraker)" in capsys.readouterr().out
    assert fake_server.fake.http_requests == [] and fake_server.fake.methods == []


def test_probe_makes_three_gentle_gets(monkeypatch, printers_file, fake_server, capsys):
    monkeypatch.setenv("PRINTERS_FILE", str(printers_file))
    monkeypatch.setattr(doctor.time, "sleep", lambda _: None)
    assert doctor.main(["--probe"]) == 0
    out = capsys.readouterr().out
    assert "output_pin fan0" in out and "output_pin LED" in out
    assert fake_server.fake.http_requests == list(doctor.PROBE_PATHS)
    assert fake_server.fake.methods == []  # no websocket, no RPC


def test_bad_config(monkeypatch, tmp_path, capsys):
    bad = tmp_path / "p.yaml"
    bad.write_text("printers:\n  - id: 'Bad Id'\n    name: x\n    host: h\n")
    monkeypatch.setenv("PRINTERS_FILE", str(bad))
    assert doctor.main([]) == 1
