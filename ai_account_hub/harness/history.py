"""On-disk history readers for Claude, Cursor, and Antigravity + thread refs.

Aggregates the shared helpers (history_common) and Codex readers (history_codex)
so ``from ...history import *`` still exposes the full public API."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from pathlib import Path

_logger = logging.getLogger("native_harness")

from ai_account_hub.harness.history_common import *  # noqa: F401,F403
from ai_account_hub.harness.history_codex import *  # noqa: F401,F403

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
