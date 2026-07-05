# Porting AI Account Hub to macOS and Linux

AI Account Hub is a **Windows-first PySide6 (Qt) desktop app** shipped as the
`ai_account_hub` package. The UI is `ai_account_hub/ui/`, the Tk-free shared
backend is `ai_account_hub/core/` (`hub_core.py`, `provider_discovery.py`), and
the native transports are in `ai_account_hub/harness/` (`native_harness.py`,
`claude_permission_bridge.py`). Provider path discovery is already
cross-platform, but several backend readers, process/desktop control, the
frameless title bar, and the browser cookie-seeding path still assume Windows.

This guide is the remaining work to run the whole GUI on macOS or Linux. Do not
call a port complete just because the provider binaries were found.

## Non-negotiables

- Keep the app a pass-through launcher and dashboard. Provider tools own prompts,
  models, context, tool execution, and session history.
- Do not pool, copy, scrape, or replay auth tokens across providers or accounts.
  Isolate accounts only through each provider's supported home/config paths.
- Show honest capability states. If a provider does not expose quota/plan/history
  locally, label it "not exposed" instead of inventing values.
- Keep local account state outside the repository and out of Git.

## What is already cross-platform

- **Base paths** in `hub_core.py` derive from `Path.home()` / env, so they resolve
  per-user on every OS: `DEFAULT_CODEX_HOME=~/.codex`, `CLAUDE_CLI_HOME=~/.claude`,
  `CURSOR_HOME=~/.cursor`, `LAUNCHER_ROOT=$AI_HUB_LAUNCHER_ROOT or
  ~/.codex-account-launcher`.
- **`CREATE_NO_WINDOW`** is `getattr(subprocess, "CREATE_NO_WINDOW", 0)` — it
  becomes `0` (no-op) on POSIX automatically.
- **`provider_discovery.py`** is the platform-neutral scanner; a port should call
  `discover_provider_tools()` rather than re-guessing in shell.
- The Qt frameless window and custom title bar/buttons render on all three OSes
  (see the caveat under *Title bar* below).

## Windows-specific code to replace (current inventory)

| Area | Where | Windows behavior | macOS / Linux replacement |
|---|---|---|---|
| Open file/folder/doc | `main_window.py` `_open_readme` / `_open_setup_doc` / "Open profile folder"; `engine.py` "Open home" (`os.startfile`) | `os.startfile(path)` | Use Qt's cross-platform `QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))` (already imported in `coding_screen.py`). Fallback: `open` (macOS) / `xdg-open` (Linux). |
| App/AppX discovery | `hub_core.py` `get_appx_install_location`, Codex/Cursor/Antigravity locators; `provider_discovery.py` | `powershell.exe … Get-AppxPackage`, `C:/Program Files`, `WindowsApps` | macOS: `/Applications/*.app`, `~/Applications`, Homebrew (`/opt/homebrew/bin`, `/usr/local/bin`). Linux: `PATH`, `~/.local/bin`, `/opt/<App>`, `.desktop` entries. |
| Title-bar theming | `hub_core.py` `configure_windows_titlebar` (`ctypes.windll` DWM) | DWM dark caption color | No-op outside Windows; Qt draws the frameless bar itself. |
| Provider process launch/stop | `engine.py`, `native_harness.py` | `Start-Process`, `subprocess` with Windows exe names | Argument-based `subprocess`; provider-specific stop that returns diagnostics, never a broad name-kill. |
| Isolated browser for "Online" | `hub_core.py` `locate_account_browser_path`, `browser_profile_launch_args` | `chrome.exe` / `msedge.exe` / `brave.exe` under Program Files / LOCALAPPDATA | See *Browser profiles* below. |
| **Desktop cookie seeding** | `hub_core.py` `seed_browser_profile_from_desktop`, `desktop_cookie_source`, `_shared_read_copy` (`ctypes` `CreateFileW`) | Copies Claude Desktop's `Local State` (DPAPI key) + `Network/Cookies` into a fresh Chrome profile | **Windows-only.** It already degrades gracefully to a one-time manual login. On macOS/Linux Chrome cookie encryption uses Keychain / libsecret-kwallet, so either implement an equivalent seeder or just leave the graceful manual-login fallback. |
| Claude Desktop state | `hub_core.py` `CLAUDE_ROAMING_HOME`, `claude_desktop_login_status` | `%APPDATA%/Claude` | macOS `~/Library/Application Support/Claude`; Linux `~/.config/Claude`. |

## Path mapping

`hub_core.py` defines these near the top; give each a per-OS branch behind the
platform adapter (below):

| Constant | Windows | macOS | Linux |
|---|---|---|---|
| `APPDATA_ROOT` | `%APPDATA%` (`~/AppData/Roaming`) | `~/Library/Application Support` | `${XDG_CONFIG_HOME:-~/.config}` |
| `LOCALAPPDATA_ROOT` | `%LOCALAPPDATA%` (`~/AppData/Local`) | `~/Library/Caches` | `${XDG_CACHE_HOME:-~/.cache}` |
| `CLAUDE_ROAMING_HOME` | `%APPDATA%/Claude` | `~/Library/Application Support/Claude` | `~/.config/Claude` |
| runtime root (`LAUNCHER_ROOT`) | `~/.codex-account-launcher` | `~/Library/Application Support/AI Account Hub` | `${XDG_DATA_HOME:-~/.local/share}/ai-account-hub` |
| discovery report | `<root>/provider-discovery.json` | same, under the macOS root | `${XDG_STATE_HOME:-~/.local/state}/ai-account-hub/provider-discovery.json` |

