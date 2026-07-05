"""Accounts dashboard screen.

Split into ``card`` (the left-rail account card + helpers), ``workers``
(background QThreads), and ``screen`` (the main ``AccountsScreen`` widget). The
public export is unchanged: ``from ai_account_hub.ui.screens.accounts_screen
import AccountsScreen``.
"""

from __future__ import annotations

from .screen import AccountsScreen

__all__ = ["AccountsScreen"]
