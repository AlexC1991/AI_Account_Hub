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
    "filesChanged", "linesAdded", "linesDeleted", "rollbacks", "compactions",
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
        "days": {},
        "activityShapes": defaultdict(int),
        "accountAttribution": set(),
        "fileHashes": set(),
    }


def _day_bucket(group: dict, day: str) -> dict:
    return group["days"].setdefault(day, {
        "tokens": 0, "workTokens": 0, "tasks": 0, "edits": 0, "files": 0,
        "tests": 0, "commands": 0, "activeMs": 0,
        "shortBurn": 0.0, "weeklyBurn": 0.0,
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
    tokens = max(0, int(group.get("workTokens") or 0))
    completed = max(0, int(group["completedTasks"]))
    short = max(0.0, float(group["shortBurn"]))
    weekly = max(0.0, float(group["weeklyBurn"]))
    active_hours = max(0.0, float(group["activeMs"]) / 3_600_000)

    def metrics(scale: float) -> dict:
        return {
            "tasks": completed * scale,
            "edits": int(group["edits"]) * scale,
            "files": int(group["filesChanged"]) * scale,
            "tests": int(group["tests"]) * scale,
            "commands": int(group["commands"]) * scale,
            "lines": (int(group["linesAdded"]) + int(group["linesDeleted"])) * scale,
        }

    return {
        "perMillionTokens": metrics(1_000_000 / tokens) if tokens else None,
        "perTenShortPoints": metrics(10 / short) if short else None,
        "perTenWeeklyPoints": metrics(10 / weekly) if weekly else None,
        "perActiveHour": metrics(1 / active_hours) if active_hours else None,
        "tokensPerCompletedTask": (tokens / completed) if completed else None,
        "tasksPerMillionTokens": (completed * 1_000_000 / tokens) if tokens else None,
    }


def build_benchmark_view(
    snapshot: dict,
    *,
    account_id: str = "all",
    model_keys: Iterable[str] | None = None,
    days: int = 30,
) -> dict:
    """Create model-only chart data; account selection remains an input filter."""
    selected_models = {str(value).lower() for value in (model_keys or []) if value}
    cutoff = (dt.date.today() - dt.timedelta(days=max(1, int(days)) - 1)).isoformat()
    groups: dict[str, dict] = {}

    for row in snapshot.get("modelUsageRows", []):
        if not isinstance(row, dict):
            continue
        if row.get("modelId") == CODEX_ACCOUNT_TOTAL_MODEL:
            continue
        if account_id != "all" and str(row.get("profileId") or "") != account_id:
            continue
        key = str(row.get("filterKey") or "").lower()
        if not key or (selected_models and key not in selected_models):
            continue
        group = groups.setdefault(key, _new_model_group(row))
        group["accountAttribution"].add(str(row.get("attributionState") or "unknown"))
        for day, bucket in (row.get("days") or {}).items():
            if str(day) < cutoff:
                continue
            target = _day_bucket(group, str(day))
            target["tokens"] += _number(bucket.get("totalTokens"))
            for field in _TOKEN_FIELDS:
                group[field] += _number(bucket.get(field))

    for task in snapshot.get("tasks", []):
        if not isinstance(task, dict) or str(task.get("day") or "") < cutoff:
            continue
        key = str(task.get("filterKey") or "").lower()
        if not key or (selected_models and key not in selected_models):
            continue
        profile_ids = set(str(value) for value in task.get("profileIds", []))
        selected_profile = _hash(account_id)
        is_shared_codex = task.get("provider") == "codex" and task.get("accountAttribution") == "shared"
        if account_id != "all" and account_id not in profile_ids and selected_profile not in profile_ids and not is_shared_codex:
            continue
        group = groups.get(key)
        if group is None:
            # Work without a canonical usage row is intentionally omitted. It
            # cannot be compared against resources consumed with confidence.
            continue
        group["accountAttribution"].add(str(task.get("accountAttribution") or "unknown"))
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
            group[field] += _number(task.get(field))
        group["fileHashes"].update(str(value) for value in task.get("fileHashes", []) if value)
        group["activityShapes"][str(task.get("activityShape") or "Investigation")] += 1
        day = _day_bucket(group, str(task.get("day") or ""))
        day["tasks"] += 1
        day["workTokens"] += task_work
        day["edits"] += _number(task.get("edits"))
        day["files"] += _number(task.get("filesChanged"))
        day["tests"] += _number(task.get("tests"))
        day["commands"] += _number(task.get("commands"))
        day["activeMs"] += _number(task.get("durationMs"))

    for segment in snapshot.get("limitSegments", []):
        if not isinstance(segment, dict) or str(segment.get("day") or "") < cutoff:
            continue
        if account_id != "all" and str(segment.get("profileId") or "") not in {account_id, _hash(account_id)}:
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
            day = _day_bucket(group, str(segment.get("day") or ""))
            day["shortBurn"] += short
            day["weeklyBurn"] += weekly

    output: list[dict] = []
    for group in groups.values():
        # A configured model is not usage. Keep only models with actual
        # canonical resource activity in the selected range.
        if int(group["totalTokens"]) <= 0:
            continue
        # No parsed tasks (e.g. inferred account-total rows with no session
        # composition): fall back to the best available non-cache estimate.
        if int(group.get("workTokens") or 0) <= 0:
            group["workTokens"] = max(0, int(group["totalTokens"]) - int(group["cachedInputTokens"]))
        # Fold reasoning into output for a fair cross-provider composition:
        # Codex reports reasoning_output_tokens separately, but Anthropic bundles
        # thinking into output_tokens and never itemises it. Left split, Claude
        # shows a 0 "reasoning" slice against Codex's ~35%, which misreads as
        # "Claude doesn't reason". Total and workTokens are unchanged.
        group["outputTokens"] = int(group.get("outputTokens") or 0) + int(group.get("reasoningTokens") or 0)
        group["reasoningTokens"] = 0
        group["ttftDistribution"] = _box_stats(group.pop("ttftMs"))
        group["taskTokenDistribution"] = _box_stats(group.pop("taskTokens"))
        group["durationDistribution"] = _box_stats(group.pop("taskDurationsMs"))
        group["activityShapes"] = dict(sorted(group["activityShapes"].items()))
        unique_files = set(group.pop("fileHashes"))
        if unique_files:
            group["filesChanged"] = len(unique_files)
        group["fileHashValues"] = sorted(unique_files)
        attributions = set(group.pop("accountAttribution"))
        group["workScope"] = (
            "shared Codex history" if "shared" in attributions
            else "selected account history" if account_id != "all"
            else "visible account history"
        )
        group["normalized"] = _normalized(group)
        group["days"] = dict(sorted(group["days"].items()))
        output.append(group)
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
        if account_id != "all" and account_id not in profile_ids and selected_profile not in profile_ids and not shared:
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
            "tokens": sum(int(item["totalTokens"]) for item in output),
            "workTokens": sum(int(item.get("workTokens") or 0) for item in output),
            "completedTasks": sum(int(item["completedTasks"]) for item in output),
            "edits": sum(int(item["edits"]) for item in output),
            "tests": sum(int(item["tests"]) for item in output),
            "shortBurn": sum(float(item["shortBurn"]) for item in output),
            "weeklyBurn": sum(float(item["weeklyBurn"]) for item in output),
        },
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
        *_TOKEN_FIELDS, *_WORK_FIELDS, "workTokens", "observedTokens", "activeMs",
        "completedTasks", "abortedTasks", "incompleteTasks", "shortBurn", "weeklyBurn",
    )
    combined: dict[str, dict] = {}
    scopes: dict[str, set[str]] = defaultdict(set)

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
                "taskTokenDistribution": None,
                "durationDistribution": None,
                "ttftDistribution": None,
                **{field: 0 for field in numeric_fields},
            }
            combined[key] = target

        for field in numeric_fields:
            target[field] += _number(source.get(field))
        for day, bucket in (source.get("days") or {}).items():
            destination = _day_bucket(target, str(day))
            for field in destination:
                destination[field] += _number((bucket or {}).get(field))
        for shape, count in (source.get("activityShapes") or {}).items():
            target["activityShapes"][str(shape)] += int(count or 0)
        target["fileHashValues"].update(
            str(value) for value in source.get("fileHashValues", []) if value
        )

        effort = str(source.get("reasoningEffort") or "")
        effort_name = str(source.get("reasoningEffortName") or "") or "Default"
        target["reasoningVariants"].append({
            "effort": effort,
            "name": effort_name,
            "filterKey": str(source.get("filterKey") or ""),
            "tokens": int(source.get("totalTokens") or 0),
        })
        target["variantFilterKeys"].append(str(source.get("filterKey") or ""))
        scope = str(source.get("workScope") or "").strip()
        if scope:
            scopes[key].add(scope)

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
        if file_hashes:
            group["filesChanged"] = len(file_hashes)
        known_scopes = scopes.get(key, set())
        group["workScope"] = (
            "shared Codex history" if "shared Codex history" in known_scopes
            else next(iter(sorted(known_scopes)), "visible account history")
        )
        group["normalized"] = _normalized(group)
        output.append(group)

    output.sort(key=lambda item: (-int(item["totalTokens"]), str(item["modelName"]).lower()))
    return output


