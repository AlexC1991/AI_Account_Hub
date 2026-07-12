"""Privacy-safe model analytics from local Codex and Claude Code sessions.

Only timestamps, model identifiers, token counters, context sizes, task timing,
and stable session/request identifiers are inspected. Prompt text, response
text, reasoning content, images, and tool payloads are never retained.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from ai_account_hub.core import hub_core

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 uses the tomli backport.
    import tomli as tomllib


MAX_HISTORY_DAYS = 365
_SKIP_MODELS = {"", "synthetic", "<synthetic>", "unknown", "unknown model"}
CODEX_ACCOUNT_TOTAL_MODEL = "codex-account-total"
_EFFORT_ALIASES = {
    "extra-high": "xhigh",
    "extra_high": "xhigh",
    "extra high": "xhigh",
}


def _number(value: object) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _iso_day(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10] if len(text) >= 10 else ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone().date().isoformat()


def _display_model(model_id: str) -> str:
    model = str(model_id or "").strip()
    lower = model.lower()
    if lower.startswith("gpt-"):
        parts = model[4:].split("-")
        suffix = " ".join(part.capitalize() for part in parts[1:])
        return "GPT-" + parts[0] + (f" {suffix}" if suffix else "")
    if lower.startswith("claude-"):
        words = model.split("-")[1:]
        if words and len(words[-1]) == 8 and words[-1].isdigit():
            words.pop()
        if len(words) >= 3 and words[-1].isdigit() and words[-2].isdigit():
            words[-2:] = [f"{words[-2]}.{words[-1]}"]
        return "Claude " + " ".join(word.capitalize() for word in words)
    return model


def _normalize_effort(value: object) -> str:
    effort = str(value or "").strip().lower()
    return _EFFORT_ALIASES.get(effort, effort)


def _display_effort(value: object) -> str:
    effort = _normalize_effort(value)
    labels = {
        "minimal": "Minimal",
        "low": "Low",
        "medium": "Medium",
        "high": "High",
        "xhigh": "XHigh",
        "ultra": "Ultra",
    }
    return labels.get(effort, effort.replace("_", " ").title())


def _model_label(model_id: object, effort: object = "") -> str:
    model = _display_model(str(model_id or "")) or "Model"
    effort_name = _display_effort(effort)
    return f"{model} · {effort_name}" if effort_name else model


def _filter_key(provider: object, model_id: object, effort: object = "") -> str:
    return "|".join((
        str(provider or "").strip().lower(),
        str(model_id or "").strip().lower(),
        _normalize_effort(effort),
    ))


def _profile_home(profile: dict, provider: str) -> Path | None:
    if provider == "claude":
        return hub_core.claude_profile_home(profile)
    raw = str(profile.get("codexHome") or "").strip()
    return Path(raw).expanduser() if raw else None


def _recent_jsonl(
    root: Path,
    cutoff: dt.datetime,
    cancelled: Callable[[], bool] | None = None,
) -> list[Path]:
    if not root.is_dir():
        return []
    cutoff_stamp = cutoff.timestamp() - 86400
    paths: list[Path] = []
    try:
        candidates = root.rglob("*.jsonl")
        for path in candidates:
            if cancelled is not None and cancelled():
                break
            try:
                if path.is_file() and path.stat().st_mtime >= cutoff_stamp:
                    paths.append(path)
            except OSError:
                continue
    except OSError:
        return []
    return sorted(paths)


def _new_group(
    profile: dict,
    provider: str,
    model_id: str,
    effort: str = "",
) -> dict[str, Any]:
    normalized_effort = _normalize_effort(effort)
    return {
        "profileId": hub_core.profile_id(profile),
        "profileName": str(profile.get("name") or "Account"),
        "provider": provider,
        "modelId": model_id,
        "modelName": _display_model(model_id),
        "reasoningEffort": normalized_effort,
        "reasoningEffortName": _display_effort(normalized_effort),
        "modelLabel": _model_label(model_id, normalized_effort),
        "filterKey": _filter_key(provider, model_id, normalized_effort),
        "contextWindow": 0,
        "days": defaultdict(_new_day),
        "sessions": set(),
        "ttftMs": [],
        "durationMs": [],
        "evidence": set(),
    }


def _new_day() -> dict[str, Any]:
    return {
        "inputTokens": 0,
        "cachedInputTokens": 0,
        "cacheCreationTokens": 0,
        "reasoningTokens": 0,
        "outputTokens": 0,
        "unclassifiedTokens": 0,
        "observedTokens": 0,
        "inferredTokens": 0,
        "totalTokens": 0,
        "turns": 0,
        "sessions": set(),
    }


def _group_for(
    groups: dict[tuple[str, str, str], dict[str, Any]],
    profile: dict,
    provider: str,
    model_id: object,
    effort: object = "",
) -> dict[str, Any] | None:
    model = str(model_id or "").strip()
    if model.lower() in _SKIP_MODELS:
        return None
    normalized_effort = _normalize_effort(effort)
    key = (hub_core.profile_id(profile), model.lower(), normalized_effort)
    if key not in groups:
        groups[key] = _new_group(profile, provider, model, normalized_effort)
    return groups[key]


def _add_tokens(
    group: dict[str, Any],
    day: str,
    session_id: str,
    *,
    input_tokens: int,
    cached_tokens: int,
    cache_creation_tokens: int,
    reasoning_tokens: int,
    output_tokens: int,
    input_includes_cached: bool = True,
    evidence: str = "observed turn metadata",
) -> None:
    if not day:
        return
    # OpenAI reports cached input as a subset of input and reasoning as a
    # subset of output. Store mutually exclusive chart segments.
    uncached_input = (
        max(0, input_tokens - cached_tokens)
        if input_includes_cached
        else input_tokens
    )
    visible_output = max(0, output_tokens - reasoning_tokens)
    total = (
        uncached_input
        + cached_tokens
        + cache_creation_tokens
        + reasoning_tokens
        + visible_output
    )
    if total <= 0:
        return
    bucket = group["days"][day]
    bucket["inputTokens"] += uncached_input
    bucket["cachedInputTokens"] += cached_tokens
    bucket["cacheCreationTokens"] += cache_creation_tokens
    bucket["reasoningTokens"] += reasoning_tokens
    bucket["outputTokens"] += visible_output
    bucket["observedTokens"] += total
    bucket["totalTokens"] += total
    bucket["turns"] += 1
    if session_id:
        bucket["sessions"].add(session_id)
        group["sessions"].add(session_id)
    if evidence:
        group["evidence"].add(evidence)


def _add_unclassified_tokens(
    group: dict[str, Any],
    day: str,
    tokens: int,
    *,
    evidence: str,
) -> None:
    if not day or tokens <= 0:
        return
    bucket = group["days"][day]
    bucket["unclassifiedTokens"] += int(tokens)
    bucket["inferredTokens"] += int(tokens)
    bucket["totalTokens"] += int(tokens)
    if evidence:
        group["evidence"].add(evidence)


def _read_rows(
    paths: Iterable[Path],
    cancelled: Callable[[], bool] | None = None,
) -> Iterable[tuple[Path, dict]]:
    for path in paths:
        if cancelled is not None and cancelled():
            return
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if cancelled is not None and cancelled():
                        return
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(row, dict):
                        yield path, row
        except OSError:
            continue


def _scan_codex(
    profile: dict,
    root: Path,
    cutoff: dt.datetime,
    groups: dict[tuple[str, str, str], dict[str, Any]],
    cancelled: Callable[[], bool] | None = None,
) -> int:
    paths = _recent_jsonl(root / "sessions", cutoff, cancelled)
    current_by_file: dict[Path, tuple[str, str]] = {}
    session_by_file: dict[Path, str] = {}
    seen_token_events: set[tuple[str, str, str, int]] = set()
    for path, row in _read_rows(paths, cancelled):
        row_type = str(row.get("type") or "")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if row_type == "session_meta":
            session_id = str(payload.get("id") or payload.get("session_id") or "")
            if session_id:
                session_by_file[path] = session_id
            continue
        if row_type == "turn_context":
            model = str(payload.get("model") or "").strip()
            if model:
                effort = _normalize_effort(
                    payload.get("effort")
                    or payload.get("reasoning_effort")
                    or payload.get("model_reasoning_effort")
                )
                current_by_file[path] = (model, effort)
            continue

        event_type = str(payload.get("type") or "")
        model, effort = current_by_file.get(path, ("", ""))
        group = _group_for(groups, profile, "codex", model, effort)
        if group is None:
            continue
        if event_type == "token_count":
            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
            usage = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
            total = _number(usage.get("total_tokens"))
            timestamp = str(row.get("timestamp") or payload.get("timestamp") or "")
            event_key = (str(path), timestamp, f"{model.lower()}|{effort}", total)
            if total <= 0 or event_key in seen_token_events:
                continue
            seen_token_events.add(event_key)
            context = _number(info.get("model_context_window"))
            group["contextWindow"] = max(group["contextWindow"], context)
            _add_tokens(
                group,
                _iso_day(timestamp),
                session_by_file.get(path, str(path)),
                input_tokens=_number(usage.get("input_tokens")),
                cached_tokens=_number(usage.get("cached_input_tokens")),
                cache_creation_tokens=0,
                reasoning_tokens=_number(usage.get("reasoning_output_tokens")),
                output_tokens=_number(usage.get("output_tokens")),
            )
        elif event_type == "task_complete":
            ttft = _number(payload.get("time_to_first_token_ms"))
            duration = _number(payload.get("duration_ms"))
            if ttft:
                group["ttftMs"].append(ttft)
            if duration:
                group["durationMs"].append(duration)
    return len(paths)


def _scan_claude(
    profile: dict,
    root: Path,
    cutoff: dt.datetime,
    groups: dict[tuple[str, str, str], dict[str, Any]],
    cancelled: Callable[[], bool] | None = None,
) -> int:
    paths = _recent_jsonl(root / "projects", cutoff, cancelled)
    best_messages: dict[str, dict[str, Any]] = {}
    session_bounds: dict[tuple[str, str], list[dt.datetime]] = {}
    for row_number, (path, row) in enumerate(_read_rows(paths, cancelled), 1):
        if str(row.get("type") or "") != "assistant":
            continue
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
        model = str(message.get("model") or "").strip()
        if not usage:
            continue
        stable_id = str(message.get("id") or row.get("requestId") or row.get("uuid") or "")
        if not stable_id:
            stable_id = f"{path}:{row_number}"
        total = sum(
            _number(usage.get(key))
            for key in (
                "input_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens", "output_tokens",
            )
        )
        if total <= 0:
            continue
        current = best_messages.get(stable_id)
        if current is None or total > current["total"]:
            best_messages[stable_id] = {
                "model": model,
                "usage": usage,
                "timestamp": str(row.get("timestamp") or message.get("created_at") or ""),
                "sessionId": str(row.get("sessionId") or path),
                "total": total,
            }

    for record in best_messages.values():
        model = str(record["model"])
        group = _group_for(groups, profile, "claude", model)
        if group is None:
            continue
        usage = record["usage"]
        timestamp = str(record["timestamp"])
        session_id = str(record["sessionId"])
        _add_tokens(
            group,
            _iso_day(timestamp),
            session_id,
            input_tokens=_number(usage.get("input_tokens")),
            cached_tokens=_number(usage.get("cache_read_input_tokens")),
            cache_creation_tokens=_number(usage.get("cache_creation_input_tokens")),
            reasoning_tokens=0,
            output_tokens=_number(usage.get("output_tokens")),
            input_includes_cached=False,
        )
        try:
            parsed = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            key = (model.lower(), session_id)
            bounds = session_bounds.setdefault(key, [parsed, parsed])
            bounds[0] = min(bounds[0], parsed)
            bounds[1] = max(bounds[1], parsed)
        except ValueError:
            # Token counters remain usable even when an optional timestamp
            # cannot contribute to the observed active-duration range.
            pass

    profile_id = hub_core.profile_id(profile)
    for (model, _session), bounds in session_bounds.items():
        group = groups.get((profile_id, model, ""))
        if group is None:
            continue
        duration_ms = int(max(0.0, (bounds[1] - bounds[0]).total_seconds()) * 1000)
        if duration_ms:
            group["durationMs"].append(duration_ms)
    return len(paths)


def _new_codex_context() -> dict[str, Any]:
    return {
        "dailyWeights": defaultdict(lambda: defaultdict(int)),
        "profileDailyWeights": defaultdict(lambda: defaultdict(int)),
        "events": [],
        "catalog": {},
        "configVariant": None,
        "profileConfigVariants": set(),
        "files": 0,
    }


def _observe_codex_variant(
    context: dict[str, Any],
    model_id: object,
    effort: object,
    source: str,
    timestamp: object = "",
) -> tuple[str, str] | None:
    model = str(model_id or "").strip()
    if model.lower() in _SKIP_MODELS:
        return None
    normalized_effort = _normalize_effort(effort)
    key = (model.lower(), normalized_effort)
    entry = context["catalog"].setdefault(key, {
        "modelId": model,
        "reasoningEffort": normalized_effort,
        "sources": set(),
        "lastSeenUtc": "",
    })
    entry["sources"].add(source)
    seen = str(timestamp or "")
    if seen and seen > str(entry.get("lastSeenUtc") or ""):
        entry["lastSeenUtc"] = seen
    return key


def _codex_config_variant(home: Path) -> tuple[str, str] | None:
    """Read only the non-secret model selectors from a Codex config."""
    path = home / "config.toml"
    if not path.is_file():
        return None
    try:
        config = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return None
    model = str(config.get("model") or "").strip()
    if model.lower() in _SKIP_MODELS:
        return None
    return model, _normalize_effort(config.get("model_reasoning_effort"))


def _scan_shared_codex_context(
    profiles: list[dict],
    cutoff: dt.datetime,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Build the model/effort timeline shared by Codex Desktop logins.

    Codex Desktop keeps conversations and model selection in the default
    ``~/.codex`` home while the Hub swaps only account authentication. The
    account app-server still owns each login's usage total, so this timeline is
    used as attribution evidence rather than as a second token total.
    """
    context = _new_codex_context()
    default_home = Path(hub_core.DEFAULT_CODEX_HOME)
    paths = _recent_jsonl(default_home / "sessions", cutoff, cancelled)
    archived = _recent_jsonl(default_home / "archived_sessions", cutoff, cancelled)
    paths = sorted(set(paths + archived))
    context["files"] = len(paths)
    current_by_file: dict[Path, tuple[str, str]] = {}
    seen_token_events: set[tuple[str, str, str, int]] = set()
    for path, row in _read_rows(paths, cancelled):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        row_type = str(row.get("type") or "")
        timestamp = str(row.get("timestamp") or payload.get("timestamp") or "")
        if row_type == "turn_context":
            model = str(payload.get("model") or "").strip()
            effort = _normalize_effort(
                payload.get("effort")
                or payload.get("reasoning_effort")
                or payload.get("model_reasoning_effort")
            )
            variant = _observe_codex_variant(
                context, model, effort, "shared turn context", timestamp
            )
            if variant is not None:
                current_by_file[path] = (model, effort)
                context["events"].append({
                    "timestamp": timestamp,
                    "day": _iso_day(timestamp),
                    "variant": variant,
                })
            continue
        if str(payload.get("type") or "") != "token_count":
            continue
        model, effort = current_by_file.get(path, ("", ""))
        variant = _observe_codex_variant(
            context, model, effort, "shared token timeline", timestamp
        )
        if variant is None:
            continue
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        usage = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
        total = _number(usage.get("total_tokens"))
        event_key = (str(path), timestamp, "|".join(variant), total)
        if total <= 0 or event_key in seen_token_events:
            continue
        seen_token_events.add(event_key)
        day = _iso_day(timestamp)
        if day:
            context["dailyWeights"][day][variant] += total

    config_variant = _codex_config_variant(default_home)
    if config_variant is not None:
        context["configVariant"] = _observe_codex_variant(
            context, config_variant[0], config_variant[1], "shared config"
        )
    for profile in profiles:
        if hub_core.provider_key(profile) != "codex":
            continue
        home = _profile_home(profile, "codex")
        if home is None:
            continue
        variant = _codex_config_variant(home)
        if variant is None:
            continue
        observed = _observe_codex_variant(
            context, variant[0], variant[1], "saved profile config"
        )
        if observed is not None:
            context["profileConfigVariants"].add(observed)
    context["events"].sort(key=lambda item: str(item.get("timestamp") or ""))
    return context


