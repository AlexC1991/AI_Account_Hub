"""Claude Desktop login-status detection and Claude usage-text parsing +
weekly-reset labels and usage buckets.

A domain extracted from hub_core (cached_claude_desktop_login_status stays in
hub_core; pulled in here via ``from hub_core import *``)."""

from __future__ import annotations

import json
import logging
import re
import datetime as dt
from pathlib import Path

from ai_account_hub.core import hub_core
from ai_account_hub.core.hub_core import *  # noqa: F401,F403

_logger = logging.getLogger(__name__)


def claude_desktop_login_status() -> dict:
    config_path = hub_core.CLAUDE_ROAMING_HOME / "config.json"
    cookie_db = hub_core.CLAUDE_ROAMING_HOME / "Network" / "Cookies"
    status = {
        "desktopInstalled": bool(hub_core.locate_claude_desktop_path()),
        "profileHome": str(hub_core.CLAUDE_ROAMING_HOME),
        "hasOAuthCache": False,
        "hasAccountUuid": False,
        "hasSessionCookie": False,
        "hasLoggedInSignal": False,
        "sessionExpires": "",
        "ready": False,
        "summary": "Claude Desktop login not detected.",
    }

    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
            status["hasOAuthCache"] = bool(data.get("oauth:tokenCache") or data.get("oauth:tokenCacheV2"))
            status["hasAccountUuid"] = bool(str(data.get("lastKnownAccountUuid") or "").strip())
        except (OSError, json.JSONDecodeError):
            pass

    for cookie_db in (
        hub_core.CLAUDE_ROAMING_HOME / "Network" / "Cookies",
        hub_core.CLAUDE_ROAMING_HOME / "Cookies",
        hub_core.CLAUDE_ROAMING_HOME / "Default" / "Network" / "Cookies",
        hub_core.CLAUDE_ROAMING_HOME / "Default" / "Cookies",
    ):
        if not cookie_db.exists():
            continue
        try:
            con = sqlite3.connect(f"file:{cookie_db}?mode=ro", uri=True)
            row = con.execute(
                """
                select name, expires_utc
                from cookies
                where host_key like '%claude.ai%' and name in ('sessionKey', 'sessionKeyLC')
                order by case name when 'sessionKey' then 0 else 1 end
                limit 1
                """
            ).fetchone()
            con.close()
            if row:
                status["hasSessionCookie"] = True
                expires = int(row[1] or 0)
                if expires > 0:
                    expiry = dt.datetime(1601, 1, 1) + dt.timedelta(microseconds=expires)
                    status["sessionExpires"] = expiry.replace(tzinfo=dt.timezone.utc).isoformat()
                break
        except Exception:
            _logger.debug("Claude Desktop cookie DB read failed", exc_info=True)

    log_path = hub_core.CLAUDE_ROAMING_HOME / "logs" / "main.log"
    if log_path.exists():
        try:
            tail = log_path.read_text(encoding="utf-8", errors="ignore")[-250_000:]
            status["hasLoggedInSignal"] = "claude.ai account active and logged in" in tail
        except OSError:
            status["hasLoggedInSignal"] = False

    status["ready"] = bool(
        status["desktopInstalled"]
        and status["hasOAuthCache"]
        and status["hasAccountUuid"]
        and (status["hasSessionCookie"] or status["hasLoggedInSignal"])
    )
    if status["ready"]:
        bits = []
        if status["hasOAuthCache"]:
            bits.append("OAuth cache")
        if status["hasAccountUuid"]:
            bits.append("account identity")
        if status["hasSessionCookie"]:
            expiry = local_datetime_label(status["sessionExpires"]) if status["sessionExpires"] else "unknown expiry"
            bits.append(f"session cookie expires {expiry}")
        elif status["hasLoggedInSignal"]:
            bits.append("running app reports logged in")
        status["summary"] = "Claude Desktop login metadata found: " + "; ".join(bits)
    elif status["hasOAuthCache"] and status["hasAccountUuid"]:
        status["summary"] = (
            "Claude Desktop account metadata found, but no Desktop session cookie was found. "
            "The app may still be on the login screen."
        )
    elif not status["desktopInstalled"]:
        status["summary"] = "Claude Desktop is not installed."
    return status



