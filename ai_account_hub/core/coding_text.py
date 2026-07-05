"""Coding-view display/parse helpers: message rendering, image refs, native
attachments, slash commands, model listings, and codex access parameters.

A leaf domain extracted from hub_core; pulls base helpers via ``from hub_core
import *`` and is re-exported through ``ai_account_hub.core``."""

from __future__ import annotations

import json
import re

from ai_account_hub.core import hub_core
from ai_account_hub.core.hub_core import *  # noqa: F401,F403

def calendar_reset_chip_label(marker: object) -> str:
    text = str(marker or "").strip()
    estimated = "estimate" in text.lower()
    account = re.split(r"\s+weekly\s+reset", text, maxsplit=1, flags=re.IGNORECASE)[0]
    account = re.sub(r"\b(claude\s+code|antigravity|codex|cursor)\b", "", account, flags=re.IGNORECASE)
    account = re.sub(r"\baccount\b", "", account, flags=re.IGNORECASE)
    account = re.sub(r"\s+", " ", account).strip()
    if not account:
        lowered = text.lower()
        account = next(
            (
                label
                for key, label in (
                    ("claude", "Claude"),
                    ("codex", "Codex"),
                    ("cursor", "Cursor"),
                    ("antigravity", "Antigravity"),
                )
                if key in lowered
            ),
            "Account",
        )
    suffix = "reset estimate" if estimated else "reset"
    return f"{clip_text(account, 12)} {suffix}"


def coding_user_message_parts(value: object) -> tuple[str, list[str]]:
    text = str(value or "").replace("\r\n", "\n").strip()
    attachments: list[str] = []
    request_marker = "## My request for Codex:"
    if "# Files mentioned by the user:" in text:
        prefix, separator, request = text.partition(request_marker)
        for match in re.finditer(r"(?m)^##\s+([^:\n]+):\s+(.+)$", prefix):
            name = match.group(1).strip()
            if name and name not in attachments:
                attachments.append(name)
        text = request.strip() if separator else re.sub(
            r"(?ms)^# Files mentioned by the user:.*?(?=^#|\Z)",
            "",
            text,
        ).strip()
    text = re.sub(r"(?is)<image\b[^>]*>.*?</image>", "", text)
    text = re.sub(r"(?is)<image\b[^>]*/?>", "", text)
    return text.strip(), attachments


