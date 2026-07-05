"""Provider and UI constant tables: provider colors/initials, project dot
colors, provider/sort/card-template choices, and the per-provider Online links.

Pure data (no imports); hub_core pulls these in via ``from .constants import *``."""

from __future__ import annotations

PROVIDER_COLORS = {
    "codex": "#4d92d6",
    "claude": "#c17c4e",
    "cursor": "#a065c9",
    "antigravity": "#a86bd6",
    "api": "#19706b",
}

PROVIDER_INITIALS = {
    "codex": "CX",
    "claude": "CC",
    "cursor": "CU",
    "antigravity": "AG",
    "api": "ALL",
}

PROJECT_DOT_COLORS = ["#e8698f", "#4fb37a", "#9d5fd6", "#4a9fd6", "#dcb04a", "#d6614a"]

PROVIDER_CHOICES = [
    ("Codex", "codex"),
    ("Claude Code", "claude"),
    ("Cursor", "cursor"),
    ("Antigravity", "antigravity"),
]

SORT_CHOICES = ["Manual", "Name", "Provider", "State", "Session left", "Weekly left", "Last refresh"]
CARD_TEMPLATE_CHOICES = ["Balanced", "Compact", "Plan Chips", "Usage First", "Identity"]

ONLINE_LINKS = {
    "codex": [
        {"key": "chat", "label": "ChatGPT", "url": "https://chatgpt.com/"},
        {"key": "billing", "label": "ChatGPT Billing", "url": "https://chatgpt.com/"},
        {"key": "workspace-billing", "label": "Workspace Billing", "url": "https://chatgpt.com/admin/billing"},
        {"key": "api-billing", "label": "API Billing", "url": "https://platform.openai.com/account/billing/overview"},
        {"key": "api-usage", "label": "API Usage", "url": "https://platform.openai.com/usage"},
        {"key": "api-keys", "label": "API Keys", "url": "https://platform.openai.com/api-keys"},
        {"key": "support", "label": "OpenAI Help", "url": "https://help.openai.com/"},
    ],
    "claude": [
        {"key": "chat", "label": "Claude Chat", "url": "https://claude.ai/"},
        {"key": "billing", "label": "Billing", "url": "https://claude.ai/settings/billing"},
        {"key": "usage", "label": "Usage", "url": "https://claude.ai/settings/usage"},
        {"key": "console-billing", "label": "Console Billing", "url": "https://platform.claude.com/settings/billing"},
        {"key": "console-usage", "label": "Console Usage", "url": "https://platform.claude.com/usage"},
        {"key": "console-limits", "label": "Console Limits", "url": "https://platform.claude.com/settings/limits"},
        {"key": "support", "label": "Claude Support", "url": "https://support.claude.com/"},
        {"key": "code-docs", "label": "Code Costs", "url": "https://code.claude.com/docs/en/costs"},
    ],
    "cursor": [
        {"key": "dashboard", "label": "Dashboard", "url": "https://cursor.com/dashboard"},
        {"key": "usage", "label": "Usage Limits", "url": "https://cursor.com/help/models-and-usage/usage-limits"},
        {"key": "pricing", "label": "Models/Pricing", "url": "https://cursor.com/docs/models-and-pricing"},
        {"key": "spend", "label": "Spend Limits", "url": "https://cursor.com/help/account-and-billing/spend-limits"},
        {"key": "docs", "label": "Cursor Docs", "url": "https://cursor.com/docs"},
    ],
    "antigravity": [
        {"key": "home", "label": "Antigravity", "url": "https://antigravity.google/"},
        {"key": "pricing", "label": "Pricing", "url": "https://antigravity.google/pricing"},
        {"key": "plans", "label": "Plans", "url": "https://antigravity.google/docs/plans"},
        {"key": "credits", "label": "AI Credits", "url": "https://antigravity.google/docs/cli/credits"},
        {"key": "cli", "label": "CLI Docs", "url": "https://antigravity.google/docs/cli-overview"},
    ],
}