def _merge_profile_codex_observations(
    context: dict[str, Any],
    groups: dict[tuple[str, str, str], dict[str, Any]],
) -> None:
    """Let one profile's exposed turns inform the shared Codex catalog."""
    for group in groups.values():
        if group["provider"] != "codex" or group["modelId"] == CODEX_ACCOUNT_TOTAL_MODEL:
            continue
        variant = _observe_codex_variant(
            context,
            group["modelId"],
            group.get("reasoningEffort"),
            "saved profile turn context",
        )
        if variant is None:
            continue
        for day, bucket in group["days"].items():
            observed = int(bucket.get("observedTokens") or 0)
            if observed > 0:
                context["profileDailyWeights"][day][variant] += observed


def _codex_weights_for_day(
    context: dict[str, Any],
    day: str,
) -> tuple[dict[tuple[str, str], int], str]:
    same_day = dict(context["dailyWeights"].get(day) or {})
    if same_day:
        return same_day, "shared Codex same-day model/effort mix"
    profile_day = dict(context["profileDailyWeights"].get(day) or {})
    if profile_day:
        return profile_day, "shared Codex profile model/effort mix"

    previous = [
        item for item in context["events"]
        if str(item.get("day") or "") and str(item.get("day")) <= day
    ]
    if previous:
        return {previous[-1]["variant"]: 1}, "latest shared Codex turn setting"

    previous_profile_days = [
        candidate for candidate in context["profileDailyWeights"] if candidate <= day
    ]
    if previous_profile_days:
        latest = max(previous_profile_days)
        weights = dict(context["profileDailyWeights"][latest])
        if weights:
            return weights, "latest saved-profile Codex setting"

    config_variant = context.get("configVariant")
    if config_variant is not None:
        return {config_variant: 1}, "shared Codex config"
    profile_variants = set(context.get("profileConfigVariants") or set())
    if len(profile_variants) == 1:
        return {next(iter(profile_variants)): 1}, "shared saved-profile config"
    return {}, ""


