import os
import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.themes import BASE_FILE, TOKENS, ThemeStore, contrast, contrast_problems, load_themes


def write(path, text):
    path.write_text(text)
    return path


def test_base_has_100_readable_themes():
    s = load_themes()
    assert s.problems == []
    assert len(s.themes) == 100
    assert sum(t.mode == "dark" for t in s.themes) == 50
    assert len({t.id for t in s.themes}) == 100
    for t in s.themes:
        assert set(t.colors) == set(TOKENS), t.id
        assert contrast_problems(t.colors) == [], t.id
        assert contrast(t.colors["accent"], t.colors["background"]) >= 3, t.id
    assert s.default == {"light": "daylight-blue", "dark": "zinc-blue"}


def test_user_file_adds_overrides_and_extends(tmp_path):
    user = write(tmp_path / "themes.yaml", """
default:
  dark: carbon-spool
themes:
  - id: carbon-spool
    name: Carbon spool
    mode: dark
    extends: carbon-teal
    radius: 6
    colors:
      accent: "#14b8a6"
  - id: zinc-blue
    colors:
      accent: "#7c3aed"
  - id: tiny
    mode: light
    colors: {background: "#fff", foreground: "#111", accent: "#1d4ed8"}
""")
    s = load_themes(BASE_FILE, user)
    by = {t.id: t for t in s.themes}
    assert s.problems == []
    assert len(s.themes) == 102
    spool = by["carbon-spool"]
    assert spool.source == "user" and spool.radius == 6 and spool.colors["accent"] == "#14b8a6"
    assert spool.colors["background"] == by["carbon-teal"].colors["background"]
    assert by["zinc-blue"].source == "edited" and by["zinc-blue"].colors["accent"] == "#7c3aed"
    assert by["zinc-blue"].colors["background"] == "#0a0a0d"  # the rest of the base theme stays
    assert set(by["tiny"].colors) == set(TOKENS)  # three colours are enough
    assert by["tiny"].name == "Tiny"
    assert s.default == {"light": "daylight-blue", "dark": "carbon-spool"}
    assert [t.id for t in s.themes][-2:] == ["carbon-spool", "tiny"]


def test_bad_themes_are_left_out_not_fatal(tmp_path):
    user = write(tmp_path / "themes.yaml", """
default:
  light: zinc-blue
themes:
  - id: neon
    mode: dark
    colors: {accent: "#39ff14"}
  - id: grey-on-grey
    mode: light
    colors: {background: "#888888", foreground: "#999999", accent: "#000000"}
  - id: orphan
    mode: dark
    extends: nope
  - id: loop-a
    extends: loop-b
  - id: loop-b
    extends: loop-a
  - id: wrong
    mode: dark
    colors: {background: "black", foreground: "#fff", accent: "#fff"}
  - name: no id
  - id: fine
    mode: dark
    extends: zinc-blue
""")
    s = load_themes(BASE_FILE, user)
    ids = {t.id for t in s.themes}
    assert "fine" in ids and len(ids) == 101
    text = "\n".join(s.problems)
    assert "neon: missing background, foreground" in text
    assert "grey-on-grey: hard to read: foreground on background" in text
    assert "orphan: extends unknown theme 'nope'" in text
    assert "loop-a: extends goes more than 3 deep" in text
    assert "wrong: background must be a hex colour" in text
    assert "theme #7 needs an id" in text
    assert "default light: no light theme called 'zinc-blue'" in text
    assert s.default["light"] == "daylight-blue"


def test_unreadable_or_missing_user_file(tmp_path):
    assert len(load_themes(BASE_FILE, tmp_path / "missing.yaml").themes) == 100
    s = load_themes(BASE_FILE, write(tmp_path / "bad.yaml", "themes: [: oops"))
    assert len(s.themes) == 100 and "can't read it" in s.problems[0]
    s = load_themes(BASE_FILE, write(tmp_path / "list.yaml", "- 1\n- 2\n"))
    assert len(s.themes) == 100 and s.problems


def test_store_reloads_when_the_file_changes(tmp_path):
    user = write(tmp_path / "themes.yaml", "themes: []\n")
    store = ThemeStore(user)
    assert len(store.get().themes) == 100
    write(user, "themes:\n  - id: extra\n    mode: dark\n    extends: zinc-blue\n")
    later = time.time() + 5
    os.utime(user, (later, later))
    assert len(store.get().themes) == 101


def test_themes_endpoint(tmp_path, printers_file):
    data = tmp_path / "data"
    data.mkdir()
    write(data / "themes.yaml", "themes:\n  - id: mine\n    name: Mine\n    mode: light\n    extends: paper-amber\n    font: serif\n")
    s = Settings(data_dir=data, printers_file=printers_file, static_dir=tmp_path / "x")
    with TestClient(create_app(s)) as c:
        body = c.get("/api/themes").json()
    assert body["default"] == {"light": "daylight-blue", "dark": "zinc-blue"}
    mine = body["themes"][-1]
    assert mine["id"] == "mine" and mine["font"] == "serif" and mine["source"] == "user"
    assert mine["colors"]["accent_foreground"] == "#ffffff"
    assert "problems" not in body


def test_themes_file_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("THEMES_FILE", "")
    assert Settings().themes_file is None
    monkeypatch.setenv("THEMES_FILE", str(tmp_path / "t.yaml"))
    assert Settings().themes_file == tmp_path / "t.yaml"
