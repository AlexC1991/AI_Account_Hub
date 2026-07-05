# AI Account Hub - PySide6 UI

This is the active AI Account Hub desktop front-end. It recreates the final
design handoff with persistent Coding and Accounts screens in a
`QStackedWidget`, so switching sections preserves the active account, project,
thread, composer, calendar, filters, and scroll position.

The former Tk UI has been retired. Its reusable provider and account logic lives
in the Tk-free backend `core/hub_core.py`; transport, discovery, permission, and
history modules sit in `core/` and `harness/` beside that shared core.

The public paid Claude enrollment guide is available from **Help > Account
setup** and at `../Docs/CLAUDE_ACCOUNT_SETUP.md`.

## Run

```bat
Start-AI-Account-Hub.bat
```

Run that single launcher from the repository root. It checks Python, installs
the declared Python package requirements when needed, refreshes provider
discovery, and then opens this app. For development, `py -3 main.py` (or
`py -3 -m ai_account_hub`) from the repo root starts the same entry point.

## Architecture

The UI reuses the existing provider logic instead of replacing the official
harnesses:

- `core/` (the `ai_account_hub.core` package) re-exports `hub_core.py`, the
  Tk-free helper layer, and holds `provider_discovery.py`.
- `engine.py` owns provider discovery and account actions. Blocking probes
  run on worker threads.
- `coding_bridge.py` reuses the `native_harness` transports directly and
  marshals their events to the UI through Qt signals.
- Profiles and settings remain in the machine-local launcher folder rather
  than inside the repository.

## Status — functional

**Accounts screen — at parity:**
- All account cards with real data + severity-colored usage bars; selecting a
  card updates the right detail rail **in place** (no rebuild).
- Real **month + week calendar** from the shared sqlite usage history, with
  prev/next/today, mode toggle, event chips, and a **day-detail modal**.
- Live stat cards + 2×2 detail tiles from real usage data.
- Every button wired to the real backend: **Refresh all / Refresh** (threaded),
  Login / Device / Logout / Open CLI / Desktop (incl. the Codex desktop-switch
  flow) / Home / Status / Doctor / Online / Use reset / Set-Clear timer /
  Use in coding / Add / Edit / Rename / Delete.

**Claude Desktop switching:**
- The Add Profile dialog offers **Claude Code (paid)**. Each profile uses
  Login for its isolated CLI identity and Desktop Login for the matching app
  session.
- Paid Code profiles verify the Claude Code account UUID against the captured
  Desktop UUID.
- Multiple paid Claude Code profiles remain isolated by their own
  `CLAUDE_CONFIG_DIR`, account UUID, and captured Desktop-state directory.
- `Open Desktop` only launches a saved Desktop state when the identities match.
  It refuses mismatched old/global Desktop logins instead of treating “logged
  in” as success.
- `Desktop Login` opens Claude Desktop with a clean logged-out state for the
  selected profile, then AI Hub watches for the official Claude Desktop login to
  complete. When Claude Desktop reports that the account is active/logged in,
  AI Hub briefly restarts Desktop, copies the now-unlocked session files,
  verifies the session cookie plus matching account UUID, and relaunches
  Desktop.
- `Open Desktop` and another `Desktop Login` also run a mandatory pending-login
  rescue before replacing active Claude state. This closes the timing gap when
  a user signs in and switches accounts before the UI watcher finishes.
- Desktop capture is internal to Desktop Login and pending-switch recovery;
  there is no separate Save Desktop step or button.
- Switching never calls Claude's provider logout. If the user clicks **Log out**
  in Claude and Claude revokes the server session, that profile must complete
  Desktop Login again.
- The backend retains a hidden Desktop-only profile path solely for regression
  coverage. **Used For Testing Claude Account Switching**; it is not exposed by
  Add/Edit in the production UI.
- The Hub does **not** convert Claude Code `.credentials.json` into Claude
  Desktop login state. Claude Desktop stores OAuth caches with Electron
  `safeStorage` and clears them when the Desktop account identity changes, so
  importing raw CLI tokens would be brittle and could bind the wrong account.

**Coding screen - working:**
- Real Codex and Claude project/thread discovery, cached off the UI thread.
- Opening a thread loads its native history incrementally and resumes its
  native session rather than creating a Hub-owned conversation.
- Live **streaming passthrough**: composer send starts/resumes a native session
  via the real transport and streams the assistant reply; Stop / New chat /
  Search / account switcher wired. Enter sends, Shift+Enter = newline.
- **Rich message blocks**: command (collapse toggle), plan (checklist),
  diff (colored), tool/result, thinking (expand), and local image previews.
- **Per-provider composer controls** (Codex reasoning+access / Claude
  model+permission-cycle / Cursor mode+model+auto-run / Antigravity
  model+autonomy), provider-scoped Skills, a `/` slash palette, attachments,
  and queued-message Steer/Edit/Delete controls.
- **Native approvals**: Codex app-server approval/input requests, and the
  Claude permission bridge, including Claude questions and plan review.

**App shell:** frameless title bar, File/Edit/Window/Theme/Help menus, animated
Hub logo, stateful section switching, automatic refresh, provider icons, and
all bundled themes as live-swappable QSS.

## Files
- `app.py` — bootstrap (`main()`).  `ui/main_window.py` — frameless shell + header + stack.
- `ui/theme.py` / `ui/tokens.py` — QSS theme manager + the design themes.
- `data.py` — profiles/discovery/limits/threads (Tk-free).
- `core/` — `hub_core.py` (backend) + `provider_discovery.py`; `engine.py` — engine.
- `coding_bridge.py` — native transport passthrough + approvals → Qt signals.
- `ui/modals.py` — add/edit profile dialogs.
- `ui/widgets.py`, `ui/calendar_widget.py`, `ui/screens/accounts_screen.py`,
  `ui/screens/coding_screen.py`.
- `harness/` — `native_harness.py` transports + `claude_permission_bridge.py`.

## Test

From the repository root:

```bat
python -m compileall -q ai_account_hub
python -m pytest -q
```
