"""Access point for the shared, Tk-free backend logic.

Imports `hub_core` (the extracted backend — constants, provider probes/refresh,
limit parsing, usage history, discovery reuse, launch/browser helpers) and
re-exposes it. hub_core has no tkinter dependency, so the Qt app depends only on
pure backend logic — the old Tkinter GUI has been removed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parent.parent / "ai-hub-calendar-gui"
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

import hub_core as mod  # the shared backend module

# Mirror every public attribute so `legacy_backend.NAME` works for anything,
# without maintaining an allow-list. Callers can also use `mod.NAME` directly.
for _name in dir(mod):
    if not _name.startswith("__"):
        globals()[_name] = getattr(mod, _name)

# Sanity: a couple of load-bearing helpers must exist, else the port is broken.
for _required in ("effective_state", "account_plan_label", "run_capture", "load_profiles"):
    if not hasattr(mod, _required):
        raise ImportError(f"hub_core is missing expected helper: {_required}")
