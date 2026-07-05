"""AI Account Hub launcher.

Thin entry point so the app can be started with ``py -3 main.py`` from the repo
root. The real bootstrap lives in :mod:`ai_account_hub.app`. Equivalent to
``python -m ai_account_hub``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when run as a loose script (repo root on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_account_hub.app import main

if __name__ == "__main__":
    raise SystemExit(main())
