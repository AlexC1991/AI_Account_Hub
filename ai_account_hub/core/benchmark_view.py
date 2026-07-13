"""Benchmark view/aggregation: turn the passive benchmark snapshot into the
per-model chart data the Statistics screen renders.

Comparison metrics are based on observed non-cache "work" tokens (total minus
cache re-reads, which are 94-98% of raw totals) so cross-model ratios reflect
real work rather than context-cache size. Split out of ``benchmark_analytics``
to keep each module under the line budget; the snapshot builder and parsers
remain there.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
from collections import defaultdict
from typing import Any, Iterable

from ai_account_hub.core.model_analytics import CODEX_ACCOUNT_TOTAL_MODEL
from ai_account_hub.core.benchmark_analytics import _float, _hash, _number

_TOKEN_FIELDS = (
    "inputTokens", "cachedInputTokens", "cacheCreationTokens", "reasoningTokens",
    "outputTokens", "unclassifiedTokens", "totalTokens",
)
_WORK_FIELDS = (
    "toolCalls", "toolErrors", "commands", "tests", "testsPassed", "edits",
    "filesChanged", "fileTouches", "linesAdded", "linesDeleted", "rollbacks", "compactions",
)
_SCALED_FIELDS = (
    *_TOKEN_FIELDS, "generatedTokens", *_WORK_FIELDS, "workTokens", "observedTokens",
    "activeMs", "completedTasks", "abortedTasks", "incompleteTasks",
    "shortBurn", "weeklyBurn",
)


def _new_model_group(source: dict) -> dict[str, Any]:
    return {
        "filterKey": str(source.get("filterKey") or ""),
        "provider": str(source.get("provider") or ""),
        "modelId": str(source.get("modelId") or ""),
        "modelName": str(source.get("modelName") or "Model"),
        "reasoningEffort": str(source.get("reasoningEffort") or ""),
        "reasoningEffortName": str(source.get("reasoningEffortName") or ""),
        "modelLabel": str(source.get("modelLabel") or source.get("modelName") or "Model"),
        **{field: 0 for field in _TOKEN_FIELDS},
        **{field: 0 for field in _WORK_FIELDS},
        "generatedTokens": 0,
        # Observed, non-cache "work" tokens summed from parsed tasks. Cache
        # re-reads (94-98% of raw totals) are excluded so cross-model
        # comparisons and per-task ratios reflect real work, not context size.
        "workTokens": 0,
        "observedTokens": 0,
        "activeMs": 0,
        "ttftMs": [],
        "taskTokens": [],
        "taskDurationsMs": [],
        "completedTasks": 0,
        "abortedTasks": 0,
        "incompleteTasks": 0,
        "shortBurn": 0.0,
        "weeklyBurn": 0.0,
        "shortBurnObservations": 0,
        "weeklyBurnObservations": 0,
        "days": {},
        "activityShapes": defaultdict(int),
        "accountAttribution": set(),
        "tokenAttribution": set(),
        "workAttribution": set(),
        "fileHashes": set(),
        "fileHashesByProfile": defaultdict(set),
        "resourceProfileIds": set(),
    }


def _day_bucket(group: dict, day: str) -> dict:
    return group["days"].setdefault(day, {
        "tokens": 0, "workTokens": 0, "completedTasks": 0, "abortedTasks": 0,
        "incompleteTasks": 0, "edits": 0, "fileTouches": 0,
        "filesChanged": 0,
        "tests": 0, "commands": 0, "activeMs": 0,
        "shortBurn": 0.0, "weeklyBurn": 0.0,
        "shortBurnObservations": 0, "weeklyBurnObservations": 0,
    })


def _box_stats(values: Iterable[int | float]) -> dict | None:
    ordered = sorted(float(value) for value in values if _float(value) is not None)
    if not ordered:
        return None

    def percentile(position: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        target = (len(ordered) - 1) * position
        lower = int(math.floor(target))
        upper = int(math.ceil(target))
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (target - lower)

    return {
        "minimum": ordered[0], "q1": percentile(0.25), "median": percentile(0.5),
        "q3": percentile(0.75), "maximum": ordered[-1], "count": len(ordered),
    }


def _normalized(group: dict) -> dict:
    # Use observed non-cache "work" tokens so ratios are not dominated by cache
    # re-reads (which are 94-98% of raw totals) or inferred account totals.
    tokens = max(
        0.0,
        float(group.get("workTokens") or 0)
        if "workTokens" in group
        else float(group.get("totalTokens") or 0) - float(group.get("cachedInputTokens") or 0),
    )
    completed = max(0.0, float(group["completedTasks"]))
    short = max(0.0, float(group["shortBurn"]))
    weekly = max(0.0, float(group["weeklyBurn"]))
    active_hours = max(0.0, float(group["activeMs"]) / 3_600_000)

    def metrics(scale: float) -> dict:
        return {
            "tasks": completed * scale,
            "edits": float(group["edits"]) * scale,
            "files": float(group["filesChanged"]) * scale,
            "tests": float(group["tests"]) * scale,
            "commands": float(group["commands"]) * scale,
            "lines": (float(group["linesAdded"]) + float(group["linesDeleted"])) * scale,
        }

    return {
        "perMillionTokens": metrics(1_000_000 / tokens) if tokens else None,
        "perTenShortPoints": metrics(10 / short) if short else None,
        "perTenWeeklyPoints": metrics(10 / weekly) if weekly else None,
        "perActiveHour": metrics(1 / active_hours) if active_hours else None,
        "tokensPerCompletedTask": (tokens / completed) if completed else None,
        "tasksPerMillionTokens": (completed * 1_000_000 / tokens) if tokens else None,
    }


def _scope_provider(account_id: str) -> str:
    prefix = "provider:"
    return account_id[len(prefix):].strip().lower() if account_id.startswith(prefix) else ""


def _scope_matches(account_id: str, provider: object, profile_id: object = "") -> bool:
    scoped_provider = _scope_provider(account_id)
    if scoped_provider:
        return str(provider or "").strip().lower() == scoped_provider
    return account_id == "all" or str(profile_id or "") == account_id


def _scale_group_for_accounts(group: dict, divisor: int, mode: str) -> dict:
    output = dict(group)
    output.pop("resourceProfileIds", None)
    output["providerAccountCount"] = max(1, int(divisor or 1))
    output["aggregationMode"] = mode
    output["aggregationDivisor"] = max(1, int(divisor or 1)) if mode == "per_provider_account" else 1
    scale = 1.0 / output["aggregationDivisor"]
    output["rawTotals"] = {
        field: float(group.get(field) or 0) for field in _SCALED_FIELDS
    }
    if scale != 1.0:
        for field in _SCALED_FIELDS:
            output[field] = float(group.get(field) or 0) * scale
        output["activityShapes"] = {
            str(key): float(value or 0) * scale
            for key, value in (group.get("activityShapes") or {}).items()
        }
        scaled_days = {}
        for day, bucket in (group.get("days") or {}).items():
            scaled_days[str(day)] = {
                str(key): (
                    float(value or 0)
                    if str(key).endswith("Observations")
                    else float(value or 0) * scale
                )
                for key, value in (bucket or {}).items()
            }
        output["days"] = scaled_days
    average_unique_files = output.pop("_averageUniqueFiles", None)
    if mode == "per_provider_account" and average_unique_files is not None:
        # Unlike additive file touches, unique files must be deduplicated inside
        # each exact profile before taking the provider-account mean.
        output["filesChanged"] = float(average_unique_files)
    output["normalized"] = _normalized(output)
    return output


def build_benchmark_view(
    snapshot: dict,
    *,
    account_id: str = "all",
    model_keys: Iterable[str] | None = None,
    days: int = 30,
    aggregation_mode: str = "combined",
) -> dict:
    """Create model-only chart data; account selection remains an input filter."""
    selected_models = {str(value).lower() for value in (model_keys or []) if value}
    cutoff = (dt.date.today() - dt.timedelta(days=max(1, int(days)) - 1)).isoformat()
    groups: dict[str, dict] = {}
    provider_accounts: dict[str, set[str]] = defaultdict(set)
    mode = (
        "per_provider_account"
        if str(aggregation_mode or "").strip().lower() == "per_provider_account"
        else "combined"
    )

    for row in snapshot.get("modelUsageRows", []):
        if not isinstance(row, dict):
            continue
        if row.get("modelId") == CODEX_ACCOUNT_TOTAL_MODEL:
            continue
        provider = str(row.get("provider") or "").strip().lower()
        row_profile_id = str(row.get("profileId") or "")
        if not _scope_matches(account_id, provider, row_profile_id):
            continue
        key = str(row.get("filterKey") or "").lower()
        if not key or (selected_models and key not in selected_models):
            continue
        group = groups.setdefault(key, _new_model_group(row))
        group["accountAttribution"].add(str(row.get("attributionState") or "unknown"))
        group["tokenAttribution"].add(str(row.get("attributionState") or "unknown"))
        row_has_usage = False
        for day, bucket in (row.get("days") or {}).items():
            if str(day) < cutoff:
                continue
            bucket_total = _number(bucket.get("totalTokens"))
            if bucket_total <= 0:
                continue
            row_has_usage = True
            target = _day_bucket(group, str(day))
            target["tokens"] += bucket_total
            for field in _TOKEN_FIELDS:
                group[field] += _number(bucket.get(field))
            generated = _number(bucket.get("reasoningTokens")) + _number(bucket.get("outputTokens"))
            group["generatedTokens"] += generated
        if row_has_usage and row_profile_id:
            provider_accounts[provider].add(row_profile_id)
            group["resourceProfileIds"].add(row_profile_id)

    for task in snapshot.get("tasks", []):
        if not isinstance(task, dict) or str(task.get("day") or "") < cutoff:
            continue
        key = str(task.get("filterKey") or "").lower()
        if not key or (selected_models and key not in selected_models):
            continue
        profile_ids = set(str(value) for value in task.get("profileIds", []))
        selected_profile = _hash(account_id)
        is_shared_codex = task.get("provider") == "codex" and task.get("accountAttribution") == "shared"
        task_provider = str(task.get("provider") or "").lower()
        scoped_provider = _scope_provider(account_id)
        if scoped_provider and task_provider != scoped_provider:
            continue
        if is_shared_codex and account_id != "all" and not scoped_provider:
            # Codex Desktop history is shared across logins. Showing that whole
            # history under every individual account would duplicate the same
            # work and falsely imply exact account attribution.
            continue
        if not scoped_provider and account_id != "all" and account_id not in profile_ids and selected_profile not in profile_ids and not is_shared_codex:
            continue
        group = groups.get(key)
        if group is None:
            # Work without a canonical usage row is intentionally omitted. It
            # cannot be compared against resources consumed with confidence.
            continue
        group["accountAttribution"].add(str(task.get("accountAttribution") or "unknown"))
        group["workAttribution"].add(str(task.get("accountAttribution") or "unknown"))
        group["activeMs"] += _number(task.get("durationMs"))
        if _number(task.get("ttftMs")):
            group["ttftMs"].append(_number(task.get("ttftMs")))
        task_total = _number(task.get("totalTokens"))
        task_work = max(0, task_total - _number(task.get("cachedInputTokens")))
        group["workTokens"] += task_work
        group["observedTokens"] += task_total
        if task_work:
            group["taskTokens"].append(task_work)
        if _number(task.get("durationMs")):
            group["taskDurationsMs"].append(_number(task.get("durationMs")))
        status = str(task.get("status") or "incomplete")
        status_key = "completedTasks" if status == "completed" else "abortedTasks" if status == "aborted" else "incompleteTasks"
        group[status_key] += 1
        for field in _WORK_FIELDS:
            if field != "fileTouches":
                group[field] += _number(task.get(field))
        group["fileTouches"] += _number(task.get("filesChanged"))
        task_file_hashes = {
            str(value) for value in task.get("fileHashes", []) if value
        }
        group["fileHashes"].update(task_file_hashes)
        if task.get("accountAttribution") == "exact":
            for profile_hash in profile_ids:
                group["fileHashesByProfile"][profile_hash].update(task_file_hashes)
        group["activityShapes"][str(task.get("activityShape") or "Investigation")] += 1
        day = _day_bucket(group, str(task.get("day") or ""))
        day[status_key] += 1
        day["workTokens"] += task_work
        day["edits"] += _number(task.get("edits"))
        day["fileTouches"] += _number(task.get("filesChanged"))
        day["filesChanged"] += _number(task.get("filesChanged"))
        day["tests"] += _number(task.get("tests"))
        day["commands"] += _number(task.get("commands"))
        day["activeMs"] += _number(task.get("durationMs"))

    for segment in snapshot.get("limitSegments", []):
        if not isinstance(segment, dict) or str(segment.get("day") or "") < cutoff:
            continue
        segment_provider = str(segment.get("provider") or "").lower()
        scoped_provider = _scope_provider(account_id)
        if scoped_provider and segment_provider != scoped_provider:
            continue
        if not scoped_provider and account_id != "all" and str(segment.get("profileId") or "") not in {account_id, _hash(account_id)}:
            continue
        for allocation in segment.get("allocations", []):
            key = str(allocation.get("filterKey") or "").lower()
            group = groups.get(key)
            if group is None:
                continue
            short = max(0.0, float(allocation.get("shortBurn") or 0))
            weekly = max(0.0, float(allocation.get("weeklyBurn") or 0))
            group["shortBurn"] += short
            group["weeklyBurn"] += weekly
            if short > 0:
                group["shortBurnObservations"] += 1
            if weekly > 0:
                group["weeklyBurnObservations"] += 1
            day = _day_bucket(group, str(segment.get("day") or ""))
            day["shortBurn"] += short
            day["weeklyBurn"] += weekly
            if short > 0:
                day["shortBurnObservations"] += 1
            if weekly > 0:
                day["weeklyBurnObservations"] += 1

    output: list[dict] = []
    for group in groups.values():
        # A configured model is not usage. Keep only models with actual
        # canonical resource activity in the selected range.
        if int(group["totalTokens"]) <= 0:
            continue
        group["ttftDistribution"] = _box_stats(group.pop("ttftMs"))
        group["taskTokenDistribution"] = _box_stats(group.pop("taskTokens"))
        group["durationDistribution"] = _box_stats(group.pop("taskDurationsMs"))
        group["activityShapes"] = dict(sorted(group["activityShapes"].items()))
        unique_files = set(group.pop("fileHashes"))
        files_by_profile = {
            str(profile_hash): set(values)
            for profile_hash, values in group.pop("fileHashesByProfile").items()
        }
        if unique_files:
            group["filesChanged"] = len(unique_files)
        group["fileHashValues"] = sorted(unique_files)
        group["fileHashValuesByProfile"] = {
            profile_hash: sorted(values)
            for profile_hash, values in sorted(files_by_profile.items())
        }
        group["taskObservations"] = (
            int(group["completedTasks"]) + int(group["abortedTasks"]) + int(group["incompleteTasks"])
        )
        group["durationObservations"] = int((group.get("durationDistribution") or {}).get("count") or 0)
        attributions = set(group.pop("accountAttribution"))
        token_attributions = set(group.pop("tokenAttribution"))
        work_attributions = set(group.pop("workAttribution"))
        group["tokenAttribution"] = (
            "mixed" if "mixed" in token_attributions or len(token_attributions) > 1
            else next(iter(token_attributions), "unknown")
        )
        group["workAttribution"] = (
            "shared" if "shared" in work_attributions
            else "exact" if "exact" in work_attributions
            else "not exposed"
        )
        provider = str(group.get("provider") or "")
        group["workScope"] = (
            "shared Codex history" if "shared" in attributions
            else "not attributable to selected Codex account"
            if provider == "codex" and account_id != "all" and not _scope_provider(account_id)
            else "selected account history" if account_id != "all"
            else "visible account history"
        )
        group["days"] = dict(sorted(group["days"].items()))
        account_count = len(provider_accounts.get(str(group.get("provider") or ""), set())) or 1
        if files_by_profile:
            group["_averageUniqueFiles"] = (
                sum(len(values) for values in files_by_profile.values()) / account_count
            )
        output.append(_scale_group_for_accounts(group, account_count, mode))
    output.sort(key=lambda item: (-int(item["totalTokens"]), str(item["modelLabel"]).lower()))

    journal = []
    visible_keys = {item["filterKey"].lower() for item in output}
    for task in reversed(snapshot.get("tasks", [])):
        key = str(task.get("filterKey") or "").lower()
        if key not in visible_keys or str(task.get("day") or "") < cutoff:
            continue
        profile_ids = set(str(value) for value in task.get("profileIds", []))
        selected_profile = _hash(account_id)
        shared = task.get("provider") == "codex" and task.get("accountAttribution") == "shared"
        task_provider = str(task.get("provider") or "").lower()
        scoped_provider = _scope_provider(account_id)
        if scoped_provider and task_provider != scoped_provider:
            continue
        if shared and account_id != "all" and not scoped_provider:
            continue
        if not scoped_provider and account_id != "all" and account_id not in profile_ids and selected_profile not in profile_ids and not shared:
            continue
        journal.append({
            "day": task.get("day", ""), "modelLabel": task.get("modelLabel", "Model"),
            "modelName": task.get("modelName", task.get("modelLabel", "Model")),
            "reasoningEffort": task.get("reasoningEffort", ""),
            "reasoningEffortName": task.get("reasoningEffortName", ""),
            "filterKey": task.get("filterKey", ""),
            "status": task.get("status", "incomplete"), "activityShape": task.get("activityShape", ""),
            "tokens": _number(task.get("totalTokens")), "activeMs": _number(task.get("durationMs")),
            "edits": _number(task.get("edits")), "files": _number(task.get("filesChanged")),
            "tests": _number(task.get("tests")), "commands": _number(task.get("commands")),
        })
        if len(journal) >= 100:
            break

    return {
        "generatedAtUtc": snapshot.get("generatedAtUtc", ""),
        "days": max(1, int(days)),
        "groups": output,
        "journal": journal,
        "summary": {
            "models": len(output),
            "tokens": sum(float(item["totalTokens"]) for item in output),
            "workTokens": sum(float(item.get("workTokens") or 0) for item in output),
            "completedTasks": sum(float(item["completedTasks"]) for item in output),
            "edits": sum(float(item["edits"]) for item in output),
            "tests": sum(float(item["tests"]) for item in output),
            "shortBurn": sum(float(item["shortBurn"]) for item in output),
            "weeklyBurn": sum(float(item["weeklyBurn"]) for item in output),
        },
        "aggregationMode": mode,
        "providerAccountCounts": {
            provider: len(profile_ids) for provider, profile_ids in sorted(provider_accounts.items())
        },
        "accountScope": account_id,
        "sourceStats": snapshot.get("sourceStats", {}),
        "privacy": snapshot.get("privacy", {}),
    }


def base_model_key(group: dict) -> str:
    """Return the stable provider/model identity without reasoning effort."""
    provider = str(group.get("provider") or "").strip().lower()
    model_id = str(group.get("modelId") or group.get("modelName") or "").strip().lower()
    return f"{provider}|{model_id}"


def aggregate_base_model_groups(groups: Iterable[dict]) -> list[dict]:
    """Combine effort variants while retaining reasoning drill-down metadata."""
    numeric_fields = (
        *_TOKEN_FIELDS, *_WORK_FIELDS, "generatedTokens", "workTokens", "observedTokens", "activeMs",
        "completedTasks", "abortedTasks", "incompleteTasks", "shortBurn", "weeklyBurn",
        "shortBurnObservations", "weeklyBurnObservations", "taskObservations",
        "durationObservations",
    )
    combined: dict[str, dict] = {}
    scopes: dict[str, set[str]] = defaultdict(set)
    token_attributions: dict[str, set[str]] = defaultdict(set)
    work_attributions: dict[str, set[str]] = defaultdict(set)

    for source in groups:
        if not isinstance(source, dict):
            continue
        key = base_model_key(source)
        if not key.strip("|"):
            continue
        target = combined.get(key)
        if target is None:
            target = {
                "filterKey": key,
                "baseModelKey": key,
                "provider": str(source.get("provider") or ""),
                "modelId": str(source.get("modelId") or ""),
                "modelName": str(source.get("modelName") or source.get("modelLabel") or "Model"),
                "modelLabel": str(source.get("modelName") or source.get("modelLabel") or "Model"),
                "reasoningEffort": "",
                "reasoningEffortName": "",
                "reasoningVariants": [],
                "variantFilterKeys": [],
                "days": {},
                "activityShapes": defaultdict(int),
                "fileHashValues": set(),
                "fileHashValuesByProfile": defaultdict(set),
                "taskTokenDistribution": None,
                "durationDistribution": None,
                "ttftDistribution": None,
                "aggregationMode": str(source.get("aggregationMode") or "combined"),
                "aggregationDivisor": int(source.get("aggregationDivisor") or 1),
                "providerAccountCount": int(source.get("providerAccountCount") or 1),
                "hasWorkTokenSource": False,
                **{field: 0 for field in numeric_fields},
            }
            combined[key] = target

        for field in numeric_fields:
            value = source.get(field)
            if field == "taskObservations" and value is None:
                value = sum(
                    float(source.get(key) or 0)
                    for key in ("completedTasks", "abortedTasks", "incompleteTasks")
                )
            elif field == "durationObservations" and value is None:
                value = int(float(source.get("activeMs") or 0) > 0)
            elif field == "shortBurnObservations" and value is None:
                value = int(float(source.get("shortBurn") or 0) > 0)
            elif field == "weeklyBurnObservations" and value is None:
                value = int(float(source.get("weeklyBurn") or 0) > 0)
            target[field] += float(_float(value) or 0)
        if "workTokens" in source:
            target["hasWorkTokenSource"] = True
        for day, bucket in (source.get("days") or {}).items():
            destination = _day_bucket(target, str(day))
            for field in destination:
                destination[field] += float(_float((bucket or {}).get(field)) or 0)
        for shape, count in (source.get("activityShapes") or {}).items():
            target["activityShapes"][str(shape)] += float(count or 0)
        target["fileHashValues"].update(
            str(value) for value in source.get("fileHashValues", []) if value
        )
        for profile_hash, values in (source.get("fileHashValuesByProfile") or {}).items():
            target["fileHashValuesByProfile"][str(profile_hash)].update(
                str(value) for value in values if value
            )

        effort = str(source.get("reasoningEffort") or "")
        effort_name = str(source.get("reasoningEffortName") or "") or "Default"
        target["reasoningVariants"].append({
            "effort": effort,
            "name": effort_name,
            "filterKey": str(source.get("filterKey") or ""),
            "tokens": float(source.get("totalTokens") or 0),
        })
        target["variantFilterKeys"].append(str(source.get("filterKey") or ""))
        scope = str(source.get("workScope") or "").strip()
        if scope:
            scopes[key].add(scope)
        token_attributions[key].add(str(source.get("tokenAttribution") or "unknown"))
        work_attributions[key].add(str(source.get("workAttribution") or "not exposed"))

    output = []
    for key, group in combined.items():
        variants = {
            (item["effort"], item["filterKey"]): item
            for item in group["reasoningVariants"]
        }
        group["reasoningVariants"] = sorted(
            variants.values(), key=lambda item: (-item["tokens"], item["name"].lower())
        )
        group["variantFilterKeys"] = [
            item["filterKey"] for item in group["reasoningVariants"] if item["filterKey"]
        ]
        group["activityShapes"] = dict(sorted(group["activityShapes"].items()))
        group["days"] = dict(sorted(group["days"].items()))
        file_hashes = set(group["fileHashValues"])
        group["fileHashValues"] = sorted(file_hashes)
        files_by_profile = {
            profile_hash: set(values)
            for profile_hash, values in group["fileHashValuesByProfile"].items()
        }
        group["fileHashValuesByProfile"] = {
            profile_hash: sorted(values)
            for profile_hash, values in sorted(files_by_profile.items())
        }
        if group.get("aggregationMode") == "per_provider_account" and files_by_profile:
            group["filesChanged"] = sum(
                len(values) for values in files_by_profile.values()
            ) / max(1, int(group.get("providerAccountCount") or 1))
        elif file_hashes:
            group["filesChanged"] = len(file_hashes) / max(1, int(group.get("aggregationDivisor") or 1))
        known_scopes = scopes.get(key, set())
        group["workScope"] = (
            "shared Codex history" if "shared Codex history" in known_scopes
            else next(iter(sorted(known_scopes)), "visible account history")
        )
        token_states = token_attributions.get(key, {"unknown"})
        group["tokenAttribution"] = (
            "mixed" if "mixed" in token_states or len(token_states) > 1
            else next(iter(token_states), "unknown")
        )
        work_states = work_attributions.get(key, {"not exposed"})
        group["workAttribution"] = (
            "shared" if "shared" in work_states
            else "exact" if "exact" in work_states
            else "not exposed"
        )
        if not group.pop("hasWorkTokenSource", False):
            group["workTokens"] = max(
                0.0,
                float(group.get("totalTokens") or 0) - float(group.get("cachedInputTokens") or 0),
            )
        group["normalized"] = _normalized(group)
        output.append(group)

    output.sort(key=lambda item: (-int(item["totalTokens"]), str(item["modelName"]).lower()))
    return output


_HEAD_TO_HEAD_METRICS = (
    "workTokens", "totalTokens", "completedTasks", "edits", "filesChanged", "tests",
    "commands", "activeMs", "shortBurn", "weeklyBurn",
)


def _comparison_metric_value(group: dict, metric: str) -> float | None:
    short_observations = group.get("shortBurnObservations")
    weekly_observations = group.get("weeklyBurnObservations")
    duration_observations = group.get("durationObservations")
    task_observations = group.get("taskObservations")
    if short_observations is None:
        short_observations = int(float(group.get("shortBurn") or 0) > 0)
    if weekly_observations is None:
        weekly_observations = int(float(group.get("weeklyBurn") or 0) > 0)
    if duration_observations is None:
        duration_observations = int(float(group.get("activeMs") or 0) > 0)
    if task_observations is None:
        task_observations = sum(
            int(group.get(key) or 0)
            for key in ("completedTasks", "abortedTasks", "incompleteTasks")
        )
    if metric == "shortBurn" and int(short_observations or 0) <= 0:
        return None
    if metric == "weeklyBurn" and int(weekly_observations or 0) <= 0:
        return None
    if metric == "activeMs" and int(duration_observations or 0) <= 0:
        return None
    if metric in {
        "workTokens", "completedTasks", "edits", "filesChanged", "tests", "commands",
    } and int(task_observations or 0) <= 0:
        return None
    return float(group.get(metric) or 0)


def build_head_to_head(
    groups: Iterable[dict],
    selections: Iterable[dict],
) -> dict:
    """Resolve two-to-four base-model/reasoning selections and their deltas.

    The first valid selection is the baseline. ``reasoning=all`` compares a
    consolidated base model; an explicit effort compares only that observed
    reasoning stream. Deltas remain descriptive and are never scored.
    """
    source_groups = [dict(group) for group in groups if isinstance(group, dict)]
    base_groups = {
        str(group.get("baseModelKey") or base_model_key(group)): group
        for group in aggregate_base_model_groups(source_groups)
    }
    selected: list[dict] = []
    selected_keys: set[str] = set()
    for request in selections:
        if not isinstance(request, dict) or len(selected) >= 4:
            continue
        base_key = str(request.get("baseModelKey") or "")
        reasoning_value = request.get("reasoning", "all")
        reasoning = "all" if reasoning_value is None else str(reasoning_value)
        selection_key = f"{base_key}::{reasoning}"
        if not base_key or selection_key in selected_keys:
            continue
        if reasoning == "all":
            source = base_groups.get(base_key)
        else:
            source = next(
                (
                    group for group in source_groups
                    if base_model_key(group) == base_key
                    and str(group.get("reasoningEffort") or "") == reasoning
                ),
                None,
            )
        if source is None:
            continue
        group = dict(source)
        group["baseModelKey"] = base_key
        group["comparisonKey"] = selection_key
        group["filterKey"] = selection_key
        effort_name = str(group.get("reasoningEffortName") or "") or "Default"
        group["comparisonReasoning"] = "All reasoning" if reasoning == "all" else effort_name
        group["reasoningEffortName"] = group["comparisonReasoning"]
        group["modelLabel"] = (
            str(group.get("modelName") or "Model")
            if reasoning == "all"
            else f"{group.get('modelName') or 'Model'} - {effort_name}"
        )
        selected.append(group)
        selected_keys.add(selection_key)

    baseline = selected[0] if selected else None
    rows = []
    for index, group in enumerate(selected):
        metrics = {}
        for metric in _HEAD_TO_HEAD_METRICS:
            value = _comparison_metric_value(group, metric)
            baseline_value = _comparison_metric_value(baseline or {}, metric)
            delta = value - baseline_value if value is not None and baseline_value is not None else None
            metrics[metric] = {
                "value": value,
                "delta": delta,
                "percent": (delta * 100 / baseline_value) if delta is not None and baseline_value else None,
            }
        normalized = group.get("normalized") or {}
        baseline_normalized = (baseline or {}).get("normalized") or {}
        for output_key, source_key in (
            ("tokensPerTask", "tokensPerCompletedTask"),
            ("tasksPerMillion", "tasksPerMillionTokens"),
        ):
            raw_value = normalized.get(source_key)
            raw_baseline = baseline_normalized.get(source_key)
            value = float(raw_value) if raw_value is not None else None
            baseline_value = float(raw_baseline) if raw_baseline is not None else None
            delta = value - baseline_value if value is not None and baseline_value is not None else None
            metrics[output_key] = {
                "value": value,
                "delta": delta,
                "percent": (delta * 100 / baseline_value) if delta is not None and baseline_value else None,
            }
        rows.append({
            "group": group,
            "baseline": index == 0,
            "metrics": metrics,
        })
    return {"groups": selected, "rows": rows}


def productivity_density_csv(view: dict) -> str:
    """Export model-only raw density metrics; never include account labels."""
    output = io.StringIO(newline="")
    fields = (
        "provider", "model", "effort", "aggregation", "provider_accounts",
        "attributed_provider_tokens", "task_attributed_tokens", "work_tokens", "active_hours",
        "short_burn", "short_burn_intervals", "weekly_burn", "weekly_burn_intervals",
        "tasks_completed", "tasks_aborted", "tasks_incomplete", "edits", "unique_files",
        "file_touches",
        "lines_added", "lines_deleted", "tests", "tests_passed", "commands",
        "tool_calls", "tool_errors", "rollbacks", "compactions", "work_scope",
    )
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for group in view.get("groups", []):
        writer.writerow({
            "provider": group.get("provider", ""),
            "model": group.get("modelName", ""),
            "effort": group.get("reasoningEffortName", ""),
            "aggregation": group.get("aggregationMode", "combined"),
            "provider_accounts": group.get("providerAccountCount", 1),
            "attributed_provider_tokens": group.get("totalTokens", 0),
            "task_attributed_tokens": group.get("observedTokens", 0),
            "work_tokens": group.get("workTokens", 0),
            "active_hours": round(float(group.get("activeMs", 0)) / 3_600_000, 3),
            "short_burn": round(float(group.get("shortBurn", 0)), 3),
            "short_burn_intervals": group.get("shortBurnObservations", 0),
            "weekly_burn": round(float(group.get("weeklyBurn", 0)), 3),
            "weekly_burn_intervals": group.get("weeklyBurnObservations", 0),
            "tasks_completed": group.get("completedTasks", 0),
            "tasks_aborted": group.get("abortedTasks", 0),
            "tasks_incomplete": group.get("incompleteTasks", 0),
            "edits": group.get("edits", 0),
            "unique_files": group.get("filesChanged", 0),
            "file_touches": group.get("fileTouches", 0),
            "lines_added": group.get("linesAdded", 0), "lines_deleted": group.get("linesDeleted", 0),
            "tests": group.get("tests", 0), "tests_passed": group.get("testsPassed", 0),
            "commands": group.get("commands", 0), "tool_calls": group.get("toolCalls", 0),
            "tool_errors": group.get("toolErrors", 0), "rollbacks": group.get("rollbacks", 0),
            "compactions": group.get("compactions", 0), "work_scope": group.get("workScope", ""),
        })
    return output.getvalue()


def privacy_violations(snapshot: dict) -> list[str]:
    """Return persisted task fields that would violate the storage contract."""
    forbidden = {
        "prompt", "response", "reasoning", "content", "command", "output",
        "filePath", "path", "diff", "profileName", "accountName", "email",
    }
    violations: list[str] = []
    for task in snapshot.get("tasks", []):
        for key in task:
            if key in forbidden:
                violations.append(str(key))
        identifiers = [task.get("profileId")] + list(task.get("profileIds") or [])
        for identifier in identifiers:
            text = str(identifier or "")
            if text and (len(text) != 64 or any(char not in "0123456789abcdef" for char in text.lower())):
                violations.append("unhashedProfileId")
    return sorted(set(violations))


__all__ = [
    "aggregate_base_model_groups", "base_model_key", "build_benchmark_view",
    "build_head_to_head", "privacy_violations", "productivity_density_csv",
]
