# AI Account Hub 1.1.1

Release date: 13 July 2026

This patch release corrects Statistics attribution, limit-window analysis, and
Community chart behavior introduced during the 1.1 feature release.

## Statistics Fixes

- Claude branch and copied-transcript messages are globally deduplicated by
  provider message ID before task and token totals are calculated.
- Codex shared Desktop history is counted once at provider scope instead of
  being duplicated across individual Codex accounts.
- All Codex, All Claude, and individual-account scopes remain separate, with
  explicit Combined totals and Average per provider account modes.
- Provider token totals and task-derived work tokens are labelled separately.
  Missing task, active-time, or limit evidence remains Not exposed instead of
  becoming a false zero.
- Daily chart values reconcile with their selected-range totals, including the
  per-provider-account average view.
- Unique files are deduplicated per account before provider-account averages;
  additive file touches remain a separate metric.

## Limits And Token Categories

- Limit history now records short and weekly window labels.
- Legacy Codex rows enter the 5-hour burn series only when their reset timing
  matches a real short window and does not duplicate weekly data.
- Claude's independently exposed 5-hour and weekly windows remain separate.
- Token category charts fold separately exposed reasoning tokens into Output
  for cross-provider presentation while raw analytics and exports retain the
  provider reasoning counter.
- Limit lines use solid segments between adjacent observations and dashed
  bridges across days without snapshots, preserving continuity without
  inventing zero usage.

## Community Fixes

- Community uploads preserve fractional per-provider-account daily averages
  instead of truncating them to integers.
- Public absolute values use per-contributor-day means so installations with
  more provider accounts do not inflate community comparisons.
- Seven, 30, and 90-day ratios are recalculated from the selected daily range.
- Lines is restored as the default Community chart.
- Synthetic staging lines now have distinct model-specific daily movement
  instead of repeating one constant ratio across every date. Real cohorts are
  never transformed and continue to use contributed daily values.
- Community dots now use live data coordinates rather than fixed model slots.
  The active ranking selects the metric pair, visible values define both axes,
  and marker size represents observation volume.

## Verification

- The complete Python and Qt suite passes with 122 tests and one intentional
  platform skip.
- The Cloudflare Worker passes TypeScript checking and Wrangler dry-run build.
- The Windows portable application builds and passes its frozen smoke test.
- Prompt text, response text, source code, diffs, file paths, account names,
  email addresses, provider credentials, and project names are not added to
  Statistics or Community payloads.

## Install Or Upgrade

1. Download and extract `AI-Account-Hub-1.1.1-windows-x64.zip`.
2. Run `AI-Account-Hub.exe`.
3. Existing profiles and history remain under
   `%USERPROFILE%\.codex-account-launcher` and are reused automatically.

AI Account Hub remains Windows-first. The executable is not code-signed, so
Windows may show a SmartScreen warning. The release includes a SHA-256 checksum
for verifying the downloaded ZIP.
