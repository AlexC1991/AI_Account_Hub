# Disabled: native "Coding" passthrough

This folder holds the **parked** coding subsystem. It is intentionally
**not tracked by git** (see the repo `.gitignore`) and is **not imported** by
the shipped app, which is Accounts-only.

## What lives here

- `harness/` — native provider transports and on-disk history readers
  (Codex app-server JSON-RPC, Claude Code stream-json + permission bridge,
  Cursor Agent print mode, Antigravity `agy`), plus `claude_permission_bridge.py`.
- `coding_bridge.py` — the `QObject` bridge that drove a coding session on top
  of the harness transports.
- `coding_screen/` — the PySide6 "Coding" view (composer, message blocks,
  thread sidebar).
- `coding_text.py` — coding-view display/parse helpers that were extracted into
  `core/` while the feature was live.

## Why it is parked

The Coding view was disabled in the public build. The Accounts dashboard is the
shipped product. Rather than delete the work, it is kept here as reference.

## Note on re-enabling

These modules still use their original absolute imports
(`ai_account_hub.harness.*`, `ai_account_hub.core.coding_text`, etc.). Those
paths no longer exist in the package, so this code will **not import as-is**.
Re-enabling means moving the pieces back into the package and re-wiring:

- `ai_account_hub/ui/main_window.py` (the Coding stack page + segment),
- `ai_account_hub/core/__init__.py` (mirror `coding_text` again),
- `ai_account_hub/core/hub_core.py` (the `native_harness` re-exports +
  `CLAUDE_PERMISSION_BRIDGE_PATH`),
- `ai_account_hub/data.py` (the native/thread data helpers),
- the `use_in_coding` action plumbing in `ui/screens/accounts_screen/`.

`calendar_reset_chip_label` (the one Accounts-facing helper that had been in
`coding_text.py`) now lives in `core/hub_core.py`.
