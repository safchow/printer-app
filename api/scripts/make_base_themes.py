"""Writes app/themes/base.yaml: 100 themes, 50 dark and 50 light.

Each theme is a surface family (background tint, radius, font) paired with an
accent. Colours are picked in OKLCH, then nudged until every text colour
reads at 4.5:1 or better on the surfaces it sits on, and the accent stands
out from the background at 3:1 or better.

    cd api && python -m scripts.make_base_themes
"""

from __future__ import annotations

from pathlib import Path

from app.themes import contrast, derive, contrast_problems, oklch, BASE_FILE

# name: (hue, chroma). chroma 0 = neutral.
ACCENTS = {
    "blue": (262, 0.21), "violet": (293, 0.22), "green": (150, 0.17), "orange": (48, 0.19),
    "rose": (12, 0.21), "amber": (75, 0.17), "lime": (130, 0.2), "cyan": (210, 0.14),
    "red": (27, 0.22), "mono": (0, 0.0), "sky": (237, 0.15), "indigo": (277, 0.2),
    "emerald": (163, 0.16), "pink": (350, 0.2), "gold": (90, 0.16), "coral": (33, 0.17),
    "mint": (168, 0.13), "teal": (185, 0.13), "fuchsia": (325, 0.25), "magenta": (335, 0.25),
    "white": (0, 0.0), "brick": (35, 0.16), "olive": (115, 0.13), "ink": (265, 0.16),
    "terracotta": (45, 0.14), "sage": (140, 0.08), "plum": (320, 0.15), "navy": (265, 0.15),
    "slate": (255, 0.05), "berry": (355, 0.19), "purple": (305, 0.21), "brown": (55, 0.1),
    "black": (0, 0.0),
}

# Accents that look best as a strong colour with white text on a dark theme.
WHITE_TEXT_ON_DARK = {"blue", "violet", "indigo", "purple", "rose", "red", "berry", "fuchsia", "magenta"}

# family: (hue, chroma, background L, raised cards?, radius, font, accents, blurb)
DARK = {
    "Zinc": (285, 0.006, 0.145, False, 10, "inter", ["blue", "violet", "green", "orange", "rose"]),
    "Graphite": (0, 0.0, 0.2, True, 4, "inter", ["amber", "lime", "cyan", "red", "mono"]),
    "Slate": (255, 0.022, 0.21, True, 10, "inter", ["sky", "indigo", "emerald", "amber", "pink"]),
    "Midnight": (268, 0.045, 0.17, True, 12, "inter", ["blue", "cyan", "violet", "gold", "coral"]),
    "Forest": (155, 0.03, 0.18, True, 10, "inter", ["mint", "lime", "gold", "sky", "rose"]),
    "Ember": (40, 0.025, 0.17, True, 8, "inter", ["orange", "amber", "red", "teal", "gold"]),
    "Plum": (320, 0.035, 0.17, True, 14, "inter", ["pink", "violet", "fuchsia", "mint", "amber"]),
    "Ocean": (230, 0.04, 0.19, True, 12, "inter", ["teal", "cyan", "sky", "coral", "lime"]),
    "Carbon": (190, 0.014, 0.13, False, 6, "inter", ["teal", "green", "cyan", "orange", "violet"]),
    "Terminal": (145, 0.02, 0.12, False, 2, "mono", ["green", "amber", "cyan", "magenta", "white"]),
}
LIGHT = {
    "Daylight": (285, 0.004, 1.0, False, 10, "inter", ["blue", "violet", "green", "orange", "rose"]),
    "Paper": (80, 0.012, 0.975, True, 8, "inter", ["amber", "brick", "olive", "ink", "teal"]),
    "Linen": (60, 0.016, 0.962, True, 12, "serif", ["terracotta", "sage", "plum", "navy", "gold"]),
    "Mist": (240, 0.012, 0.975, True, 10, "inter", ["blue", "indigo", "teal", "coral", "slate"]),
    "Mint": (165, 0.022, 0.975, True, 12, "inter", ["green", "teal", "blue", "coral", "violet"]),
    "Blush": (10, 0.016, 0.978, True, 14, "inter", ["rose", "berry", "coral", "plum", "teal"]),
    "Lavender": (295, 0.022, 0.975, True, 14, "inter", ["violet", "purple", "pink", "blue", "teal"]),
    "Sand": (85, 0.028, 0.955, True, 6, "inter", ["orange", "brown", "olive", "teal", "red"]),
    "Cloud": (220, 0.02, 0.985, True, 16, "system", ["sky", "cyan", "indigo", "lime", "pink"]),
    "Lab": (0, 0.0, 0.99, False, 2, "system", ["black", "blue", "red", "green", "orange"]),
}


def nudge(start: float, step: float, ok, lo: float = 0.05, hi: float = 0.99) -> float:
    L = start
    while lo <= L <= hi and not ok(L):
        L += step
    return min(hi, max(lo, L))


