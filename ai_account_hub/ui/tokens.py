"""Design tokens for the AI Account Hub Qt port.

Ported verbatim from the design handoff `themes.py` (OKLCH source converted to
sRGB hex). These are the single source of truth for color; screens never
hardcode hex values -- they read from the active theme dict via ThemeManager.
"""

from __future__ import annotations

import colorsys

THEMES: dict[str, dict[str, str]] = {
    "Midnight Slate": {
        "bg": "#070c11", "panel": "#0e1319", "panel2": "#171c23", "panelHover": "#21272e",
        "border": "#282f36", "borderStrong": "#404952",
        "text": "#eceff1", "text2": "#9fa5ac", "text3": "#666d74",
        "accent": "#2c90e8", "accentText": "#fcfcfc",
        "success": "#45b164", "warn": "#e3ae28", "danger": "#e1514e",
    },
    "Emerald Graphite": {
        "bg": "#080d09", "panel": "#0f1410", "panel2": "#171e18", "panelHover": "#212923",
        "border": "#29302a", "borderStrong": "#414b43",
        "text": "#edefed", "text2": "#a0a6a1", "text3": "#676e68",
        "accent": "#3eb268", "accentText": "#040b06",
        "success": "#3eb268", "warn": "#e3ae28", "danger": "#e1514e",
    },
    "Indigo Night": {
        "bg": "#0b0a12", "panel": "#12121a", "panel2": "#1b1b24", "panelHover": "#262530",
        "border": "#302f3a", "borderStrong": "#474654",
        "text": "#eeeef2", "text2": "#a4a3ad", "text3": "#6b6a75",
        "accent": "#9776fb", "accentText": "#fcfcfc",
        "success": "#45b164", "warn": "#e3ae28", "danger": "#e84d66",
    },
    "Warm Carbon": {
        "bg": "#110c08", "panel": "#1a130f", "panel2": "#231c18", "panelHover": "#2f2721",
        "border": "#372e29", "borderStrong": "#51453e",
        "text": "#f2eeea", "text2": "#aba39c", "text3": "#736a63",
        "accent": "#e78b30", "accentText": "#0f0703",
        "success": "#45b164", "warn": "#f2a618", "danger": "#e24947",
    },
    "Crimson Black": {
        "bg": "#060404", "panel": "#0f0808", "panel2": "#1a0f10", "panelHover": "#2a1b1b",
        "border": "#2e2021", "borderStrong": "#4c3738",
        "text": "#f6f0ef", "text2": "#aea1a0", "text3": "#736565",
        "accent": "#e62b34", "accentText": "#fcfcfc",
        "accentGradA": "#f93440", "accentGradB": "#55101d",
        "success": "#3eab5e", "warn": "#f09c17", "danger": "#f8495a",
    },
    "Neon Aurora": {
        "bg": "#07060f", "panel": "#0f0e19", "panel2": "#171724", "panelHover": "#242535",
        "border": "#2a2939", "borderStrong": "#434258",
        "text": "#f1f1f6", "text2": "#aaa9b7", "text3": "#6d6d7b",
        "accent": "#ac77fa", "accentText": "#fcfcfc",
        "accentGradA": "#bc77ff", "accentGradB": "#00bfdf", "accentGradC": "#f35cbc",
        "success": "#2bbb71", "warn": "#e6ad00", "danger": "#f34e6a",
    },
    "Sunset Ember": {
        "bg": "#0f0907", "panel": "#19100e", "panel2": "#241915", "panelHover": "#342521",
        "border": "#382a26", "borderStrong": "#57423e",
        "text": "#f7f0ed", "text2": "#b5a7a2", "text3": "#7a6b66",
        "accent": "#f27636", "accentText": "#120805",
        "accentGradA": "#ff8300", "accentGradB": "#e62845",
        "success": "#45b164", "warn": "#f5a400", "danger": "#ea3b48",
    },
    "Cobalt Chrome": {
        "bg": "#050b0f", "panel": "#0c1419", "panel2": "#131d24", "panelHover": "#1d2b33",
        "border": "#233037", "borderStrong": "#394a55",
        "text": "#eef2f5", "text2": "#a3acb3", "text3": "#667077",
        "accent": "#00a1db", "accentText": "#fcfcfc",
        "accentGradA": "#00c6d8", "accentGradB": "#2e5bda",
        "success": "#45b164", "warn": "#e3ae28", "danger": "#e1514e",
    },
    # Monochrome. In dark mode the accent/text are white on near-black; the
    # light variant flips it to black on near-white (see light_variant()).
    "Black & White": {
        "bg": "#0b0b0b", "panel": "#151515", "panel2": "#1f1f1f", "panelHover": "#2b2b2b",
        "border": "#343434", "borderStrong": "#525252",
        "text": "#f4f4f4", "text2": "#b2b2b2", "text3": "#7c7c7c",
        "accent": "#f4f4f4", "accentText": "#0b0b0b",
        "success": "#57b06e", "warn": "#d9a52c", "danger": "#dd5750",
    },
}

