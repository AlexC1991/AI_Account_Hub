"""Shared setup, fixtures, and helpers for the Qt UI test suites. Not collected
by pytest (no test_ prefix); imported by test_qt_port.py and test_claude_desktop.py."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
import datetime as _dt
from types import SimpleNamespace
from pathlib import Path


TEST_ROOT = tempfile.TemporaryDirectory()
os.environ["AI_HUB_LAUNCHER_ROOT"] = TEST_ROOT.name
os.environ["AI_HUB_DISCOVERY_BOOTSTRAPPED"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTextEdit

from ai_account_hub import data
from ai_account_hub import core as L
from ai_account_hub.ui.main_window import MainWindow
from ai_account_hub.coding_bridge import CodingBridge
from ai_account_hub.engine import HubEngine
from ai_account_hub.ui.modals import AddProfileDialog

# Re-export everything the test modules use (incl. underscore-prefixed helpers/
# aliases that ``import *`` would otherwise skip) so ``from _qt_common import *``
# is enough in each test file.
__all__ = [
    "json", "os", "shutil", "sqlite3", "tempfile", "time", "_dt",
    "SimpleNamespace", "Path", "TEST_ROOT",
    "QApplication", "QTextEdit",
    "data", "L", "MainWindow", "CodingBridge", "HubEngine", "AddProfileDialog",
    "_write_cookie_db", "_write_claude_code_state", "_profiles", "_window",
]


def _write_cookie_db(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute("create table cookies (host_key text, name text, expires_utc integer, value text)")
        con.execute(
            "insert into cookies(host_key, name, expires_utc, value) values (?, ?, ?, ?)",
            (".claude.ai", "sessionKey", 99999999999999999, value),
        )
        con.commit()
    finally:
        con.close()


def _write_claude_code_state(home: Path, account_uuid: str, email: str = "user@example.com") -> None:
    home.mkdir(parents=True, exist_ok=True)
    payload = {
        "oauthAccount": {
            "accountUuid": account_uuid,
            "emailAddress": email,
            "organizationUuid": f"org-{account_uuid}",
            "organizationType": "claude_pro",
        }
    }
    (home / ".claude.json").write_text(json.dumps(payload), encoding="utf-8")


def _profiles() -> list[dict]:
    root = Path(TEST_ROOT.name)
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    codex_home = root / "codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    claude_home = root / "claude"
    claude_home.mkdir(parents=True, exist_ok=True)
    _write_claude_code_state(claude_home, "claude-test-account")
    return [
        L.normalize_profile(
            {
                "id": "codex:test",
                "name": "Codex Test",
                "provider": "codex",
                "codexHome": str(codex_home),
                "workspace": str(workspace),
                "accountPlan": "Plus",
                "shortLimitUsedPercent": "25",
                "weeklyLimitUsedPercent": "40",
            },
            0,
        ),
        L.normalize_profile(
            {
                "id": "claude:test",
                "name": "Claude Test",
                "provider": "claude",
                "codexHome": str(claude_home),
                "claudeConfigDir": str(claude_home),
                "workspace": str(workspace),
                "accountPlan": "Pro",
                "shortLimitUsedPercent": "10",
                "weeklyLimitUsedPercent": "20",
                "usageSummary": {"claudeAuthStatus": {"loggedIn": True}},
            },
            1,
        ),
    ]



def _window() -> tuple[QApplication, MainWindow]:
    app = QApplication.instance() or QApplication([])
    data._ENGINE = None
    L.save_profiles(_profiles())
    L.save_settings(
        {
            "theme": "Midnight Slate",
            "autoRefreshEnabled": True,
            "autoRefreshMinutes": 10,
            "sortMode": "Manual",
            "cardTemplate": "Balanced",
        }
    )
    window = MainWindow(app)
    window.show()
    app.processEvents()
    return app, window


