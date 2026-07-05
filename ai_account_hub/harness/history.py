"""On-disk session/thread history readers and parsers for Codex, Claude, Cursor, and Antigravity, plus shared text/diff/image helpers."""

from __future__ import annotations

import datetime as dt
import difflib
import json
import logging
import os
import re
import sqlite3
from pathlib import Path

_logger = logging.getLogger("native_harness")

def extract_message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def compact_json_text(value: object, limit: int = 1200) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            text = str(value)
    return compact_history_text(strip_ansi(text.strip()), limit=limit)


def local_image_ref(path: object = "", name: object = "", url: object = "", data: object = "", media_type: object = "") -> dict:
    return {
        "name": str(name or "").strip(),
        "path": clean_windows_path_text(path),
        "url": str(url or "").strip(),
        "data": str(data or "").strip(),
        "mediaType": str(media_type or "").strip(),
    }


def claude_content_image_refs(content: object) -> list[dict]:
    refs: list[dict] = []
    blocks = content if isinstance(content, list) else []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "image":
            continue
        source = block.get("source") if isinstance(block.get("source"), dict) else {}
        refs.append(
            local_image_ref(
                path=source.get("path") or source.get("file_path"),
                url=source.get("url"),
                data=source.get("data"),
                media_type=source.get("media_type") or source.get("mediaType"),
                name=block.get("name") or source.get("name"),
            )
        )
    return [ref for ref in refs if ref.get("path") or ref.get("url") or ref.get("data")]


def claude_tool_summary(name: str, tool_input: object) -> str:
    payload = tool_input if isinstance(tool_input, dict) else {}
    command = str(payload.get("command") or "").strip()
    description = str(payload.get("description") or "").strip()
    if command:
        lines = [command]
        if description:
            lines.append(description)
        return compact_history_text("\n".join(lines), limit=900)
    questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
    if questions:
        labels: list[str] = []
        for question in questions[:3]:
            if isinstance(question, dict):
                label = str(question.get("question") or question.get("header") or "").strip()
                if label:
                    labels.append(label)
        if labels:
            return compact_history_text("; ".join(labels), limit=900)
    plan = str(payload.get("plan") or "").strip()
    if plan:
        return compact_history_text(plan, limit=900)
    if payload:
        return compact_json_text(payload, limit=900)
    return name


def claude_tool_result_text(entry: dict, block: dict) -> str:
    result = entry.get("tool_use_result")
    content = block.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = extract_message_text(content)
        if not text:
            text = compact_json_text(content, limit=900)
    elif content is not None:
        text = compact_json_text(content, limit=900)
    elif result is not None:
        text = compact_json_text(result, limit=900)
    else:
        text = "Tool result"
    return compact_history_text(text, limit=1000)


CLAUDE_FILE_TOOL_NAMES = {
    "edit",
    "multiedit",
    "write",
    "notebookedit",
    "str_replace_editor",
    "str_replace_based_edit_tool",
}


def text_looks_like_diff(value: object) -> bool:
    text = strip_ansi(str(value or ""))
    return bool(re.search(r"(?m)^(diff --git |@@ |\+\+\+ |--- )", text))


