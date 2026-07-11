# AI Account Hub 1.1.0

Release date: 11 July 2026

## Highlights

- A complete Personal Usage Statistics workspace with separate Overview,
  Models, Productivity, and Compare sections.
- Head-to-head model comparisons using shared line charts, zero-based vertical
  bars, a clear baseline, and absolute values alongside signed differences.
- Same-model reasoning comparisons for observed settings such as High, XHigh,
  and Ultra, with a reversible **Compare reasoning** flow.
- Passive Productivity Density facts for tokens, tasks, edits, files, tests,
  commands, lines changed, active time, and measured limit movement.
- A compact Best Next tray widget and Hub-styled Signal Rail notifications.
- Automatic provider discovery on every launch, including current Microsoft
  Store Codex package handling.

## Accounts And Limits

- Account cards show weekly and 5-hour capacity, readiness, active state, and
  countdowns using provider-exposed data.
- Calendar days combine token totals, usage records, and weekly-reset markers.
- Visible accounts provide pooled weekly and session capacity without merging
  authentication or provider quota.
- Codex reset credits are shown and usable when OpenAI exposes them.
- Codex rollover protection now validates the 5-hour and weekly windows
  independently. An expired session can become ready without accepting an
  impossible early weekly reset or borrowing the weekly countdown.
- Paid Claude Code profiles keep CLI authentication isolated and can capture a
  matching official Claude Desktop session for later switching.

## Statistics

- Model names are consolidated while observed reasoning settings remain
  available for filtering and comparison.
- Line and vertical-bar charts have independent modes, focus views, polished
  tooltips, PNG export, and CSV export.
- Compare supports two to four model/reasoning selections and always treats the
  first row as the descriptive baseline.
- Comparison bars begin at zero and show actual observed values. Signed labels
  and detail rows report the difference from the baseline.
- **Compare reasoning** records the current roster, fills it with the baseline
  model's observed reasoning settings, reports how many variants are shown, and
  restores the prior roster in one click.
- Analytics remain factual and local. No quality score, survey, synthetic test
  prompt, or prompt-content classification is performed.

## Desktop Experience

- The source launcher installs missing Python requirements, scans providers,
  starts the GUI without leaving a CMD taskbar entry, and logs hidden startup
  failures.
- The tray widget can include or exclude providers and individual accounts.
- Signal Rail warns when active limits are low or exhausted and reports
  confirmed resets without relying on generic Windows notification styling.
- Theme tokens apply across Accounts, Statistics, dialogs, charts, the tray
  widget, and Signal Rail in dark and light appearances.

## Privacy And Storage

- Profiles, provider sessions, cookies, runtime databases, and diagnostics stay
  outside the repository and are excluded from release packages.
- The analytics cache stores numeric aggregates and hashed identifiers, not
  prompts, responses, source code, diffs, command payloads, tool output, email
  addresses, account names, or raw file paths.
- **File > Local data...** separates Hub-owned storage from official provider
  history and cleans only disposable caches and old numeric analytics rows.

## Known Limits

- Version 1.1.0 is Windows-first; macOS and Linux still require native process,
  packaging, tray, notification, and provider-state adapters.
- Cursor and Antigravity model/token analytics remain **Not exposed** because no
  dependable privacy-safe local telemetry source is currently available.
- Codex model history is shared by the local Codex installation. Where exact
  per-account attribution is unavailable, the Hub labels inferred attribution.
- Provider logout can revoke captured sessions. Claude Desktop users may need
  to repeat Desktop Login after logging out inside the official app.
- The Windows executable is not code-signed yet. SmartScreen may identify it as
  an unrecognized app; each release includes a SHA-256 checksum file.

## Install Or Upgrade

1. Download and extract `AI-Account-Hub-1.1.0-windows-x64.zip`.
2. Run `AI-Account-Hub.exe`; the portable artifact already contains Python and Qt.
3. Add accounts from the empty first-run dashboard, or continue using existing
   profiles stored under `%USERPROFILE%\.codex-account-launcher`.

Source-checkout users can continue running `Start-AI-Account-Hub.bat`; it
installs requirements and performs the same provider scan before launch.

Upgrading does not require copying profile or login files into the project
folder. Existing external Hub data is reused automatically.