def parse_claude_reset_label(label: object, base: dt.datetime | None = None) -> str:
    text = re.sub(r"\s*\([^)]*\)\s*$", "", str(label or "").strip())
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    now = base.astimezone() if base is not None else dt.datetime.now().astimezone()
    lower = text.lower()
    day_offset = None
    if lower.startswith("today"):
        day_offset = 0
        text = re.sub(r"(?i)^today(?:\s+at)?\s*,?\s*", "", text)
    elif lower.startswith("tomorrow"):
        day_offset = 1
        text = re.sub(r"(?i)^tomorrow(?:\s+at)?\s*,?\s*", "", text)

    normalized = re.sub(r"(?i)(\d)(am|pm)\b", r"\1 \2", text).upper()
    candidates: list[dt.datetime] = []
    for fmt in (
        "%b %d, %I:%M %p",
        "%B %d, %I:%M %p",
        "%b %d %I:%M %p",
        "%B %d %I:%M %p",
        "%b %d, %I %p",
        "%B %d, %I %p",
        "%b %d %I %p",
        "%B %d %I %p",
    ):
        try:
            # Parse with an explicit leap-safe year. Python 3.14 warns when a
            # day/month format has no year because Feb 29 is otherwise
            # ambiguous, and Python 3.15 will make that behavior stricter.
            parsed = dt.datetime.strptime(f"2000 {normalized}", f"%Y {fmt}").replace(
                year=now.year
            )
            if parsed.astimezone() < now - dt.timedelta(days=1):
                parsed = parsed.replace(year=now.year + 1)
            candidates.append(parsed)
        except ValueError:
            pass
    for fmt in ("%I:%M %p", "%I %p"):
        try:
            time_value = dt.datetime.strptime(normalized, fmt).time()
            date_value = now.date() + dt.timedelta(days=day_offset or 0)
            parsed = dt.datetime.combine(date_value, time_value)
            if day_offset is None and parsed.astimezone() < now - dt.timedelta(minutes=5):
                parsed += dt.timedelta(days=1)
            candidates.append(parsed)
        except ValueError:
            pass

    if not candidates:
        return ""
    return candidates[0].astimezone(dt.timezone.utc).isoformat()


def parse_claude_usage_text(text: object) -> dict:
    result = {
        "sessionUsedPercent": None,
        "sessionResetUtc": "",
        "weeklyUsedPercent": None,
        "weeklyResetUtc": "",
        "weeklyModelUsedPercent": {},
        "summary": str(text or "").strip(),
    }
    weekly_candidates: list[dict] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("current session"):
            match = re.search(r"(\d+(?:\.\d+)?)%\s+used", line, re.IGNORECASE)
            if match:
                result["sessionUsedPercent"] = float(match.group(1))
            reset_match = re.search(r"\bresets\s+(.+)$", line, re.IGNORECASE)
            if reset_match:
                result["sessionResetUtc"] = parse_claude_reset_label(reset_match.group(1))
            continue

        weekly_match = re.match(
            r"current week(?:\s*\((?P<label>[^)]*)\))?\s*:\s*(?P<body>.*)$",
            line,
            re.IGNORECASE,
        )
        if not weekly_match:
            continue
        label = str(weekly_match.group("label") or "").strip()
        body = str(weekly_match.group("body") or "")
        used_match = re.search(r"(\d+(?:\.\d+)?)%\s+used", body, re.IGNORECASE)
        reset_match = re.search(r"\bresets\s+(.+)$", body, re.IGNORECASE)
        used_percent = float(used_match.group(1)) if used_match else None
        reset_utc = parse_claude_reset_label(reset_match.group(1)) if reset_match else ""
        normalized_label = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
        is_aggregate = not label or normalized_label in {"all", "all model", "all models"}
        weekly_candidates.append(
            {
                "label": label,
                "usedPercent": used_percent,
                "resetUtc": reset_utc,
                "isAggregate": is_aggregate,
            }
        )
        if label and not is_aggregate and used_percent is not None:
            result["weeklyModelUsedPercent"][label] = used_percent

    if weekly_candidates:
        # Claude Code 2.1.197 added model-specific rows such as
        # "Current week (Fable)". Prefer the all-model row so a later
        # model-specific percentage cannot overwrite the account total.
        aggregate = next((item for item in weekly_candidates if item["isAggregate"]), None)
        if aggregate is None:
            aggregate = next((item for item in weekly_candidates if item["resetUtc"]), weekly_candidates[0])
        result["weeklyUsedPercent"] = aggregate["usedPercent"]
        result["weeklyResetUtc"] = aggregate["resetUtc"]
    return result


