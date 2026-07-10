# Porting AI Account Hub to macOS and Linux

AI Account Hub currently ships as a Windows-first standalone PySide6
application. Its runtime path is:

```text
main.py -> ai_account_hub.app.main() -> QApplication -> MainWindow
```

The supported UI is the Accounts dashboard. The old Coding workbench and
native harness modules are not part of the public package. Provider commands,
account refreshes, desktop switching, the system-tray widget, and Signal Rail
notifications are owned by the modules under `ai_account_hub/`.

This document is the implementation contract for a real macOS or Linux port.
The repository does not yet contain a signed `.app`, AppImage, or distro
package, so do not describe either platform as supported until the acceptance
matrix at the end passes on that operating system.

## Non-negotiables

- Keep the Hub a pass-through dashboard and launcher. Official provider tools
  continue to own authentication, prompts, models, context, and execution.
- Never merge credentials or copy auth state between unrelated accounts.
- Use supported per-profile home/config paths such as `CODEX_HOME` and
  `CLAUDE_CONFIG_DIR`.
- Show `not exposed` when a provider does not publish quota, plan, or history.
- Keep profiles, history, browser sessions, logs, and generated discovery data
  outside the application bundle and outside Git.
- A missing provider, tray host, notification service, or optional desktop app
  must reduce capability without preventing the Hub from opening.

## Current application layout

```text
main.py                         thin exception-logging entry point
ai_account_hub/app.py           QApplication bootstrap
ai_account_hub/data.py          UI-facing profile and usage API
ai_account_hub/engine.py        provider discovery, refresh, launch and actions
ai_account_hub/engine_claude_desktop.py
                                Claude Desktop capture/switch flow
ai_account_hub/core/            state, history, browser and discovery logic
ai_account_hub/ui/main_window.py
                                standalone window and lifecycle
ai_account_hub/ui/tray_widget.py
                                QSystemTrayIcon and Best Next popup
ai_account_hub/ui/signal_rail.py
                                custom account notifications
scripts/codex-account-limits-helper.mjs
                                Codex app-server rate-limit reader
```

There are two different launch products to account for:

1. **Source/development launch**: Python and dependencies are installed on the
   machine, then `python3 -m ai_account_hub` starts the GUI.
2. **Packaged standalone launch**: a native `.app`, Linux binary directory, or
   later AppImage contains Python, Qt, the Hub package, and required resources.

The Windows batch file is only a source-checkout bootstrap. A packaged macOS or
Linux application must not open a terminal or depend on that batch file.

## Already portable

- The main UI, themes, account cards, calendar, SQLite history, workers, and
  Signal Rail renderer use PySide6/Python APIs.
- `provider_discovery.py` already has Windows, macOS, and Linux candidate sets.
- `Path.home()` and `AI_HUB_LAUNCHER_ROOT` make the runtime root configurable.
- Windows subprocess flags use `getattr(..., 0)` and become no-ops on POSIX.
- `QSystemTrayIcon.isSystemTrayAvailable()` already prevents minimize-to-tray
  from hiding the window when no tray is present.
- Provider absence is represented as a missing capability, not a startup error.

Portable Qt code still needs target-OS testing. In particular, tray geometry,
frameless windows, always-on-top tool windows, and global popup placement vary
between Cocoa, X11, and Wayland.

## Platform work inventory