def htmlish_attr(source: str, name: str) -> str:
    match = re.search(rf"""{name}\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", source, flags=re.I)
    if not match:
        return ""
    return next((group for group in match.groups() if group is not None), "").strip()


def markdown_image_refs(value: object) -> list[dict]:
    text = str(value or "")
    refs: list[dict] = []
    pattern = re.compile(r"!\[([^\]]*)\]\((<[^>]+>|[^)]+)\)")
    for match in pattern.finditer(text):
        label = match.group(1).strip()
        target = match.group(2).strip().strip("<>").strip()
        if not target:
            continue
        refs.append(
            {
                "name": label or Path(target).name or "Image",
                "path": target if not target.startswith(("http://", "https://", "data:")) else "",
                "url": target if target.startswith(("http://", "https://")) else "",
                "data": target.split(",", 1)[1] if target.startswith("data:") and "," in target else "",
                "mediaType": target[5:].split(";", 1)[0] if target.startswith("data:") else "",
            }
        )
    return refs


def image_refs_from_transport_text(value: object) -> list[dict]:
    text = str(value or "")
    refs: list[dict] = []
    for match in re.finditer(r"(?is)<image\b([^>]*)>(.*?)</image>|<image\b([^>]*)/?>", text):
        attrs = match.group(1) or match.group(3) or ""
        name = htmlish_attr(attrs, "name") or "Image"
        path = clean_windows_path_text(htmlish_attr(attrs, "path"))
        url = htmlish_attr(attrs, "url")
        if path or url:
            refs.append({"name": name, "path": path, "url": url, "data": "", "mediaType": ""})
    refs.extend(markdown_image_refs(text))
    unique: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        key = str(ref.get("path") or ref.get("url") or ref.get("data") or ref.get("name") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return unique


def coding_user_message_details(value: object) -> tuple[str, list[dict]]:
    raw = str(value or "").replace("\r\n", "\n").strip()
    body, attachment_names = coding_user_message_parts(raw)
    body = re.sub(r"!\[[^\]]*\]\((<[^>]+>|[^)]+)\)", "", body).strip()
    refs: list[dict] = []
    prefix = raw.partition("## My request for Codex:")[0]
    for match in re.finditer(r"(?m)^##\s+([^:\n]+):\s+(.+)$", prefix):
        name = match.group(1).strip()
        path = clean_windows_path_text(match.group(2).strip())
        if name:
            refs.append({"name": name, "path": path, "url": "", "data": "", "mediaType": ""})
    refs.extend(image_refs_from_transport_text(raw))
    known = {str(ref.get("name") or "").lower() for ref in refs if str(ref.get("name") or "").strip()}
    for name in attachment_names:
        if name.lower() not in known:
            refs.append({"name": name, "path": "", "url": "", "data": "", "mediaType": ""})
    unique: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        key = str(ref.get("path") or ref.get("url") or ref.get("data") or ref.get("name") or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return body, unique


CODING_IMAGE_ATTACHMENT_SUFFIXES = {".png", ".gif", ".webp", ".jpg", ".jpeg"}
CODING_SIDEBAR_SELECTED_THREAD_PREVIEW_LIMIT = 7
CODING_SIDEBAR_COLLAPSED_THREAD_PREVIEW_LIMIT = 3
CODING_NATIVE_RENDER_DELAY_MS = 150
CODING_NATIVE_FULL_RENDER_DELAY_MS = 90
CODING_COMMAND_OUTPUT_LIMIT = 1600
CODING_ACTIVITY_TEXT_LIMIT = 1200


def coding_display_text(value: object) -> str:
    text = strip_ansi("" if value is None else str(value))
    return text.replace("\r\n", "\n").replace("\r", "\n")


def coding_compact_display_text(value: object, limit: int = CODING_ACTIVITY_TEXT_LIMIT) -> str:
    return compact_history_text(coding_display_text(value).strip(), limit=limit)


def coding_command_display_line(command: str) -> str:
    text = coding_display_text(command).strip()
    match = re.search(r"(?:^|\s)-Command\s+(.+)$", text, flags=re.I)
    if match:
        candidate = match.group(1).strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] and candidate[0] in {"'", '"'}:
            candidate = candidate[1:-1].strip()
        if candidate:
            return candidate
    return text


def coding_command_activity_parts(value: object) -> tuple[str, str, str]:
    lines = [line.rstrip() for line in coding_display_text(value).strip().splitlines()]
    lines = [line for line in lines if line.strip()]
    if not lines:
        return "", "", ""
    command = ""
    details = ""
    output_lines: list[str] = []
    if lines[0].strip().lower() == "output":
        output_lines = lines[1:]
    else:
        command = coding_command_display_line(lines[0])
        rest = lines[1:]
        if rest and rest[0].strip().lower() != "output":
            details = rest[0].strip()
            rest = rest[1:]
        if rest and rest[0].strip().lower() == "output":
            rest = rest[1:]
        output_lines = rest
    output = "\n".join(output_lines).strip()
    return command, details, output


def native_attachment_kind(path: Path) -> str:
    return "image" if Path(path).suffix.lower() in CODING_IMAGE_ATTACHMENT_SUFFIXES else "file"


def native_attachment_size_label(path: Path) -> str:
    try:
        size = Path(path).stat().st_size
    except OSError:
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def native_attachment_status_text(attachments: list[Path]) -> str:
    count = len(attachments)
    if count == 0:
        return "Native passthrough"
    return f"{count} attachment{'s' if count != 1 else ''} staged"


def native_attachment_prompt(text: str, attachments: list[Path]) -> str:
    clean_text = str(text or "").strip()
    if not attachments:
        return clean_text
    lines = ["# Files attached by the user", ""]
    for index, path in enumerate(attachments, start=1):
        path = Path(path)
        lines.append(f"{index}. {path.name}")
        lines.append(f"   Path: {path}")
        lines.append(f"   Type: {native_attachment_kind(path)}")
    lines.extend(["", "# User request", clean_text or "Please review the attached files."])
    return "\n".join(lines)


def parse_coding_slash_command(value: object) -> dict | None:
    text = str(value or "").strip()
    if not text.startswith("/"):
        return None
    match = re.match(r"^/([A-Za-z][\w-]*)(?:\s+([\s\S]*))?$", text)
    if not match:
        return {"name": "", "args": "", "raw": text}
    return {"name": match.group(1).lower(), "args": str(match.group(2) or "").strip(), "raw": text}


def compact_duration_seconds(value: float) -> str:
    seconds = max(0, int(round(value)))
    minutes, second_part = divmod(seconds, 60)
    hours, minute_part = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minute_part}m"
    if minutes:
        return f"{minutes}m {second_part}s"
    return f"{second_part}s"


def codex_goal_display_text(goal: object) -> str:
    if not isinstance(goal, dict):
        return "No Codex goal is set for this thread."
    objective = str(goal.get("objective") or "").strip()
    status = str(goal.get("status") or "active").strip()
    tokens = goal.get("tokensUsed")
    budget = goal.get("tokenBudget")
    time_used = sanitize_float(goal.get("timeUsedSeconds"))
    parts = [f"Status: {status}"]
    if tokens is not None:
        token_text = f"Tokens: {compact_number(sanitize_float(tokens) or 0)}"
        if budget is not None:
            token_text += f" / {compact_number(sanitize_float(budget) or 0)}"
        parts.append(token_text)
    if time_used is not None and time_used > 0:
        parts.append(f"Time: {compact_duration_seconds(time_used)}")
    if objective:
        parts.append("")
        parts.append(objective)
    return "\n".join(parts)


def coding_sidebar_thread_preview_limit(thread_count: int, selected: bool, expanded: bool) -> int:
    if expanded:
        return max(0, thread_count)
    limit = (
        CODING_SIDEBAR_SELECTED_THREAD_PREVIEW_LIMIT
        if selected
        else CODING_SIDEBAR_COLLAPSED_THREAD_PREVIEW_LIMIT
    )
    return max(0, min(thread_count, limit))


CODING_FALLBACK_MODELS = {
    # Keep fallbacks generic so the UI does not advertise speculative or
    # provider-internal model names when a harness cannot list models.
    "codex": [("Provider default", "")],
    "claude": [("Provider default", "")],
    "cursor": [("Provider default", "")],
    "antigravity": [("Provider default", "")],
}

CODING_ACCESS_OPTIONS = {
    "codex": [("Workspace", "workspace"), ("Read only", "read-only"), ("Full access", "full-access")],
    "claude": [
        ("Accept edits", "accept-edits"),
        ("Plan mode", "plan"),
        ("Manual approval", "default"),
        ("Full access", "full-access"),
    ],
    "cursor": [("Agent", "default"), ("Ask", "ask"), ("Manual edit", "plan"), ("Auto-run", "full-access")],
    "antigravity": [("Manual", "default"), ("Supervised", "sandbox"), ("Autonomous", "full-access")],
}

CODING_PERSONALITY_OPTIONS = {
    "codex": [("Friendly", "friendly"), ("Pragmatic", "pragmatic"), ("None", "none")],
    "claude": [("Provider default", "")],
    "cursor": [("Provider default", "")],
    "antigravity": [("Provider default", "")],
}

CODING_EFFORT_OPTIONS = {
    "codex": [
        ("Default", ""),
        ("Low", "low"),
        ("Medium", "medium"),
        ("High", "high"),
        ("Extra high", "xhigh"),
    ],
    "claude": [
        ("Default", ""),
        ("Low", "low"),
        ("Medium", "medium"),
        ("High", "high"),
        ("Extra high", "xhigh"),
        ("Max", "max"),
    ],
    "cursor": [("Model default", "")],
    "antigravity": [("Model default", "")],
}


def read_coding_profile_defaults(profile: dict) -> dict[str, str]:
    provider = provider_key(profile)
    if provider == "codex":
        path = Path(str(profile.get("codexHome") or hub_core.DEFAULT_CODEX_HOME)) / "config.toml"
        if not path.is_file():
            return {}
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, tomllib.TOMLDecodeError):
            return {}
        sandbox = str(payload.get("sandbox_mode") or "")
        access = {
            "workspace-write": "workspace",
            "read-only": "read-only",
            "danger-full-access": "full-access",
        }.get(sandbox, "workspace")
        return {
            "model": str(payload.get("model") or ""),
            "effort": str(payload.get("model_reasoning_effort") or ""),
            "access": access,
            "personality": str(payload.get("personality") or "friendly"),
        }
    if provider == "claude":
        projects = claude_profile_home(profile) / "projects"
        try:
            sessions = sorted(projects.rglob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)[:12]
        except OSError:
            sessions = []
        for session in sessions:
            try:
                lines = session.read_text(encoding="utf-8", errors="replace").splitlines()[-160:]
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = row.get("message") if isinstance(row.get("message"), dict) else {}
                model = str(message.get("model") or "")
                if model:
                    return {"model": model, "effort": "", "access": "default"}
    return {}


def parse_native_model_listing(value: object) -> list[dict]:
    models: list[dict] = []
    seen: set[str] = set()
    ansi = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
    for raw_line in ansi.sub("", str(value or "")).splitlines():
        line = raw_line.strip().lstrip("*-> ").strip()
        if not line or line.lower().startswith(("available model", "model ", "error:", "usage:")):
            continue
        candidate = re.split(r"\s{2,}|\t|\s+-\s+", line, maxsplit=1)[0].strip(" `\"'")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/\-\[\]=,]*", candidate):
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        models.append({"model": candidate, "displayName": candidate, "isDefault": False, "supportedReasoningEfforts": []})
    return models


def codex_access_parameters(access: str, workspace: Path) -> dict:
    if access == "read-only":
        return {
            "approvalPolicy": "on-request",
            "threadSandbox": "read-only",
            "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        }
    if access == "full-access":
        return {
            "approvalPolicy": "on-request",
            "threadSandbox": "danger-full-access",
            "sandboxPolicy": {"type": "dangerFullAccess"},
        }
    return {
        "approvalPolicy": "on-request",
        "threadSandbox": "workspace-write",
        "sandboxPolicy": {
            "type": "workspaceWrite",
            "writableRoots": [str(Path(workspace))],
            "networkAccess": False,
            "excludeTmpdirEnvVar": False,
            "excludeSlashTmp": False,
        },
    }


