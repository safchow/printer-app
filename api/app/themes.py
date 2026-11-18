"""Colour themes from YAML.

app/themes/base.yaml ships with the app. An optional user file (THEMES_FILE,
default DATA_DIR/themes.yaml) adds themes, overrides base ones by id and can
change which themes "System" uses. The merged list is served at /api/themes
and the web app turns each colour into a CSS variable.

A theme only needs background, foreground and accent; every other colour is
derived from those unless the theme (or the theme it extends) sets it. A
theme that can't be used (bad colour, unreadable text, unknown parent) is
left out and logged. The app never fails to start because of a theme.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

BASE_FILE = Path(__file__).parent / "themes" / "base.yaml"

# Colour tokens, as written in YAML. The web app maps each to --token-name
# (underscores become hyphens; accent and accent_foreground become --brand).
TOKENS = (
    "background", "foreground", "card", "card_foreground", "popover", "popover_foreground",
    "primary", "primary_foreground", "secondary", "secondary_foreground",
    "muted", "muted_foreground", "stage", "border", "input", "ring",
    "accent", "accent_foreground", "destructive", "destructive_foreground",
    "warn", "warn_foreground", "offline", "offline_foreground",
)
REQUIRED = ("background", "foreground", "accent")
FONTS = ("inter", "system", "mono", "serif")
MODES = ("light", "dark")
MAX_EXTENDS = 3
MIN_TEXT_CONTRAST = 4.5

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


# ---------------------------------------------------------------- colour maths

def parse_hex(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(min(1, max(0, v)) * 255):02x}" for v in rgb)


def _linear(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _gamma(v: float) -> float:
    return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055


def luminance(c: str) -> float:
    r, g, b = (_linear(v) for v in parse_hex(c))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a: str, b: str, t: float) -> str:
    """t of the way from a to b, mixed in linear light."""
    pa, pb = parse_hex(a), parse_hex(b)
    return to_hex(tuple(_gamma(_linear(x) + (_linear(y) - _linear(x)) * t) for x, y in zip(pa, pb)))  # type: ignore[arg-type]


def oklch_to_rgb(L: float, C: float, h: float) -> tuple[float, float, float]:
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    r = 4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    bl = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_
    return _gamma(r) if r > 0 else r, _gamma(g) if g > 0 else g, _gamma(bl) if bl > 0 else bl


def oklch(L: float, C: float, h: float) -> str:
    """OKLCH to hex, lowering chroma until the colour fits in sRGB."""
    while True:
        rgb = oklch_to_rgb(L, C, h)
        if all(-0.001 <= v <= 1.001 for v in rgb) or C <= 0:
            return to_hex(rgb)
        C = max(0.0, C - 0.002)


# ---------------------------------------------------------------- derivation

def _readable(on: str, *candidates: str) -> str:
    return max(candidates, key=lambda c: contrast(on, c))


def derive(mode: str, given: dict[str, str]) -> dict[str, str]:
    """Fill every token that isn't set, from background, foreground and accent."""
    c = dict(given)
    bg, fg, dark = c["background"], c["foreground"], mode == "dark"
    c.setdefault("card", bg)
    c.setdefault("card_foreground", fg)
    c.setdefault("muted", mix(bg, fg, 0.07 if dark else 0.045))
    c.setdefault("popover", c["muted"] if dark else c["card"])
    c.setdefault("popover_foreground", fg)
    c.setdefault("secondary", c["muted"])
    c.setdefault("secondary_foreground", fg)
    c.setdefault("primary", fg)
    c.setdefault("primary_foreground", bg)
    if "muted_foreground" not in c:
        # The lightest (or darkest) grey that still reads on every surface.
        surfaces = (bg, c["card"], c["muted"], c["popover"])
        t = 0.45
        while t < 0.95:
            m = mix(bg, fg, t)
            if min(contrast(m, s) for s in surfaces) >= MIN_TEXT_CONTRAST:
                break
            t += 0.02
        c["muted_foreground"] = mix(bg, fg, t)
    c.setdefault("border", mix(bg, fg, 0.14 if dark else 0.11))
    c.setdefault("input", c["border"])
    c.setdefault("stage", mix(bg, fg, 0.06 if dark else 0.09))
    c.setdefault("ring", c["accent"])
    c.setdefault("accent_foreground", _readable(c["accent"], "#ffffff", bg, fg, "#000000"))
    if dark:
        c.setdefault("destructive", "#f87171")
        c.setdefault("warn", mix(bg, "#f59e0b", 0.12))
        c.setdefault("warn_foreground", "#fcd34d")
        c.setdefault("offline", mix(bg, "#ef4444", 0.14))
        c.setdefault("offline_foreground", "#fca5a5")
    else:
        c.setdefault("destructive", "#b91c1c")
        c.setdefault("warn", mix(bg, "#f59e0b", 0.1))
        c.setdefault("warn_foreground", "#92400e")
        c.setdefault("offline", mix(bg, "#ef4444", 0.08))
        c.setdefault("offline_foreground", "#b91c1c")
    c.setdefault("destructive_foreground", _readable(c["destructive"], "#ffffff", "#000000", bg, fg))
    return c