def hydrate_claude_profile_from_cached_usage(profile: dict) -> bool:
    summary = profile.get("usageSummary") if isinstance(profile.get("usageSummary"), dict) else {}
    cached_text = str(summary.get("claudeUsageStatus") or "").strip()
    if not cached_text:
        return False
    parsed = parse_claude_usage_text(cached_text)
    changed = False
    session_used = parsed.get("sessionUsedPercent")
    weekly_used = parsed.get("weeklyUsedPercent")
    session_reset = str(parsed.get("sessionResetUtc") or "")
    weekly_reset = str(parsed.get("weeklyResetUtc") or "")
    if session_used is not None and str(profile.get("shortLimitUsedPercent") or "") != str(session_used):
        profile["shortLimitUsedPercent"] = str(session_used)
        changed = True
    if weekly_used is not None and str(profile.get("weeklyLimitUsedPercent") or "") != str(weekly_used):
        profile["weeklyLimitUsedPercent"] = str(weekly_used)
        changed = True
    if session_reset and not str(profile.get("shortLimitResetUtc") or "").strip():
        profile["shortLimitResetUtc"] = session_reset
        changed = True
    if weekly_reset and not str(profile.get("weeklyLimitResetUtc") or "").strip():
        profile["weeklyLimitResetUtc"] = weekly_reset
        changed = True
    if weekly_reset and not str(profile.get("weeklyResetEstimateUtc") or "").strip():
        profile["weeklyResetEstimateUtc"] = weekly_reset
        profile["weeklyResetEstimateSource"] = "claude-usage"
        changed = True
    return changed


def claude_usage_total_tokens(usage: dict) -> int:
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return sum(int(sanitize_float(usage.get(key)) or 0) for key in keys)


def build_claude_usage_buckets(projects_root: Path = hub_core.CLAUDE_PROJECTS_ROOT) -> list[dict]:
    if not projects_root.exists():
        return []
    # Claude Code may write the same assistant message into several project
    # transcripts and may repeat it within one file. Usage is cumulative for
    # that message ID, so retain the largest non-zero record globally instead
    # of summing every copy.
    messages: dict[str, dict] = {}
    buckets: dict[str, dict] = {}
    for path in projects_root.rglob("*.jsonl"):
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                timestamp = parse_iso_datetime(item.get("timestamp"))
                message = item.get("message") if isinstance(item.get("message"), dict) else {}
                usage = message.get("usage") if isinstance(message.get("usage"), dict) else None
                if timestamp is None or not usage:
                    continue
                total_tokens = claude_usage_total_tokens(usage)
                if total_tokens <= 0:
                    continue
                stable_id = str(
                    message.get("id")
                    or item.get("requestId")
                    or item.get("uuid")
                    or f"{path}:{line_number}"
                )
                current = messages.get(stable_id)
                if current is None or total_tokens > current["tokens"]:
                    messages[stable_id] = {
                        "timestamp": timestamp,
                        "tokens": total_tokens,
                    }

    for message in messages.values():
        timestamp = message["timestamp"]
        day = timestamp.date().isoformat()
        total_tokens = int(message["tokens"])
        bucket = buckets.setdefault(
            day,
            {
                "date": day,
                "tokens": 0,
                "messageCount": 0,
                "first": timestamp,
                "last": timestamp,
            },
        )
        bucket["tokens"] += total_tokens
        bucket["messageCount"] += 1
        if timestamp < bucket["first"]:
            bucket["first"] = timestamp
        if timestamp > bucket["last"]:
            bucket["last"] = timestamp

    rows: list[dict] = []
    for day, bucket in sorted(buckets.items()):
        duration = bucket["last"] - bucket["first"]
        minutes = max(0, int(round(duration.total_seconds() / 60)))
        rows.append(
            {
                "date": day,
                "tokens": int(bucket["tokens"]),
                "activeMinutes": minutes,
                "messageCount": int(bucket["messageCount"]),
                "source": "claude-code-jsonl-v2",
            }
        )
    return rows