_HEAD_TO_HEAD_METRICS = (
    "workTokens", "totalTokens", "completedTasks", "edits", "filesChanged", "tests",
    "commands", "activeMs", "shortBurn", "weeklyBurn",
)


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
            value = float(group.get(metric) or 0)
            baseline_value = float((baseline or {}).get(metric) or 0)
            delta = value - baseline_value
            metrics[metric] = {
                "value": value,
                "delta": delta,
                "percent": (delta * 100 / baseline_value) if baseline_value else None,
            }
        normalized = group.get("normalized") or {}
        baseline_normalized = (baseline or {}).get("normalized") or {}
        for output_key, source_key in (
            ("tokensPerTask", "tokensPerCompletedTask"),
            ("tasksPerMillion", "tasksPerMillionTokens"),
        ):
            value = float(normalized.get(source_key) or 0)
            baseline_value = float(baseline_normalized.get(source_key) or 0)
            delta = value - baseline_value
            metrics[output_key] = {
                "value": value,
                "delta": delta,
                "percent": (delta * 100 / baseline_value) if baseline_value else None,
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
        "provider", "model", "effort", "tokens", "active_hours", "short_burn",
        "weekly_burn", "tasks_completed", "tasks_aborted", "edits", "files",
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
            "tokens": group.get("totalTokens", 0),
            "active_hours": round(float(group.get("activeMs", 0)) / 3_600_000, 3),
            "short_burn": round(float(group.get("shortBurn", 0)), 3),
            "weekly_burn": round(float(group.get("weeklyBurn", 0)), 3),
            "tasks_completed": group.get("completedTasks", 0),
            "tasks_aborted": group.get("abortedTasks", 0),
            "edits": group.get("edits", 0), "files": group.get("filesChanged", 0),
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