| Area | Current location | Windows behavior | Port requirement |
|---|---|---|---|
| Open files/folders | `ui/main_window.py`, `engine.py`, `engine_claude_desktop.py` | `os.startfile` | Route through `QDesktopServices.openUrl(QUrl.fromLocalFile(...))`. |
| Desktop launch/stop | `engine.py`, `engine_claude_desktop.py` | PowerShell, AppX, Explorer and Windows process inspection | Add provider-specific Cocoa/Linux implementations behind one adapter. |
| Provider fallback locators | `core/locators.py`, `core/hub_core.py` | AppX, Registry/WindowsApps and `.exe` paths | Keep shared discovery first; replace compatibility fallbacks per OS. |
| Terminal launch | `engine.py` | PowerShell with a visible console | macOS Terminal/iTerm or a configured terminal; Linux terminal argv without shell interpolation. |
| Window integration | `core/palette.py`, `app.py`, `main.py` | DWM title-bar calls, AppUserModelID and Win32 crash dialog | Make these explicit no-ops on POSIX and provide native bundle metadata. |
| Browser profiles | `core/browser.py` | Chrome-family Windows paths and Win32 shared reads | Add native executable paths; keep isolated `--user-data-dir` profiles. |
| Claude Desktop state | `engine_claude_desktop.py`, `core/claude_status.py` | `%APPDATA%/Claude` and Windows processes | Map Application Support/XDG paths and provider process identity. |
| Codex helper resource | `core/hub_core.py` `HELPER_PATH` | Reads `scripts/...mjs` beside the source checkout | Resolve it from packaged application resources and include it in every build. |
| Public docs/resources | `ui/main_window.py` Help actions | Reads `README.md` and `docs/` beside the checkout | Bundle those files or hide unavailable Help actions in packaged builds. |

## Add a platform adapter

Create one module, for example `ai_account_hub/platform_adapter.py`, and move
operating-system decisions behind a small typed API:

```python
class PlatformAdapter:
    def open_path(self, path: Path) -> bool: ...
    def open_url(self, url: str) -> bool: ...
    def launch_terminal(self, argv: list[str], env: dict[str, str], cwd: Path) -> dict: ...
    def launch_desktop(self, provider: str, target: Path, workspace: Path) -> dict: ...
    def stop_desktop(self, provider: str) -> dict: ...
    def provider_state_paths(self, provider: str) -> dict[str, Path]: ...
    def app_resource(self, relative: str) -> Path: ...
    def signal_rail_fallback_corner(self) -> str: ...
```

Rules:

- Always pass subprocess arguments as a list. Never concatenate paths or
  profile data into shell commands.
- Return structured diagnostics instead of swallowing errors.
- Never stop a process by a broad name when an executable/bundle identity can
  be verified.
- Use `QDesktopServices` for normal URLs/files before shelling out to `open` or
  `xdg-open`.
- Keep Windows behavior in the Windows adapter instead of scattering new
  `sys.platform` checks through UI code.

## Runtime data paths

Prefer `QStandardPaths` in the adapter, while preserving
`AI_HUB_LAUNCHER_ROOT` as the test/portable override.

| Data | Windows today | macOS target | Linux target |
|---|---|---|---|
| Profiles/settings/history | `~/.codex-account-launcher` | `~/Library/Application Support/AI Account Hub` | `${XDG_DATA_HOME:-~/.local/share}/ai-account-hub` |
| Logs/state report | same runtime root | `~/Library/Logs/AI Account Hub` or app state root | `${XDG_STATE_HOME:-~/.local/state}/ai-account-hub` |
| Cache/icons | same runtime root | `~/Library/Caches/AI Account Hub` | `${XDG_CACHE_HOME:-~/.cache}/ai-account-hub` |
| Claude Desktop | `%APPDATA%/Claude` | `~/Library/Application Support/Claude` | `${XDG_CONFIG_HOME:-~/.config}/Claude` |
| Cursor state | `%APPDATA%/Cursor` | `~/Library/Application Support/Cursor` | `${XDG_CONFIG_HOME:-~/.config}/Cursor` |

Do not silently move existing user data. If the port changes a default path,
implement an explicit one-time migration with a backup and a diagnostic log.

## Source launcher

The source launcher should fail clearly if dependency installation fails. Do
not use `pip install ... || true`, because that hides the actual startup error.

```sh
#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON=${PYTHON:-python3}
VENV=${AI_HUB_VENV:-"$ROOT/.venv"}

if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"
"$VENV/bin/python" "$ROOT/ai_account_hub/core/provider_discovery.py" \
  --write-report --quiet || true

cd "$ROOT"
exec "$VENV/bin/python" -m ai_account_hub
```

Provider discovery is allowed to fail because the GUI can rescan and show
missing capabilities. Python/PySide installation is not allowed to fail
silently.

## Packaged standalone application

