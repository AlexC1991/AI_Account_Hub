"""AI Account Hub launcher.

Thin entry point so the app can be started with ``py -3 main.py`` from the repo
root. The real bootstrap lives in :mod:`ai_account_hub.app`. Equivalent to
``python -m ai_account_hub``.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import traceback
from pathlib import Path

# Make the package importable when run as a loose script (repo root on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _log_path() -> Path:
    configured = os.environ.get("AI_HUB_LAUNCH_LOG", "").strip()
    if configured:
        return Path(configured).expanduser()
    launcher_root = os.environ.get("AI_HUB_LAUNCHER_ROOT", "").strip()
    root = Path(launcher_root).expanduser() if launcher_root else Path.home() / ".codex-account-launcher"
    return root / "logs" / "ai-account-hub.log"


def _record_exception(exc_type, value, tb) -> Path:
    target = _log_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            handle.write(f"\n[{stamp}] Unhandled AI Account Hub error\n")
            traceback.print_exception(exc_type, value, tb, file=handle)
    except OSError:
        pass
    return target


def _exception_hook(exc_type, value, tb) -> None:
    target = _record_exception(exc_type, value, tb)
    if sys.stderr is not None:
        traceback.print_exception(exc_type, value, tb)
        return
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                f"AI Account Hub stopped because of an error.\n\nDetails: {target}",
                "AI Account Hub",
                0x10,
            )
        except Exception:
            pass


def _run() -> int:
    sys.excepthook = _exception_hook
    try:
        from ai_account_hub.app import main

        return int(main())
    except BaseException:
        exc_type, value, tb = sys.exc_info()
        _exception_hook(exc_type, value, tb)
        return 1

if __name__ == "__main__":
    raise SystemExit(_run())
