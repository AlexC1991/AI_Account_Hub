"""Theme and palette layer: design theme tokens, derived color palettes, theme
name normalization, the coding palette, and Windows title-bar theming.

Self-contained (imports only ctypes/sys). The rest of the backend pulls these
names in via ``from .palette import *`` in hub_core.
"""

from __future__ import annotations

import ctypes
import logging
import sys

_logger = logging.getLogger(__name__)

DESIGN_THEME_TOKENS = {
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
}


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = str(color).strip().lstrip("#")
    if len(value) != 6:
        return (0, 0, 0)
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _blend_hex(foreground: str, background: str, alpha: float = 0.18) -> str:
    fg = _hex_to_rgb(foreground)
    bg = _hex_to_rgb(background)
    mixed = tuple(round(bg[index] + (fg[index] - bg[index]) * alpha) for index in range(3))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _theme_palette(tokens: dict[str, str]) -> dict[str, str]:
    panel = tokens["panel"]
    panel2 = tokens["panel2"]
    accent = tokens["accent"]
    return {
        "BG": tokens["bg"],
        "PANEL": panel,
        "PANEL_ALT": panel2,
        "PANEL_HOVER": tokens["panelHover"],
        "INK": tokens["text"],
        "MUTED": tokens["text2"],
        "TEXT_FAINT": tokens["text3"],
        "LINE": tokens["border"],
        "LINE_STRONG": tokens["borderStrong"],
        "PRIMARY": accent,
        "PRIMARY_HOVER": tokens.get("accentGradB", accent),
        "PRIMARY_TEXT": tokens["accentText"],
        "GREEN": tokens["success"],
        "GREEN_SOFT": _blend_hex(tokens["success"], panel, 0.18),
        "RED": tokens["danger"],
        "RED_SOFT": _blend_hex(tokens["danger"], panel, 0.18),
        "AMBER": tokens["warn"],
        "AMBER_SOFT": _blend_hex(tokens["warn"], panel, 0.18),
        "BLUE": accent,
        "BLUE_SOFT": _blend_hex(accent, panel, 0.18),
        "DARK": tokens["bg"],
        "ACCENT_GRAD_A": tokens.get("accentGradA", accent),
        "ACCENT_GRAD_B": tokens.get("accentGradB", accent),
        "ACCENT_GRAD_C": tokens.get("accentGradC", ""),
        "METER_BG": _blend_hex(tokens["borderStrong"], panel2, 0.34),
        "CALENDAR_OUTSIDE": _blend_hex(tokens["border"], tokens["bg"], 0.26),
        "CARD_SELECTED": _blend_hex(accent, panel, 0.22),
        "CARD_HAIRLINE": tokens["border"],
    }


LEGACY_LIGHT_PALETTE = {
    "BG": "#edf2ef",
    "PANEL": "#ffffff",
    "PANEL_ALT": "#f8faf8",
    "PANEL_HOVER": "#eef3ef",
    "INK": "#17211c",
    "MUTED": "#647269",
    "TEXT_FAINT": "#8a9690",
    "LINE": "#d8e0da",
    "LINE_STRONG": "#b8c7bf",
    "PRIMARY": "#2b7c4b",
    "PRIMARY_HOVER": "#256d42",
    "PRIMARY_TEXT": "#ffffff",
    "GREEN": "#2b7c4b",
    "GREEN_SOFT": "#e0f3e7",
    "RED": "#b42318",
    "RED_SOFT": "#ffe5e3",
    "AMBER": "#9a5d00",
    "AMBER_SOFT": "#fff0cd",
    "BLUE": "#236f95",
    "BLUE_SOFT": "#e2f1f7",
    "DARK": "#1c2922",
    "ACCENT_GRAD_A": "#2b7c4b",
    "ACCENT_GRAD_B": "#256d42",
    "ACCENT_GRAD_C": "",
    "METER_BG": "#e8eee9",
    "CALENDAR_OUTSIDE": "#f7faf8",
    "CARD_SELECTED": "#f4fbf7",
    "CARD_HAIRLINE": "#e2e9e4",
}