# (text, surfaces it has to read on)
TEXT_PAIRS = (
    ("foreground", ("background",)),
    ("card_foreground", ("card",)),
    ("popover_foreground", ("popover",)),
    ("primary_foreground", ("primary",)),
    ("secondary_foreground", ("secondary",)),
    ("muted_foreground", ("background", "card", "muted")),
    ("accent_foreground", ("accent",)),
    ("destructive_foreground", ("destructive",)),
    ("destructive", ("background",)),
    ("warn_foreground", ("warn",)),
    ("offline_foreground", ("offline",)),
)


def contrast_problems(colors: dict[str, str]) -> list[str]:
    out = []
    for text, surfaces in TEXT_PAIRS:
        for s in surfaces:
            r = contrast(colors[text], colors[s])
            if r < MIN_TEXT_CONTRAST:
                out.append(f"{text} on {s} is {r:.1f}:1 (needs {MIN_TEXT_CONTRAST}:1)")
    return out


# ---------------------------------------------------------------- loading

@dataclass
class Theme:
    id: str
    name: str
    mode: str
    colors: dict[str, str]
    radius: float = 10
    font: str = "inter"
    source: str = "base"  # base, user, or edited (a base theme the user file changed)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "mode": self.mode, "radius": self.radius,
                "font": self.font, "source": self.source, "colors": self.colors}


@dataclass
class ThemeSet:
    themes: list[Theme]
    default: dict[str, str]
    problems: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {"default": self.default, "themes": [t.public() for t in self.themes]}


def _read(path: Path, label: str, problems: list[str]) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        problems.append(f"{label}: can't read it ({e})")
        return {}
    if not isinstance(raw, dict):
        problems.append(f"{label}: expected a mapping with a themes list")
        return {}
    return raw


def _entries(raw: dict[str, Any], label: str, problems: list[str]) -> list[dict[str, Any]]:
    themes = raw.get("themes") or []
    if not isinstance(themes, list):
        problems.append(f"{label}: themes must be a list")
        return []
    out = []
    for i, t in enumerate(themes):
        if not isinstance(t, dict) or not isinstance(t.get("id"), str) or not _ID.match(t["id"]):
            problems.append(f"{label}: theme #{i + 1} needs an id (lowercase letters, digits, - or _)")
            continue
        out.append(t)
    return out


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = {**base, **{k: v for k, v in over.items() if k != "colors"}}
    out["colors"] = {**(base.get("colors") or {}), **(over.get("colors") or {})}
    return out