def first_text_value(payload: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def claude_tool_file_path(payload: dict) -> str:
    return clean_windows_path_text(
        first_text_value(
            payload,
            (
                "file_path",
                "filePath",
                "path",
                "notebook_path",
                "notebookPath",
                "target_file",
                "targetFile",
            ),
        )
    )


def unified_diff_from_text(path: str, before: object, after: object, label: str = "") -> str:
    before_text = str(before or "")
    after_text = str(after or "")
    if before_text == after_text:
        return ""
    display_path = clean_windows_path_text(path) or "file"
    suffix = f" ({label})" if label else ""
    lines = difflib.unified_diff(
        before_text.splitlines(),
        after_text.splitlines(),
        fromfile=f"a/{display_path}{suffix}",
        tofile=f"b/{display_path}{suffix}",
        lineterm="",
    )
    return "\n".join(lines)


def claude_tool_input_diff(name: str, tool_input: object) -> str:
    payload = tool_input if isinstance(tool_input, dict) else {}
    if not payload:
        return ""
    for key in ("diff", "patch", "changes"):
        value = payload.get(key)
        if isinstance(value, str) and text_looks_like_diff(value):
            return compact_history_text(value, limit=7000)
    tool_name = str(name or "").strip().lower()
    path = claude_tool_file_path(payload)
    if tool_name == "multiedit" and isinstance(payload.get("edits"), list):
        diffs: list[str] = []
        for index, edit in enumerate(payload.get("edits") or [], start=1):
            if not isinstance(edit, dict):
                continue
            diff = unified_diff_from_text(
                path,
                edit.get("old_string") or edit.get("oldString") or edit.get("old_text") or edit.get("oldText"),
                edit.get("new_string") or edit.get("newString") or edit.get("new_text") or edit.get("newText"),
                label=f"edit {index}",
            )
            if diff:
                diffs.append(diff)
        return compact_history_text("\n".join(diffs), limit=7000)
    if tool_name in CLAUDE_FILE_TOOL_NAMES:
        before = first_text_value(payload, ("old_string", "oldString", "old_text", "oldText", "original", "before"))
        after = first_text_value(payload, ("new_string", "newString", "new_text", "newText", "content", "after"))
        if before or after:
            return compact_history_text(unified_diff_from_text(path, before, after), limit=7000)
    return ""


def claude_edit_line_counts(payload: dict, kind: str) -> tuple[int, int]:
    """Approximate +added/-removed line counts from a Claude Edit/Write/MultiEdit
    tool input (Claude records the content but not diff stats). Computed here from
    the full payload, before any UI truncation."""
    def nlines(value: object) -> int:
        text = str(value or "")
        return text.count("\n") + 1 if text else 0

    if isinstance(payload.get("edits"), list):
        added = removed = 0
        for edit in payload["edits"]:
            if isinstance(edit, dict):
                added += nlines(edit.get("new_string"))
                removed += nlines(edit.get("old_string"))
        return added, removed
    if kind == "write" or payload.get("content") is not None:
        return nlines(payload.get("content")), 0
    return nlines(payload.get("new_string")), nlines(payload.get("old_string"))


def claude_tool_activity_fields(name: str, tool_input: object) -> dict:
    payload = tool_input if isinstance(tool_input, dict) else {}
    tool_name = str(name or "").strip()
    lowered = tool_name.lower()
    fields: dict[str, object] = {}
    if lowered in {"enterplanmode", "exitplanmode"}:
        fields["kind"] = "plan"
        fields["title"] = "Plan"
        return fields
    path = claude_tool_file_path(payload)
    diff = claude_tool_input_diff(tool_name, payload)
    if path:
        change: dict[str, object] = {"path": path, "kind": lowered or "file"}
        added, removed = claude_edit_line_counts(payload, lowered)
        if added or removed:
            change["added"], change["removed"] = added, removed
        fields["changes"] = [change]
    if diff:
        fields["kind"] = "diff"
        fields["title"] = "File changes"
        fields["diff"] = diff
    elif path and lowered in CLAUDE_FILE_TOOL_NAMES:
        fields["kind"] = "file_change"
        fields["title"] = "File change"
    return fields


def claude_tool_result_fields(entry: dict, block: dict, text: str = "") -> dict:
    result = entry.get("tool_use_result") if isinstance(entry.get("tool_use_result"), dict) else {}
    fields: dict[str, object] = {}
    image_refs = claude_content_image_refs(block.get("content"))
    if image_refs:
        fields["kind"] = "image"
        fields["title"] = "Image"
        fields["imageRefs"] = image_refs
        return fields
    result_path = clean_windows_path_text(
        first_text_value(
            result,
            ("filePath", "file_path", "path", "planFilePath", "plan_file_path"),
        )
    )
    if result_path:
        fields["changes"] = [{"path": result_path, "kind": "result"}]
    plan_text = str(result.get("plan") or "").strip()
    if plan_text:
        fields["kind"] = "plan"
        fields["title"] = "Plan"
    if text_looks_like_diff(text):
        fields["kind"] = "diff"
        fields["title"] = "File changes"
        fields["diff"] = compact_history_text(text, limit=7000)
    return fields


def claude_history_messages_from_entry(entry: dict, line_number: int, path: Path) -> list[dict]:
    entry_type = str(entry.get("type") or "")
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content") if isinstance(message, dict) else ""
    timestamp = str(entry.get("timestamp") or "")
    entry_id = str(entry.get("uuid") or f"{Path(path).stem}:{line_number}")
    messages: list[dict] = []
    text = extract_message_text(content).strip()
    image_refs = claude_content_image_refs(content)
    if entry_type == "user" and text and is_claude_user_prompt(text):
        messages.append(
            {
                "role": "user",
                "text": compact_history_text(text),
                "timestamp": timestamp,
                "nativeId": entry_id,
                "imageRefs": image_refs,
            }
        )
    elif entry_type == "assistant" and text:
        messages.append(
            {
                "role": "assistant",
                "text": compact_history_text(text),
                "timestamp": timestamp,
                "nativeId": entry_id,
                "imageRefs": image_refs,
            }
        )
    elif image_refs:
        messages.append(
            {
                "role": "activity",
                "kind": "image",
                "title": "Image",
                "text": "Image attached",
                "timestamp": timestamp,
                "nativeId": f"{entry_id}:images",
                "imageRefs": image_refs,
            }
        )
    blocks = content if isinstance(content, list) else []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        native_id = str(block.get("id") or block.get("tool_use_id") or f"{entry_id}:{index}")
        if block_type == "tool_use":
            name = str(block.get("name") or "Claude tool")
            fields = claude_tool_activity_fields(name, block.get("input"))
            fields.setdefault("kind", "tool")
            fields.setdefault("title", name)
            messages.append(
                {
                    "role": "activity",
                    "text": claude_tool_summary(name, block.get("input")),
                    "timestamp": timestamp,
                    "nativeId": native_id,
                    "status": "requested",
                    **fields,
                }
            )
        elif block_type == "tool_result":
            result_text = claude_tool_result_text(entry, block)
            fields = claude_tool_result_fields(entry, block, result_text)
            fields.setdefault("kind", "result")
            fields.setdefault("title", "Tool result")
            messages.append(
                {
                    "role": "activity",
                    "text": result_text,
                    "timestamp": timestamp,
                    "nativeId": native_id,
                    "status": "completed",
                    **fields,
                }
            )
        elif block_type == "thinking":
            thinking = str(block.get("thinking") or "").strip()
            if thinking:
                messages.append(
                    {
                        "role": "activity",
                        "kind": "reasoning",
                        "title": "Thinking",
                        "text": compact_history_text(thinking, limit=900),
                        "timestamp": timestamp,
                        "nativeId": f"{entry_id}:thinking:{index}",
                    }
                )
    return messages


def is_claude_user_prompt(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    internal_markers = (
        "<local-command-caveat>",
        "<command-name>",
        "<local-command-stdout>",
        "<local-command-stderr>",
    )
    return not any(marker in stripped for marker in internal_markers)


def discover_claude_threads(projects_root: Path, cwd: Path | None = None, limit: int = 100) -> list[dict]:
    root = Path(projects_root)
    if not root.exists():
        return []
    candidates = sorted(root.rglob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    threads: list[dict] = []
    seen_sessions: set[str] = set()
    wanted_cwd = str(Path(cwd)).lower() if cwd is not None else ""
    for path in candidates:
        preview = ""
        thread_cwd = ""
        session_id = path.stem
        created_at = path.stat().st_ctime
        updated_at = path.stat().st_mtime
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(entry, dict):
                        continue
                    session_id = str(entry.get("sessionId") or entry.get("session_id") or session_id)
                    thread_cwd = str(entry.get("cwd") or thread_cwd)
                    if entry.get("type") == "user" and not preview:
                        message = entry.get("message")
                        content = message.get("content") if isinstance(message, dict) else ""
                        candidate = extract_message_text(content).strip()
                        if is_claude_user_prompt(candidate):
                            preview = candidate
                    if preview and thread_cwd:
                        break
        except OSError:
            continue
        if wanted_cwd and thread_cwd.lower() != wanted_cwd:
            continue
        if not preview:
            continue
        if session_id in seen_sessions:
            continue
        seen_sessions.add(session_id)
        threads.append(
            {
                "id": session_id,
                "provider": "claude",
                "preview": preview or "Claude Code session",
                "cwd": thread_cwd,
                "createdAt": created_at,
                "updatedAt": updated_at,
                "path": str(path),
                "status": {"type": "notLoaded"},
            }
        )
        if len(threads) >= limit:
            break
    return threads


def compact_native_history_messages(messages: list[dict], provider: str = "history", max_messages: int = 420) -> list[dict]:
    if len(messages) <= max_messages:
        return messages
    head_count = 30
    tail_count = max_messages - head_count - 1
    omitted = len(messages) - head_count - tail_count
    return (
        messages[:head_count]
        + [
            {
                "role": "activity",
                "kind": "notice",
                "title": "History compacted",
                "text": f"History compacted: {omitted:,} older {provider} records hidden to keep the UI responsive.",
                "nativeId": f"{provider}-history-compacted-messages",
            }
        ]
        + messages[-tail_count:]
    )


def read_claude_thread(path: Path) -> list[dict]:
    messages: list[dict] = []
    seen: set[str] = set()
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            entry_type = str(entry.get("type") or "")
            if entry_type not in {"user", "assistant"}:
                continue
            entry_id = str(entry.get("uuid") or "")
            if entry_id and entry_id in seen:
                continue
            if entry_id:
                seen.add(entry_id)
            messages.extend(claude_history_messages_from_entry(entry, line_number, Path(path)))
    return compact_native_history_messages(messages, provider="claude")


def truncate_history_text(text: str, limit: int = 60000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n...[truncated by AI Account Hub]"


def compact_history_text(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n\n...[{len(text) - limit:,} characters hidden by AI Account Hub]"


ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\][^\x07]*(?:\x07|\x1b\\)|[@-_][0-?]*[ -/]*[@-~])")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def strip_ansi(text: str) -> str:
    raw = "" if text is None else str(text)
    value = ANSI_ESCAPE_RE.sub("", raw)
    return CONTROL_CHAR_RE.sub("", value)


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


def cursor_project_name_candidates(cwd: Path) -> list[str]:
    try:
        raw = str(Path(cwd).resolve())
    except OSError:
        raw = str(cwd)
    drive, tail = os.path.splitdrive(raw)
    pieces = [piece for piece in re.split(r"[\\/]+", tail.strip("\\/")) if piece]
    drive_letters = []
    if drive:
        letter = drive[:1]
        drive_letters.extend([letter, letter.lower(), letter.upper()])
    else:
        drive_letters.append("")
    candidates: list[str] = []
    for letter in drive_letters:
        text = "-".join([part for part in [letter, *pieces] if part])
        encoded = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
        if encoded and encoded not in candidates:
            candidates.append(encoded)
    return candidates


def cursor_project_dirs(cursor_home: Path, cwd: Path) -> list[Path]:
    projects = Path(cursor_home) / "projects"
    if not projects.is_dir():
        return []
    candidates = cursor_project_name_candidates(cwd)
    found: list[Path] = []
    for name in candidates:
        direct = projects / name
        if direct.is_dir() and direct not in found:
            found.append(direct)
    if found:
        return found
    wanted = {name.lower() for name in candidates}
    try:
        for child in projects.iterdir():
            if child.is_dir() and child.name.lower() in wanted:
                found.append(child)
    except OSError:
        return []
    return found


def cursor_message_text(entry: dict) -> str:
    message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    content = message.get("content") if isinstance(message, dict) else entry.get("content")
    if content is None:
        content = entry.get("text") or entry.get("messageText")
    return truncate_history_text(extract_message_text(content).strip())


def read_cursor_thread(path: Path) -> list[dict]:
    messages: list[dict] = []
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            role = str(entry.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            text = cursor_message_text(entry)
            if not text:
                continue
            messages.append(
                {
                    "role": role,
                    "text": text,
                    "timestamp": str(entry.get("timestamp") or entry.get("createdAt") or ""),
                    "nativeId": str(entry.get("id") or entry.get("uuid") or f"{Path(path).stem}:{line_number}"),
                }
            )
    return messages


def discover_cursor_threads(cursor_home: Path, cwd: Path, limit: int = 100) -> list[dict]:
    paths: list[Path] = []
    for project_dir in cursor_project_dirs(cursor_home, cwd):
        transcript_root = project_dir / "agent-transcripts"
        if not transcript_root.is_dir():
            continue
        try:
            for path in transcript_root.glob("*/*.jsonl"):
                if path.parent.name == "subagents":
                    continue
                if path.stem != path.parent.name:
                    continue
                paths.append(path)
        except OSError:
            continue
    paths = sorted(set(paths), key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    threads: list[dict] = []
    for path in paths[:limit]:
        messages = read_cursor_thread(path)
        preview = next((str(message.get("text") or "") for message in messages if message.get("role") == "user"), "")
        threads.append(
            {
                "id": path.stem,
                "provider": "cursor",
                "preview": preview or "Cursor Agent session",
                "cwd": str(Path(cwd)),
                "createdAt": path.stat().st_ctime if path.exists() else 0,
                "updatedAt": path.stat().st_mtime if path.exists() else 0,
                "path": str(path),
                "status": {"type": "notLoaded"},
            }
        )
    return threads


def antigravity_last_conversation_id(cli_home: Path, cwd: Path) -> str:
    cache = Path(cli_home) / "cache" / "last_conversations.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    wanted = str(Path(cwd)).lower()
    for raw_path, conversation_id in data.items():
        if str(raw_path).lower() == wanted:
            return str(conversation_id or "")
    return ""


def antigravity_cli_home() -> Path:
    override = os.environ.get("ANTIGRAVITY_CLI_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gemini" / "antigravity-cli"


def antigravity_transcript_path(cli_home: Path, session_id: str) -> Path:
    return Path(cli_home) / "brain" / session_id / ".system_generated" / "logs" / "transcript.jsonl"


def extract_antigravity_user_request(content: str) -> str:
    match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", content, flags=re.S)
    return match.group(1).strip() if match else content.strip()


def read_antigravity_thread(cli_home: Path, session_id: str) -> list[dict]:
    if not session_id:
        _logger.debug("read_antigravity_thread: empty session id for cli_home=%s", cli_home)
        return []
    path = antigravity_transcript_path(cli_home, session_id)
    if not path.is_file():
        _logger.debug("read_antigravity_thread: transcript not found at %s", path)
        return []
    messages: list[dict] = []
    skipped_types: dict[str, int] = {}
    malformed = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                _logger.debug("read_antigravity_thread: malformed JSON at %s:%s", path, line_number)
                continue
            if not isinstance(entry, dict):
                malformed += 1
                continue
            entry_type = str(entry.get("type") or "")
            content = str(entry.get("content") or "").strip()
            if not content:
                skipped_types[entry_type or "<empty>"] = skipped_types.get(entry_type or "<empty>", 0) + 1
                continue
            if entry_type == "USER_INPUT":
                text = extract_antigravity_user_request(content)
                role = "user"
            elif entry_type in {"PLANNER_RESPONSE", "AGENT_RESPONSE"}:
                text = content
                role = "assistant"
            else:
                skipped_types[entry_type or "<empty>"] = skipped_types.get(entry_type or "<empty>", 0) + 1
                continue
            messages.append(
                {
                    "role": role,
                    "text": text,
                    "nativeId": f"{session_id}:{line_number}",
                    "timestamp": str(entry.get("created_at") or ""),
                }
            )
    if not messages:
        _logger.debug(
            "read_antigravity_thread: no readable messages in %s; malformed=%s skipped=%s",
            path,
            malformed,
            skipped_types,
        )
    elif malformed or skipped_types:
        _logger.debug(
            "read_antigravity_thread: read %s messages from %s; malformed=%s skipped=%s",
            len(messages),
            path,
            malformed,
            skipped_types,
        )
    return messages


def discover_antigravity_threads(cli_home: Path, cwd: Path, limit: int = 100) -> list[dict]:
    latest = antigravity_last_conversation_id(cli_home, cwd)
    if not latest:
        return []
    path = antigravity_transcript_path(cli_home, latest)
    messages = read_antigravity_thread(cli_home, latest)
    preview = next((str(message.get("text") or "") for message in messages if message.get("role") == "user"), "")
    modified = path.stat().st_mtime if path.exists() else 0
    return [
        {
            "id": latest,
            "provider": "antigravity",
            "preview": preview or "Antigravity session",
            "cwd": str(Path(cwd)),
            "createdAt": path.stat().st_ctime if path.exists() else 0,
            "updatedAt": modified,
            "path": str(path),
            "status": {"type": "notLoaded"},
        }
    ][:limit]


_CURSOR_PATH_ROOT_TOKENS = {
    "github", "documents", "desktop", "source", "sources", "repos", "repo",
    "projects", "project", "dev", "code", "onedrive", "users", "home",
    "workspace", "workspaces", "git", "src",
}


def _cursor_path_root_tokens() -> set[str]:
    """Root/path-container folder names to strip when decoding a Cursor project
    dir. Includes the current user's home-dir/login name so the decode works for
    any user, not a hardcoded one."""
    tokens = set(_CURSOR_PATH_ROOT_TOKENS)
    for name in (os.environ.get("USERNAME"), os.environ.get("USER"), Path.home().name):
        if name:
            tokens.add(str(name).lower())
    return tokens


def cursor_decode_project_name(dir_name: str) -> str:
    """Best-effort readable project name from a Cursor project dir name
    (e.g. 'a-Github-AI-GUI' -> 'AI-GUI'). Cursor encodes the workspace path with
    every non-alphanumeric run collapsed to '-', so the original '\\' vs '_' is
    unrecoverable; drop the drive letter and any leading path-root folders and
    keep the trailing component(s)."""
    root_tokens = _cursor_path_root_tokens()
    parts = [p for p in str(dir_name).split("-") if p]
    if parts and len(parts[0]) == 1 and parts[0].isalpha():
        parts = parts[1:]  # drop drive letter
    last_root = -1
    for i, part in enumerate(parts):
        if part.lower() in root_tokens:
            last_root = i
    if 0 <= last_root < len(parts) - 1:
        parts = parts[last_root + 1:]
    return "-".join(parts) or str(dir_name)


def cursor_clean_user_text(text: object) -> str:
    """Unwrap Cursor's ``<user_query>…</user_query>`` (and drop other angle-tag
    wrappers) so previews read as plain text."""
    raw = str(text or "").strip()
    match = re.search(r"<user_query>\s*(.*?)\s*</user_query>", raw, flags=re.S | re.I)
    if match:
        raw = match.group(1).strip()
    raw = re.sub(r"^<[^>]+>\s*|\s*</[^>]+>$", "", raw).strip()
    return raw


def discover_all_cursor_threads(cursor_home: Path, limit: int = 200) -> list[dict]:
    """Every Cursor Agent thread across all of Cursor's projects (not just one
    workspace), each tagged with a readable project name so the sidebar can group
    them like Codex/Claude. Transcripts with no user turn are skipped."""
    projects_root = Path(cursor_home) / "projects"
    if not projects_root.is_dir():
        return []
    try:
        project_dirs = [d for d in projects_root.iterdir() if d.is_dir()]
    except OSError:
        return []
    threads: list[dict] = []
    for pdir in project_dirs:
        transcript_root = pdir / "agent-transcripts"
        if not transcript_root.is_dir():
            continue
        name = cursor_decode_project_name(pdir.name)
        try:
            paths = [
                p for p in transcript_root.glob("*/*.jsonl")
                if p.parent.name != "subagents" and p.stem == p.parent.name
            ]
        except OSError:
            continue
        for path in paths:
            messages = read_cursor_thread(path)
            preview = next((str(m.get("text") or "") for m in messages if m.get("role") == "user"), "")
            preview = cursor_clean_user_text(preview)
            if not preview:
                continue  # empty / agent-only transcript
            threads.append({
                "id": path.stem,
                "provider": "cursor",
                "preview": preview,
                "cwd": name,
                "createdAt": path.stat().st_ctime if path.exists() else 0,
                "updatedAt": path.stat().st_mtime if path.exists() else 0,
                "path": str(path),
                "status": {"type": "notLoaded"},
            })
    return sorted(threads, key=lambda t: float(t.get("updatedAt") or 0), reverse=True)[:limit]


def discover_all_antigravity_threads(cli_home: Path, limit: int = 100) -> list[dict]:
    """Every Antigravity conversation tracked in last_conversations.json, keyed by
    the workspace it ran in, so the sidebar can group them by project."""
    cache = Path(cli_home) / "cache" / "last_conversations.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    threads: list[dict] = []
    for raw_path, conversation_id in data.items():
        cid = str(conversation_id or "")
        if not cid:
            continue
        path = antigravity_transcript_path(cli_home, cid)
        messages = read_antigravity_thread(cli_home, cid)
        preview = next((str(m.get("text") or "") for m in messages if m.get("role") == "user"), "")
        threads.append({
            "id": cid,
            "provider": "antigravity",
            "preview": preview or "Antigravity session",
            "cwd": clean_windows_path_text(raw_path),
            "createdAt": path.stat().st_ctime if path.exists() else 0,
            "updatedAt": path.stat().st_mtime if path.exists() else 0,
            "path": str(path),
            "status": {"type": "notLoaded"},
        })
    return sorted(threads, key=lambda t: float(t.get("updatedAt") or 0), reverse=True)[:limit]


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


def clean_windows_path_text(value: object) -> str:
    text = str(value or "").strip()
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def normalized_path_key(path: object) -> str:
    text = clean_windows_path_text(path)
    if not text:
        return ""
    try:
        return str(Path(text).expanduser().resolve()).lower()
    except OSError:
        return str(Path(text).expanduser()).lower()


def path_is_same_or_child(path: object, root: object) -> bool:
    path_key = normalized_path_key(path)
    root_key = normalized_path_key(root)
    if not path_key or not root_key:
        return False
    if path_key == root_key:
        return True
    separator = "\\" if "\\" in root_key else "/"
    return path_key.startswith(root_key.rstrip("\\/") + separator)


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


def load_thread_refs(path: Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def thread_ref_workspace_key(item: dict) -> tuple[str, str, str] | tuple[str, str, str, str]:
    provider = str(item.get("provider") or "")
    profile_id = str(item.get("profileId") or "")
    project_key = normalized_path_key(item.get("projectPath"))
    if project_key:
        return provider, profile_id, project_key
    return provider, profile_id, "session", str(item.get("nativeSessionId") or "")


def upsert_thread_ref(path: Path, item: dict) -> None:
    target = Path(path)
    refs = load_thread_refs(target)
    key = thread_ref_workspace_key(item)
    refs = [ref for ref in refs if thread_ref_workspace_key(ref) != key]
    refs.insert(0, dict(item))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(refs[:500], indent=2), encoding="utf-8")
    temporary.replace(target)


def thread_ref(
    provider: str,
    profile_id: str,
    project_path: Path,
    session_id: str,
    title: str = "",
    native_home: Path | None = None,
) -> dict:
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "provider": provider,
        "profileId": profile_id,
        "projectPath": str(Path(project_path)),
        "nativeSessionId": session_id,
        "nativeHomePath": str(native_home) if native_home else "",
        "title": title,
        "updatedAt": now,
    }




__all__ = [
    "extract_message_text",
    "compact_json_text",
    "local_image_ref",
    "claude_content_image_refs",
    "claude_tool_summary",
    "claude_tool_result_text",
    "text_looks_like_diff",
    "first_text_value",
    "claude_tool_file_path",
    "unified_diff_from_text",
    "claude_tool_input_diff",
    "claude_edit_line_counts",
    "claude_tool_activity_fields",
    "claude_tool_result_fields",
    "claude_history_messages_from_entry",
    "is_claude_user_prompt",
    "discover_claude_threads",
    "compact_native_history_messages",
    "read_claude_thread",
    "truncate_history_text",
    "compact_history_text",
    "strip_ansi",
    "compact_codex_tool_call",
    "compact_codex_tool_output",
    "compact_codex_file_history_messages",
    "cursor_project_name_candidates",
    "cursor_project_dirs",
    "cursor_message_text",
    "read_cursor_thread",
    "discover_cursor_threads",
    "antigravity_last_conversation_id",
    "antigravity_cli_home",
    "antigravity_transcript_path",
    "extract_antigravity_user_request",
    "read_antigravity_thread",
    "discover_antigravity_threads",
    "cursor_decode_project_name",
    "cursor_clean_user_text",
    "discover_all_cursor_threads",
    "discover_all_antigravity_threads",
    "codex_session_id_from_path",
    "codex_parse_timestamp",
    "clean_windows_path_text",
    "normalized_path_key",
    "path_is_same_or_child",
    "codex_content_text",
    "codex_content_image_refs",
    "is_visible_codex_user_text",
    "codex_history_homes",
    "load_codex_saved_workspaces",
    "load_codex_session_index",
    "load_codex_state_threads",
    "codex_session_paths",
    "apply_codex_state_metadata",
    "codex_session_summary",
    "discover_codex_file_threads",
    "read_codex_session_file",
    "codex_thread_messages",
    "codex_activity_kind",
    "codex_activity_title",
    "summarize_codex_item",
    "load_thread_refs",
    "thread_ref_workspace_key",
    "upsert_thread_ref",
    "thread_ref",
]
