# Provider Native Integration Audit

Date: 2026-07-01

## Current Direction

AI Account Hub is a thin native passthrough, not a replacement harness. It owns profile selection, launch/switching, local history indexing, calendar/usage presentation, and account rollups. Codex, Claude Code, Antigravity, and Cursor should keep owning their own agent loop, tools, prompts, memory, and session formats.

## Wired Providers

### Codex

Status: strongest integration.

- Uses `codex app-server --listen stdio://` with per-profile `CODEX_HOME`.
- Supports native thread list/start/resume/read.
- Streams agent text, command output, reasoning summaries, plan updates, file changes, unified turn diffs, and token usage.
- Handles native approval requests and user-input questions through the app-server request channel.
- Reads Codex account rate limits, usage buckets, weekly reset estimates, and reset credits through the local helper.
- Can switch the desktop app by closing existing Codex processes, syncing the selected profile, and relaunching the official desktop app.

Remaining checks:

- A successful live Codex coding turn still needs to be retested once a selected account is not at its short-term limit.

### Claude Code

Status: native CLI passthrough plus best-effort usage parsing and permission prompt bridge.

- Uses Claude Code print mode with `--output-format stream-json`, `--input-format text`, `--include-partial-messages`, native session ids, and resume flags. The Hub does not force `--brief` by default because that changes Claude's native response style.
- Uses per-profile `CLAUDE_CONFIG_DIR` / Claude home isolation.
- Reads `claude auth status` for CLI login metadata.
- Probes `/usage` and parses session/week percentages and reset text when Claude exposes them.
- Indexes local Claude Code JSONL history under the selected profile home for day-level token/message buckets.
- Launches Claude Desktop separately when requested.
- Starts a local MCP permission bridge for native Claude Code turns and passes it through `--mcp-config` plus `--permission-prompt-tool mcp__ai-account-hub-permissions__mcp_auth_tool`.
- The bridge receives Claude permission requests with `{ tool_name, input, tool_use_id }`, asks the Hub UI, and returns Claude's required JSON decision shape.
- `AskUserQuestion`, `EnterPlanMode`, and `ExitPlanMode` permission requests are handled through the MCP permission bridge when Claude Code emits them. `AskUserQuestion` opens a Hub dialog and returns `updatedInput` with `questions` and `answers`, so Claude can continue with the answer.
- `ExitPlanMode` opens an editable Hub plan-review dialog. The approved or edited plan is returned through `updatedInput`, and a live smoke verified Claude continues after the edited plan is approved.
- Claude `rate_limit_event` stream items update the selected Claude profile's weekly/session usage data where exposed.

Limitations:

- Claude plan only appears when `auth status`, desktop state, or usage output exposes a subscription/plan field. On this machine it can still display `Plan not exposed` because the installed Claude Code status output does not consistently publish paid-plan metadata.
- General permission prompts, plan review, and Claude `AskUserQuestion` are wired through the MCP bridge. Free-form mid-turn follow-up text outside provider-exposed tools still requires the next user turn, matching Claude Code print-mode behavior.

### Antigravity

Status: native CLI print-mode passthrough.

- Uses the healthy local `agy.exe`, not the broken roaming shim.
- Sends prompts through Antigravity's `--print` mode and resumes by conversation id when available.
- Reads local Antigravity metadata and desktop install state.
- Legacy Gemini/Google profile aliases now migrate to Antigravity because local Gemini auth is deprecated for this workflow.

Limitations:

- Antigravity usage/credit windows are not exposed through local state yet.
- `--print` currently takes the prompt as a command argument, so it is less private than Codex/Claude stdin paths.

### Cursor

Status: native Cursor Agent installed; login still required.

- Discovers Cursor Desktop, desktop launcher, and the installed Cursor Agent under `%LOCALAPPDATA%\cursor-agent`.
- Uses Cursor Agent print mode with `--print --output-format stream-json --stream-partial-output --trust`.
- Cursor Agent `status --format json` and `about --format json` feed account/version state.
- Cursor Agent `login` is wired to the Login button.
- Reads local Cursor Agent transcripts under `.cursor/projects/<project>/agent-transcripts`.
- Opens Cursor Desktop and account links.

Limitations:

- Current local Cursor Agent status is unauthenticated until the user completes `cursor-agent login`.
- Cursor usage, quota, and reset windows are not exposed through local app state.

## UI State

- Coding is now the default section.
- Accounts and calendar remain available under the Accounts section.
- Selecting an account in Accounts can hand it to Coding with `Use in Coding`.
- Coding has a left project/thread sidebar, native provider/account selector, bottom composer, optional session/files/terminal inspector, and screen-fit startup sizing.
- Codex native plans render as readable plan activity instead of raw JSON.
- Codex native approvals/questions open Hub-styled dialogs and respond through the native protocol.
- Claude native permission prompts and `AskUserQuestion` prompts open Hub-styled dialogs through the MCP bridge.

## Remaining Product Gaps

1. Retest a successful Codex live turn after an account leaves cooldown.
2. Complete Cursor Agent login and run a live Cursor coding E2E.
3. Recheck Claude plan metadata after Claude Code updates; current local output does not consistently expose the plan.
4. Add provider quota parsing for Antigravity and Cursor only if official local commands or APIs expose it.
5. Keep browser chat/account links as convenience shortcuts. Do not copy cookies between providers or browsers; that is brittle, likely to revoke sessions, and turns the Hub into a credential broker instead of a passthrough launcher.

## Sources

- OpenAI Codex app-server docs: https://developers.openai.com/codex/app-server
- Claude Code CLI reference: https://docs.anthropic.com/en/docs/claude-code/cli-reference
