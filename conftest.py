"""Pytest configuration.

Its mere presence at the repository root makes pytest add the repo root to
``sys.path`` (prepend import mode), so tests can ``import ai_account_hub`` without
installing the package first. Run the suite from the repo root with ``pytest``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Qt tests must never require a visible display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
