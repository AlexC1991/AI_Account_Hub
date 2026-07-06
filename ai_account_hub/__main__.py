"""Allow ``python -m ai_account_hub`` to launch the GUI."""

from __future__ import annotations

from ai_account_hub.app import main

if __name__ == "__main__":
    raise SystemExit(main())
