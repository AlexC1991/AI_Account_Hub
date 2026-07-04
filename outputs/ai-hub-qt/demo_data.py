"""Demo mode for screenshots / release images.

Set the environment variable ``AI_HUB_DEMO=1`` before launching and the app
shows a set of **fake** accounts with realistic usage/limits/history instead of
your real profiles. It never reads or writes your real ``profiles.json`` (see the
guards in ``data.py`` and ``screens/accounts_screen.py``), so you can capture an
accurate, private-data-free screenshot of the genuine UI.

All names, emails, and numbers here are invented placeholders.
"""

from __future__ import annotations

import datetime as _dt
import os

DEMO: bool = os.environ.get("AI_HUB_DEMO", "").strip().lower() not in ("", "0", "false", "no")


def _iso(*, days: float = 0, hours: float = 0) -> str:
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=days, hours=hours)).isoformat()


# (name, provider, plan, email, used_weekly%, used_session%, state, weekly_reset_days, session_reset_hours)
_SPEC = [
    ("NovaCode Proton", "claude", "Pro", "novacode.proton@example.com", 38, 38, "ready", 6.2, 4.6),
    ("Alpha KCCA Account", "codex", "Plus", "alpha.kcca@example.com", 66, 34, "ready", 3.1, 5.8),
    ("Beta KCP Account", "codex", "Plus", "beta.kcp@example.com", 79, 21, "ready", 4.4, 6.5),
    ("Gamma Gmail Account", "codex", "Plus", "gamma.gmail@example.com", 100, 100, "not_ready", 1.0, 0.0),
    ("Delta Gravity Account", "antigravity", "Pro", "delta.gravity@example.com", 46, 52, "ready", 2.0, 3.2),
    ("Epsilon Cursor", "cursor", "Pro", "epsilon.cursor@example.com", 27, 61, "ready", 5.0, 4.0),
    ("Zeta Codex Account", "codex", "Plus", "zeta.codex@example.com", 45, 55, "ready", 6.9, 2.1),
]


def demo_profiles() -> list[dict]:
    profiles: list[dict] = []
    for i, (name, provider, plan, email, uw, us, state, wr, sr) in enumerate(_SPEC):
        pid = f"demo:{provider}:{i}"
        profile: dict = {
            "id": pid,
            "name": name,
            "provider": provider,
            "plan": plan,
            "accountType": plan,
            "accountEmail": email,
            "workspace": "~/Documents/Projects",
            "state": state,
            "weeklyLimitUsedPercent": uw,
            "shortLimitUsedPercent": us,
            "weeklyLimitResetUtc": _iso(days=wr),
            "weeklyResetEstimateUtc": _iso(days=wr),
            "shortLimitResetUtc": _iso(hours=sr),
            "usageSummary": {"claudeAuthStatus": {"loggedIn": True}, "desktopReady": True},
            "claudeDesktopCaptured": provider == "claude",
            "lastLimitsRefreshUtc": _iso(hours=-0.2),
        }
        if state == "not_ready":
            profile["limitReachedType"] = "weekly"
            profile["cooldownUntilUtc"] = _iso(hours=23.75)
        profiles.append(profile)
    return profiles


def demo_history_entries(profiles=None, iso_day: str | None = None) -> list[dict]:
    """Fake per-account, per-day usage buckets for the calendar and stat cards.
    Signature matches ``legacy_backend.history_usage_entries`` (it is swapped in
    for it in demo mode)."""
    today = _dt.date.today()
    first = today.replace(day=1)
    entries: list[dict] = []
    for i, (name, provider, plan, email, uw, us, state, wr, sr) in enumerate(_SPEC):
        pid = f"demo:{provider}:{i}"
        profile = {"id": pid, "name": name, "provider": provider, "plan": plan}
        # a few active days this month, weighted so totals look plausible
        day = first
        seed = (i + 3)
        while day <= today:
            if (day.day * seed) % 5 in (0, 2, 3):  # ~active on some days
                tokens = 40_000_000 + ((day.day * seed * 37) % 900) * 1_000_000
                minutes = 180 + (day.day * seed * 11) % 260
                iso = day.isoformat()
                if iso_day is None or iso == iso_day:
                    entries.append({
                        "profileId": pid,
                        "profile": profile,
                        "day": iso,
                        "tokens": tokens,
                        "minutes": None if provider == "codex" else minutes,
                        "messageCount": 3 + (day.day % 9),
                        "source": provider,
                        "bucket": {},
                    })
            day += _dt.timedelta(days=1)
    return entries
