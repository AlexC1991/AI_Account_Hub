# AI Account Hub architecture

Developer notes for the standalone `ai_account_hub` application. For setup and
normal use, see the top-level [`README.md`](../README.md). For target-OS work,
see [`PORTING_MACOS_LINUX.md`](PORTING_MACOS_LINUX.md).

The Hub is a pass-through account dashboard and launcher. It does not proxy
provider traffic, combine credentials, or replace the official provider tools.

## Startup path

All supported Python entry points converge on the same application bootstrap:

```text
Start-AI-Account-Hub.bat (Windows source bootstrap)
             or main.py
             or python -m ai_account_hub
                         |
                         v
              ai_account_hub.app.main()
                         |
                         v
              QApplication + MainWindow
```

`main.py` installs the top-level exception logger. `app.py` owns Qt application
creation and platform application identity. `MainWindow` owns the visible
window, account screen, timers, tray controller, and orderly shutdown.

## Package layout

```text
ai_account_hub/
  app.py                         QApplication bootstrap
  data.py                        UI-facing profile/usage API
  demo_data.py                   explicit AI_HUB_DEMO=1 sample data
  engine.py                      provider discovery and account actions
  engine_claude_desktop.py       Claude Desktop capture/switch mixin

  ui/
    main_window.py               standalone shell and lifecycle
    theme.py, tokens.py          QSS theme manager and design tokens
    account_notifications.py     transition rules and notification settings
    signal_rail.py               custom themed notification overlays
    tray_widget.py               system tray and Best Next popup
    calendar_widget.py           month/week usage and reset calendar
    modals.py                    account/day dialogs
    widgets/                     chrome, controls and indicators
    screens/accounts_screen/     account cards, workers, actions and stats

  core/
    __init__.py                  public backend facade
    hub_core.py                  shared state, limits and profile helpers
    history_db.py                SQLite usage/limit history
    provider_discovery.py        deterministic provider scanner
    browser.py                   isolated browser profiles
    claude_status.py             Claude state readers
    locators.py                  compatibility provider locators
    constants.py, palette.py     shared metadata and legacy palette helpers

scripts/
  codex-account-limits-helper.mjs
                                  Codex app-server rate-limit/usage probe
```

The public package contains the Accounts product. The earlier Coding workbench
and native harness are not shipped in this release.

## Data flow

1. `MainWindow` creates `AccountsScreen`, `TrayController`, and
   `AccountNotificationMonitor`.
2. Account workers call the blocking `data.refresh_one()` API off the UI thread.
3. `data.py` delegates to `HubEngine`, which uses the provider's official CLI,
   desktop state, or documented local files.
4. `hub_core` normalizes limits/state and records history in SQLite.
5. The screen refreshes cards, calendar, stats, detail rail, tray widget, and
   notification monitor from the same profile dictionaries.

No worker should modify Qt widgets. Results return through Qt signals and the UI
thread performs rendering.

## Core facade

UI and engine modules normally use:

```python
from ai_account_hub import core as L
```

`core/__init__.py` re-exports the shared backend API from `hub_core.py` and the
smaller core modules. Tests that patch path constants must patch the underlying
module referenced by `L.mod`; patching only a copied facade attribute does not
change functions that close over `hub_core` globals.

## Provider boundaries

- **Codex**: an isolated `CODEX_HOME` is passed to `codex app-server`. The Node
  helper warms the selected account and reconciles several rate-limit reads,
  because a newly started app-server can briefly expose a default empty window.
  Python also rejects impossible rollovers before a previously advertised reset.
- **Claude Code**: each profile has an isolated config directory. Claude Desktop
  capture/switch logic is separate because CLI and Desktop authentication are
  independent provider sessions.
- **Cursor**: Desktop, shell launcher, and Cursor Agent are distinct discovered
  capabilities. Missing quota fields stay `not exposed`.
- **Antigravity**: Desktop and a healthy standalone `agy` are separate
  capabilities; provider-owned login remains external to the Hub.

Provider discovery proves that software exists and can often answer
`--version`. It does not prove that an account is logged in.

## Tray lifecycle

`TrayController` is created with the main window and owns:

- `QSystemTrayIcon` and its context menu
- the compact Best Next popup
- widget visibility settings
- Signal Rail delivery and the native-message fallback

Minimize hides the main window only when the tray icon is actually available.
Close exits the Hub, stops workers, closes overlays/popups, and removes the tray
icon. The tray's explicit Exit action follows the same shutdown path.

## Notifications

`AccountNotificationMonitor` compares consecutive profile snapshots and emits
structured account events only for meaningful transitions. It latches warnings
to avoid repeating the same low-usage event every refresh.

`SignalRailManager` renders those events as themed `Qt.Tool` windows. It owns at
most three notifications, anchors them to the tray/screen work area, pauses the
timer on hover, and routes card clicks back to the relevant profile. Native
`QSystemTrayIcon.showMessage()` is only a fallback when the custom overlay
cannot be shown.

## Local state and security

Runtime data lives under `AI_HUB_LAUNCHER_ROOT` or the platform default, never
inside the source/package directory. It includes profiles, settings, SQLite
history, browser profiles, logs, generated icons, and desktop-switch state.

The discovery report contains installation paths and versions only. It must not
contain provider tokens, cookies, auth files, or a full environment dump.

## Standalone packaging

A frozen build must bundle provider icons, the Codex Node helper, Qt plugins,
and any public docs still linked by the Help menu. It must resolve those files
through an application-resource helper instead of assuming the source checkout
is beside `__file__`.

User state must remain outside the frozen directory or macOS `.app` bundle. See
the porting guide for the full resource and lifecycle contract.

## Development checks

From the repository root:

```text
python -m compileall -q ai_account_hub
node --check scripts/codex-account-limits-helper.mjs
```

Offscreen boot test:

```text
set QT_QPA_PLATFORM=offscreen
set AI_HUB_LAUNCHER_ROOT=<temporary-directory>
py -3 -c "from PySide6.QtWidgets import QApplication; from ai_account_hub.ui.main_window import MainWindow; app=QApplication([]); MainWindow(app); print('boots')"
```

Never run tests against the maintainer's real launcher root.
