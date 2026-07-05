"""Shared text/diff/image/path helpers used by all provider history readers."""

from __future__ import annotations

import difflib
import json
import logging
import re
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


def text_looks_like_diff(value: object) -> bool:
    text = strip_ansi(str(value or ""))
    return bool(re.search(r"(?m)^(diff --git |@@ |\+\+\+ |--- )", text))


def first_text_value(payload: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return ""


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


