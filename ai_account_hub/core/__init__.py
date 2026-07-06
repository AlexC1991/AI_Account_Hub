"""Tk-free backend for AI Account Hub.

The bulk of the backend lives in :mod:`ai_account_hub.core.hub_core` (constants,
provider probes/refresh, limit parsing, usage history, discovery reuse, and
launch/browser helpers). This package re-exposes every public ``hub_core``
attribute so callers can simply do ``from ai_account_hub import core`` and use
``core.NAME`` — the historical access point (formerly ``legacy_backend``).
"""

from __future__ import annotations

from . import hub_core
# Leaf domains extracted from hub_core (each does ``from hub_core import *``
# internally and adds its own functions). Mirrored here too so ``core.NAME``
# still exposes the full public API regardless of which module a function lives
# in now.
from . import history_db, browser, locators, claude_status

# ``mod`` is the underlying hub_core module (historical name from the old
# ``legacy_backend`` shim). Patch attributes on ``core.mod`` to affect the real
# backend module (``core.NAME`` below is only a mirrored snapshot).
mod = hub_core

# Mirror every public attribute so ``core.NAME`` works for anything, without
# maintaining an allow-list. ``core.hub_core.NAME`` / ``core.mod.NAME`` too.
for _src in (hub_core, history_db, browser, locators, claude_status):
    for _name in dir(_src):
        if not _name.startswith("__"):
            globals()[_name] = getattr(_src, _name)

# Sanity: a few load-bearing helpers must exist, else the backend is broken.
for _required in ("effective_state", "account_plan_label", "run_capture", "load_profiles"):
    if not hasattr(hub_core, _required):
        raise ImportError(f"hub_core is missing expected helper: {_required}")

del _src, _name, _required