Qt for Python provides `pyside6-deploy`, which creates a native `.app` on macOS
and a Linux binary. Start with its `standalone` directory mode because this Hub
has external resources and a Node helper; move to one-file packaging only after
resource resolution is proven.

Build independently on each target operating system:

```sh
python3 -m venv .venv-build
. .venv-build/bin/activate
python -m pip install -r requirements.txt
python -m pip install nuitka
pyside6-deploy main.py --init
# Set mode=standalone, title, icon and output paths in pysidedeploy.spec.
pyside6-deploy -c pysidedeploy.spec
```

Every standalone build must contain:

- `ai_account_hub/assets/*.png`
- `scripts/codex-account-limits-helper.mjs`
- Qt platform/image/icon plugins selected by the deployment tool
- `README.md` and public `docs/*.md` if Help-menu links remain visible
- an `.icns` app icon on macOS and installed PNG/icon-theme assets on Linux

The build must not contain:

- `profiles.json`, auth files, cookies, browser profiles, history databases, or
  the developer's discovery report
- `.claude/`, `.codex-accounts/`, Local Storage, IndexedDB, logs, tests, or demo
  captures containing personal data

`HELPER_PATH` currently assumes a source checkout. Before packaging, route it
through `PlatformAdapter.app_resource()` and verify that spaces and non-ASCII
characters in the installed app path work. Node.js remains an external Codex
quota-probe dependency unless a later release deliberately bundles a supported
Node runtime.

Useful official deployment references:

