"""Cross-platform discovery for the official provider applications and CLIs.

Discovery normally maps existing installations in place. The one exception is
the Store-packaged Codex CLI: Windows exposes its physical path through an app
execution alias but may deny direct process execution from ``WindowsApps``.
For that package only, discovery stages the signed CLI executable in the Hub's
machine-local runtime directory. No credentials or provider state are copied.
Discovery is repeatable so tools installed later are found on the next launch.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
REPORT_SCHEMA_VERSION = 1
REPORT_MAX_AGE_SECONDS = 90


def utc_now() -> str:
    """Return a stable UTC timestamp for reports and freshness checks."""

    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def platform_key(value: str | None = None) -> str:
    """Normalize Python and human platform names into three adapter keys."""

    raw = (value or sys.platform or platform.system()).lower()
    if raw.startswith(("win", "cygwin", "msys")):
        return "windows"
    if raw.startswith(("darwin", "mac")):
        return "macos"
    return "linux"


def expanded_path(value: object, home: Path) -> Path:
    """Expand environment variables and a leading home marker."""

    text = os.path.expandvars(str(value or "").strip())
    if text.startswith("~"):
        text = str(home) + text[1:]
    return Path(text)


def valid_target(path: Path, kind: str = "file") -> bool:
    """Check a candidate without executing it or following provider auth state."""

    return path.is_dir() if kind == "directory" else path.is_file()


def newest_files(root: Path, names: Iterable[str]) -> list[Path]:
    """Return matching files newest-first while tolerating inaccessible folders."""

    if not root.is_dir():
        return []
    matches: list[Path] = []
    try:
        for name in names:
            matches.extend(path for path in root.rglob(name) if path.is_file())
    except OSError:
        return []
    return sorted(matches, key=lambda path: path.stat().st_mtime, reverse=True)


def command_candidates(names: Iterable[str], env: dict[str, str]) -> list[tuple[Path, str]]:
    """Resolve command names against the launcher's current PATH."""

    found: list[tuple[Path, str]] = []
    search_path = env.get("PATH")
    for name in names:
        value = shutil.which(name, path=search_path)
        if value:
            found.append((Path(value), f"PATH:{name}"))
    return found


def windows_app_paths(executable_names: Iterable[str]) -> list[tuple[Path, str]]:
    """Read Windows App Paths registry entries used by conventional installers."""

    if platform_key() != "windows":
        return []
    try:
        import winreg
    except ImportError:
        return []

    found: list[tuple[Path, str]] = []
    roots = (
        (winreg.HKEY_CURRENT_USER, "HKCU"),
        (winreg.HKEY_LOCAL_MACHINE, "HKLM"),
    )
    for executable in executable_names:
        subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
        for root, label in roots:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    raw, _kind = winreg.QueryValueEx(key, None)
            except OSError:
                continue
            path = Path(str(raw))
            if path.is_file():
                found.append((path, f"registry:{label}"))
    return found