DEFAULT_THEME = "Midnight Slate"

# Provider identity colors (avatar chips), independent of the active theme.
PROVIDER_COLORS = {
    "codex": "#4d92d6",
    "claude": "#c17c4e",
    "cursor": "#a065c9",
    "antigravity": "#a86bd6",
    "api": "#19706b",
}
PROVIDER_LETTERS = {"codex": "CX", "claude": "CC", "cursor": "CU", "antigravity": "AG", "api": "AL"}
PROVIDER_LABELS = {
    "codex": "Codex",
    "claude": "Claude Code",
    "cursor": "Cursor",
    "antigravity": "Antigravity",
    "api": "API",
}

# Project sidebar identity dots, cycled by index, independent of theme.
PROJECT_DOT_COLORS = ["#e8698f", "#4fb37a", "#9d5fd6", "#4a9fd6", "#dcb04a", "#d6614a"]


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = str(color).strip().lstrip("#")
    if len(value) != 6:
        return (0, 0, 0)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgba(color: str, alpha: float) -> str:
    r, g, b = hex_to_rgb(color)
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


def soft(color: str, alpha: float = 0.16) -> str:
    """A *Soft variant: the base color at low alpha (for tinted pill fills)."""
    return rgba(color, alpha)


def severity_color(theme: dict[str, str], percent_left: float | None) -> str:
    """Design 3a severity: <20 red, 20-49 amber, >=50 green."""
    if percent_left is None:
        return theme["text3"]
    if percent_left >= 50:
        return theme["success"]
    if percent_left >= 20:
        return theme["warn"]
    return theme["danger"]


def _to_hls(hexcolor: str) -> tuple[float, float, float]:
    r, g, b = (c / 255 for c in hex_to_rgb(hexcolor))
    return colorsys.rgb_to_hls(r, g, b)


