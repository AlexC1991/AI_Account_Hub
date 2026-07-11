"""Local-storage reporting and conservative Hub-owned cleanup.

Provider history is measured for transparency but is never removed here. The
cleanup boundary is deliberately limited to AI Account Hub's SQLite retention
and disposable caches inside its isolated browser profiles.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sqlite3
from pathlib import Path

from ai_account_hub.core import hub_core


HISTORY_RETENTION_DAYS = 400
_BROWSER_CACHE_NAMES = {
    "cache", "code cache", "gpucache", "dawncache", "grshadercache",
    "shadercache", "graphitedawncache",
}


def format_bytes(value: int | float) -> str:
    amount = max(0.0, float(value or 0))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return "0 B"


def directory_size(path: Path) -> int:
    total = 0
    try:
        for root, _directories, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _browser_cache_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    result = []
    root_resolved = root.resolve()
    for current, directories, _files in os.walk(root):
        current_path = Path(current)
        for name in list(directories):
            candidate = current_path / name
            if name.lower() not in _BROWSER_CACHE_NAMES:
                continue
            try:
                candidate.resolve().relative_to(root_resolved)
            except (OSError, ValueError):
                continue
            result.append(candidate)
            directories.remove(name)
    return result


def storage_report() -> dict:
    """Return Hub-managed and provider-owned sizes without reading file content."""
    root = hub_core.LAUNCHER_ROOT
    database = hub_core.HISTORY_DB_FILE
    browser_root = hub_core.BROWSER_PROFILES_ROOT
    cache_dirs = _browser_cache_directories(browser_root)
    managed_total = directory_size(root)
    database_bytes = database.stat().st_size if database.is_file() else 0
    browser_bytes = directory_size(browser_root)
    browser_cache_bytes = sum(directory_size(path) for path in cache_dirs)
    desktop_state_bytes = sum(
        directory_size(path)
        for path in (
            root / "claude-desktop-states",
            root / "claude-desktop-default-backup",
            root / "desktop-default-backup",
        )
    )
    provider_locations = (
        ("Codex history and runtime", hub_core.DEFAULT_CODEX_HOME),
        ("Codex desktop installation", hub_core.LOCALAPPDATA_ROOT / "OpenAI" / "Codex"),
        ("Claude Code history", hub_core.CLAUDE_CLI_HOME),
        ("Claude Desktop data", hub_core.CLAUDE_ROAMING_HOME),
    )
    providers = [
        {"label": label, "path": str(path), "bytes": directory_size(path), "managed": False}
        for label, path in provider_locations
        if path.exists()
    ]
    try:
        disk = shutil.disk_usage(root if root.exists() else Path.home())
        disk_values = {"total": disk.total, "used": disk.used, "free": disk.free}
    except OSError:
        disk_values = {"total": 0, "used": 0, "free": 0}
    return {
        "root": str(root),
        "managedBytes": managed_total,
        "databaseBytes": database_bytes,
        "browserBytes": browser_bytes,
        "browserCacheBytes": browser_cache_bytes,
        "desktopStateBytes": desktop_state_bytes,
        "otherManagedBytes": max(
            0, managed_total - database_bytes - browser_bytes - desktop_state_bytes
        ),
        "providerBytes": sum(int(item["bytes"]) for item in providers),
        "providers": providers,
        "disk": disk_values,
    }


def cleanup_managed_storage(retention_days: int = HISTORY_RETENTION_DAYS) -> dict:
    """Prune old numeric snapshots and disposable isolated-browser caches."""
    retention = max(30, int(retention_days or HISTORY_RETENTION_DAYS))
    before = storage_report()
    removed_rows = 0
    cutoff_day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retention)).date().isoformat()
    cutoff_utc = f"{cutoff_day}T00:00:00+00:00"
    database = hub_core.HISTORY_DB_FILE
    if database.is_file():
        connection = sqlite3.connect(database)
        try:
            statements = (
                ("delete from usage_history where bucket_day < ?", cutoff_day),
                ("delete from limit_history where refreshed_at_utc < ?", cutoff_utc),
                ("delete from benchmark_source_cache where scanned_at_utc < ?", cutoff_utc),
                ("delete from benchmark_tasks where updated_at_utc < ?", cutoff_utc),
            )
            for sql, value in statements:
                try:
                    cursor = connection.execute(sql, (value,))
                except sqlite3.OperationalError:
                    continue
                removed_rows += max(0, int(cursor.rowcount or 0))
            connection.commit()
        finally:
            connection.close()
        # VACUUM runs only on an explicit cleanup request, never during refresh.
        connection = sqlite3.connect(database)
        try:
            connection.execute("vacuum")
        finally:
            connection.close()

    failed_paths = []
    removed_cache_directories = 0
    for cache_path in _browser_cache_directories(hub_core.BROWSER_PROFILES_ROOT):
        try:
            shutil.rmtree(cache_path)
            removed_cache_directories += 1
        except OSError:
            failed_paths.append(str(cache_path))

    after = storage_report()
    return {
        "freedBytes": max(0, int(before["managedBytes"]) - int(after["managedBytes"])),
        "removedRows": removed_rows,
        "removedCacheDirectories": removed_cache_directories,
        "failedPaths": failed_paths,
        "report": after,
        "retentionDays": retention,
    }


__all__ = [
    "HISTORY_RETENTION_DAYS", "cleanup_managed_storage", "directory_size",
    "format_bytes", "storage_report",
]