def windows_appx_locations(env: dict[str, str]) -> dict[str, Path]:
    """Query Store/MSIX packages once instead of scanning protected WindowsApps."""

    if platform_key() != "windows":
        return {}
    powershell = shutil.which("powershell.exe", path=env.get("PATH")) or shutil.which("pwsh.exe", path=env.get("PATH"))
    if not powershell:
        return {}
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "$items=@(); "
        "foreach($name in @('OpenAI.Codex','Claude')) { "
        "$pkg=Get-AppxPackage $name | Sort-Object Version -Descending | Select-Object -First 1; "
        "if($pkg){$items += [pscustomobject]@{Name=$pkg.Name;InstallLocation=$pkg.InstallLocation}} }; "
        "$items | ConvertTo-Json -Compress"
    )
    try:
        process = subprocess.run(
            [powershell, "-NoProfile", "-Command", script],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=8,
            creationflags=CREATE_NO_WINDOW,
        )
        payload = json.loads(process.stdout or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    rows = payload if isinstance(payload, list) else [payload]
    return {
        str(row.get("Name")): Path(str(row.get("InstallLocation")))
        for row in rows
        if isinstance(row, dict) and row.get("Name") and row.get("InstallLocation")
    }


def staged_codex_cli_path(env: dict[str, str], home: Path) -> Path:
    """Return the machine-local executable path used for Store Codex builds."""

    runtime_root = Path(
        env.get("AI_HUB_LAUNCHER_ROOT", str(home / ".codex-account-launcher"))
    ).expanduser()
    return runtime_root / "provider-tools" / "codex" / "codex.exe"


def stage_windows_codex_cli(
    appx_root: Path | None,
    env: dict[str, str],
    home: Path,
) -> tuple[Path | None, str]:
    """Stage an executable Store CLI without touching auth or package state.

    ``copy2`` preserves the source timestamp, so normal startup only compares
    metadata. A changed package is copied to a temporary sibling and atomically
    replaced; if an older staged CLI is currently running, discovery keeps that
    runnable copy and reports the deferred update instead of breaking launch.
    """

    if appx_root is None:
        return None, ""
    source = next(
        (
            candidate
            for candidate in (
                appx_root / "app" / "resources" / "codex.exe",
                appx_root / "app" / "resources" / "bin" / "codex.exe",
            )
            if candidate.is_file()
        ),
        None,
    )
    if source is None:
        return None, "The installed Codex package did not expose a CLI executable."

    target = staged_codex_cli_path(env, home)
    try:
        source_stat = source.stat()
        current = target.stat() if target.is_file() else None
        if (
            current is not None
            and current.st_size == source_stat.st_size
            and current.st_mtime_ns == source_stat.st_mtime_ns
        ):
            return target, ""
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return target, ""
    except OSError as error:
        if target.is_file():
            return target, f"Codex CLI staging update was deferred: {error}"
        return None, f"Codex CLI could not be staged from the installed package: {error}"


def resolve_target(
    *,
    env: dict[str, str],
    home: Path,
    override_names: Iterable[str],
    command_names: Iterable[str],
    known_candidates: Iterable[tuple[Path, str]],
    kind: str = "file",
    require_runnable: bool = False,
) -> dict:
    """Resolve one tool with clear precedence and diagnostics.

    Explicit environment overrides win. An invalid override is reported but
    does not prevent fallback discovery, which keeps a stale setting from
    breaking an otherwise healthy installation.
    """

    warnings: list[str] = []
    checked: list[str] = []
    candidates: list[tuple[Path, str]] = []
    for variable in override_names:
        raw = env.get(variable, "").strip()
        if not raw:
            continue
        path = expanded_path(raw, home)
        checked.append(str(path))
        if valid_target(path, kind):
            runnable, version = probe_command(path) if require_runnable else (True, "")
            if not runnable:
                warnings.append(f"{variable} exists but did not pass a bounded --version probe: {path}")
                continue
            return {
                "found": True,
                "path": str(path.resolve()),
                "source": f"env:{variable}",
                "warnings": warnings,
                "checked": checked,
                "version": version,
            }
        warnings.append(f"{variable} points to a missing {kind}: {path}")

    candidates.extend(command_candidates(command_names, env))
    candidates.extend(known_candidates)
    seen: set[str] = set()
    for path, source in candidates:
        key = os.path.normcase(str(path))
        if not str(path) or key in seen:
            continue
        seen.add(key)
        checked.append(str(path))
        if valid_target(path, kind):
            runnable, version = probe_command(path) if require_runnable else (True, "")
            if not runnable:
                warnings.append(f"{source} candidate exists but could not run --version: {path}")
                continue
            resolved = path.resolve() if path.exists() else path
            return {
                "found": True,
                "path": str(resolved),
                "source": source,
                "warnings": warnings,
                "checked": checked,
                "version": version,
            }

    return {
        "found": False,
        "path": "",
        "source": "",
        "warnings": warnings,
        "checked": checked,
        "version": "",
    }


def common_cli_dirs(home: Path, env: dict[str, str], system: str) -> list[Path]:
    """List standard user-level binary directories used by official installers."""

    directories = [
        home / ".local" / "bin",
        home / "bin",
        home / ".npm-global" / "bin",
    ]
    if system == "windows":
        appdata = Path(env.get("APPDATA", home / "AppData" / "Roaming"))
        local = Path(env.get("LOCALAPPDATA", home / "AppData" / "Local"))
        directories.extend(
            [
                appdata / "npm",
                local / "Microsoft" / "WinGet" / "Links",
            ]
        )
    elif system == "macos":
        directories.extend([Path("/opt/homebrew/bin"), Path("/usr/local/bin")])
    else:
        directories.extend([Path("/usr/local/bin"), Path("/usr/bin")])
    return directories


def named_candidates(directories: Iterable[Path], names: Iterable[str], source: str = "known-path") -> list[tuple[Path, str]]:
    """Build candidate tuples without requiring the paths to exist yet."""

    return [(directory / name, source) for directory in directories for name in names]


def windows_provider_candidates(
    env: dict[str, str],
    home: Path,
    appx: dict[str, Path],
) -> dict[str, list[tuple[Path, str]]]:
    """Return Windows paths used by native, Store, WinGet, and npm installs."""

    appdata = Path(env.get("APPDATA", home / "AppData" / "Roaming"))
    local = Path(env.get("LOCALAPPDATA", home / "AppData" / "Local"))
    program_files = Path(env.get("ProgramFiles", "C:/Program Files"))
    program_files_x86 = Path(env.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
    cli_dirs = common_cli_dirs(home, env, "windows")

    codex_cli = named_candidates(cli_dirs, ("codex.exe", "codex.cmd", "codex.bat"))
    codex_cli.extend((path, "Codex app bundle") for path in newest_files(local / "OpenAI" / "Codex" / "bin", ("codex.exe",)))
    codex_appx = appx.get("OpenAI.Codex")

    claude_cli = named_candidates(cli_dirs, ("claude.exe", "claude.cmd", "claude.bat"))
    claude_cli.extend((path, "Claude Desktop bundle") for path in newest_files(appdata / "Claude" / "claude-code", ("claude.exe",)))
    claude_appx = appx.get("Claude")

    cursor_roots = (
        program_files / "cursor",
        program_files_x86 / "cursor",
        local / "Programs" / "Cursor",
        local / "Programs" / "cursor",
    )
    cursor_agent_dirs = cli_dirs + [local / "cursor-agent", home / ".cursor" / "bin"]

    antigravity_roots = (
        local / "Programs" / "Antigravity",
        program_files / "Google" / "Antigravity",
        program_files / "Antigravity",
    )

    return {
        "codex_cli": codex_cli,
        "codex_desktop": (
            [(codex_appx, "AppX:OpenAI.Codex")] if codex_appx else []
        ),
        "claude_cli": claude_cli,
        "claude_desktop": (
            [(claude_appx / "app" / "Claude.exe", "AppX:Claude")] if claude_appx else []
        )
        + named_candidates(
            (local / "Programs" / "Claude", program_files / "Claude"),
            ("Claude.exe",),
        ),
        "cursor_desktop": named_candidates(cursor_roots, ("Cursor.exe",)) + windows_app_paths(("Cursor.exe",)),
        "cursor_cli": [
            (root / "resources" / "app" / "bin" / "cursor.cmd", "Cursor desktop bundle")
            for root in cursor_roots
        ],
        "cursor_agent": named_candidates(
            cursor_agent_dirs,
            ("cursor-agent.exe", "cursor-agent.cmd", "cursor-agent.ps1", "agent.exe", "agent.cmd"),
        ),
        "antigravity_desktop": named_candidates(antigravity_roots, ("Antigravity.exe",)) + windows_app_paths(("Antigravity.exe",)),
        "antigravity_cli": named_candidates(
            cli_dirs + [local / "agy" / "bin", appdata / "Antigravity" / "bin"],
            ("agy.exe", "agy.cmd", "agy.bat", "agy-node.cmd"),
        ),
    }


def posix_provider_candidates(system: str, home: Path, env: dict[str, str]) -> dict[str, list[tuple[Path, str]]]:
    """Return macOS/Linux paths documented by native and package installers."""

    cli_dirs = common_cli_dirs(home, env, system)
    executable_names = {
        "codex_cli": ("codex",),
        "claude_cli": ("claude",),
        "cursor_cli": ("cursor",),
        "cursor_agent": ("cursor-agent", "agent"),
        "antigravity_cli": ("agy",),
    }
    result = {key: named_candidates(cli_dirs, names) for key, names in executable_names.items()}

    if system == "macos":
        app_dirs = (Path("/Applications"), home / "Applications")
        result.update(
            {
                "codex_desktop": named_candidates(app_dirs, ("Codex.app",), kind_source("application bundle")),
                "claude_desktop": named_candidates(app_dirs, ("Claude.app",), kind_source("application bundle")),
                "cursor_desktop": named_candidates(app_dirs, ("Cursor.app",), kind_source("application bundle")),
                "antigravity_desktop": named_candidates(app_dirs, ("Antigravity.app",), kind_source("application bundle")),
            }
        )
    else:
        result.update(
            {
                "codex_desktop": [],
                "claude_desktop": named_candidates(
                    (Path("/opt/Claude"), Path("/usr/lib/claude"), home / ".local" / "opt" / "Claude"),
                    ("claude", "Claude"),
                ),
                "cursor_desktop": named_candidates(
                    (Path("/opt/Cursor"), Path("/usr/bin"), home / ".local" / "bin"),
                    ("cursor", "Cursor"),
                ),
                "antigravity_desktop": named_candidates(
                    (Path("/opt/Antigravity"), Path("/usr/bin"), home / ".local" / "bin"),
                    ("antigravity", "Antigravity"),
                ),
            }
        )
    return result


def kind_source(label: str) -> str:
    """Keep call sites readable when a candidate's source is descriptive."""

    return label


def probe_command(path_value: str | Path, timeout: float = 4) -> tuple[bool, str]:
    """Return whether a CLI can execute and its bounded version text."""

    path = Path(str(path_value or ""))
    if not path.is_file():
        return False, ""
    command: list[str]
    if platform_key() == "windows" and path.suffix.lower() == ".ps1":
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if not powershell:
            return False, ""
        command = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path), "--version"]
    else:
        command = [str(path), "--version"]
    try:
        process = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return False, ""
    output = (process.stdout.strip() or process.stderr.strip()).splitlines()
    version = output[0][:160] if output else ""
    return process.returncode == 0, version


