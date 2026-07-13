# Real-World Usage Analytics

The Statistics workspace describes one person's observed use of Codex
and Claude Code. It answers a resource question:

> How much observable engineering activity happened for the resources consumed?

It does not answer whether one model produced better code. There is no benchmark
prompt, survey, rating, manual task category, quality score, or hidden semantic
classification.

## Metric Contract

The Hub keeps resource totals and observable work separate. They can describe
the same period without being identical:

| Metric | Meaning | Source |
| --- | --- | --- |
| Attributed provider tokens | Provider-reported token total attributed to a used model/reasoning setting | Codex account usage allocation or Claude message usage |
| Work tokens | Task-attributed total minus cache re-reads | Deduplicated local task history |
| Completed tasks | Tasks with provider completion evidence | Codex completion events or completed Claude responses |
| Active time | Observed task span | Local history; spans over four hours are excluded as idle-contaminated |
| Unique files | Distinct hashed file identities in the selected range | Edit tool activity |
| File touches | Additive changed-file operations, including repeat touches | Edit tool activity |
| 5h / weekly burn | Positive percentage-point movement between trusted snapshots | Saved provider limit snapshots |

Task-derived work, edits, file touches, tests, commands, and active time include
all observed task outcomes. The Completed Tasks counter includes only completed
outcomes. Consequently, **work tokens per completed task** is a cost-per-outcome
measure: resources spent on aborted or incomplete attempts remain in the
numerator. It is not the median size of a successful task.

Canonical token fields stay provider-native. Codex reasoning is retained when
Codex exposes it separately, including in exports and internal reconciliation.
The cross-provider **Token category mix** folds that value into **Output** so
Claude is not shown with a misleading zero-reasoning slice when it does not
expose a separate counter. Category totals still reconcile to the attributed
provider total; unknown remainder stays **Unclassified**.

New limit-history snapshots retain the provider's short and weekly window
labels. Existing unlabeled Claude short-window rows remain valid. Existing
unlabeled Codex rows enter the 5-hour series only when their reset horizon is
within eight hours and they are not a duplicate of the weekly window. Ambiguous
or weekly-shaped rows remain available to the weekly series but are excluded
from 5-hour burn.

Every additive time-series value reconciles with its matching selected-range
total. Ratios use one declared denominator throughout: tokens per task and tasks
per million use Work Tokens, while token-category charts use Attributed Provider
Tokens. A missing source or denominator remains **Not exposed**, which is
different from a measured zero.

## Account And Provider Aggregation

**Combined totals** sum the selected account pool. **Average per provider
account** divides each provider's additive metrics only by that provider's
accounts with attributed usage in the selected range. For example, three Codex
accounts and one Claude account produce `Codex / 3` and `Claude / 1`; Claude is
never divided by four. The same range-level divisor is used for every day, so
line totals continue to reconcile with bars and tables. Ratios built from
additive metrics and per-task distributions do not change merely because an
owner has more accounts. Unique files are deduplicated inside each exact
profile before their provider-account mean is calculated.

Provider scopes can isolate All Codex Accounts or All Claude Accounts before
applying either aggregation. Individual Claude Code profiles retain exact local
work attribution where the profile history exposes it. Codex Desktop history is
shared across switched logins: the all-Codex/provider view includes that shared
history once, while an individual Codex account shows its attributed provider
usage but leaves account-specific work metrics **Not exposed**. The Hub does not
copy the same shared work into every Codex account.

## Productivity Density

Productivity Density is intentionally a bundle rather than one number. For each
used provider/model/reasoning-effort setting, the Hub can show:

- attributed provider tokens and task-derived work tokens
- observed active time
- measured 5h and weekly percentage-point burn
- completed, aborted, and incomplete tasks
- edit operations and unique hashed files
- lines added and deleted
- test commands and successful test commands
- commands, tool calls, tool errors, rollbacks, and context compactions

The same facts can be normalized per 1 million work tokens, per 10 measured limit
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

## Community Results

The Community section is a separate aggregate view, not another local account
filter. It groups privacy-thresholded contributions by provider, model, and
reasoning setting and can rank one factual metric at a time. Dots use a metric
pair chosen from the active ranking, calculate both axes from the visible live
values, and size markers by observation volume. Lines show aggregate movement
over time; Vertical Bars compare absolute values from zero. Each desktop submission
is already a per-provider-account daily mean. The Worker then reports absolute
daily values as a per-contributor-day mean, so neither extra provider accounts
nor a larger daily cohort silently increases a model's public bar.
Tokens-per-task and limit-per-task ratios use the underlying contributor-day
totals as weighted observations. Contributor-days and observed completed tasks
remain separate sample-size fields.

Community labels distinguish synthetic staging samples, real collection that
has not reached publication threshold, and qualifying real cohorts. A cohort
and each visible day require at least 10 distinct installations before public
results can expose them. Sample size and observation range remain visible so a
small cohort is not presented with false authority.

These are resource and observable-work comparisons, not quality rankings.
Lower token use, higher task count, or lower limit burn may reflect different
work rather than a better model. The application therefore does not combine
the measures into one universal score.

Community sharing is off by default and is not required to read public results.
After opt-in, the Hub contributes one allowlisted numeric summary per UTC day.
The fixed header panel shows the upload schedule and last receipt and provides
preview, settings, and signed withdrawal actions.

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
For this reason, per-account token totals can be separated, but observed Codex
work is shown only in a pooled Codex scope and remains marked **shared Codex
history**. It is not repeated under individual Codex logins. Claude Code profile
histories are attributed to their isolated profile when the provider files
expose that scope. Claude assistant message IDs are deduplicated globally so
copied branches and transcript rewrites do not multiply tokens or activity.

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

The optional Community payload applies a second strict allowlist before any
network request. It excludes account and project identity, prompts, responses,
source, diffs, raw paths, command payloads, provider credentials, and local
history rows. See [Community Telemetry And Global Model Comparisons](COMMUNITY_TELEMETRY_SECURITY_PLAN.md)
for the signed protocol, server boundary, suppression threshold, and residual
integrity limits.

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
