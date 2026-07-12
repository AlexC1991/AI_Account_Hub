"""Pure display/label formatters extracted from hub_core to keep it under the
1400-line rule. Leaf helpers with no internal hub_core callers; base helpers
(clip_text, mask_email, compact_number, ...) arrive via ``from hub_core import
*`` and everything is re-exposed through ``ai_account_hub.core`` (the L.* mirror).
"""

from __future__ import annotations

import datetime as dt
import re

from ai_account_hub.core import hub_core  # noqa: F401 (parity with sibling domains)
from ai_account_hub.core.hub_core import *  # noqa: F401,F403


def native_token_usage_label(usage: object) -> str:
    if not isinstance(usage, dict):
        return "-"

    def find_total(value: object) -> int | None:
        if not isinstance(value, dict):
            return None
        for key in ("totalTokens", "total_tokens"):
            number = sanitize_float(value.get(key))
            if number is not None:
                return int(number)
        for key in ("total", "last"):
            nested = find_total(value.get(key))
            if nested is not None:
                return nested
        input_tokens = sanitize_float(value.get("inputTokens") or value.get("input_tokens"))
        output_tokens = sanitize_float(value.get("outputTokens") or value.get("output_tokens"))
        if input_tokens is not None or output_tokens is not None:
            return int((input_tokens or 0) + (output_tokens or 0))
        return None

    total = find_total(usage)
    return compact_number(total) if total is not None else "-"


def format_codex_plan_update(plan: object, explanation: object = "") -> str:
    lines: list[str] = []
    if str(explanation or "").strip():
        lines.append(str(explanation).strip())
    entries = plan if isinstance(plan, list) else []
    status_labels = {
        "completed": "done",
        "complete": "done",
        "inProgress": "active",
        "in_progress": "active",
        "pending": "todo",
    }
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        step = str(entry.get("step") or entry.get("text") or "").strip()
        if not step:
            continue
        status = status_labels.get(str(entry.get("status") or "pending"), str(entry.get("status") or "todo"))
        lines.append(f"[{status}] {step}")
    if not lines and isinstance(plan, dict):
        for key, value in plan.items():
            lines.append(f"{key}: {value}")
    if not lines and str(plan or "").strip():
        lines.append(str(plan).strip())
    return "Plan\n" + "\n".join(lines) if lines else ""


def format_minutes(minutes: int | float | None) -> str:
    if minutes is None:
        return "-"
    try:
        total = int(round(float(minutes)))
    except (TypeError, ValueError):
        return "-"
    if total <= 0:
        return "0m"
    hours = total // 60
    mins = total % 60
    if hours:
        return f"{hours}h {mins:02d}m"
    return f"{mins}m"


def local_datetime_label(raw: object) -> str:
    parsed = parse_iso_datetime(raw)
    if parsed is None:
        return "-"
    local = parsed.astimezone()
    return local.strftime("%Y-%m-%d %H:%M")


def calendar_reset_chip_label(marker: object) -> str:
    """Short 'Account reset' chip label for a usage-calendar reset marker."""
    text = str(marker or "").strip()
    estimated = "estimate" in text.lower()
    account = re.split(r"\s+weekly\s+reset", text, maxsplit=1, flags=re.IGNORECASE)[0]
    account = re.sub(r"\b(claude\s+code|antigravity|codex|cursor)\b", "", account, flags=re.IGNORECASE)
    account = re.sub(r"\baccount\b", "", account, flags=re.IGNORECASE)
    account = re.sub(r"\s+", " ", account).strip()
    if not account:
        lowered = text.lower()
        account = next(
            (
                label
                for key, label in (
                    ("claude", "Claude"),
                    ("codex", "Codex"),
                    ("cursor", "Cursor"),
                    ("antigravity", "Antigravity"),
                )
                if key in lowered
            ),
            "Account",
        )
    suffix = "reset estimate" if estimated else "reset"
    return f"{clip_text(account, 12)} {suffix}"


def clip_middle_text(value: object, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if max_chars <= 1 or len(text) <= max_chars:
        return text
    if max_chars <= 4:
        return text[:max_chars]
    available = max_chars - 3
    left = max(1, available // 2)
    right = max(1, available - left)
    return f"{text[:left].rstrip()}...{text[-right:].lstrip()}"


def account_identity_label(profile: dict) -> str:
    email = str(profile.get("accountEmail") or "").strip()
    name = str(profile.get("accountName") or "").strip()
    if email and name and email.lower() not in name.lower():
        return f"{name} / {email}"
    return email or name or "-"


def masked_account_identity_label(profile: dict) -> str:
    email = str(profile.get("accountEmail") or "").strip()
    name = str(profile.get("accountName") or "").strip()
    if email:
        return mask_email(email)
    if name:
        return clip_text(name, 28)
    return "-"