def probe_version(path_value: str, timeout: float = 4) -> str:
    """Compatibility wrapper used by report enrichment and tests."""

    runnable, version = probe_command(path_value, timeout=timeout)
    return version if runnable else ""


def discover_provider_tools(
    *,
    env: dict[str, str] | None = None,
    system: str | None = None,
    home: Path | None = None,
    probe_versions: bool = True,
) -> dict:
    """Discover every supported provider from scratch.

    No prior report is consulted here. Callers that need startup caching should
    use `load_fresh_report` explicitly, making rescan behavior easy to audit.
    """

    environment = dict(os.environ if env is None else env)
    current_system = platform_key(system)
    user_home = Path(home or environment.get("USERPROFILE") or environment.get("HOME") or Path.home()).expanduser()
    appx = windows_appx_locations(environment) if current_system == "windows" else {}
    candidates = (
        windows_provider_candidates(environment, user_home, appx)
        if current_system == "windows"
        else posix_provider_candidates(current_system, user_home, environment)
    )
    staged_codex = None
    codex_stage_warning = ""
    if current_system == "windows":
        staged_codex, codex_stage_warning = stage_windows_codex_cli(
            appx.get("OpenAI.Codex"), environment, user_home
        )
        if staged_codex is not None:
            candidates.setdefault("codex_cli", []).insert(
                0, (staged_codex, "staged Store Codex CLI")
            )

    def resolve(
        key: str,
        overrides: tuple[str, ...],
        commands: tuple[str, ...],
        *,
        directory: bool = False,
        runnable: bool = False,
    ) -> dict:
        return resolve_target(
            env=environment,
            home=user_home,
            override_names=overrides,
            command_names=commands,
            known_candidates=candidates.get(key, []),
            kind="directory" if directory else "file",
            require_runnable=runnable and probe_versions,
        )

    mac_apps = current_system == "macos"
    claude_desktop_commands = ("Claude.exe",) if current_system == "windows" else (() if mac_apps else ("claude-desktop",))
    cursor_desktop_commands = ("Cursor.exe",) if current_system == "windows" else (() if mac_apps else ("cursor",))
    antigravity_desktop_commands = ("Antigravity.exe",) if current_system == "windows" else (() if mac_apps else ("antigravity",))
    providers = {
        "codex": {
            "cli": resolve(
                "codex_cli",
                ("AI_HUB_CODEX_CLI_PATH", "CODEX_CLI_PATH"),
                () if staged_codex is not None else ("codex.exe", "codex.cmd", "codex"),
                runnable=True,
            ),
            "desktop": resolve("codex_desktop", ("AI_HUB_CODEX_DESKTOP_PATH",), (), directory=mac_apps or current_system == "windows"),
        },
        "claude": {
            "cli": resolve(
                "claude_cli",
                ("AI_HUB_CLAUDE_CLI_PATH", "CLAUDE_CODE_PATH"),
                ("claude.exe", "claude.cmd", "claude"),
                runnable=True,
            ),
            "desktop": resolve(
                "claude_desktop",
                ("AI_HUB_CLAUDE_DESKTOP_PATH", "CLAUDE_DESKTOP_PATH"),
                claude_desktop_commands,
                directory=mac_apps,
            ),
        },
        "cursor": {
            "desktop": resolve(
                "cursor_desktop",
                ("AI_HUB_CURSOR_DESKTOP_PATH", "CURSOR_PATH"),
                cursor_desktop_commands,
                directory=mac_apps,
            ),
            "cli": resolve("cursor_cli", ("AI_HUB_CURSOR_CLI_PATH", "CURSOR_CLI_PATH"), ("cursor.cmd", "cursor")),
            "agent": resolve(
                "cursor_agent",
                ("AI_HUB_CURSOR_AGENT_PATH", "CURSOR_AGENT_PATH"),
                ("cursor-agent.exe", "cursor-agent.cmd", "cursor-agent", "agent.exe", "agent"),
                runnable=True,
            ),
        },
        "antigravity": {
            "desktop": resolve(
                "antigravity_desktop",
                ("AI_HUB_ANTIGRAVITY_DESKTOP_PATH", "ANTIGRAVITY_PATH"),
                antigravity_desktop_commands,
                directory=mac_apps,
            ),
            "cli": resolve(
                "antigravity_cli",
                ("AI_HUB_ANTIGRAVITY_CLI_PATH", "ANTIGRAVITY_CLI_PATH"),
                ("agy.exe", "agy.cmd", "agy"),
                runnable=True,
            ),
        },
    }
    if codex_stage_warning:
        providers["codex"]["cli"].setdefault("warnings", []).append(codex_stage_warning)

    support_dirs = common_cli_dirs(user_home, environment, current_system)
    support = {
        "python": {
            "found": bool(sys.executable),
            "path": sys.executable,
            "source": "current interpreter",
            "warnings": [],
            "checked": [sys.executable],
            "version": platform.python_version(),
        },
        "node": resolve_target(
            env=environment,
            home=user_home,
            override_names=("AI_HUB_NODE_PATH", "NODE_PATH"),
            command_names=("node.exe", "node"),
            known_candidates=named_candidates(
                support_dirs
                + ([Path(environment.get("ProgramFiles", "C:/Program Files")) / "nodejs"] if current_system == "windows" else []),
                ("node.exe", "node"),
            ),
            require_runnable=probe_versions,
        ),
        "git": resolve_target(
            env=environment,
            home=user_home,
            override_names=("AI_HUB_GIT_PATH",),
            command_names=("git.exe", "git"),
            known_candidates=[],
            require_runnable=probe_versions,
        ),
    }

    if probe_versions:
        probe_targets = {
            "codex": providers["codex"]["cli"],
            "claude": providers["claude"]["cli"],
            "cursor": providers["cursor"]["agent"],
            "antigravity": providers["antigravity"]["cli"],
            "node": support["node"],
            "git": support["git"],
        }
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                name: executor.submit(probe_version, target.get("path", ""))
                for name, target in probe_targets.items()
                if target.get("found") and not target.get("version")
            }
            for name, future in futures.items():
                version = future.result()
                if name in providers:
                    slot = "agent" if name == "cursor" else "cli"
                    if not providers[name][slot].get("version"):
                        providers[name][slot]["version"] = version
                else:
                    if not support[name].get("version"):
                        support[name]["version"] = version

    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "generatedAtUtc": utc_now(),
        "platform": current_system,
        "architecture": platform.machine(),
        "home": str(user_home),
        "providers": providers,
        "support": support,
    }


