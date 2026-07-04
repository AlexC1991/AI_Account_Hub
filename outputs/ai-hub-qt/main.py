"""Entry point for the AI Account Hub Qt (PySide6) port.

Run:  py main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication

from main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AI Account Hub")
    window = MainWindow(app)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
