# Provider Discovery Specification

This document defines how AI Account Hub locates official provider software. It is intentionally separate from profile and authentication logic.

## Goals

- Rescan on every launcher start and every direct GUI start.
- Rescan when the user selects Reload.
- Support standard, per-user, package-manager, portable, and explicitly overridden installs.
- Keep one discovery implementation shared by launchers, the GUI, diagnostics, and tests.
- Record enough evidence to troubleshoot a missing provider without collecting credentials.
- Let any subset of providers be installed. One missing provider must not block the hub.

## Non-goals

- Installing, updating, or repairing provider software.
- Reading, moving, refreshing, or validating provider auth tokens.
- Treating a discovered executable as proof that the user is logged in.
- Searching every disk recursively.
- Guessing quota, plan, or usage data from installation state.

## Startup Contract

`Start-AI-Account-Hub.bat` must:

1. Resolve a Python 3 interpreter.
2. Run `provider_discovery.py --write-report --quiet`.
3. Set `AI_HUB_DISCOVERY_BOOTSTRAPPED=1` only when that preflight succeeds.
4. Start the GUI even if preflight fails.
5. Hand the normal GUI process to `pythonw.exe` and exit so the bootstrap CMD process does not remain in the taskbar. `AI_HUB_CONSOLE=1` preserves the synchronous console path for diagnostics.

The GUI must:

1. Reuse a valid launcher report only when `AI_HUB_DISCOVERY_BOOTSTRAPPED=1` and the report is no more than 90 seconds old.
2. Otherwise perform a new scan and atomically replace the report.
3. Fall back to compatibility probes if the shared scanner raises an unexpected error.
4. Rescan unconditionally when Reload is selected.

This means a provider installed after the hub was downloaded appears on the next launch. A provider installed while the hub is already open appears after Reload.

## Resolution Precedence

Each target is resolved independently:

1. Valid explicit `AI_HUB_*_PATH` override
2. Matching command on the launch process `PATH`
3. Documented native per-user installation paths
4. Package-manager, application bundle, AppX, registry, and conventional system paths
5. Missing

An invalid override adds a warning and discovery continues. Duplicate candidates are removed using normalized paths.

Desktop, shell launcher, and native agent are distinct targets. For example:

- `Cursor.exe` is Cursor Desktop.
- `cursor.cmd` is Cursor's shell launcher.
- `cursor-agent` is Cursor Agent.

They must not be substituted for one another merely because their names share `cursor`.

## Provider Targets

Codex:

- `desktop`: installed Codex application/package
- `cli`: `codex`

Claude:

- `desktop`: Claude Desktop
- `cli`: `claude` / Claude Code

Cursor:

- `desktop`: Cursor editor application
- `cli`: `cursor` shell launcher
- `agent`: `cursor-agent` or its documented alias

Antigravity:

- `desktop`: Antigravity 2.0 standalone application
- `cli`: `agy`

The legacy Antigravity `agy-node.cmd` shim may be discovered as a path but must fail the separate health probe and must not be marked ready.

## Report Schema

The report is written outside the repository as `provider-discovery.json`.

Top-level fields:

- `schemaVersion`: integer schema version
- `generatedAtUtc`: ISO-8601 UTC timestamp
- `platform`: `windows`, `macos`, or `linux`
- `architecture`: host architecture
- `home`: resolved user home used for known paths
- `providers`: provider target maps
- `support`: Python, Node.js, and Git maps

Each target contains:

- `found`: boolean
- `path`: resolved path or an empty string
- `source`: override, PATH, package, registry, bundle, or known-path source
- `warnings`: invalid override or recoverable discovery warnings
- `checked`: ordered candidate paths considered before resolution
- `version`: bounded `--version` output where supported

The report must never contain:

- environment dumps
- API keys
- OAuth tokens
- refresh tokens
- cookies
- provider auth files
- command output from login or account-status probes

Writes must be atomic: write a sibling temporary file and replace the final report only after valid JSON is complete.

## Platform Rules

Windows:

- Query `PATH`, per-user bins, WinGet links, conventional install roots, App Paths, and relevant AppX packages.
- Do not recursively scan entire `Program Files` or `WindowsApps`.
- Accept `.exe`, `.cmd`, `.bat`, and provider-specific `.ps1` launchers where the downstream runner supports them.

macOS:

- Query `PATH`, `~/.local/bin`, Homebrew prefixes, `/Applications`, and `~/Applications`.
- Represent `.app` bundles as desktop directory targets.
- Launch bundles through the platform adapter, not by executing the directory.

Linux:

- Query `PATH`, `~/.local/bin`, `/usr/local/bin`, `/usr/bin`, and small provider-specific `/opt` roots.
- Support package-managed desktop launchers and documented CLI binaries.
- Respect `HOME` and XDG paths in the eventual platform adapter.

## Failure Behavior

- Missing optional providers: start normally and show Missing.
- Missing Node.js: start normally; disable/report Node-dependent Codex limit probing.
- Invalid override: warn, then continue normal discovery.
- Required runnable CLI probe timeout or failure: skip that candidate, record a warning, and continue to the next candidate.
- Optional version enrichment timeout: keep the already resolved path and leave its version empty.
- Broken discovery JSON: ignore it and rescan.
- Discovery exception: use compatibility probes and keep the GUI available.

## Acceptance Tests

A release is ready only when tests prove:

- An invalid override falls back to a valid installation.
- A second scan finds a binary created after the first scan.
- Windows candidates include current official user install roots.
- macOS and Linux candidates include native user bins and application bundles.
- Cursor desktop, shell CLI, and agent remain distinct.
- Reports are atomic, expire correctly, and omit secret environment values.
- The root batch launcher invokes discovery before the GUI.
- A GUI self-test succeeds when using a fresh launcher report.
