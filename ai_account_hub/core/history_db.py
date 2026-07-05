"""Local usage-history database (SQLite): recording per-refresh usage buckets
and reading them back for the calendar/stat cards.

A leaf domain extracted from hub_core."""

from __future__ import annotations

import json
import sqlite3

from ai_account_hub.core.hub_core import *  # noqa: F401,F403

def init_history_db() -> None:
    LAUNCHER_ROOT.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(HISTORY_DB_FILE)
    try:
        connection.executescript(
            """
            create table if not exists usage_history (
                profile_id text not null,
                profile_name text not null,
                provider text not null,
                bucket_day text not null,
                tokens integer not null default 0,
                active_minutes integer,
                message_count integer,
                source text not null,
                bucket_hash text not null,
                bucket_json text not null,
                first_seen_utc text not null,
                last_seen_utc text not null,
                primary key (profile_id, bucket_day, source, bucket_hash)
            );
            create index if not exists idx_usage_history_day on usage_history(bucket_day);
            create index if not exists idx_usage_history_profile on usage_history(profile_id);

            create table if not exists limit_history (
                id integer primary key autoincrement,
                profile_id text not null,
                profile_name text not null,
                provider text not null,
                refreshed_at_utc text not null,
                refresh_reason text not null,
                state text not null,
                short_used_percent real,
                short_left_percent real,
                short_reset_utc text,
                weekly_used_percent real,
                weekly_left_percent real,
                weekly_reset_utc text,
                weekly_estimate_utc text,
                reset_credits_available text,
                limit_reached_type text,
                last_error text
            );
            create unique index if not exists idx_limit_history_profile_refresh
                on limit_history(profile_id, refreshed_at_utc, refresh_reason);
            create index if not exists idx_limit_history_profile on limit_history(profile_id);
            """
        )
        connection.commit()
    finally:
        connection.close()


def history_bucket_hash(bucket: dict) -> str:
    text = json.dumps(bucket, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def history_bucket_source(profile: dict, bucket: dict) -> str:
    source = str(bucket.get("source") or "").strip()
    return source or provider_key(profile)


def history_message_count(bucket: dict) -> int | None:
    for key in ("messageCount", "messages", "requestCount", "requests"):
        value = sanitize_float(bucket.get(key))
        if value is not None:
            return int(round(value))
    return None


def record_profile_history(profile: dict, refresh_reason: str = "refresh") -> None:
    init_history_db()
    now = iso_utc_now()
    pid = profile_id(profile)
    provider = provider_key(profile)
    name = str(profile.get("name") or "Account")
    connection = sqlite3.connect(HISTORY_DB_FILE)
    try:
        for bucket in profile.get("usageDailyBuckets") or []:
            if not isinstance(bucket, dict):
                continue
            day = day_from_bucket(bucket)
            if not day:
                continue
            bucket_json = json.dumps(bucket, sort_keys=True, default=str)
            connection.execute(
                """
                insert into usage_history (
                    profile_id, profile_name, provider, bucket_day, tokens, active_minutes,
                    message_count, source, bucket_hash, bucket_json, first_seen_utc, last_seen_utc
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(profile_id, bucket_day, source, bucket_hash) do update set
                    profile_name = excluded.profile_name,
                    provider = excluded.provider,
                    tokens = excluded.tokens,
                    active_minutes = excluded.active_minutes,
                    message_count = excluded.message_count,
                    bucket_json = excluded.bucket_json,
                    last_seen_utc = excluded.last_seen_utc
                """,
                (
                    pid,
                    name,
                    provider,
                    day,
                    tokens_from_bucket(bucket),
                    minutes_from_bucket(bucket),
                    history_message_count(bucket),
                    history_bucket_source(profile, bucket),
                    history_bucket_hash(bucket),
                    bucket_json,
                    now,
                    now,
                ),
            )

        refreshed_at = iso_from_value(profile.get("lastLimitsRefreshUtc")) or now
        weekly_reset = profile.get("weeklyResetEstimateUtc") or profile.get("weeklyLimitResetUtc")
        connection.execute(
            """
            insert or ignore into limit_history (
                profile_id, profile_name, provider, refreshed_at_utc, refresh_reason, state,
                short_used_percent, short_left_percent, short_reset_utc,
                weekly_used_percent, weekly_left_percent, weekly_reset_utc, weekly_estimate_utc,
                reset_credits_available, limit_reached_type, last_error
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                name,
                provider,
                refreshed_at,
                refresh_reason,
                effective_state(profile),
                sanitize_float(profile.get("shortLimitUsedPercent")),
                percent_left(profile.get("shortLimitUsedPercent")),
                iso_from_value(profile.get("shortLimitResetUtc")),
                sanitize_float(profile.get("weeklyLimitUsedPercent")),
                percent_left(profile.get("weeklyLimitUsedPercent")),
                iso_from_value(profile.get("weeklyLimitResetUtc")),
                iso_from_value(weekly_reset),
                str(profile.get("resetCreditsAvailable") or ""),
                str(profile.get("limitReachedType") or ""),
                str(profile.get("lastLimitsError") or profile.get("lastUsageError") or ""),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def seed_history_from_profiles(profiles: list[dict]) -> None:
    for profile in profiles:
        record_profile_history(profile, refresh_reason="seed")


def history_usage_entries(profiles: list[dict], iso_day: str | None = None) -> list[dict]:
    init_history_db()
    profiles_by_id = {profile_id(profile): profile for profile in profiles}
    allowed = set(profiles_by_id)
    query = (
        "select profile_id, profile_name, provider, bucket_day, tokens, active_minutes, "
        "message_count, source, bucket_json from usage_history"
    )
    params: list[object] = []
    if iso_day is not None:
        query += " where bucket_day = ?"
        params.append(iso_day)
    query += " order by bucket_day, profile_name, provider"
    connection = sqlite3.connect(HISTORY_DB_FILE)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(query, params).fetchall()
    finally:
        connection.close()

    # Each refresh appends a NEW cumulative snapshot for the same day (the bucket
    # hash changes as the day's tokens grow), so a single day can have many rows.
    # Keep only the largest snapshot per (profile, day, source) — summing every
    # snapshot over-counts tokens and minutes massively (e.g. "201h active/day").
    best: dict[tuple, dict] = {}
    for row in rows:
        pid = str(row["profile_id"])
        if pid not in allowed:
            continue
        profile = profiles_by_id.get(pid) or {"id": pid, "name": row["profile_name"], "provider": row["provider"]}
        try:
            bucket = json.loads(str(row["bucket_json"] or "{}"))
        except json.JSONDecodeError:
            bucket = {}
        entry = {
            "profileId": pid,
            "profile": profile,
            "day": str(row["bucket_day"] or ""),
            "tokens": int(row["tokens"] or 0),
            "minutes": None if row["active_minutes"] is None else int(row["active_minutes"]),
            "messageCount": None if row["message_count"] is None else int(row["message_count"]),
            "source": str(row["source"] or ""),
            "bucket": bucket,
        }
        key = (pid, entry["day"], entry["source"])
        current = best.get(key)
        if current is None or entry["tokens"] >= current["tokens"]:
            best[key] = entry
    return list(best.values())


def history_limit_count() -> int:
    init_history_db()
    connection = sqlite3.connect(HISTORY_DB_FILE)
    try:
        row = connection.execute("select count(*) from limit_history").fetchone()
        return int(row[0] if row else 0)
    finally:
        connection.close()