def _from_hls(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def _relative_luminance(color: str) -> float:
    channels = []
    for value in hex_to_rgb(color):
        channel = value / 255
        channels.append(
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio used by theme tests and accessible token derivation."""
    high, low = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (high + 0.05) / (low + 0.05)


def _readable_text(background: str) -> str:
    return (
        "#ffffff"
        if contrast_ratio("#ffffff", background) >= contrast_ratio("#111111", background)
        else "#111111"
    )


def _contrast_accent(accent: str, background: str, minimum: float = 3.0) -> str:
    """Darken a light-mode accent until outlines and selected states are visible."""
    if contrast_ratio(accent, background) >= minimum:
        return accent
    hue, lightness, saturation = _to_hls(accent)
    while lightness > 0.22:
        lightness -= 0.02
        candidate = _from_hls(hue, lightness, saturation)
        if contrast_ratio(candidate, background) >= minimum:
            return candidate
    return _from_hls(hue, 0.22, saturation)


def _text_safe_accent(accent: str, minimum: float = 4.5) -> str:
    """Nudge an accent away from the black/white contrast dead zone."""
    if max(
        contrast_ratio("#ffffff", accent),
        contrast_ratio("#111111", accent),
    ) >= minimum:
        return accent
    hue, lightness, saturation = _to_hls(accent)
    while lightness > 0.18:
        lightness -= 0.01
        candidate = _from_hls(hue, lightness, saturation)
        if contrast_ratio("#ffffff", candidate) >= minimum:
            return candidate
    return _from_hls(hue, 0.18, saturation)


def light_variant(name: str, dark: dict[str, str]) -> dict[str, str]:
    """Derive a properly-designed light-mode token set from a dark theme (not a
    naive inversion). Keeps the accent hue for identity, but builds a real
    surface hierarchy — a light-*gray* base with white cards so panels pop —
    dark readable text, a vivid mid-dark accent that carries white button text
    and reads as text/outline on white, and darkened semantic colors.
    'Black & White' flips to black-on-white."""
    ah, _al, asat = _to_hls(dark.get("accent", "#2c90e8"))
    tint = 0.04 if asat > 0.05 else 0.0
    light = dict(dark)
    light["bg"] = _from_hls(ah, 0.925, tint)          # light-gray base
    light["panel"] = _from_hls(ah, 0.965, tint * 0.7)  # rail / nested surfaces
    light["panel2"] = _from_hls(ah, 1.0, 0.0)          # white cards
    light["panelHover"] = _from_hls(ah, 0.90, tint)    # hover darkens on white
    light["border"] = _from_hls(ah, 0.83, tint)
    light["borderStrong"] = _from_hls(ah, 0.66, tint)
    light["text"] = _from_hls(ah, 0.16, min(0.28, asat))
    light["text2"] = _from_hls(ah, 0.40, min(0.14, asat))
    light["text3"] = _from_hls(ah, 0.50, min(0.10, asat))
    for key in ("success", "warn", "danger"):
        hh, ll, ss = _to_hls(dark.get(key, "#888888"))
        light[key] = _from_hls(hh, min(ll, 0.42), min(1.0, ss + 0.08))
    # Mid-dark, vivid accent: legible as text/outline on white, and white button
    # text sits on it cleanly (so filled CTAs work in every theme).
    ah2, al2, as2 = _to_hls(dark.get("accent", "#2c90e8"))
    light["accent"] = _text_safe_accent(
        _contrast_accent(
            _from_hls(ah2, min(al2, 0.50), max(as2, 0.5)),
            light["bg"],
        )
    )
    light["accentText"] = _readable_text(light["accent"])
    # Drop dark-theme gradient stops so light CTAs use the solid darkened accent.
    for grad_key in ("accentGradA", "accentGradB", "accentGradC"):
        light.pop(grad_key, None)
    if name == "Black & White":
        light.update({
            "bg": "#f1f1f1", "panel": "#f8f8f8", "panel2": "#ffffff", "panelHover": "#e7e7e7",
            "border": "#d9d9d9", "borderStrong": "#b8b8b8",
            "text": "#141414", "text2": "#4c4c4c", "text3": "#767676",
            "accent": "#181818", "accentText": "#ffffff",
        })
    return light


def _lift(hexcolor: str, delta: int) -> str:
    r, g, b = hex_to_rgb(hexcolor)
    return f"#{min(255, r + delta):02x}{min(255, g + delta):02x}{min(255, b + delta):02x}"


# HDR/OLED screens render the near-black base surfaces as harsh, crushed black.
# Lift the whole dark surface ramp uniformly so it reads as deep gray while
# keeping the same relative contrast between bg / panel / panel2 (and their
# borders, which stay lighter still). Applied once at import.
_HDR_LIFT = 13
for _theme in THEMES.values():
    for _key in ("bg", "panel", "panel2", "panelHover"):
        _theme[_key] = _lift(_theme[_key], _HDR_LIFT)
    # Text-bearing selected states use a solid accent. Choose whichever neutral
    # foreground has the stronger contrast for each theme instead of assuming
    # every hue can safely carry white text.
    _theme["accent"] = _text_safe_accent(_theme["accent"])
    _theme["accentText"] = _readable_text(_theme["accent"])