def make(family: str, spec, accent: str, mode: str) -> dict:
    h, c, bgL, raised, radius, font, _ = spec
    dark = mode == "dark"
    bg = oklch(bgL, c, h)
    if dark:
        card = oklch(bgL + 0.025, c, h) if raised else bg
        muted = oklch(bgL + 0.05, c * 1.1, h)
        border = oklch(bgL + 0.12, c * 1.1, h)
        stage = oklch(bgL + 0.04, c, h)
        fg = oklch(0.97, min(c, 0.01), h)
        surfaces = (bg, card, muted)
        mL = nudge(0.68, 0.01, lambda L: min(contrast(oklch(L, c * 0.8, h), s) for s in surfaces) >= 4.6)
        muted_fg = oklch(mL, c * 0.8, h)
    else:
        card = oklch(min(1.0, bgL + 0.02), c * 0.5, h) if raised else bg
        muted = oklch(bgL - 0.035, c * 1.3, h)
        border = oklch(bgL - 0.085, c * 1.4, h)
        stage = oklch(bgL - 0.07, c * 1.3, h)
        fg = oklch(0.2, min(c * 1.5, 0.03), h)
        surfaces = (bg, card, muted)
        mL = nudge(0.55, -0.01, lambda L: min(contrast(oklch(L, c * 1.6, h), s) for s in surfaces) >= 4.6)
        muted_fg = oklch(mL, c * 1.6, h)

    ah, ac = ACCENTS[accent]
    if accent in ("mono", "white", "black"):
        ah, ac = h, min(c, 0.01)
    if dark:
        if accent in WHITE_TEXT_ON_DARK:
            aL = nudge(0.6, -0.01, lambda L: contrast(oklch(L, ac, ah), "#ffffff") >= 4.6)
            acc, acc_fg = oklch(aL, ac, ah), "#ffffff"
            if contrast(acc, bg) < 3:  # too close to the background: go light instead
                accent_light = True
            else:
                accent_light = False
        else:
            accent_light = True
        if accent_light:
            start = 0.92 if accent in ("mono", "white") else 0.74
            dark_fg = oklch(0.17, min(c, 0.02), h)
            aL = nudge(start, 0.01, lambda L: contrast(oklch(L, ac, ah), dark_fg) >= 4.8)
            acc, acc_fg = oklch(aL, ac, ah), dark_fg
    else:
        start = 0.28 if accent in ("mono", "black") else 0.56
        aL = nudge(start, -0.01, lambda L: contrast(oklch(L, ac, ah), "#ffffff") >= 4.8
                   and contrast(oklch(L, ac, ah), bg) >= 4.5)
        acc, acc_fg = oklch(aL, ac, ah), "#ffffff"

    colors = {
        "background": bg, "foreground": fg, "card": card, "muted": muted,
        "muted_foreground": muted_fg, "border": border, "stage": stage,
        "accent": acc, "accent_foreground": acc_fg,
    }
    full = derive(mode, colors)
    bad = contrast_problems(full)
    assert not bad, (family, accent, bad)
    assert contrast(acc, bg) >= 3, (family, accent, contrast(acc, bg))
    return {
        "id": f"{family}-{accent}".lower(),
        "name": f"{family} {accent.title()}",
        "mode": mode, "radius": radius, "font": font, "colors": colors,
    }


HEADER = """\
# Base themes. Generated by api/scripts/make_base_themes.py: edit that and
# run it again rather than editing this file by hand.
#
# Add your own themes in DATA_DIR/themes.yaml (or the file THEMES_FILE names),
# in the same shape. A theme needs id, mode and the colours background,
# foreground and accent; everything else is worked out from those, or taken
# from the theme it extends. Using a base theme's id changes that theme.
#
# Colours: background foreground card card_foreground popover popover_foreground
#   primary primary_foreground secondary secondary_foreground muted
#   muted_foreground stage border input ring accent accent_foreground
#   destructive destructive_foreground warn warn_foreground offline
#   offline_foreground
# radius: corner size in pixels (0-24). font: inter, system, mono or serif.
version: 1
default:
  light: daylight-blue
  dark: zinc-blue
themes:
"""


def dump(themes: list[dict]) -> str:
    out = [HEADER]
    for t in themes:
        out.append(f"  - id: {t['id']}\n    name: {t['name']}\n    mode: {t['mode']}\n"
                   f"    radius: {t['radius']}\n    font: {t['font']}\n    colors:\n")
        for k, v in t["colors"].items():
            out.append(f"      {k}: \"{v}\"\n")
    return "".join(out)


def main() -> None:
    themes = []
    for mode, families in (("dark", DARK), ("light", LIGHT)):
        for family, spec in families.items():
            for accent in spec[6]:
                themes.append(make(family, spec, accent, mode))
    assert len(themes) == 100 and len({t["id"] for t in themes}) == 100
    Path(BASE_FILE).parent.mkdir(exist_ok=True)
    Path(BASE_FILE).write_text(dump(themes))
    print(f"wrote {len(themes)} themes to {BASE_FILE}")


if __name__ == "__main__":
    main()