Tests and CI should always set `AI_HUB_LAUNCHER_ROOT` to a temp dir. If a port
changes the report path, pass `AI_HUB_DISCOVERY_REPORT` — do not fork the schema.

## Required platform adapter

Add one module (e.g. `ai_account_hub/platform_adapter.py`) and route the
Windows-specific calls through it:

```python
class PlatformAdapter:
    def open_path(self, path: Path) -> None: ...          # replaces os.startfile
    def open_url(self, url: str) -> None: ...
    def launch_terminal(self, argv: list[str], env: dict, cwd: Path) -> None: ...
    def launch_desktop(self, provider: str, target: str, workspace: Path) -> None: ...
    def stop_desktop(self, provider: str) -> dict: ...     # returns diagnostics
    def provider_state_paths(self, provider: str) -> dict[str, Path]: ...
    def browser_candidates(self) -> list[Path]: ...
```

Rules:

- Keep `subprocess` argument-based; never build shell strings from paths/user input.
- Keep `creationflags` conditional (Windows constants are meaningless on POSIX).
- macOS: prefer `open` / `osascript`; Linux: `xdg-open` / `.desktop` entries and a
  detected terminal (`x-terminal-emulator`, `gnome-terminal`, `konsole`, `wezterm`).
- Make DWM styling and any `ctypes.windll` call a no-op outside Windows.

## Provider discovery targets

- **Codex** — CLI `codex` on `PATH`, `~/.local/bin`, `/opt/homebrew/bin`,
  `/usr/local/bin`; macOS `/Applications/Codex.app`; preserve `CODEX_HOME` per
  profile; don't claim a Linux desktop target unless OpenAI ships one.
- **Claude Code** — CLI `claude` on `PATH` and `~/.local/bin/claude` (+ Homebrew);
  macOS opens `Claude.app`; keep `CLAUDE_CONFIG_DIR` per profile; never treat the
  `claude` CLI as Claude Desktop. Keep the one-time identity binding and verify a
  restored session before launch; never call the provider's logout during a Hub
  account switch.
- **Cursor** — CLI `cursor-agent`/`cursor`; state `state.vscdb` at
  `~/Library/Application Support/Cursor/User/globalStorage/` (macOS) or
  `~/.config/Cursor/User/globalStorage/` (Linux). Cursor exposes no CLI usage —
  keep the "not exposed" labels and the web-dashboard "Online" link.
- **Antigravity** — CLI `agy` on `PATH`/`~/.local/bin`; settings under
  `~/.gemini/antigravity-cli`; login is desktop-only (Google SSO). Keep quota
  labels conservative; the app already treats it as web-dashboard-only.

## Launcher

Ship a POSIX launcher only after the adapter work above passes a smoke test. It
should mirror `Start-AI-Account-Hub.bat` but target the Qt entry point:

```sh
#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DISCOVERY="$ROOT/ai_account_hub/core/provider_discovery.py"
python3 -m pip install -r "$ROOT/requirements.txt" >/dev/null 2>&1 || true
if python3 "$DISCOVERY" --write-report --quiet; then export AI_HUB_DISCOVERY_BOOTSTRAPPED=1; fi
cd "$ROOT" && exec python3 -m ai_account_hub
```

## Browser profiles ("Online")

Port the browser presets in `locate_account_browser_path` /
`browser_profile_launch_args` with OS-specific executables and always pass an
explicit `--user-data-dir` for the isolated per-account session:

```text
macOS Chrome: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
Linux Chrome: google-chrome | chromium | chromium-browser
macOS Edge:   /Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge
Linux Edge:   microsoft-edge
```

The Windows-only desktop cookie **seeding** (`seed_browser_profile_from_desktop`)
may stay a no-op on POSIX — the isolated profile just asks for a one-time login.

## Title bar

`main_window.py` sets `Qt.FramelessWindowHint` and draws its own title bar +
min/max/close buttons. This works cross-platform, but on macOS it removes the
native traffic lights and on Linux it drops WM decorations/snapping. Either keep
the custom bar (consistent look) or, per OS, fall back to a native frame when
`sys.platform != "win32"`. `configure_windows_titlebar` (DWM) must be a no-op off
Windows.

## Testing checklist

Verify with compilation plus an offscreen boot/self-test:

```sh
python3 -m compileall -q ai_account_hub

AI_HUB_LAUNCHER_ROOT="$(mktemp -d)" QT_QPA_PLATFORM=offscreen \
  python3 -c "from PySide6.QtWidgets import QApplication; a=QApplication([]); \
  from ai_account_hub.ui.main_window import MainWindow; MainWindow(a); print('boots')"
```

Add platform-specific checks for: `.app` launching (macOS); XDG data/config/state
roots (Linux); missing `open`/`xdg-open`/terminal diagnostics; paths with spaces
and non-ASCII; a provider installed after the first scan followed by Reload; and
machines with none / only one provider installed.

## Definition of done

A macOS or Linux port is complete only when:

- Startup discovery maps every installed provider and tolerates all missing ones.
- Desktop, CLI, and "Online" actions use native mechanisms (no `os.startfile`,
  PowerShell, `taskkill`, DWM, `ctypes.windll`, or `WindowsApps` on POSIX).
- Local account-state readers use correct platform paths or show "not exposed".
- Reload detects a provider installed after startup.
- The offscreen boot/self-test passes on that OS.
- Packaging (PyInstaller `.app` / AppImage / distro package, CI matrix) writes
  runtime data outside the app bundle and the repository.