def _allocate_weighted(
    total: int,
    weights: dict[tuple[str, str], int],
) -> list[tuple[tuple[str, str], int]]:
    """Allocate an integer total exactly, using largest remainders."""
    positive = [(variant, max(0, int(weight))) for variant, weight in weights.items()]
    positive = [(variant, weight) for variant, weight in positive if weight > 0]
    if total <= 0 or not positive:
        return []
    denominator = sum(weight for _variant, weight in positive)
    allocations: list[list[Any]] = []
    assigned = 0
    for variant, weight in sorted(positive):
        numerator = int(total) * weight
        amount, remainder = divmod(numerator, denominator)
        allocations.append([variant, amount, remainder])
        assigned += amount
    for item in sorted(allocations, key=lambda value: (-value[2], value[0]))[: total - assigned]:
        item[1] += 1
    return [(item[0], int(item[1])) for item in allocations if int(item[1]) > 0]


def _median(values: list[int]) -> int | None:
    return int(statistics.median(values)) if values else None


def _add_codex_history_fallbacks(
    profiles: list[dict],
    groups: dict[tuple[str, str, str], dict[str, Any]],
    coverage: list[dict],
    context: dict[str, Any],
) -> None:
    """Fill account/day gaps from Codex app-server history.

    Codex Desktop does not always write sessions into the selected profile's
    isolated home. The app-server daily bucket remains authoritative for total
    account usage, but it does not expose token composition. Model and effort
    are attributed from the shared Codex turn timeline/config and kept visibly
    marked as inferred. Only the positive difference is added so exact local
    session rows are never counted twice.
    """
    try:
        from ai_account_hub.core.history_db import history_usage_entries

        entries = history_usage_entries(profiles)
    except Exception:
        return
    profiles_by_id = {
        hub_core.profile_id(profile): profile
        for profile in profiles
        if hub_core.provider_key(profile) == "codex"
    }
    attributed: dict[tuple[str, str], int] = defaultdict(int)
    for group in groups.values():
        if group["provider"] != "codex" or group["modelId"] == CODEX_ACCOUNT_TOTAL_MODEL:
            continue
        for day, bucket in group["days"].items():
            attributed[(group["profileId"], day)] += int(bucket["totalTokens"])

    tracked_by_day: dict[tuple[str, str], int] = {}
    for entry in entries:
        pid = str(entry.get("profileId") or "")
        day = str(entry.get("day") or "")
        if pid in profiles_by_id and day:
            key = (pid, day)
            tracked_by_day[key] = max(
                tracked_by_day.get(key, 0),
                max(0, int(entry.get("tokens") or 0)),
            )

    inferred_profiles: set[str] = set()
    unknown_profiles: set[str] = set()
    for (pid, day), tracked in sorted(tracked_by_day.items()):
        profile = profiles_by_id.get(pid)
        if profile is None or not day:
            continue
        difference = max(0, tracked - attributed.get((pid, day), 0))
        if difference <= 0:
            continue
        weights, evidence = _codex_weights_for_day(context, day)
        allocations = _allocate_weighted(difference, weights)
        if allocations:
            for (model_key, effort), amount in allocations:
                catalog_entry = context["catalog"].get((model_key, effort)) or {}
                model_id = str(catalog_entry.get("modelId") or model_key)
                group = _group_for(groups, profile, "codex", model_id, effort)
                if group is not None:
                    _add_unclassified_tokens(
                        group,
                        day,
                        amount,
                        evidence=evidence,
                    )
            inferred_profiles.add(pid)
            continue

        group = _group_for(groups, profile, "codex", CODEX_ACCOUNT_TOTAL_MODEL)
        if group is not None:
            group["modelName"] = "Codex usage (model unknown)"
            group["modelLabel"] = group["modelName"]
            _add_unclassified_tokens(
                group,
                day,
                difference,
                evidence="account total without model evidence",
            )
            unknown_profiles.add(pid)

    for item in coverage:
        pid = item.get("profileId")
        if pid in inferred_profiles:
            item["state"] = "mixed" if item.get("state") == "observed" else "inferred"
        elif pid in unknown_profiles and item.get("state") != "observed":
            item["state"] = "account_totals"


