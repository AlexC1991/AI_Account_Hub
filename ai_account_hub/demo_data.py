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
import hashlib
import os

DEMO: bool = os.environ.get("AI_HUB_DEMO", "").strip().lower() not in ("", "0", "false", "no")


def _demo_hash(value: object) -> str:
    """Use the same irreversible identifier shape as persisted analytics."""
    return hashlib.sha256(str(value or "").encode("utf-8", "replace")).hexdigest()


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


def demo_model_analytics(profiles: list[dict]) -> dict:
    """Fake model-level telemetry used only by the explicit demo process."""
    model_for_profile = {
        0: (
            ("claude-opus-4-8", "Claude Opus 4.8", "", ""),
            ("claude-sonnet-5", "Claude Sonnet 5", "", ""),
        ),
        1: (("gpt-5.5", "GPT-5.5", "xhigh", "XHigh"),),
        2: (("gpt-5.5", "GPT-5.5", "high", "High"),),
        3: (("gpt-5.6-sol", "GPT-5.6 Sol", "ultra", "Ultra"),),
        6: (("gpt-5.6-terra", "GPT-5.6 Terra", "xhigh", "XHigh"),),
    }
    entries_by_profile: dict[str, list[dict]] = {}
    for entry in demo_history_entries(profiles):
        entries_by_profile.setdefault(str(entry["profileId"]), []).append(entry)
    rows: list[dict] = []
    coverage: list[dict] = []
    for profile_index, profile in enumerate(profiles):
        pid = str(profile.get("id") or "")
        provider = str(profile.get("provider") or "")
        base = {
            "profileId": pid,
            "profileName": str(profile.get("name") or "Account"),
            "provider": provider,
        }
        model_specs = model_for_profile.get(profile_index, ())
        if provider not in {"codex", "claude"} or not model_specs:
            coverage.append({**base, "state": "not_exposed", "files": 0})
            continue
        profile_entries = entries_by_profile.get(pid, [])
        observed_days = 0
        for model_index, (model_id, model_name, effort, effort_name) in enumerate(model_specs):
            days: dict[str, dict] = {}
            totals = {
                "inputTokens": 0, "cachedInputTokens": 0,
                "cacheCreationTokens": 0, "reasoningTokens": 0,
                "outputTokens": 0, "unclassifiedTokens": 0,
                "observedTokens": 0, "inferredTokens": 0,
                "totalTokens": 0, "turns": 0,
            }
            for entry_index, entry in enumerate(profile_entries):
                if len(model_specs) > 1 and entry_index % len(model_specs) != model_index:
                    continue
                total = int(entry.get("tokens") or 0)
                cached = int(total * (0.50 + 0.04 * ((profile_index + model_index) % 3)))
                uncached = int(total * 0.22)
                cache_write = int(total * 0.04) if provider == "claude" else 0
                reasoning = int(total * 0.08) if provider == "codex" else 0
                output = max(0, total - cached - uncached - cache_write - reasoning)
                bucket = {
                    "inputTokens": uncached,
                    "cachedInputTokens": cached,
                    "cacheCreationTokens": cache_write,
                    "reasoningTokens": reasoning,
                    "outputTokens": output,
                    "unclassifiedTokens": 0,
                    "observedTokens": total,
                    "inferredTokens": 0,
                    "totalTokens": total,
                    "turns": int(entry.get("messageCount") or 1),
                    "sessions": 1,
                }
                days[str(entry["day"])] = bucket
                for key in totals:
                    totals[key] += bucket[key]
            if not days:
                continue
            observed_days += len(days)
            rows.append({
                **base,
                "modelId": model_id,
                "modelName": model_name,
                "modelLabel": f"{model_name} · {effort_name}" if effort_name else model_name,
                "reasoningEffort": effort,
                "reasoningEffortName": effort_name,
                "filterKey": f"{provider}|{model_id}|{effort}",
                "attributionState": "observed",
                "evidence": ["demo numeric telemetry"],
                "contextWindow": 1_000_000 if provider == "codex" else 200_000,
                "sessions": len(days),
                "medianTtftMs": 1800 if provider == "codex" else 2400,
                "medianDurationMs": (18 + profile_index * 3) * 60 * 1000,
                "days": days,
                **totals,
            })
        coverage.append({
            **base,
            "state": "observed" if observed_days else "no_history",
            "files": observed_days,
        })
    rows.sort(key=lambda row: -int(row["totalTokens"]))
    model_catalog = [
        {
            "provider": row["provider"],
            "modelId": row["modelId"],
            "modelName": row["modelName"],
            "modelLabel": row["modelLabel"],
            "reasoningEffort": row["reasoningEffort"],
            "reasoningEffortName": row["reasoningEffortName"],
            "filterKey": row["filterKey"],
            "profileIds": [row["profileId"]],
            "evidence": ["demo numeric telemetry"],
            "lastSeenUtc": "",
        }
        for row in rows
    ]
    return {
        "generatedAtUtc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "historyDays": 90,
        "rows": rows,
        "coverage": coverage,
        "modelCatalog": model_catalog,
        "codexShared": {
            "files": 0,
            "currentModelId": "gpt-5.6-terra",
            "currentModelName": "GPT-5.6 Terra",
            "currentReasoningEffort": "xhigh",
            "currentReasoningEffortName": "XHigh",
            "currentLabel": "GPT-5.6 Terra · XHigh",
            "currentSource": "demo numeric telemetry",
            "lastSeenUtc": "",
        },
    }


