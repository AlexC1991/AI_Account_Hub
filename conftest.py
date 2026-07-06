"""Pytest configuration.

Its mere presence at the repository root makes pytest add the repo root to
``sys.path`` (prepend import mode), so tests can ``import ai_account_hub`` without
installing the package first. Run the suite from the repo root with ``pytest``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# SAFETY: never let a test touch the user's real launcher directory
# (~/.codex-account-launcher — where the live profiles.json / history DB live).
# conftest runs before any test module is imported, so setting this here forces
# a throwaway launcher root *before* the backend reads AI_HUB_LAUNCHER_ROOT at
# import time, regardless of collection order or single-file runs. Only override
# when it is unset or still points at the real default (respect an explicit CI
# temp path).
_real_default = Path.home() / ".codex-account-launcher"
_current = os.environ.get("AI_HUB_LAUNCHER_ROOT", "").strip()
if not _current or Path(_current) == _real_default:
    os.environ["AI_HUB_LAUNCHER_ROOT"] = tempfile.mkdtemp(prefix="ai-hub-test-launcher-")

# Qt tests must never require a visible display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
