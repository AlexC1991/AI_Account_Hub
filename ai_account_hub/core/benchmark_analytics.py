"""Passive real-world resource and engineering-activity analytics.

The importer reads provider JSONL files without modifying them and persists
only numeric task/activity aggregates. Prompt text, response text, commands,
source code, diffs, file paths, and tool output are never stored.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from ai_account_hub.core import hub_core
from ai_account_hub.core.history_db import history_limit_entries
from ai_account_hub.core.model_analytics import (
    _display_effort,
    _display_model,
    _filter_key,
    _iso_day,
    _model_label,
    _normalize_effort,
    build_model_analytics,
)


PARSER_VERSION = 4  # bump: recognise the newer Codex "exec" tool for commands/tests
DEFAULT_HISTORY_DAYS = 180
MAX_LIMIT_GAP_MINUTES = 20
CODEX_SHARED_PROFILE = "__codex_shared__"

_COMMAND_TOOLS = {"shell_command", "exec_command", "bash", "powershell", "exec", "shell"}
_EDIT_TOOLS = {"apply_patch", "edit", "write"}
_TEST_PATTERN = re.compile(
    r"(?ix)(?:^|[;&|]\s*)"
    r"(?:python\s+-m\s+pytest|pytest|py\s+-\d+(?:\.\d+)?\s+-m\s+pytest|"
    r"npm\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test|yarn\s+test|"
    r"cargo\s+test|go\s+test|dotnet\s+test|mvn\s+test|gradle\s+test|"
    r"ctest|vitest|jest)\b"
)
_EXIT_CODE_PATTERN = re.compile(r"(?im)^\s*Exit code:\s*(-?\d+)\s*$")
# Newer Codex models (e.g. gpt-5.6-*) invoke the "exec" custom tool with a
# JS-ish body instead of a JSON object, e.g.
#   const r = await tools.exec_command({"cmd": "pytest -q"});
# Locate each such call so the real command text can be recovered.
_EXEC_WRAPPER_PATTERN = re.compile(r"(?:exec_command|local_shell|container\.exec|\bshell)\s*\(\s*\{")


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _hash(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", "replace")).hexdigest()


def _number(value: object) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _parse_time(value: object) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _iso_time(value: object) -> str:
    parsed = _parse_time(value)
    return parsed.isoformat() if parsed else ""


def _task_id(provider: str, session_id: object, turn_id: object) -> str:
    return _hash("|".join((provider, str(session_id or ""), str(turn_id or ""))))


def _new_task(
    provider: str,
    profile_id: str,
    session_id: str,
    turn_id: str,
    *,
    project_id: str = "",
    account_attribution: str = "exact",
) -> dict[str, Any]:
    return {
        "taskId": _task_id(provider, session_id, turn_id),
        "provider": provider,
        "profileId": _hash(profile_id) if profile_id else "",
        "profileIds": [_hash(profile_id)] if profile_id else [],
        "accountAttribution": account_attribution,
        "sessionId": _hash(session_id),
        "turnId": _hash(turn_id),
        "projectId": project_id,
        "startedAtUtc": "",
        "completedAtUtc": "",
        "day": "",
        "status": "incomplete",
        "modelId": "",
        "modelName": "",
        "reasoningEffort": "",
        "reasoningEffortName": "",
        "modelLabel": "",
        "filterKey": "",
        "inputTokens": 0,
        "cachedInputTokens": 0,
        "cacheCreationTokens": 0,
        "reasoningTokens": 0,
        "outputTokens": 0,
        "totalTokens": 0,
        "durationMs": 0,
        "ttftMs": 0,
        "toolCalls": 0,
        "toolErrors": 0,
        "commands": 0,
        "tests": 0,
        "testsPassed": 0,
        "edits": 0,
        "fileHashes": [],
        "filesChanged": 0,
        "linesAdded": 0,
        "linesDeleted": 0,
        "rollbacks": 0,
        "compactions": 0,
        "activityShape": "Investigation",
        "provenance": "observed",
    }


def _set_model(task: dict[str, Any], model_id: object, effort: object = "") -> None:
    model = str(model_id or "").strip()
    if not model:
        return
    normalized_effort = _normalize_effort(effort)
    task["modelId"] = model
    task["modelName"] = _display_model(model)
    task["reasoningEffort"] = normalized_effort
    task["reasoningEffortName"] = _display_effort(normalized_effort)
    task["modelLabel"] = _model_label(model, normalized_effort)
    task["filterKey"] = _filter_key(task["provider"], model, normalized_effort)


def _finalize_task(task: dict[str, Any]) -> dict[str, Any]:
    files = sorted(set(str(value) for value in task.pop("fileHashes", []) if value))
    task["fileHashes"] = files
    task["filesChanged"] = len(files)
    if not task.get("day"):
        task["day"] = _iso_day(task.get("startedAtUtc") or task.get("completedAtUtc"))
    if task["edits"] or task["linesAdded"] or task["linesDeleted"]:
        task["activityShape"] = "Change"
    elif task["tests"] and task["tests"] >= max(1, task["commands"] // 2):
        task["activityShape"] = "Verification"
    elif task["toolCalls"] and task["commands"]:
        task["activityShape"] = "Mixed"
    else:
        task["activityShape"] = "Investigation"
    return task


def _command_text(value: object) -> str:
    """Flatten a command field that may be a string or an argv list."""
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return str(value or "")


def _command_from_arguments(arguments: object) -> str:
    if isinstance(arguments, dict):
        return _command_text(arguments.get("command") or arguments.get("cmd"))
    text = str(arguments or "")
    if not text:
        return ""
    # Direct JSON payload: {"command": ...} or {"cmd": ...}.
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        decoded = None
    if isinstance(decoded, dict):
        return _command_text(decoded.get("command") or decoded.get("cmd"))
    # Newer Codex "exec" custom tool wraps the call in a JS-ish snippet. Pull the
    # command out of every ``exec_command({...})`` object argument it contains so
    # a chained call (``a; b``) still contributes its full command text.
    parts: list[str] = []
    decoder = json.JSONDecoder()
    for match in _EXEC_WRAPPER_PATTERN.finditer(text):
        brace = text.find("{", match.start())
        if brace < 0:
            continue
        try:
            obj, _end = decoder.raw_decode(text[brace:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            command = _command_text(obj.get("cmd") or obj.get("command"))
            if command:
                parts.append(command)
    return "\n".join(parts)


def _is_test_command(command: str) -> bool:
    return bool(command and _TEST_PATTERN.search(command))


def _count_diff(diff: object) -> tuple[int, int]:
    added = deleted = 0
    for line in str(diff or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            deleted += 1
    return added, deleted


def _exclusive_openai_usage(usage: dict) -> tuple[int, int, int, int, int, int]:
    input_tokens = _number(usage.get("input_tokens"))
    cached = _number(usage.get("cached_input_tokens"))
    reasoning = _number(usage.get("reasoning_output_tokens"))
    output = _number(usage.get("output_tokens"))
    uncached = max(0, input_tokens - cached)
    visible_output = max(0, output - reasoning)
    total = uncached + cached + reasoning + visible_output
    return uncached, cached, 0, reasoning, visible_output, total


def _new_source_payload() -> dict[str, Any]:
    return {"tasks": [], "events": 0, "files": 1}


def _benchmark_db() -> Path:
    return hub_core.HISTORY_DB_FILE


def init_benchmark_db() -> None:
    hub_core.LAUNCHER_ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(_benchmark_db())
    try:
        connection.executescript(
            """
            create table if not exists benchmark_source_cache (
                source_id text primary key,
                provider text not null,
                profile_id text not null,
                file_size integer not null,
                modified_ns integer not null,
                parser_version integer not null,
                payload_json text not null,
                scanned_at_utc text not null
            );
            create table if not exists benchmark_tasks (
                task_id text primary key,
                provider text not null,
                profile_id text not null,
                day text not null,
                model_key text not null,
                payload_json text not null,
                updated_at_utc text not null
            );
            create index if not exists idx_benchmark_tasks_day on benchmark_tasks(day);
            create index if not exists idx_benchmark_tasks_model on benchmark_tasks(model_key);
            """
        )
        # Parser v3 hashes profile identifiers before persistence. Remove older
        # payloads so a legacy Codex home path cannot remain in the cache.
        connection.execute(
            "delete from benchmark_source_cache where parser_version < ?",
            (PARSER_VERSION,),
        )
        cutoff = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400)
        ).isoformat()
        connection.execute(
            "delete from benchmark_source_cache where scanned_at_utc < ?",
            (cutoff,),
        )
        connection.execute(
            "delete from benchmark_tasks where updated_at_utc < ?",
            (cutoff,),
        )
        connection.commit()
    finally:
        connection.close()


def _load_cached_source(
    connection: sqlite3.Connection,
    source_id: str,
    size: int,
    modified_ns: int,
) -> dict | None:
    row = connection.execute(
        "select payload_json, file_size, modified_ns, parser_version "
        "from benchmark_source_cache where source_id = ?",
        (source_id,),
    ).fetchone()
    if row is None or int(row[1]) != size or int(row[2]) != modified_ns or int(row[3]) != PARSER_VERSION:
        return None
    try:
        value = json.loads(str(row[0]))
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _save_cached_source(
    connection: sqlite3.Connection,
    source_id: str,
    provider: str,
    profile_id: str,
    size: int,
    modified_ns: int,
    payload: dict,
) -> None:
    connection.execute(
        """
        insert into benchmark_source_cache (
            source_id, provider, profile_id, file_size, modified_ns,
            parser_version, payload_json, scanned_at_utc
        ) values (?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(source_id) do update set
            provider=excluded.provider, profile_id=excluded.profile_id,
            file_size=excluded.file_size, modified_ns=excluded.modified_ns,
            parser_version=excluded.parser_version,
            payload_json=excluded.payload_json, scanned_at_utc=excluded.scanned_at_utc
        """,
        (
            source_id, provider, profile_id, size, modified_ns, PARSER_VERSION,
            json.dumps(payload, separators=(",", ":"), sort_keys=True), _utc_now(),
        ),
    )


def _parse_codex_file(
    path: Path,
    profile_id: str,
    account_attribution: str,
) -> dict:
    payload = _new_source_payload()
    tasks: dict[str, dict[str, Any]] = {}
    session_id = str(path.stem)
    project_id = ""
    current_turn = ""
    current_model = ""
    current_effort = ""
    pending_calls: dict[str, tuple[str, bool]] = {}
    seen_events: set[str] = set()

    def get_task(turn_id: object = "") -> dict[str, Any]:
        nonlocal current_turn
        turn = str(turn_id or current_turn or "unknown")
        current_turn = turn
        key = _task_id("codex", session_id, turn)
        task = tasks.get(key)
        if task is None:
            task = _new_task(
                "codex", profile_id, session_id, turn,
                project_id=project_id,
                account_attribution=account_attribution,
            )
            tasks[key] = task
        if current_model:
            _set_model(task, current_model, current_effort)
        return task

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return payload
    with handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(row, dict):
                continue
            payload["events"] += 1
            row_type = str(row.get("type") or "")
            value = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            timestamp = str(row.get("timestamp") or value.get("timestamp") or "")
            if row_type == "session_meta":
                session_id = str(value.get("id") or value.get("session_id") or session_id)
                project_id = _hash(value.get("cwd") or "") if value.get("cwd") else ""
                continue
            if row_type == "turn_context":
                current_turn = str(value.get("turn_id") or current_turn or f"turn-{line_number}")
                current_model = str(value.get("model") or current_model)
                current_effort = _normalize_effort(
                    value.get("effort")
                    or value.get("reasoning_effort")
                    or value.get("model_reasoning_effort")
                    or current_effort
                )
                task = get_task(current_turn)
                if not task["startedAtUtc"]:
                    task["startedAtUtc"] = _iso_time(timestamp)
                continue

            event_type = str(value.get("type") or "")
            if row_type == "event_msg":
                turn_id = str(value.get("turn_id") or current_turn or "unknown")
                task = get_task(turn_id)
                if event_type == "task_started":
                    task["startedAtUtc"] = _iso_time(value.get("started_at") or timestamp)
                elif event_type == "task_complete":
                    task["completedAtUtc"] = _iso_time(value.get("completed_at") or timestamp)
                    task["status"] = "completed"
                    task["durationMs"] = max(task["durationMs"], _number(value.get("duration_ms")))
                    task["ttftMs"] = max(task["ttftMs"], _number(value.get("time_to_first_token_ms")))
                elif event_type == "turn_aborted":
                    task["completedAtUtc"] = _iso_time(value.get("completed_at") or timestamp)
                    task["status"] = "aborted"
                    task["durationMs"] = max(task["durationMs"], _number(value.get("duration_ms")))
                elif event_type == "thread_rolled_back":
                    task["rollbacks"] += max(1, _number(value.get("num_turns")))
                elif event_type == "context_compacted":
                    task["compactions"] += 1
                elif event_type == "mcp_tool_call_end":
                    result = value.get("result") if isinstance(value.get("result"), dict) else {}
                    if result and "Ok" not in result:
                        task["toolErrors"] += 1
                elif event_type == "patch_apply_end" and bool(value.get("success")):
                    event_id = str(value.get("call_id") or f"{timestamp}:{line_number}")
                    event_hash = _hash(f"patch|{session_id}|{event_id}")
                    if event_hash in seen_events:
                        continue
                    seen_events.add(event_hash)
                    task["edits"] += 1
                    changes = value.get("changes") if isinstance(value.get("changes"), dict) else {}
                    for file_path, change in changes.items():
                        task["fileHashes"].append(_hash(file_path))
                        if isinstance(change, dict):
                            added, deleted = _count_diff(change.get("unified_diff"))
                            task["linesAdded"] += added
                            task["linesDeleted"] += deleted
                elif event_type == "token_count":
                    info = value.get("info") if isinstance(value.get("info"), dict) else {}
                    usage = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
                    total_marker = _number(usage.get("total_tokens"))
                    event_hash = _hash(
                        f"token|{session_id}|{turn_id}|{timestamp}|{current_model}|{total_marker}"
                    )
                    if total_marker <= 0 or event_hash in seen_events:
                        continue
                    seen_events.add(event_hash)
                    uncached, cached, cache_write, reasoning, output, total = _exclusive_openai_usage(usage)
                    task["inputTokens"] += uncached
                    task["cachedInputTokens"] += cached
                    task["cacheCreationTokens"] += cache_write
                    task["reasoningTokens"] += reasoning
                    task["outputTokens"] += output
                    task["totalTokens"] += total
                continue

            if row_type != "response_item":
                continue
            response_type = str(value.get("type") or "")
            if response_type in {"function_call", "custom_tool_call"}:
                task = get_task()
                call_id = str(value.get("call_id") or value.get("id") or f"call-{line_number}")
                event_hash = _hash(f"call|{session_id}|{call_id}")
                if event_hash in seen_events:
                    continue
                seen_events.add(event_hash)
                name = str(value.get("name") or "").strip().lower()
                task["toolCalls"] += 1
                command = _command_from_arguments(value.get("arguments") or value.get("input"))
                is_test = name in _COMMAND_TOOLS and _is_test_command(command)
                if name in _COMMAND_TOOLS:
                    task["commands"] += 1
                    if is_test:
                        task["tests"] += 1
                pending_calls[call_id] = (name, is_test)
            elif response_type in {"function_call_output", "custom_tool_call_output"}:
                call_id = str(value.get("call_id") or value.get("id") or "")
                call = pending_calls.get(call_id)
                if not call:
                    continue
                task = get_task()
                output = value.get("output")
                output_text = output if isinstance(output, str) else json.dumps(output, default=str)
                match = _EXIT_CODE_PATTERN.search(output_text)
                if match:
                    exit_code = int(match.group(1))
                    if exit_code != 0:
                        task["toolErrors"] += 1
                    elif call[1]:
                        task["testsPassed"] += 1
                elif "script error" in output_text.lower() or "exit code: 1" in output_text.lower():
                    task["toolErrors"] += 1

    payload["tasks"] = [_finalize_task(task) for task in tasks.values() if task.get("modelId")]
    return payload


def _claude_real_user(row: dict, message: dict) -> bool:
    if bool(row.get("isMeta")) or bool(row.get("isSidechain")):
        return False
    content = message.get("content")
    if isinstance(content, list) and content:
        block_types = {
            str(block.get("type") or "")
            for block in content if isinstance(block, dict)
        }
        if block_types and block_types <= {"tool_result"}:
            return False
    return message.get("role") == "user"


def _parse_claude_file(path: Path, profile_id: str) -> dict:
    payload = _new_source_payload()
    tasks: dict[str, dict[str, Any]] = {}
    current_task = ""
    session_id = path.stem
    project_id = ""
    best_messages: dict[str, dict[str, Any]] = {}
    result_errors: dict[str, bool] = {}
    task_models: dict[str, dict[tuple[str, str], int]] = defaultdict(lambda: defaultdict(int))

    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return payload
    with handle:
        for row_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(row, dict):
                continue
            payload["events"] += 1
            session_id = str(row.get("sessionId") or session_id)
            if row.get("cwd") and not project_id:
                project_id = _hash(row.get("cwd"))
            row_type = str(row.get("type") or "")
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            if row_type == "user" and _claude_real_user(row, message):
                user_id = str(row.get("uuid") or row.get("promptId") or f"user-{row_number}")
                current_task = _task_id("claude", session_id, user_id)
                task = _new_task(
                    "claude", profile_id, session_id, user_id,
                    project_id=project_id,
                    account_attribution="exact",
                )
                task["startedAtUtc"] = _iso_time(row.get("timestamp"))
                tasks.setdefault(current_task, task)
                continue

            content = message.get("content")
            if row_type == "user" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    call_id = str(block.get("tool_use_id") or "")
                    if call_id:
                        result_errors[call_id] = bool(block.get("is_error"))
                continue
            if row_type != "assistant" or not current_task:
                continue
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            model = str(message.get("model") or "").strip()
            message_id = str(message.get("id") or row.get("requestId") or row.get("uuid") or f"row-{row_number}")
            total = sum(
                _number(usage.get(key))
                for key in (
                    "input_tokens", "cache_creation_input_tokens",
                    "cache_read_input_tokens", "output_tokens",
                )
            )
            candidate = {
                "taskId": current_task,
                "model": model,
                "usage": usage,
                "content": content if isinstance(content, list) else [],
                "timestamp": str(row.get("timestamp") or ""),
                "total": total,
            }
            previous = best_messages.get(message_id)
            if previous is None or total >= int(previous.get("total") or 0):
                best_messages[message_id] = candidate

    pending_tests: dict[str, str] = {}
    pending_tools: dict[str, str] = {}
    for record in best_messages.values():
        task = tasks.get(str(record["taskId"]))
        if task is None:
            continue
        model = str(record["model"] or "")
        usage = record["usage"]
        if model:
            task_models[task["taskId"]][(model, "")] += int(record["total"])
        task["inputTokens"] += _number(usage.get("input_tokens"))
        task["cacheCreationTokens"] += _number(usage.get("cache_creation_input_tokens"))
        task["cachedInputTokens"] += _number(usage.get("cache_read_input_tokens"))
        task["outputTokens"] += _number(usage.get("output_tokens"))
        task["totalTokens"] += int(record["total"])
        timestamp = _iso_time(record["timestamp"])
        if timestamp and timestamp > task["completedAtUtc"]:
            task["completedAtUtc"] = timestamp
        has_response = False
        for block in record["content"]:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "text":
                has_response = True
                continue
            if block_type != "tool_use":
                continue
            call_id = str(block.get("id") or "")
            name = str(block.get("name") or "").strip()
            lower_name = name.lower()
            arguments = block.get("input") if isinstance(block.get("input"), dict) else {}
            task["toolCalls"] += 1
            if call_id:
                pending_tools[call_id] = task["taskId"]
            if lower_name in _COMMAND_TOOLS:
                task["commands"] += 1
                command = _command_from_arguments(arguments)
                if _is_test_command(command):
                    task["tests"] += 1
                    if call_id:
                        pending_tests[call_id] = task["taskId"]
            if lower_name in _EDIT_TOOLS:
                task["edits"] += 1
                file_path = arguments.get("file_path") or arguments.get("file")
                if file_path:
                    task["fileHashes"].append(_hash(file_path))
                if lower_name == "edit":
                    old_text = arguments.get("old_string") or ""
                    new_text = arguments.get("new_string") or ""
                    task["linesDeleted"] += len(str(old_text).splitlines())
                    task["linesAdded"] += len(str(new_text).splitlines())
                elif lower_name == "write":
                    task["linesAdded"] += len(str(arguments.get("content") or "").splitlines())
        if has_response:
            task["status"] = "completed"

    for call_id, failed in result_errors.items():
        task_id = pending_tests.get(call_id)
        if task_id and task_id in tasks and not failed:
            tasks[task_id]["testsPassed"] += 1
        if failed:
            # Tool-use IDs are globally stable in Claude transcripts. The
            # cache retains only the owning task ID and numeric error count.
            owner = pending_tools.get(call_id)
            if owner and owner in tasks:
                tasks[owner]["toolErrors"] += 1

    for task in tasks.values():
        models = task_models.get(task["taskId"]) or {}
        if models:
            (model, effort), _tokens = max(models.items(), key=lambda item: item[1])
            _set_model(task, model, effort)
            if len(models) > 1:
                task["provenance"] = "mixed-model task"
        started = _parse_time(task.get("startedAtUtc"))
        completed = _parse_time(task.get("completedAtUtc"))
        if started and completed and completed >= started:
            task["durationMs"] = int((completed - started).total_seconds() * 1000)
    payload["tasks"] = [_finalize_task(task) for task in tasks.values() if task.get("modelId")]
    return payload


def _recent_sources(root: Path, cutoff: dt.datetime) -> list[Path]:
    """Return recent JSONL sources without exposing their paths downstream."""
    if not root.is_dir():
        return []
    cutoff_stamp = cutoff.timestamp() - 86400
    sources: list[Path] = []
    try:
        candidates = root.rglob("*.jsonl")
        for path in candidates:
            try:
                if path.is_file() and path.stat().st_mtime >= cutoff_stamp:
                    sources.append(path)
            except OSError:
                continue
    except OSError:
        return []
    return sorted(sources, key=lambda item: str(item).lower())


def _source_specs(profiles: list[dict], cutoff: dt.datetime) -> list[tuple[str, str, Path, str]]:
    specs: list[tuple[str, str, Path, str]] = []
    seen: set[tuple[str, str, str]] = set()

    codex_profiles = [
        profile for profile in profiles
        if hub_core.provider_key(profile) == "codex" and not bool(profile.get("hidden"))
    ]
    if codex_profiles:
        for folder in ("sessions", "archived_sessions"):
            for path in _recent_sources(Path(hub_core.DEFAULT_CODEX_HOME) / folder, cutoff):
                key = ("codex", CODEX_SHARED_PROFILE, str(path).lower())
                if key not in seen:
                    seen.add(key)
                    specs.append(("codex", CODEX_SHARED_PROFILE, path, "shared"))

    for profile in profiles:
        if bool(profile.get("hidden")):
            continue
        provider = hub_core.provider_key(profile)
        profile_id = hub_core.profile_id(profile)
        roots: list[Path] = []
        attribution = "exact"
        if provider == "codex":
            raw = str(profile.get("codexHome") or "").strip()
            if raw:
                home = Path(raw).expanduser()
                try:
                    is_shared = home.resolve() == Path(hub_core.DEFAULT_CODEX_HOME).resolve()
                except OSError:
                    is_shared = str(home).lower() == str(hub_core.DEFAULT_CODEX_HOME).lower()
                if not is_shared:
                    roots.extend((home / "sessions", home / "archived_sessions"))
        elif provider == "claude" and not hub_core.claude_desktop_only(profile):
            roots.append(hub_core.claude_profile_home(profile) / "projects")
        else:
            continue
        for root in roots:
            for path in _recent_sources(root, cutoff):
                key = (provider, profile_id, str(path).lower())
                if key in seen:
                    continue
                seen.add(key)
                specs.append((provider, profile_id, path, attribution))
    return specs


_TASK_COUNTERS = (
    "inputTokens", "cachedInputTokens", "cacheCreationTokens", "reasoningTokens",
    "outputTokens", "totalTokens", "durationMs", "ttftMs", "toolCalls",
    "toolErrors", "commands", "tests", "testsPassed", "edits", "filesChanged",
    "linesAdded", "linesDeleted", "rollbacks", "compactions",
)


def _merge_task(existing: dict | None, incoming: dict) -> dict:
    if existing is None:
        return dict(incoming)
    merged = dict(existing)
    for key in _TASK_COUNTERS:
        # Copied and incrementally rewritten provider transcripts contain the
        # same task. Max preserves the most complete observation without
        # inflating it by the number of copies.
        merged[key] = max(_number(existing.get(key)), _number(incoming.get(key)))
    merged["profileIds"] = sorted(set(
        [str(value) for value in existing.get("profileIds", []) if value]
        + [str(value) for value in incoming.get("profileIds", []) if value]
    ))
    merged["fileHashes"] = sorted(set(
        [str(value) for value in existing.get("fileHashes", []) if value]
        + [str(value) for value in incoming.get("fileHashes", []) if value]
    ))
    merged["filesChanged"] = len(merged["fileHashes"])
    starts = [value for value in (existing.get("startedAtUtc"), incoming.get("startedAtUtc")) if value]
    ends = [value for value in (existing.get("completedAtUtc"), incoming.get("completedAtUtc")) if value]
    merged["startedAtUtc"] = min(starts) if starts else ""
    merged["completedAtUtc"] = max(ends) if ends else ""
    if incoming.get("status") == "completed" or existing.get("status") == "completed":
        merged["status"] = "completed"
    elif incoming.get("status") == "aborted" or existing.get("status") == "aborted":
        merged["status"] = "aborted"
    if existing.get("accountAttribution") != "exact" and incoming.get("accountAttribution") == "exact":
        merged["profileId"] = incoming.get("profileId")
        merged["accountAttribution"] = "exact"
    if incoming.get("totalTokens", 0) > existing.get("totalTokens", 0):
        for key in (
            "modelId", "modelName", "reasoningEffort", "reasoningEffortName",
            "modelLabel", "filterKey", "provider", "projectId", "day",
        ):
            merged[key] = incoming.get(key, merged.get(key))
    return _finalize_task(merged)


def _persist_tasks(tasks: list[dict]) -> None:
    init_benchmark_db()
    connection = sqlite3.connect(_benchmark_db())
    try:
        connection.execute("delete from benchmark_tasks")
        now = _utc_now()
        for task in tasks:
            connection.execute(
                "insert into benchmark_tasks (task_id, provider, profile_id, day, model_key, "
                "payload_json, updated_at_utc) values (?, ?, ?, ?, ?, ?, ?)",
                (
                    task["taskId"], task.get("provider", ""), task.get("profileId", ""),
                    task.get("day", ""), task.get("filterKey", ""),
                    json.dumps(task, separators=(",", ":"), sort_keys=True), now,
                ),
            )
        connection.commit()
    finally:
        connection.close()


def _scan_tasks(
    profiles: list[dict],
    cutoff: dt.datetime,
    cancelled: Callable[[], bool] | None,
) -> tuple[list[dict], dict]:
    init_benchmark_db()
    connection = sqlite3.connect(_benchmark_db())
    merged: dict[str, dict] = {}
    stats = {"files": 0, "cachedFiles": 0, "parsedFiles": 0, "events": 0}
    try:
        for provider, profile_id, path, attribution in _source_specs(profiles, cutoff):
            if cancelled is not None and cancelled():
                break
            try:
                stat = path.stat()
            except OSError:
                continue
            source_id = _hash(f"{provider}|{profile_id}|{path}")
            payload = _load_cached_source(connection, source_id, stat.st_size, stat.st_mtime_ns)
            if payload is None:
                payload = (
                    _parse_codex_file(path, profile_id, attribution)
                    if provider == "codex"
                    else _parse_claude_file(path, profile_id)
                )
                _save_cached_source(
                    connection, source_id, provider, _hash(profile_id),
                    stat.st_size, stat.st_mtime_ns, payload,
                )
                stats["parsedFiles"] += 1
            else:
                stats["cachedFiles"] += 1
            stats["files"] += 1
            stats["events"] += _number(payload.get("events"))
            for task in payload.get("tasks", []):
                if not isinstance(task, dict) or not task.get("taskId"):
                    continue
                merged[task["taskId"]] = _merge_task(merged.get(task["taskId"]), task)
        connection.commit()
    finally:
        connection.close()
    tasks = sorted(
        merged.values(),
        key=lambda item: (str(item.get("startedAtUtc") or ""), str(item.get("taskId") or "")),
    )
    _persist_tasks(tasks)
    stats["tasks"] = len(tasks)
    return tasks, stats


def _limit_segments(profiles: list[dict], cutoff: dt.datetime, tasks: list[dict]) -> list[dict]:
    rows = history_limit_entries(profiles, cutoff.isoformat())
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("profileId") or "")].append(row)
    profile_provider = {
        hub_core.profile_id(profile): hub_core.provider_key(profile) for profile in profiles
    }
    # Index each parsed task once by provider and calendar day. The old loop
    # reparsed every task timestamp for every adjacent limit snapshot, which
    # became quadratic on long histories and dominated warm refreshes.
    task_days: dict[tuple[str, str], list[tuple[dt.datetime, dt.datetime, dict]]] = defaultdict(list)
    for task in tasks:
        provider = str(task.get("provider") or "")
        task_start = _parse_time(task.get("startedAtUtc"))
        task_end = _parse_time(task.get("completedAtUtc")) or task_start
        if not provider or not task_start or not task_end:
            continue
        day = task_start.date()
        final_day = task_end.date()
        # Real coding tasks normally occupy one day. The bound protects this
        # index from malformed multi-year timestamps without losing endpoints.
        indexed_days = 0
        while day <= final_day and indexed_days < 32:
            task_days[(provider, day.isoformat())].append((task_start, task_end, task))
            day += dt.timedelta(days=1)
            indexed_days += 1
        if final_day >= day:
            task_days[(provider, final_day.isoformat())].append((task_start, task_end, task))
    segments: list[dict] = []
    for profile_id, snapshots in grouped.items():
        snapshots.sort(key=lambda item: str(item.get("refreshedAtUtc") or ""))
        for before, after in zip(snapshots, snapshots[1:]):
            start = _parse_time(before.get("refreshedAtUtc"))
            end = _parse_time(after.get("refreshedAtUtc"))
            if not start or not end or end <= start:
                continue
            gap = (end - start).total_seconds() / 60
            if gap > MAX_LIMIT_GAP_MINUTES:
                continue
            short_before = _float(before.get("shortUsedPercent"))
            short_after = _float(after.get("shortUsedPercent"))
            weekly_before = _float(before.get("weeklyUsedPercent"))
            weekly_after = _float(after.get("weeklyUsedPercent"))
            short_burn = max(0.0, short_after - short_before) if short_before is not None and short_after is not None else 0.0
            weekly_burn = max(0.0, weekly_after - weekly_before) if weekly_before is not None and weekly_after is not None else 0.0
            if short_burn <= 0 and weekly_burn <= 0:
                continue
            provider = profile_provider.get(profile_id, str(after.get("provider") or ""))
            candidates: list[dict] = []
            seen_tasks: set[str] = set()
            profile_hash = _hash(profile_id)
            day = start.date()
            while day <= end.date():
                for task_start, task_end, task in task_days.get((provider, day.isoformat()), []):
                    task_id = str(task.get("taskId") or id(task))
                    if task_id in seen_tasks:
                        continue
                    seen_tasks.add(task_id)
                    if provider == "claude" and profile_hash not in set(task.get("profileIds") or []):
                        continue
                    if task_start <= end and task_end >= start:
                        candidates.append(task)
                day += dt.timedelta(days=1)
            weights = [max(1, _number(task.get("totalTokens"))) for task in candidates]
            weight_total = sum(weights)
            allocations = []
            for task, weight in zip(candidates, weights):
                allocations.append({
                    "filterKey": task.get("filterKey", ""),
                    "shortBurn": short_burn * weight / weight_total if weight_total else 0.0,
                    "weeklyBurn": weekly_burn * weight / weight_total if weight_total else 0.0,
                })
            segments.append({
                "profileId": _hash(profile_id),
                "provider": provider,
                "day": end.astimezone().date().isoformat(),
                "startedAtUtc": start.isoformat(),
                "completedAtUtc": end.isoformat(),
                "shortBurn": short_burn,
                "weeklyBurn": weekly_burn,
                "allocations": allocations,
                "provenance": "derived from adjacent provider limit snapshots",
            })
    return segments


def build_benchmark_analytics(
    profiles: list[dict],
    history_days: int = DEFAULT_HISTORY_DAYS,
    cancelled: Callable[[], bool] | None = None,
    model_snapshot: dict | None = None,
) -> dict:
    """Build the passive, privacy-safe resource and work snapshot."""
    selected_days = max(1, min(365, int(history_days or DEFAULT_HISTORY_DAYS)))
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=selected_days)
    visible = [dict(profile) for profile in profiles if not bool(profile.get("hidden"))]
    models = model_snapshot or build_model_analytics(
        visible,
        history_days=min(selected_days, 365),
        cancelled=cancelled,
    )
    tasks, source_stats = _scan_tasks(visible, cutoff, cancelled)
    segments = _limit_segments(visible, cutoff, tasks)
    return {
        "generatedAtUtc": _utc_now(),
        "historyDays": selected_days,
        "modelUsageRows": models.get("rows", []),
        "modelCatalog": models.get("modelCatalog", []),
        "coverage": models.get("coverage", []),
        "codexShared": models.get("codexShared", {}),
        "tasks": tasks,
        "limitSegments": segments,
        "sourceStats": source_stats,
        "privacy": {
            "stored": "numeric aggregates and hashes only",
            "excluded": [
                "prompts", "responses", "reasoning text", "source code", "diffs",
                "commands", "tool output", "file paths", "account names",
            ],
        },
    }


__all__ = [
    "DEFAULT_HISTORY_DAYS", "MAX_LIMIT_GAP_MINUTES", "PARSER_VERSION",
    "CODEX_SHARED_PROFILE", "build_benchmark_analytics", "init_benchmark_db",
]