def _finalize(groups: dict[tuple[str, str, str], dict[str, Any]]) -> list[dict]:
    rows: list[dict] = []
    for group in groups.values():
        days: dict[str, dict] = {}
        totals = _new_day()
        for day, raw in sorted(group["days"].items()):
            bucket = {key: value for key, value in raw.items() if key != "sessions"}
            bucket["sessions"] = len(raw["sessions"])
            days[day] = bucket
            for key in (
                "inputTokens", "cachedInputTokens", "cacheCreationTokens",
                "reasoningTokens", "outputTokens", "unclassifiedTokens",
                "observedTokens", "inferredTokens",
                "totalTokens", "turns",
            ):
                totals[key] += int(raw[key])
        if not days:
            continue
        observed_tokens = int(totals["observedTokens"])
        inferred_tokens = int(totals["inferredTokens"])
        attribution = (
            "mixed" if observed_tokens and inferred_tokens
            else "observed" if observed_tokens
            else "inferred" if inferred_tokens
            else "unknown"
        )
        rows.append(
            {
                "profileId": group["profileId"],
                "profileName": group["profileName"],
                "provider": group["provider"],
                "modelId": group["modelId"],
                "modelName": group["modelName"],
                "modelLabel": group["modelLabel"],
                "reasoningEffort": group["reasoningEffort"],
                "reasoningEffortName": group["reasoningEffortName"],
                "filterKey": group["filterKey"],
                "attributionState": attribution,
                "evidence": sorted(group["evidence"]),
                "contextWindow": group["contextWindow"],
                "sessions": len(group["sessions"]),
                "medianTtftMs": _median(group["ttftMs"]),
                "medianDurationMs": _median(group["durationMs"]),
                "days": days,
                **{key: totals[key] for key in (
                    "inputTokens", "cachedInputTokens", "cacheCreationTokens",
                    "reasoningTokens", "outputTokens", "unclassifiedTokens",
                    "observedTokens", "inferredTokens",
                    "totalTokens", "turns",
                )},
            }
        )
    rows.sort(key=lambda row: (-int(row["totalTokens"]), row["modelName"].lower()))
    return rows