MOCK_THEME_PALETTES = {"Light": LEGACY_LIGHT_PALETTE, **{name: _theme_palette(tokens) for name, tokens in DESIGN_THEME_TOKENS.items()}}
THEME_CHOICES = tuple(MOCK_THEME_PALETTES.keys())
THEME_ALIASES = {"light": "Light", "dark": "Midnight Slate"}


def normalize_theme_name(theme_name: object) -> str:
    text = str(theme_name or "").strip()
    return THEME_ALIASES.get(text.lower(), text if text in MOCK_THEME_PALETTES else "Midnight Slate")


def is_dark_theme(theme_name: object) -> bool:
    return normalize_theme_name(theme_name) != "Light"


PRIMARY = "#2995ff"
PRIMARY_HOVER = "#1d7bd6"
PRIMARY_TEXT = "#fcfcfc"
CARD_SELECTED = "#f4fbf7"
CARD_SELECTED_DARK = "#172c41"
CARD_HAIRLINE = "#e2e9e4"
CARD_HAIRLINE_DARK = "#334047"
CALENDAR_OUTSIDE = "#f7faf8"
CALENDAR_OUTSIDE_DARK = "#171c21"
CALENDAR_HEADER = "#f8faf8"
CALENDAR_HEADER_DARK = "#192127"
METER_BG = "#e8eee9"
METER_BG_DARK = "#2b353b"


def coding_palette(theme_name: str) -> dict[str, str]:
    if is_dark_theme(theme_name):
        theme = MOCK_THEME_PALETTES[normalize_theme_name(theme_name)]
        return {
            "bg": theme["BG"],
            "rail": theme["PANEL"],
            "panel": theme["PANEL_ALT"],
            "panel_alt": theme["PANEL"],
            "active": theme["PANEL_ALT"],
            "field": theme["PANEL_ALT"],
            "composer": theme["PANEL"],
            "ink": theme["INK"],
            "muted": theme["MUTED"],
            "faint": theme["LINE_STRONG"],
            "line": theme["LINE"],
            "line_strong": theme["LINE_STRONG"],
        }
    return {
        "bg": "#f6f8f7",
        "rail": "#eef2ef",
        "panel": "#ffffff",
        "panel_alt": "#f8faf8",
        "active": "#e7ede9",
        "field": "#ffffff",
        "composer": "#ffffff",
        "ink": "#17211c",
        "muted": "#647269",
        "faint": "#8a9690",
        "line": "#d8e0da",
        "line_strong": "#b8c7bf",
    }


def apply_theme(theme_name: str) -> None:
    theme = MOCK_THEME_PALETTES[normalize_theme_name(theme_name)]
    globals().update(theme)
    globals().update(
        {
            "CARD_SELECTED_DARK": theme["CARD_SELECTED"],
            "CARD_HAIRLINE_DARK": theme["CARD_HAIRLINE"],
            "CALENDAR_OUTSIDE_DARK": theme["CALENDAR_OUTSIDE"],
            "CALENDAR_HEADER_DARK": theme["PANEL_ALT"],
            "METER_BG_DARK": theme["METER_BG"],
        }
    )


def configure_windows_titlebar(window, theme_name: str) -> None:
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        raw_hwnd = int(window.winfo_id())
        user32 = ctypes.windll.user32
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetAncestor.restype = ctypes.c_void_p
        handles = [raw_hwnd]
        for candidate in (user32.GetParent(raw_hwnd), user32.GetAncestor(raw_hwnd, 2)):
            candidate_int = int(candidate or 0)
            if candidate_int and candidate_int not in handles:
                handles.append(candidate_int)
        enabled = ctypes.c_int(1 if is_dark_theme(theme_name) else 0)
        if is_dark_theme(theme_name):
            caption = ctypes.c_int(0x001F1A15)
            text = ctypes.c_int(0x00F3F5F2)
        else:
            caption = ctypes.c_int(0x00EFF2ED)
            text = ctypes.c_int(0x001C2117)
        for handle in handles:
            hwnd = ctypes.c_void_p(handle)
            for attr in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    ctypes.c_int(attr),
                    ctypes.byref(enabled),
                    ctypes.sizeof(enabled),
                )
            for attr, value in ((35, caption), (36, text)):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    ctypes.c_int(attr),
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
    except Exception:
        _logger.debug("configure_windows_titlebar failed", exc_info=True)
        return