- [Qt for Python deployment](https://doc.qt.io/qtforpython-6/deployment/index.html)
- [pyside6-deploy](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html)
- [Qt macOS deployment](https://doc.qt.io/qt-6/macos-deployment.html)
- [Qt Linux deployment](https://doc.qt.io/qt-6/linux-deployment.html)

## System tray and application lifecycle

`TrayController` uses `QSystemTrayIcon` and must remain capability-driven:

1. Call `QSystemTrayIcon.isSystemTrayAvailable()` before enabling hide-to-tray.
2. If unavailable, normal minimize must leave a recoverable taskbar/dock window.
3. Keep an explicit **Exit AI Account Hub** menu action that shuts down workers,
   Signal Rail windows, the popup, and the tray icon.
4. Keep close-button behavior distinct from minimize behavior. The current Hub
   exits on Close and hides only after Minimize when a tray exists.
5. Do not rely on double-click to restore on macOS; Qt documents that double
   click is unavailable when a context menu is attached. Single-click Trigger
   and the menu's Open action must both work.

Platform expectations:

- **macOS**: the tray icon is a menu-bar status item. Anchor Best Next below the
  status bar and stack Signal Rail downward from the top-right work area.
- **Linux/X11**: anchor to the tray geometry when the desktop exposes it.
- **Linux/Wayland**: global window placement and tray geometry can be restricted
  by the compositor. Detect invalid geometry and use the screen work-area
  fallback; do not keep moving the popup to the cursor.
- **No tray host**: keep the main window available and leave tray-only controls
  disabled. The Accounts dashboard and automatic refresh must still work.

Test late tray availability too. Qt can add a visible `QSystemTrayIcon` when a
tray appears after startup, but the Hub still needs to resync its popup and
availability state.

## Signal Rail notifications

Signal Rail is a Hub-owned Qt overlay, not an operating-system notification:

- `SignalRailToast` is a frameless `Qt.Tool` window with the current Hub theme.
- `SignalRailManager` shows at most three cards, pauses timeout on hover, and
  opens the relevant profile when clicked.
- The tray icon geometry chooses the monitor and corner. If geometry is invalid,
  use a platform-aware work-area corner (top-right on macOS; bottom-right on
  Windows and most Linux desktops).
- The native `QSystemTrayIcon.showMessage()` path is a fallback only. Qt warns
  that system messages can be suppressed by desktop settings, so critical state
  must also remain visible in the account card/activity history.

Port tests must cover:

- dark/light plus every Hub theme
- 100%, warning, 0%, ready, and long account/reset labels
- one, two, three, and overflow notifications
- hover pause, dismiss, account activation, and timeout cleanup
- top/bottom taskbars, macOS menu bar, multi-monitor and high-DPI scaling
- X11 and at least one Wayland compositor
- full-screen/Spaces behavior on macOS
- no extra taskbar or Dock entry for each toast

If a compositor cannot place the custom overlay reliably, disable Signal Rail
there and use the platform notification service plus in-app history. Do not show
the same event through both paths.

## Provider discovery and launch targets

- **Codex**: find `codex` on `PATH`, `~/.local/bin`, Homebrew prefixes, and the
  macOS `Codex.app` bundle when installed. Preserve `CODEX_HOME`. Linux desktop
  support must not be claimed unless a real desktop target is discovered.
- **Claude Code**: find `claude` independently from Claude Desktop. Preserve
  `CLAUDE_CONFIG_DIR`; implement Desktop capture only after the target OS state
  paths and process lifecycle are verified.
- **Cursor**: keep Desktop, shell launcher, and `cursor-agent` as separate
  capabilities. Continue showing quota as not exposed when its CLI omits it.
- **Antigravity**: find a healthy `agy`, not a similarly named broken shim.
  Desktop login remains provider-owned.

macOS desktop bundles must be launched through `open -a`/bundle APIs rather than
executing the `.app` directory. Linux desktop targets should use a verified
executable or desktop entry. A provider installed while the Hub is running must
appear after restart; if the port adds an explicit **Rescan installations**
command, it must call the same shared scanner.

## Browser profiles and cookies

Keep one isolated browser user-data directory per Hub profile. Add native
browser candidates such as:

```text
macOS Chrome: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
macOS Edge:   /Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge
Linux:        google-chrome | chromium | chromium-browser | microsoft-edge
```

The Windows Claude cookie-seeding path uses Windows file/credential behavior.
Do not copy it mechanically to macOS or Linux. Keychain/libsecret integration
requires a separate security review. Until then, open the isolated browser and
ask for one manual login; never claim that provider CLI OAuth is a reusable web
cookie.

## Window chrome

The Hub currently uses `Qt.FramelessWindowHint` and its own title bar. Decide per
platform:

- macOS may use a native frame/traffic lights while retaining the Hub header.
- Linux may use native decorations so window-manager snapping and accessibility
  continue to work.
- Windows DWM helpers must remain guarded and no-op on POSIX.

Test keyboard movement, screen-reader names, maximize/restore, high DPI, and
multiple desktops before retaining the custom frame on a target OS.

## Test commands

Source-checkout smoke test:

```sh
python3 -m compileall -q ai_account_hub

AI_HUB_LAUNCHER_ROOT="$(mktemp -d)" QT_QPA_PLATFORM=offscreen \
python3 -c 'from PySide6.QtWidgets import QApplication; from ai_account_hub.ui.main_window import MainWindow; app=QApplication([]); window=MainWindow(app); print("boots")'
```

Run platform tests with a temporary launcher root. Never point CI at a real
home directory containing provider sessions.

## Acceptance matrix

A target is complete only when all of these pass on native hardware or a real
desktop VM:

| Area | macOS | Linux X11 | Linux Wayland |
|---|---:|---:|---:|
| Source launch and frozen standalone launch | Required | Required | Required |
| Fresh first run with no providers | Required | Required | Required |
| Provider installed after startup + restart/rescan | Required | Required | Required |
| Correct data/config/cache paths | Required | Required | Required |
| Desktop, CLI, Online and Open Folder actions | Required | Required | Required |
| Tray available and tray unavailable behavior | Required | Required | Required |
| Best Next placement and account switching | Required | Required | Required |
| Signal Rail placement, stack and click behavior | Required | Required | Required/fallback |
| Auto refresh while main window is hidden | Required | Required | Required |
| Packaged resources and Codex helper resolution | Required | Required | Required |
| No console/terminal left open | Required | Required | Required |
| No private data inside build artifact | Required | Required | Required |

Finally, build and test each artifact on its target OS. A Windows build passing
offscreen Qt tests is useful, but it is not evidence that Cocoa, X11, Wayland,
bundle signing, or native provider launching works.
