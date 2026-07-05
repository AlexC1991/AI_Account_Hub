# AI Account Hub — architecture

Developer notes on how the `ai_account_hub` package is laid out and why. For
usage and install, see the top-level [`README.md`](../README.md).

The app is a **pass-through launcher and dashboard**: it manages isolated local
profiles and launches each provider's own official CLI/app with the right
profile active. Provider tools own prompts, models, context, tool execution, and
session history — the Hub never proxies, pools, or auto-rotates accounts.

## Package layout

```
ai_account_hub/
  app.py                bootstrap (main()); main.py / python -m ai_account_hub call it
  data.py               UI-facing data layer (bridges UI <-> core)
  demo_data.py          AI_HUB_DEMO=1 sample accounts for screenshots
  engine.py             HubEngine: discovery + account actions on worker threads
  engine_claude_desktop.py   _ClaudeDesktopMixin: the Claude Desktop switch flow
  coding_bridge.py      native transports -> Qt signals

  ui/                   PySide6 front-end (never imported by the backend)
    main_window.py      frameless shell + header + QStackedWidget of screens
    theme.py, tokens.py QSS theme manager + design themes
    widgets/            chrome / indicators / controls (aggregated in __init__)
    calendar_widget.py, modals.py
    screens/accounts_screen/   card / workers / screen (+ data/actions mixins)
    screens/coding_screen/     helpers + threads/composer/blocks mixins + screen

  core/                 Tk-free backend
    __init__.py         re-exports every domain module (see "the core mirror")
    hub_core.py         the shared backend (limits, state, launch, provider probes)
    palette.py constants.py coding_text.py history_db.py browser.py
    locators.py claude_status.py     domains extracted from hub_core
    provider_discovery.py            the platform-neutral scanner

  harness/              native provider transports + history readers
    native_harness.py   thin aggregator re-exporting the modules below
    transports.py       Codex app-server JSON-RPC + Claude/Cursor stream-json
    history.py history_common.py history_codex.py    on-disk session readers
    locators.py         cursor-agent / agy discovery + wait_until
    claude_permission_bridge.py      MCP approval bridge (spawned by Claude Code)
```

Everything is kept **<= 1400 lines per file** on purpose; larger units are split
into cohesive modules or mixins behind an aggregator.

## Key concepts

- **The core mirror.** UI/engine code does `from ai_account_hub import core as L`
  and calls `L.<name>`. `core/__init__.py` imports `hub_core` plus each extracted
  domain module and mirrors their public attributes, so `L.foo` resolves no
  matter which module `foo` now lives in. `L.mod` is the underlying `hub_core`
  module (patch attributes there to affect the real backend, e.g. in tests).
- **Aggregator splits.** `native_harness` and several `core` domains are split
  into submodules that each `from hub_core import *`; the aggregator/`__init__`
  re-exports the public API so no call site changed. Extracted `core` modules
  reference monkeypatch-sensitive path constants as `hub_core.NAME` (not the
  star-imported copy) and keep their own `_logger`.
- **Mixin screens.** The big single classes (`AccountsScreen`, `CodingScreen`,
  `HubEngine`) are split into method-group mixins that the main class inherits.
- **Passthrough transports.** Codex via `codex app-server` JSON-RPC with a
  per-profile `CODEX_HOME`; Claude Code via stream-json + the loopback MCP
  permission bridge; Cursor via `cursor-agent --print`; Antigravity via `agy
  --print` plus reading its own transcript.
- **Coding view is disabled** in the UI for this release (`CODING_UI_ENABLED`);
  the Accounts dashboard is the supported surface.
- **Local state stays outside the repo** — profiles, settings, and usage history
  live under `~/.codex-account-launcher`.

## Development

From the repository root:

```bat
python -m compileall -q ai_account_hub
python -m pytest -q
```

Tests use Qt's offscreen platform and are sandboxed to a throwaway launcher
directory (`conftest.py`), so they never touch your real `profiles.json`.
