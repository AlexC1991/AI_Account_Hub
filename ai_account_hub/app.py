"""Application bootstrap for AI Account Hub (PySide6 / Qt).

Prefer launching via ``py -3 main.py`` from the repo root, or ``python -m
ai_account_hub``. Both resolve to :func:`main` here.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ai_account_hub.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AI Account Hub")
    window = MainWindow(app)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