def write_discovery_report(report: dict, path: Path) -> Path:
    """Write the startup report atomically so partial JSON is never consumed."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def parse_utc(value: object) -> dt.datetime | None:
    """Parse report timestamps without depending on the GUI date helpers."""

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def load_fresh_report(path: Path, max_age_seconds: int = REPORT_MAX_AGE_SECONDS) -> dict | None:
    """Load a recent report only when its schema and timestamp are valid."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schemaVersion") != REPORT_SCHEMA_VERSION:
        return None
    generated = parse_utc(payload.get("generatedAtUtc"))
    if generated is None:
        return None
    age = (dt.datetime.now(dt.timezone.utc) - generated).total_seconds()
    return payload if -5 <= age <= max_age_seconds else None


def default_report_path(env: dict[str, str] | None = None) -> Path:
    """Keep machine-specific discovery output outside the Git checkout."""

    environment = os.environ if env is None else env
    explicit = environment.get("AI_HUB_DISCOVERY_REPORT", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    root = Path(environment.get("AI_HUB_LAUNCHER_ROOT", str(Path.home() / ".codex-account-launcher"))).expanduser()
    return root / "provider-discovery.json"


def summarize_report(report: dict) -> list[str]:
    """Produce safe, concise diagnostics without printing auth material."""

    lines = [f"Platform: {report.get('platform', 'unknown')} ({report.get('architecture', 'unknown')})"]
    providers = report.get("providers") if isinstance(report.get("providers"), dict) else {}
    for provider in ("codex", "claude", "cursor", "antigravity"):
        slots = providers.get(provider) if isinstance(providers.get(provider), dict) else {}
        parts = []
        for slot in ("desktop", "cli", "agent"):
            target = slots.get(slot)
            if isinstance(target, dict):
                parts.append(f"{slot}={'found' if target.get('found') else 'missing'}")
        lines.append(f"{provider}: {', '.join(parts) or 'not mapped'}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover AI Account Hub provider installations.")
    parser.add_argument("--write-report", action="store_true", help="Write provider-discovery.json outside the repository.")
    parser.add_argument("--report-path", help="Override the discovery report destination.")
    parser.add_argument("--no-probe", action="store_true", help="Skip bounded --version probes.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the summary used by interactive diagnostics.")
    args = parser.parse_args(argv)

    report = discover_provider_tools(probe_versions=not args.no_probe)
    path = Path(args.report_path).expanduser() if args.report_path else default_report_path()
    if args.write_report:
        write_discovery_report(report, path)
    if not args.quiet:
        for line in summarize_report(report):
            print(line)
        if args.write_report:
            print(f"Report: {path}")
    # Missing optional providers are not a launcher failure. The GUI reports
    # each capability honestly and can be relaunched after installing a tool.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