def load_themes(base_file: Path = BASE_FILE, user_file: Path | None = None) -> ThemeSet:
    problems: list[str] = []
    base_raw = _read(base_file, base_file.name, problems)
    user_raw = _read(user_file, user_file.name, problems) if user_file else {}

    # Raw entries by id, in order: base first, then new user themes.
    entries: dict[str, dict[str, Any]] = {}
    source: dict[str, str] = {}
    for t in _entries(base_raw, base_file.name, problems):
        entries[t["id"]] = t
        source[t["id"]] = "base"
    for t in _entries(user_raw, user_file.name if user_file else "", problems):
        if t["id"] in entries and source[t["id"]] != "user":
            entries[t["id"]] = _merge(entries[t["id"]], t)
            source[t["id"]] = "edited"
        else:
            entries[t["id"]] = t
            source[t["id"]] = "user"

    def resolve(tid: str, depth: int = 0) -> dict[str, Any]:
        t = entries[tid]
        parent = t.get("extends")
        if not parent:
            return t
        if depth >= MAX_EXTENDS:
            raise ValueError(f"extends goes more than {MAX_EXTENDS} deep")
        if parent not in entries or parent == tid:
            raise ValueError(f"extends unknown theme {parent!r}")
        return _merge(resolve(parent, depth + 1), {k: v for k, v in t.items() if k != "extends"})

    themes: list[Theme] = []
    for tid in entries:
        try:
            t = resolve(tid)
            themes.append(_build(tid, t, source[tid]))
        except ValueError as e:
            problems.append(f"theme {tid}: {e}")

    by_id = {t.id: t for t in themes}
    default = {"light": "", "dark": ""}
    for raw in (base_raw, user_raw):
        d = raw.get("default")
        if isinstance(d, dict):
            for mode in MODES:
                want = d.get(mode)
                if want is None:
                    continue
                if want in by_id and by_id[want].mode == mode:
                    default[mode] = want
                else:
                    problems.append(f"default {mode}: no {mode} theme called {want!r}")
    for mode in MODES:
        if not default[mode]:
            default[mode] = next((t.id for t in themes if t.mode == mode), "")

    for p in problems:
        log.warning("themes: %s", p)
    return ThemeSet(themes, default, problems)


def _build(tid: str, t: dict[str, Any], source: str) -> Theme:
    mode = t.get("mode")
    if mode not in MODES:
        raise ValueError("mode must be light or dark")
    colors = t.get("colors") or {}
    if not isinstance(colors, dict):
        raise ValueError("colors must be a mapping")
    for k, v in colors.items():
        if k not in TOKENS:
            raise ValueError(f"unknown colour {k!r}")
        if not isinstance(v, str) or not _HEX.match(v):
            raise ValueError(f"{k} must be a hex colour like \"#1a2b3c\"")
    missing = [k for k in REQUIRED if k not in colors]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    resolved = derive(mode, {k: to_hex(parse_hex(v)) for k, v in colors.items()})
    bad = contrast_problems(resolved)
    if bad:
        raise ValueError("hard to read: " + "; ".join(bad))
    radius = t.get("radius", 10)
    if not isinstance(radius, (int, float)) or not 0 <= radius <= 24:
        raise ValueError("radius must be a number of pixels from 0 to 24")
    font = t.get("font", "inter")
    if font not in FONTS:
        raise ValueError(f"font must be one of {', '.join(FONTS)}")
    name = t.get("name") or tid.replace("-", " ").replace("_", " ").title()
    return Theme(tid, str(name), mode, resolved, radius, font, source)


class ThemeStore:
    """Loads themes and reloads when either file changes."""

    def __init__(self, user_file: Path | None, base_file: Path = BASE_FILE):
        self.user_file, self.base_file = user_file, base_file
        self._key: tuple | None = None
        self._set: ThemeSet | None = None

    def _stamp(self, p: Path | None) -> float | None:
        try:
            return p.stat().st_mtime if p and p.is_file() else None
        except OSError:
            return None

    def get(self) -> ThemeSet:
        key = (self._stamp(self.base_file), self._stamp(self.user_file))
        if self._set is None or key != self._key:
            self._set, self._key = load_themes(self.base_file, self.user_file), key
        return self._set
