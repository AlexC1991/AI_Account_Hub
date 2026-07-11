# Real-World Usage Analytics

The Statistics workspace describes one person's observed use of Codex
and Claude Code. It answers a resource question:

> How much observable engineering activity happened for the resources consumed?

It does not answer whether one model produced better code. There is no benchmark
prompt, survey, rating, manual task category, quality score, or hidden semantic
classification.

## Productivity Density

Productivity Density is intentionally a bundle rather than one number. For each
used provider/model/reasoning-effort setting, the Hub can show:

- total and categorized tokens
- observed active time
- measured 5h and weekly percentage-point burn
- completed, aborted, and incomplete tasks
- edit operations and unique hashed files
- lines added and deleted
- test commands and successful test commands
- commands, tool calls, tool errors, rollbacks, and context compactions

The same facts can be normalized per 1 million tokens, per 10 measured limit
points, or per active hour. A missing denominator stays **Not exposed**. It is
never replaced with zero or an invented estimate.

## Views

Each graph has its own selector, so two different views can be compared without
changing the rest of the page. The left side remains a time-series line chart;
the right remains a vertical-bar chart:

- token activity and completed work
- 5h and weekly limit burn
- token category mix and engineering activity
- resource/work position and tokens per completed task
- per-task token and active-duration distributions

The Models section's taller lower panel switches independently between its
base-model table and numeric activity journal.

The Compare section has its own controls and does not reuse the Models filter.
It accepts two to four base-model/reasoning selections, uses the first as a
baseline, overlays the selected time series on a shared date scale, and shows a
full-value vertical comparison chart plus an absolute-and-delta table.
**Compare reasoning** temporarily replaces the roster with up to four observed
reasoning settings from the baseline model. The UI reports the visible variant
count, and **Restore comparison** returns to the previous mixed-model roster.
Every
bar starts at zero. The baseline remains a full vertical bar and also draws a
horizontal reference line across the plot. Comparison labels and table rows
keep the observed value separate from the signed difference.
Positive and negative differences describe resource or activity movement only;
neither sign is treated as a quality judgment.

All used base models are displayed. Zero-use configured models are omitted. The
Hub does not collapse the tail into an `Other` bucket or silently keep only the
top five. Reasoning variants remain available after selecting a base model and
can be isolated directly from chart legends or the Reasoning filter. Stacked
token-category bars keep an elided model label below every bar, so model
identity never depends on hover alone.

The mouse wheel scales the value axis while preserving the underlying values
and displaying the active scale on the chart. This lifts small non-zero series
away from the baseline without converting them to percentages. **Shift + mouse
wheel** zooms the time axis; dragging pans only after time zoom is active.
**Reset** restores both axes. Charts also support focus, PNG capture, and
model-only CSV export.

## Identity And Attribution

The Models section navigates by `provider + base model`, so GPT-5.5 appears once
even when High and XHigh were both used. Selecting that base model turns its
exposed reasoning efforts into separate chart series. The reasoning control can
show all efforts or only one, and reasoning order is available alongside usage
and model-name sorting. Raw analytics and CSV exports retain
`provider + model + reasoning effort`; the consolidation is a presentation
layer and does not discard attribution evidence.

The same model name used through different providers remains separate because
the harness behavior and provider telemetry are different. Account names are
not chart series and do not appear in exports.

Account selection filters resource records. Codex Desktop has one shared local
conversation/model history even while the Hub switches account authentication.
For this reason, per-account token totals can be separated while observed Codex
work remains marked **shared Codex history**. Claude Code profile histories are
attributed to their isolated profile when the provider files expose that scope.

## Limit Burn Rules

Limit burn is derived only when two snapshots for the same account:

- are no more than 20 minutes apart;
- expose both percentages being compared; and
- move forward in used percentage.

Decreases are treated as resets and are excluded. Long gaps are excluded. A
segment is allocated among overlapping used models by observed task-token
weight. These rules prefer an honest gap over a misleading efficiency claim.

## Local Privacy Boundary

Provider JSONL files are read locally and never modified. The persistent SQLite
cache stores numeric counters, timestamps, provider/model identifiers, stable
task IDs, and hashes. It does not store:

- prompts, responses, or reasoning text
- source code or diffs
- commands or tool output
- file or project paths
- account names or email addresses

CSV exports contain model/resource/work aggregates only. PNG exports capture
the selected model-only chart.

The Hub bounds its own numeric history and source cache to 400 days, preserving
the longest 365-day Statistics view. **File → Local data...** reports Hub-owned
storage and provider-owned storage separately. Its explicit cleanup action can
remove old Hub rows and isolated-browser caches, but never profiles, cookies,
saved provider sessions, desktop states, or official transcript history.

## Current Coverage

Codex and paid Claude Code profiles are supported when their official local
history exposes the relevant fields. Cursor, Antigravity, provider billing
prices, and subjective quality remain **Not exposed** until a dependable,
privacy-safe source exists.
