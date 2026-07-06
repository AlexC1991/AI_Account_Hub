"""Codex session/thread history readers (rollout files + the state DB)."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
from pathlib import Path

_logger = logging.getLogger("native_harness")

from ai_account_hub.harness.history_common import *  # noqa: F401,F403

def compact_codex_tool_call(payload: dict) -> str:
    name = str(payload.get("name") or "Tool call")
    raw_arguments = str(payload.get("arguments") or "").strip()
    arguments: object = {}
    if raw_arguments:
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            arguments = raw_arguments
    if name in {"shell_command", "exec_command"} and isinstance(arguments, dict):
        command = str(arguments.get("command") or arguments.get("cmd") or "").strip()
        workdir = str(arguments.get("workdir") or "").strip()
        lines = ["Command", compact_history_text(command, limit=700) if command else name]
        if workdir:
            lines.append(f"In {workdir}")
        return "\n".join(line for line in lines if line)
    if isinstance(arguments, dict):
        prompt = str(arguments.get("prompt") or arguments.get("description") or arguments.get("query") or "").strip()
        if prompt:
            return f"{name}\n{compact_history_text(prompt, limit=700)}"
    if raw_arguments and "base64" not in raw_arguments and "data:image" not in raw_arguments:
        return f"{name}\n{compact_history_text(raw_arguments, limit=700)}"
    return name


def compact_codex_tool_output(output: object) -> str:
    text = strip_ansi(str(output or "")).strip()
    if not text:
        return ""
    exit_match = re.search(r"Exit code:\s*([^\r\n]+)", text)
    wall_match = re.search(r"Wall time:\s*([^\r\n]+)", text)
    output_match = re.search(r"(?:^|\n)Output:\s*(.*)$", text, flags=re.S)
    details: list[str] = []
    if exit_match:
        details.append(f"exit {exit_match.group(1).strip()}")
    if wall_match:
        details.append(wall_match.group(1).strip())
    body = output_match.group(1).strip() if output_match else text
    header = "Result"
    if details:
        header = f"Result  {' | '.join(details)}"
    if not body:
        return header
    if "base64" in body or "data:image" in body:
        return f"{header}\nOutput omitted ({len(body):,} characters, image/binary payload)."
    if len(body) > 1200:
        return f"{header}\n{compact_history_text(body, limit=900)}"
    return f"{header}\n{body}"


def compact_codex_file_history_messages(messages: list[dict], compacted_activity: int) -> list[dict]:
    chat_messages: list[dict] = []
    turn_started_at = 0.0
    commentary_count = 0
    latest_commentary: dict | None = None
    for message in messages:
        role = str(message.get("role") or "")
        phase = str(message.get("phase") or "")
        if role == "user":
            if latest_commentary is not None:
                pending = dict(latest_commentary)
                pending["role"] = "assistant"
                pending["muted"] = True
                chat_messages.append(pending)
            chat_messages.append(message)
            turn_started_at = codex_parse_timestamp(message.get("timestamp"))
            commentary_count = 0
            latest_commentary = None
            continue
        if role == "commentary":
            commentary_count += 1
            latest_commentary = message
            continue
        if role not in {"assistant", "error"}:
            continue
        if role == "assistant" and phase == "final_answer" and turn_started_at:
            completed_at = codex_parse_timestamp(message.get("timestamp"))
            elapsed = max(0, round(completed_at - turn_started_at)) if completed_at else 0
            if elapsed:
                minutes, seconds = divmod(elapsed, 60)
                duration = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
                chat_messages.append(
                    {
                        "role": "turn_meta",
                        "text": f"Worked for {duration}",
                        "timestamp": message.get("timestamp"),
                        "nativeId": f"{message.get('nativeId') or 'codex-turn'}:meta",
                        "commentaryCount": commentary_count,
                    }
                )
        chat_messages.append(message)
        commentary_count = 0
        latest_commentary = None
    if latest_commentary is not None:
        pending = dict(latest_commentary)
        pending["role"] = "assistant"
        pending["muted"] = True
        chat_messages.append(pending)
    if not chat_messages and messages:
        chat_messages = messages[:80]
    if compacted_activity:
        notice = {
            "role": "activity",
            "text": f"Tool activity compacted: {compacted_activity:,} calls/results hidden from file history.",
            "nativeId": "codex-history-compacted-tools",
        }
        insert_at = 1 if chat_messages else 0
        chat_messages = chat_messages[:insert_at] + [notice] + chat_messages[insert_at:]
    max_messages = 360
    if len(chat_messages) <= max_messages:
        return chat_messages
    head_count = 24
    tail_count = max_messages - head_count - 1
    omitted = len(chat_messages) - head_count - tail_count
    return (
        chat_messages[:head_count]
        + [
            {
                "role": "activity",
                "text": f"History compacted: {omitted:,} older messages hidden to keep the UI responsive.",
                "nativeId": "codex-history-compacted-messages",
            }
        ]
        + chat_messages[-tail_count:]
    )


def codex_session_id_from_path(path: Path) -> str:
    match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
        Path(path).stem,
        flags=re.I,
    )
    return match.group(1) if match else Path(path).stem


def codex_parse_timestamp(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        return dt.datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def codex_content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if "text" in content:
            return str(content.get("text") or "")
        if "content" in content:
            return codex_content_text(content.get("content"))
        return ""
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if "text" in block:
                parts.append(str(block.get("text") or ""))
            elif "content" in block:
                parts.append(codex_content_text(block.get("content")))
    return "".join(parts)


def codex_content_image_refs(content: object) -> list[dict]:
    refs: list[dict] = []
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list):
        return refs
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type in {"localImage", "input_image", "image", "image_url"}:
            source = block.get("source") if isinstance(block.get("source"), dict) else {}
            refs.append(
                local_image_ref(
                    path=block.get("path") or block.get("file_path") or source.get("path"),
                    url=block.get("image_url") or block.get("url") or source.get("url"),
                    data=block.get("data") or source.get("data"),
                    media_type=block.get("media_type") or source.get("media_type"),
                    name=block.get("name") or block.get("filename"),
                )
            )
        nested = block.get("content")
        if isinstance(nested, (dict, list)):
            refs.extend(codex_content_image_refs(nested))
    return [ref for ref in refs if ref.get("path") or ref.get("url") or ref.get("data")]


def is_visible_codex_user_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    hidden_prefixes = (
        "<environment_context>",
        "<developer_context>",
        "<permissions instructions>",
        "<app-context>",
        "<collaboration_mode>",
        "<codex_internal_context",
    )
    return not any(stripped.startswith(prefix) for prefix in hidden_prefixes)


def codex_history_homes(codex_home: Path, include_default: bool = True) -> list[Path]:
    homes: list[Path] = []
    for home in [Path(codex_home), Path.home() / ".codex"] if include_default else [Path(codex_home)]:
        key = normalized_path_key(home)
        if key and all(normalized_path_key(existing) != key for existing in homes):
            homes.append(home)
    return homes


def load_codex_saved_workspaces(codex_home: Path, include_default: bool = True) -> list[str]:
    workspaces: list[str] = []
    seen: set[str] = set()
    for home in codex_history_homes(codex_home, include_default=include_default):
        state_path = Path(home) / ".codex-global-state.json"
        if not state_path.is_file():
            continue
        try:
            data = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for key in ("project-order", "electron-saved-workspace-roots", "active-workspace-roots"):
            values = data.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                text = clean_windows_path_text(value)
                key_text = normalized_path_key(text)
                if not text or key_text in seen:
                    continue
                seen.add(key_text)
                workspaces.append(text)
    return workspaces


def load_codex_session_index(codex_homes: list[Path]) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for home in codex_homes:
        index_path = Path(home) / "session_index.jsonl"
        if not index_path.is_file():
            continue
        try:
            with index_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    session_id = str(row.get("id") or row.get("session_id") or row.get("sessionId") or "")
                    if not session_id:
                        continue
                    existing = indexed.get(session_id, {})
                    updated = codex_parse_timestamp(row.get("updated_at") or row.get("updatedAt"))
                    existing_updated = codex_parse_timestamp(existing.get("updated_at") or existing.get("updatedAt"))
                    if not existing or updated >= existing_updated:
                        indexed[session_id] = row
        except OSError:
            continue
    return indexed


def load_codex_state_threads(codex_homes: list[Path]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id: dict[str, dict] = {}
    by_path: dict[str, dict] = {}
    for home in codex_homes:
        db_path = Path(home) / "state_5.sqlite"
        if not db_path.is_file():
            continue
        try:
            connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
        except sqlite3.Error:
            continue
        try:
            # select * so we tolerate older/newer schemas; the extra columns
            # (archived, tokens_used) let callers match Codex Desktop, which
            # hides archived and empty (zero-token) threads.
            rows = connection.execute(
                "select * from threads order by updated_at desc"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            connection.close()
        for row in rows:
            item = dict(row)
            session_id = str(item.get("id") or "")
            rollout_path = clean_windows_path_text(item.get("rollout_path"))
            cwd = clean_windows_path_text(item.get("cwd"))
            if not session_id:
                continue
            normalized = {
                "id": session_id,
                "path": rollout_path,
                "cwd": cwd,
                "title": str(item.get("title") or ""),
                "preview": str(item.get("preview") or ""),
                "firstUserMessage": str(item.get("first_user_message") or ""),
                "createdAt": float(item.get("created_at") or 0),
                "updatedAt": float(item.get("updated_at") or 0),
                "archived": int(item.get("archived") or 0),
                "tokensUsed": int(item.get("tokens_used") or 0),
                "hasUserEvent": int(item.get("has_user_event") or 0),
            }
            existing = by_id.get(session_id)
            if not existing or normalized["updatedAt"] >= float(existing.get("updatedAt") or 0):
                by_id[session_id] = normalized
            path_key = normalized_path_key(rollout_path)
            if path_key:
                by_path[path_key] = normalized
    return by_id, by_path


def codex_session_paths(codex_home: Path, include_default: bool = True) -> list[Path]:
    paths: list[Path] = []
    for home in codex_history_homes(codex_home, include_default=include_default):
        for root_name in ("sessions", "archived_sessions"):
            root = Path(home) / root_name
            if not root.is_dir():
                continue
            try:
                paths.extend(root.rglob("*.jsonl"))
            except OSError:
                continue
    unique: dict[str, Path] = {}
    for path in paths:
        unique[str(path).lower()] = path
    return sorted(unique.values(), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)


def apply_codex_state_metadata(summary: dict, state: dict | None, index: dict[str, dict]) -> dict:
    result = dict(summary)
    if state:
        state_id = str(state.get("id") or "")
        if state_id:
            result["id"] = state_id
        state_cwd = clean_windows_path_text(state.get("cwd"))
        if state_cwd:
            result["cwd"] = state_cwd
            result["actualCwd"] = state_cwd
        state_path = clean_windows_path_text(state.get("path"))
        if state_path:
            result["path"] = state_path
        state_created = float(state.get("createdAt") or 0)
        state_updated = float(state.get("updatedAt") or 0)
        if state_created:
            result["createdAt"] = state_created
        if state_updated:
            result["updatedAt"] = max(float(result.get("updatedAt") or 0), state_updated)
        state_title = str(state.get("title") or "").strip()
        if state_title:
            result["preview"] = state_title
        # Carry Codex Desktop's own thread flags so callers can hide archived
        # and empty (zero-token, never-run) sessions the way the app does.
        result["hasState"] = True
        result["archived"] = int(state.get("archived") or 0)
        result["tokensUsed"] = int(state.get("tokensUsed") or 0)
        result["hasUserEvent"] = int(state.get("hasUserEvent") or 0)
    session_id = str(result.get("id") or "")
    indexed = index.get(session_id, {})
    indexed_title = str(indexed.get("thread_name") or indexed.get("title") or "").strip()
    indexed_updated = codex_parse_timestamp(indexed.get("updated_at") or indexed.get("updatedAt"))
    if indexed_title:
        result["preview"] = indexed_title
    if indexed_updated:
        result["updatedAt"] = max(float(result.get("updatedAt") or 0), indexed_updated)
    return result


def codex_session_summary(path: Path, index: dict[str, dict] | None = None) -> dict:
    session_id = codex_session_id_from_path(path)
    preview = ""
    thread_cwd = ""
    created_at = path.stat().st_ctime if path.exists() else 0.0
    updated_at = path.stat().st_mtime if path.exists() else 0.0
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                row_type = str(row.get("type") or "")
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                if row_type == "session_meta":
                    session_id = str(payload.get("id") or payload.get("session_id") or session_id)
                    thread_cwd = clean_windows_path_text(payload.get("cwd") or thread_cwd)
                    timestamp = codex_parse_timestamp(payload.get("timestamp"))
                    if timestamp:
                        created_at = timestamp
                elif row_type == "turn_context":
                    thread_cwd = clean_windows_path_text(payload.get("cwd") or thread_cwd)
                elif row_type == "response_item" and not preview:
                    if str(payload.get("type") or "") != "message":
                        continue
                    if str(payload.get("role") or "").lower() != "user":
                        continue
                    candidate = codex_content_text(payload.get("content")).strip()
                    if is_visible_codex_user_text(candidate):
                        preview = candidate
                if preview and thread_cwd:
                    break
    except OSError:
        pass
    indexed = (index or {}).get(session_id, {})
    indexed_title = str(indexed.get("thread_name") or indexed.get("title") or "").strip()
    indexed_updated = codex_parse_timestamp(indexed.get("updated_at") or indexed.get("updatedAt"))
    if indexed_updated:
        updated_at = max(updated_at, indexed_updated)
    return {
        "id": session_id,
        "provider": "codex",
        "preview": indexed_title or preview or "Codex session",
        "cwd": thread_cwd,
        "actualCwd": thread_cwd,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "path": str(path),
        "source": "codex-file",
        "status": {"type": "notLoaded"},
    }


def discover_codex_file_threads(
    codex_home: Path,
    cwd: Path | None = None,
    limit: int = 100,
    include_default: bool = True,
) -> list[dict]:
    homes = codex_history_homes(codex_home, include_default=include_default)
    index = load_codex_session_index(homes)
    state_by_id, state_by_path = load_codex_state_threads(homes)
    paths = codex_session_paths(codex_home, include_default=include_default)
    for state in state_by_id.values():
        state_path = Path(clean_windows_path_text(state.get("path")))
        if state_path.is_file() and all(normalized_path_key(path) != normalized_path_key(state_path) for path in paths):
            paths.append(state_path)
    summaries: list[dict] = []
    seen: set[str] = set()
    for path in sorted(paths, key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
        summary = codex_session_summary(path, index=index)
        state = state_by_path.get(normalized_path_key(path)) or state_by_id.get(str(summary.get("id") or ""))
        summary = apply_codex_state_metadata(summary, state, index)
        session_id = str(summary.get("id") or "")
        if not session_id or session_id in seen:
            continue
        seen.add(session_id)
        summaries.append(summary)
    wanted = Path(cwd) if cwd is not None else None
    if wanted is not None:
        summaries = [
            dict(summary, cwd=clean_windows_path_text(summary.get("cwd") or summary.get("actualCwd") or str(wanted)))
            for summary in summaries
        ]
    return sorted(summaries, key=lambda item: float(item.get("updatedAt") or 0), reverse=True)[:limit]


def read_codex_session_file(path: Path) -> list[dict]:
    messages: list[dict] = []
    compacted_activity = 0
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("type") != "response_item":
                continue
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            payload_type = str(payload.get("type") or "")
            native_id = str(payload.get("id") or f"{Path(path).stem}:{line_number}")
            timestamp = str(row.get("timestamp") or payload.get("timestamp") or "")
            if payload_type == "message":
                role = str(payload.get("role") or "").lower()
                if role not in {"user", "assistant"}:
                    continue
                text = codex_content_text(payload.get("content")).strip()
                image_refs = codex_content_image_refs(payload.get("content"))
                if role == "user" and not is_visible_codex_user_text(text):
                    continue
                if not text and not image_refs:
                    continue
                messages.append(
                    {
                        "role": "commentary" if role == "assistant" and str(payload.get("phase") or "") == "commentary" else role,
                        "text": compact_history_text(text),
                        "timestamp": timestamp,
                        "nativeId": native_id,
                        "phase": str(payload.get("phase") or ""),
                        "imageRefs": image_refs,
                    }
                )
            elif payload_type == "function_call":
                text = compact_codex_tool_call(payload)
                if text:
                    messages.append(
                        {
                            "role": "activity",
                            "kind": "tool",
                            "title": str(payload.get("name") or "Tool call"),
                            "text": text,
                            "timestamp": timestamp,
                            "nativeId": native_id,
                            "status": "requested",
                        }
                    )
                    compacted_activity += 1
            elif payload_type == "function_call_output":
                text = compact_codex_tool_output(payload.get("output"))
                if text:
                    messages.append(
                        {
                            "role": "activity",
                            "kind": "result",
                            "title": "Tool result",
                            "text": text,
                            "timestamp": timestamp,
                            "nativeId": native_id,
                            "status": "completed",
                        }
                    )
                    compacted_activity += 1
            elif payload_type == "reasoning":
                summary = codex_content_text(payload.get("summary")).strip()
                if summary:
                    messages.append(
                        {
                            "role": "activity",
                            "kind": "reasoning",
                            "title": "Reasoning",
                            "text": compact_history_text(f"Reasoning\n{summary}", limit=1200),
                            "timestamp": timestamp,
                            "nativeId": native_id,
                        }
                    )
                    compacted_activity += 1
    return compact_codex_file_history_messages(messages, compacted_activity)


def codex_thread_messages(thread: dict) -> list[dict]:
    messages: list[dict] = []
    for turn in thread.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        for item in turn.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type == "userMessage":
                content = item.get("content") or []
                text = "".join(
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
                image_refs = codex_content_image_refs(content)
                if text or image_refs:
                    messages.append({"role": "user", "text": text, "nativeId": str(item.get("id") or ""), "imageRefs": image_refs})
            elif item_type == "agentMessage":
                text = str(item.get("text") or "")
                if text:
                    messages.append({"role": "assistant", "text": text, "nativeId": str(item.get("id") or "")})
            elif item_type == "plan":
                text = str(item.get("text") or "")
                if text:
                    messages.append({"role": "activity", "kind": "plan", "title": "Plan", "text": f"Plan\n{text}", "nativeId": str(item.get("id") or "")})
            elif item_type in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabToolCall", "webSearch", "imageView", "contextCompaction"}:
                activity: dict = {
                    "role": "activity",
                    "text": summarize_codex_item(item),
                    "nativeId": str(item.get("id") or ""),
                    "kind": codex_activity_kind(item),
                    "title": codex_activity_title(item),
                    "status": str(item.get("status") or ""),
                }
                if item_type == "fileChange":
                    activity["changes"] = [change for change in item.get("changes") or [] if isinstance(change, dict)]
                    diffs = [
                        str(change.get("diff") or "")
                        for change in activity["changes"]
                        if isinstance(change, dict) and str(change.get("diff") or "").strip()
                    ]
                    if diffs:
                        activity["diff"] = "\n".join(diffs)
                        activity["kind"] = "diff"
                        activity["title"] = "File changes"
                elif item_type == "imageView":
                    path_text = str(item.get("path") or "").strip()
                    if path_text:
                        activity["imageRefs"] = [local_image_ref(path=path_text, name=Path(path_text).name)]
                messages.append(activity)
    return messages


def codex_activity_kind(item: dict) -> str:
    item_type = str(item.get("type") or "")
    return {
        "commandExecution": "command",
        "fileChange": "file_change",
        "mcpToolCall": "tool",
        "dynamicToolCall": "tool",
        "collabToolCall": "tool",
        "webSearch": "tool",
        "imageView": "image",
        "contextCompaction": "notice",
        "plan": "plan",
    }.get(item_type, "activity")


def codex_activity_title(item: dict) -> str:
    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        return "Command"
    if item_type == "fileChange":
        return "File changes"
    if item_type == "mcpToolCall":
        return f"MCP {item.get('tool') or 'tool'}"
    if item_type == "dynamicToolCall":
        return str(item.get("tool") or "Tool")
    if item_type == "collabToolCall":
        return "Collaboration"
    if item_type == "webSearch":
        return "Web search"
    if item_type == "imageView":
        return "Image"
    if item_type == "contextCompaction":
        return "Context compacted"
    return item_type or "Activity"


def summarize_codex_item(item: dict) -> str:
    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        command = strip_ansi(str(item.get("command") or "Command")).strip()
        status = str(item.get("status") or "")
        exit_code = item.get("exitCode")
        duration = item.get("durationMs")
        details = [status]
        if exit_code is not None:
            details.append(f"exit {exit_code}")
        if duration is not None:
            details.append(f"{duration} ms")
        output = strip_ansi(str(item.get("aggregatedOutput") or "")).strip()
        summary = f"{command}\n{' | '.join(part for part in details if part)}".strip()
        if not output:
            return summary
        if "base64" in output or "data:image" in output:
            output_text = f"Output omitted ({len(output):,} characters, image/binary payload)."
        else:
            output_text = compact_history_text(output, limit=1800)
        return f"{summary}\nOutput\n{output_text}".strip()
    if item_type == "fileChange":
        paths = [
            str(change.get("path") or "")
            for change in item.get("changes") or []
            if isinstance(change, dict)
        ]
        detail = ", ".join(path for path in paths if path) or "Files changed"
        return f"{detail}\n{item.get('status') or ''}".strip()
    if item_type == "mcpToolCall":
        return f"MCP {item.get('server') or ''} / {item.get('tool') or ''}\n{item.get('status') or ''}".strip()
    if item_type == "dynamicToolCall":
        return f"{item.get('tool') or 'Dynamic tool'}\n{item.get('status') or ''}".strip()
    if item_type == "collabToolCall":
        return f"Collaboration\n{item.get('status') or ''}".strip()
    if item_type == "webSearch":
        return f"Web search\n{item.get('query') or ''}".strip()
    if item_type == "imageView":
        return f"Viewed image\n{item.get('path') or ''}".strip()
    if item_type == "contextCompaction":
        return "Context compacted"
    return item_type or "Native activity"

