"""Coding view (design section 4).

Split into ``helpers`` (free-standing helpers + the CODING_UI_ENABLED flag),
three method-group mixins (``threads``, ``composer``, ``blocks``), and ``screen``
(the ``CodingScreen`` widget that mixes them). Public export unchanged:
``from ai_account_hub.ui.screens.coding_screen import CodingScreen``.
"""

from __future__ import annotations

from .screen import CodingScreen

__all__ = ["CodingScreen"]