def demo_benchmark_analytics(profiles: list[dict]) -> dict:
    """Complete fake Statistics snapshot; never scans real provider history."""
    model_snapshot = demo_model_analytics(profiles)
    tasks: list[dict] = []
    limit_segments: list[dict] = []
    shapes = ("Feature", "Debugging", "Refactor", "Verification")
    for model_index, row in enumerate(model_snapshot["rows"]):
        for day_index, (day, bucket) in enumerate(sorted(row.get("days", {}).items())):
            total = int(bucket.get("totalTokens") or 0)
            task_id = f"demo-task-{model_index}-{day_index}"
            edits = 3 + ((model_index * 7 + day_index * 3) % 28)
            files = 1 + ((model_index + day_index) % 7)
            tests = (model_index + day_index) % 9
            commands = 4 + ((model_index * 5 + day_index * 2) % 24)
            tasks.append({
                "taskId": _demo_hash(task_id),
                "provider": row["provider"],
                "profileId": _demo_hash(row["profileId"]),
                "profileIds": [_demo_hash(row["profileId"])],
                "accountAttribution": "exact",
                "day": day,
                "filterKey": row["filterKey"],
                "modelId": row["modelId"],
                "modelName": row["modelName"],
                "modelLabel": row["modelLabel"],
                "reasoningEffort": row["reasoningEffort"],
                "reasoningEffortName": row["reasoningEffortName"],
                "status": "completed" if (model_index + day_index) % 6 else "incomplete",
                "activityShape": shapes[(model_index + day_index) % len(shapes)],
                "totalTokens": total,
                "durationMs": (12 + ((model_index * 9 + day_index * 4) % 52)) * 60_000,
                "ttftMs": 900 + ((model_index * 211 + day_index * 97) % 2800),
                "edits": edits,
                "filesChanged": files,
                "fileHashes": [f"demo-file-{model_index}-{day_index}-{index}" for index in range(files)],
                "linesAdded": edits * 5,
                "linesDeleted": edits * 2,
                "tests": tests,
                "testsPassed": max(0, tests - (1 if day_index % 8 == 0 else 0)),
                "commands": commands,
                "toolCalls": commands + edits,
                "toolErrors": 1 if day_index % 9 == 0 else 0,
                "rollbacks": 1 if day_index % 13 == 0 else 0,
                "compactions": 1 if total > 500_000_000 else 0,
            })
            short_burn = round(0.8 + ((model_index + day_index) % 5) * 0.55, 2)
            weekly_burn = round(0.3 + ((model_index * 2 + day_index) % 4) * 0.35, 2)
            limit_segments.append({
                "profileId": row["profileId"],
                "provider": row["provider"],
                "day": day,
                "shortBurn": short_burn,
                "weeklyBurn": weekly_burn,
                "allocations": [{
                    "filterKey": row["filterKey"],
                    "shortBurn": short_burn,
                    "weeklyBurn": weekly_burn,
                }],
            })
    return {
        "generatedAtUtc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "historyDays": 90,
        "modelUsageRows": model_snapshot["rows"],
        "modelCatalog": model_snapshot["modelCatalog"],
        "coverage": model_snapshot["coverage"],
        "codexShared": model_snapshot["codexShared"],
        "tasks": tasks,
        "limitSegments": limit_segments,
        "sourceStats": {
            "files": sum(int(item.get("files") or 0) for item in model_snapshot["coverage"]),
            "cachedFiles": 0,
            "parsedFiles": 0,
            "events": len(tasks) * 6,
            "tasks": len(tasks),
        },
        "privacy": {
            "storedContent": False,
            "storedPaths": False,
            "demo": True,
        },
    }
