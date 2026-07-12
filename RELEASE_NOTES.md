# AI Account Hub 1.1

Release date: 12 July 2026

## Highlights

- A fifth Statistics workspace for privacy-thresholded Community model and
  reasoning comparisons.
- Opt-in signed daily contributions with exact payload preview, local consent,
  upload receipts, and signed withdrawal.
- Dots, line, and vertical-bar Community views with provider, range, and ranking
  controls.
- A compact Community sharing panel that remains available from Accounts and
  Statistics without changing the current workspace.
- Personal Usage Statistics with separate Overview, Models, Productivity, and
  Compare sections.
- A compact Best Next tray widget, Signal Rail account warnings, and automatic
  provider discovery on every launch.

## Community Results

- Community results are grouped by provider, model, and reasoning setting.
- Rankings expose one factual metric at a time, including tasks per 5-hour
  capacity and tokens per completed task. They do not claim model quality.
- Real cohorts and individual days remain private until at least 10 distinct
  installations contribute to the same cohort.
- Until a real cohort qualifies, the interface clearly labels its synthetic
  staging preview and reports real collection progress separately.
- Public results remain readable without enabling uploads.

## Community Sharing

- Sharing is disabled by default and never becomes enabled through an upgrade.
- The fixed header control opens a compact panel with current state, last
  upload, daily schedule, payload preview, full settings, and deletion.
- The client sends one allowlisted numeric summary per UTC day. Prompt text,
  responses, source code, diffs, file paths, account names, email addresses,
  provider credentials, and project names are excluded.
- Every installation creates its own P-256 signing key. Windows DPAPI protects
  the private key locally; no shared upload secret or Cloudflare credential is
  packaged with the application.
- Signed requests use bounded timestamps and nonces. The Worker rejects replay,
  unknown fields, invalid signatures, and duplicate daily submissions.
- Signed withdrawal deletes the installation's accepted raw submissions and
  rebuilds future aggregates without that contribution.
- This release uses the labelled Cloudflare staging pilot. It is not presented
  as an expert-approved production leaderboard.

## Accounts And Limits

- Account cards show weekly and 5-hour capacity, readiness, active state, and
  provider-exposed reset countdowns.
- Calendar days combine token totals, usage records, and weekly-reset markers.
- Visible accounts provide pooled weekly and session capacity without merging
  authentication or provider quota.
- Codex reset credits remain visible and usable when OpenAI exposes them.
- Codex rollover protection validates the 5-hour and weekly windows
  independently and requires confirmation for suspicious exhausted-window
  rollovers.
- Paid Claude Code profiles keep CLI authentication isolated and can capture a
  matching official Claude Desktop session for later switching.

## Personal Statistics

- Model names are consolidated while observed reasoning settings remain
  available for filtering and comparison.
- Compare accepts two to four model/reasoning selections, uses the first as the
  baseline, and keeps absolute values separate from signed differences.
- Same-model reasoning comparison supports observed settings such as High,
  XHigh, and Ultra and can restore the previous mixed-model roster.
- Productivity Density keeps tokens, tasks, edits, files, tests, commands,
  lines changed, active time, and measured limit movement as separate facts.
- Line and vertical-bar charts retain independent modes, focus views, polished
  tooltips, PNG export, and CSV export.
- Analytics remain factual and local. No quality score, survey, synthetic test
  prompt, or prompt-content classification is performed.

## Desktop And Packaging

- The source launcher installs missing Python requirements, scans providers,
  starts through `pythonw.exe`, and records hidden startup failures without
  leaving a CMD taskbar entry.
- The tray widget can include or exclude providers and individual accounts.
- Signal Rail warns when active limits are low or exhausted and reports
  confirmed resets using the Hub's visual language.
- The GitHub **Build Windows executable** workflow produces a portable Windows
  x64 ZIP and SHA-256 checksum. A `v1.1` tag also creates or updates a draft
  GitHub Release using these notes.
- The portable executable includes the public setup guides, analytics and
  Community security documentation, and the current README screenshots.

## Known Limits

- Version 1.1 is Windows-first. macOS and Linux still require native process,
  packaging, tray, notification, provider-state, and secure-key adapters.
- Community uploads currently require Windows DPAPI. Community results remain
  readable on other platforms once the UI is ported.
- Cursor and Antigravity model/token analytics remain **Not exposed** because no
  dependable privacy-safe local telemetry source is currently available.
- Codex model history is shared by the local Codex installation. Where exact
  per-account attribution is unavailable, the Hub labels inferred attribution.
- Provider logout can revoke captured sessions. Claude Desktop users may need
  to repeat Desktop Login after logging out inside the official application.
- The Windows executable is not code-signed. SmartScreen may identify it as an
  unrecognized application; verify the ZIP against the published checksum.

## Install Or Upgrade

1. Download and extract `AI-Account-Hub-1.1-windows-x64.zip`.
2. Run `AI-Account-Hub.exe`; the portable folder already contains Python and Qt.
3. Add accounts from the empty first-run dashboard, or continue using existing
   profiles stored under `%USERPROFILE%\.codex-account-launcher`.
4. Leave Community sharing off, or inspect the exact payload and opt in from the
   fixed header control.

Source-checkout users can continue running `Start-AI-Account-Hub.bat`; it
installs requirements and performs the same provider scan before launch.
Upgrading reuses external Hub data and does not require copying profile or login
files into the application folder.
