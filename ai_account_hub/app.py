"""Application bootstrap shared by source and packaged standalone launches.

Prefer launching via ``py -3 main.py`` from the repo root, or ``python -m
ai_account_hub``. Frozen macOS/Linux/Windows builds resolve to :func:`main`
through the same thin entry point.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from ai_account_hub.ui.main_window import MainWindow


def _set_windows_app_id() -> None:
    """Give tray notifications and taskbar grouping a stable application ID."""

    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "AlexC1991.AIAccountHub"
        )
    except Exception:
        pass


def _instance_name() -> str:
    """Scope the single-instance channel to the Hub's local data root."""
    configured = os.environ.get("AI_HUB_LAUNCHER_ROOT", "").strip()
    root = Path(configured).expanduser() if configured else Path.home() / ".codex-account-launcher"
    digest = hashlib.sha256(str(root.resolve()).casefold().encode("utf-8")).hexdigest()[:16]
    return f"AIAccountHub-{digest}"


def _notify_existing_instance(name: str) -> bool:
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(250):
        return False
    socket.write(b"show")
    socket.flush()
    socket.waitForBytesWritten(250)
    socket.disconnectFromServer()
    return True


def _claim_single_instance(app: QApplication) -> QLocalServer | None:
    """Own the local channel, or focus the existing Hub and return ``None``."""
    name = _instance_name()
    if _notify_existing_instance(name):
        return None
    server = QLocalServer(app)
    if server.listen(name):
        return server
    # A crashed process can leave a stale Unix socket on non-Windows systems.
    # Recheck before removing it so two simultaneous launches cannot both win.
    if _notify_existing_instance(name):
        return None
    QLocalServer.removeServer(name)
    if not server.listen(name):
        raise RuntimeError(f"Could not create the AI Account Hub instance channel: {server.errorString()}")
    return server


def main() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("AI Account Hub")
    instance_server = _claim_single_instance(app)
    if instance_server is None:
        return 0
    window = MainWindow(app)

    def show_existing_window() -> None:
        while instance_server.hasPendingConnections():
            connection = instance_server.nextPendingConnection()
            connection.readAll()
            connection.disconnectFromServer()
        window._restore_from_tray()

    instance_server.newConnection.connect(show_existing_window)
    # Keep both objects alive for the full event loop. Qt parent ownership is
    # sufficient for the server, while these attributes make that explicit.
    app._ai_hub_instance_server = instance_server
    app._ai_hub_window = window
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
