"""Reusable Qt widgets, grouped by role.

The public API is unchanged — import widgets straight from this package, e.g.
``from ai_account_hub.ui.widgets import Avatar, make_button, TitleBar``. The
implementation is split into cohesive modules: ``chrome`` (logo, buttons, title
bar + shared token state), ``indicators`` (read-only display widgets), and
``controls`` (interactive controls).
"""

from __future__ import annotations

from .chrome import (
    AccentButton,
    NetworkLogo,
    TitleBar,
    WinButton,
    make_button,
    network_icon,
    set_active_tokens,
)
from .indicators import (
    AccentBar,
    Avatar,
    Dot,
    ElidedLabel,
    FolderTag,
    SeverityBar,
    StatusPill,
)
from .controls import (
    CyclePill,
    SegmentedControl,
    SegmentedSlider,
    Spinner,
    ToggleSwitch,
)

__all__ = [
    "AccentBar", "AccentButton", "Avatar", "CyclePill", "Dot", "ElidedLabel",
    "FolderTag", "NetworkLogo", "SegmentedControl", "SegmentedSlider",
    "SeverityBar", "Spinner", "StatusPill", "TitleBar", "ToggleSwitch",
    "WinButton", "make_button", "network_icon", "set_active_tokens",
]