def _build_model_catalog(
    rows: list[dict],
    context: dict[str, Any],
    codex_profile_ids: list[str],
) -> list[dict]:
    catalog: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add(
        provider: str,
        model_id: str,
        effort: str,
        profile_ids: Iterable[str],
        evidence: Iterable[str],
        last_seen: str = "",
    ) -> None:
        if model_id.lower() in _SKIP_MODELS or model_id == CODEX_ACCOUNT_TOTAL_MODEL:
            return
        key = (provider, model_id.lower(), _normalize_effort(effort))
        entry = catalog.setdefault(key, {
            "provider": provider,
            "modelId": model_id,
            "modelName": _display_model(model_id),
            "reasoningEffort": _normalize_effort(effort),
            "reasoningEffortName": _display_effort(effort),
            "modelLabel": _model_label(model_id, effort),
            "filterKey": _filter_key(provider, model_id, effort),
            "profileIds": set(),
            "evidence": set(),
            "lastSeenUtc": "",
        })
        entry["profileIds"].update(str(pid) for pid in profile_ids if pid)
        entry["evidence"].update(str(item) for item in evidence if item)
        if last_seen and last_seen > entry["lastSeenUtc"]:
            entry["lastSeenUtc"] = last_seen

    for shared in context["catalog"].values():
        add(
            "codex",
            str(shared["modelId"]),
            str(shared["reasoningEffort"]),
            codex_profile_ids,
            shared["sources"],
            str(shared.get("lastSeenUtc") or ""),
        )
    for row in rows:
        add(
            str(row.get("provider") or ""),
            str(row.get("modelId") or ""),
            str(row.get("reasoningEffort") or ""),
            [str(row.get("profileId") or "")],
            row.get("evidence") or [],
        )

    output: list[dict] = []
    for entry in catalog.values():
        output.append({
            **entry,
            "profileIds": sorted(entry["profileIds"]),
            "evidence": sorted(entry["evidence"]),
        })
    output.sort(key=lambda item: (
        str(item["provider"]), str(item["modelName"]).lower(),
        str(item["reasoningEffort"]),
    ))
    return output


