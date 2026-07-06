"""AI Account Hub — a Windows-first desktop dashboard and passthrough launcher
for managing multiple AI coding accounts (Codex, Claude Code, Cursor,
Antigravity).

Package layout:
- ``ai_account_hub.ui``      — the PySide6 (Qt) front-end (windows, screens, widgets).
- ``ai_account_hub.core``    — the Tk-free backend (provider probes, limits, usage
  history, discovery, launch/browser helpers). Re-exports ``core.hub_core``.
- ``ai_account_hub.data``    — the UI-facing data layer bridging UI and core.
- ``ai_account_hub.engine``  — the shared backend engine (refresh/save/history).
"""

from __future__ import annotations

__version__ = "1.0.0"