def build_model_analytics(
    profiles: list[dict],
    history_days: int = MAX_HISTORY_DAYS,
    cancelled: Callable[[], bool] | None = None,
) -> dict:
    """Return a privacy-safe model snapshot for the supplied Hub profiles."""
    now = dt.datetime.now(dt.timezone.utc)
    selected_days = max(1, min(MAX_HISTORY_DAYS, int(history_days)))
    cutoff = now - dt.timedelta(days=selected_days)
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    coverage: list[dict] = []
    codex_profiles = [
        profile for profile in profiles
        if hub_core.provider_key(profile) == "codex"
    ]
    codex_context = (
        _scan_shared_codex_context(codex_profiles, cutoff, cancelled)
        if codex_profiles
        else _new_codex_context()
    )
    for profile in profiles:
        if cancelled is not None and cancelled():
            break
        provider = hub_core.provider_key(profile)
        pid = hub_core.profile_id(profile)
        base = {
            "profileId": pid,
            "profileName": str(profile.get("name") or "Account"),
            "provider": provider,
        }
        if provider not in {"codex", "claude"}:
            coverage.append({**base, "state": "not_exposed", "files": 0})
            continue
        if provider == "claude" and hub_core.claude_desktop_only(profile):
            coverage.append({**base, "state": "not_exposed", "files": 0})
            continue
        home = _profile_home(profile, provider)
        if home is None:
            coverage.append({**base, "state": "missing_home", "files": 0})
            continue
        before = len(groups)
        files = (
            _scan_codex(profile, home, cutoff, groups, cancelled)
            if provider == "codex"
            else _scan_claude(profile, home, cutoff, groups, cancelled)
        )
        state = "observed" if len(groups) > before else ("no_history" if files else "missing_history")
        coverage.append({**base, "state": state, "files": files})
    _merge_profile_codex_observations(codex_context, groups)
    _add_codex_history_fallbacks(profiles, groups, coverage, codex_context)
    rows = _finalize(groups)
    codex_profile_ids = [hub_core.profile_id(profile) for profile in codex_profiles]

    current_variant: tuple[str, str] | None = None
    current_source = ""
    current_seen = ""
    if codex_context["events"]:
        latest = codex_context["events"][-1]
        current_variant = latest["variant"]
        current_source = "latest shared turn"
        current_seen = str(latest.get("timestamp") or "")
    elif codex_context.get("configVariant") is not None:
        current_variant = codex_context["configVariant"]
        current_source = "shared config"
    current_catalog = (
        codex_context["catalog"].get(current_variant) if current_variant else None
    ) or {}
    return {
        "generatedAtUtc": now.isoformat(),
        "historyDays": selected_days,
        "rows": rows,
        "coverage": coverage,
        "modelCatalog": _build_model_catalog(rows, codex_context, codex_profile_ids),
        "codexShared": {
            "files": int(codex_context.get("files") or 0),
            "currentModelId": str(current_catalog.get("modelId") or ""),
            "currentModelName": _display_model(str(current_catalog.get("modelId") or "")),
            "currentReasoningEffort": str(current_catalog.get("reasoningEffort") or ""),
            "currentReasoningEffortName": _display_effort(
                current_catalog.get("reasoningEffort")
            ),
            "currentLabel": _model_label(
                current_catalog.get("modelId"),
                current_catalog.get("reasoningEffort"),
            ) if current_catalog else "",
            "currentSource": current_source,
            "lastSeenUtc": current_seen,
        },
    }


def reconcile_claude_history(profiles: list[dict], _snapshot: dict) -> None:
    """Replace legacy duplicate-inflated Claude daily rows with v2 totals."""
    try:
        from ai_account_hub.core.claude_status import build_claude_usage_buckets
        from ai_account_hub.core.history_db import record_profile_history
    except Exception:
        return
    for profile in profiles:
        if hub_core.provider_key(profile) != "claude" or hub_core.claude_desktop_only(profile):
            continue
        daily = build_claude_usage_buckets(hub_core.claude_profile_home(profile) / "projects")
        if not daily:
            continue
        corrected = dict(profile)
        corrected["usageDailyBuckets"] = daily
        record_profile_history(corrected, refresh_reason="analytics-reconcile")


__all__ = [
    "CODEX_ACCOUNT_TOTAL_MODEL",
    "MAX_HISTORY_DAYS",
    "_display_effort",
    "_model_label",
    "build_model_analytics",
    "reconcile_claude_history",
]
