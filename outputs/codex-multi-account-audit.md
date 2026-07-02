# Codex Multi-Account Extension Audit

Date: 2026-06-30

## Executive Summary

A Codex account switcher is feasible if it uses supported Codex state boundaries: separate `CODEX_HOME` directories, each with its own auth cache, config, sessions, plugins, logs, and local state.

An automatic account pooler that changes accounts when one hits a rate/usage cooldown is not a safe build target. OpenAI terms prohibit circumventing rate limits or restrictions, and a tool designed to keep working by rotating accounts after a limit event would likely be interpreted that way.

The safest product shape is:

- A local "Codex Account Launcher" window.
- User-created profiles, one per account/workspace.
- Manual account selection.
- Login/status buttons per profile.
- CLI/app-server actions use isolated profile homes directly.
- Windows desktop launch stops lingering Codex Desktop processes, saves the active desktop auth back to its profile, then uses an explicit opaque `auth.json` sync into the default Codex home because the packaged app does not reliably inherit per-launch `CODEX_HOME`.
- No token parsing/display, no automated rate-limit evasion.
- Optional API-key mode or OpenAI-supported credits/plan upgrades for more usage.

## Local Audit Findings

Current machine/context:

- Codex desktop package: `OpenAI.Codex_26.623.9142.0_x64__2p2nqsd0c76g0`.
- Working bundled CLI: `C:\Users\batty\AppData\Local\OpenAI\Codex\bin\d8dfab353c0001dc\codex.exe`.
- CLI version: `codex-cli 0.142.4`.
- WindowsApps `codex.exe` launcher is visible on PATH but failed from this shell with Access Denied.
- `codex doctor` reports local config/auth are healthy.
- Auth storage mode is file-backed under `C:\Users\batty\.codex\auth.json`.
- Stored auth mode is ChatGPT, not API key.
- WebSocket connectivity is healthy.
- Plugins, apps, hooks, browser, Computer Use, and secret auth storage are enabled/stable where relevant.
- No persistent app-server daemon is currently running; app-server is available but experimental.

Isolation check:

- Running the CLI with a fresh `CODEX_HOME` directory produced `Not logged in`.
- This confirms that `CODEX_HOME` is a practical boundary for separate account state.

## Source Findings

Codex authentication:

- Codex supports ChatGPT sign-in and API-key sign-in.
- Codex caches login details locally and reuses them across the app, CLI, and IDE extension.
- File-backed credentials live under `CODEX_HOME` as `auth.json`, and should be treated like a password.

Codex configuration:

- `CODEX_HOME` controls Codex local state: config, auth, logs, sessions, skills, plugins, and standalone package metadata.
- Config profiles are useful for model/sandbox/tool defaults, but they are not the right boundary for separate account credentials.
- Project config is intentionally prevented from redirecting credential/provider-auth state.

Plugins:

- Plugins can bundle skills, MCP servers, app integrations, hooks, and install metadata.
- Plugins are a good packaging format for reusable workflows.
- Plugins are not the right layer for replacing Codex's core login/session manager or desktop account UI.

App server:

- The app-server can expose Codex over stdio, Unix sockets, or WebSocket endpoints.
- It is useful for custom clients or wrappers, but current docs and CLI mark it experimental.
- A custom launcher could use it later, but the first version should prefer launching supported Codex processes with explicit `CODEX_HOME`.
- The generated app-server protocol exposes `account/rateLimits/read`, `account/usage/read`, and `account/rateLimitResetCredit/consume`.
- `account/rateLimitResetCredit/consume` requires an idempotency key and returns `reset`, `nothingToReset`, `noCredit`, or `alreadyRedeemed`.
- The launcher should call the reset endpoint only after a manual confirmation and only when a prior rate-limit refresh reports at least one available reset credit.

Policy:

- OpenAI account-sharing guidance says an account is meant for the individual who created it.
- OpenAI terms prohibit circumventing rate limits, restrictions, or protective measures.
- Codex plan docs point users who need more usage toward additional credits, API usage, or suitable ChatGPT plans/workspaces.

## Feasibility Matrix

| Idea | Feasible | Update-resistant | Policy risk | Notes |
|---|---:|---:|---:|---|
| Manual profile launcher using separate `CODEX_HOME` dirs | Yes | High | Low | Best option. |
| Per-account login/status window | Yes | High | Low | Use `codex login status`; never read token contents. |
| Launch Codex CLI with selected account home | Yes | High | Low | CLI is straightforward with per-profile `CODEX_HOME`. |
| Launch Codex Desktop with selected account | Partial | Medium | Low | Windows desktop uses the default app profile; workaround is automated process stop plus opaque selected-profile auth sync into `~\.codex` before launch. |
| Shared dashboard showing "logged in/not logged in" | Yes | High | Low | Avoid exposing email/token details unless Codex status exposes them safely. |
| API-key based usage profile | Yes | High | Low/Medium | Usage billed through Platform account; some ChatGPT-only features may differ. |
| Manual reset-credit button using app-server | Yes | Medium/High | Low | Supported protocol path; must be explicit and user-confirmed. |
| Automatic switch when cooldown/rate limit is detected | Technically possible | Medium | High | Do not build; likely rate-limit circumvention. |
| Pool all accounts behind one virtual account | Technically possible only by proxying/rotating credentials | Low/Medium | Very high | Do not build. |
| Editing packaged Codex app files | Possible but brittle | Low | Medium | Avoid; updates will overwrite it and could break trust/security. |
| Plugin that replaces Codex auth | No supported path found | Low | High | Plugins should not own core auth/session behavior. |

## Recommended Architecture

### Version 1: Local Account Launcher

Build a small local app or script that stores profile metadata only:

```json
[
  {
    "name": "Account A",
    "codexHome": "C:\\Users\\batty\\.codex-accounts\\account-a"
  },
  {
    "name": "Account B",
    "codexHome": "C:\\Users\\batty\\.codex-accounts\\account-b"
  },
  {
    "name": "Account C",
    "codexHome": "C:\\Users\\batty\\.codex-accounts\\account-c"
  }
]
```

Each profile gets its own:

- `config.toml`
- `auth.json` or keyring-backed auth
- plugin installs
- sessions
- logs
- caches

Core actions:

- `Login`: run `codex login` with that profile's `CODEX_HOME`.
- `Status`: run `codex login status` with that profile's `CODEX_HOME`.
- `Open CLI`: start a terminal with `CODEX_HOME` set.
- `Open App`: stop Codex Desktop processes, save previous active desktop auth back to its profile, copy selected profile `auth.json` as an opaque file into the default Codex home, then run `codex app`.
- `Copy baseline config`: copy non-secret settings from current `config.toml`.

Guardrails:

- Never read, parse, or display `auth.json`.
- Copy `auth.json` only as an opaque local file for the Windows desktop workaround, with a one-time backup of the previous default auth file.
- Never switch accounts automatically from rate-limit/cooldown detection; desktop account switching remains a user-click action.
- Never auto-switch based on rate-limit/cooldown text.
- Never retry failed usage on another account automatically.
- Keep profile names user-defined and local.
- Keep all launcher settings outside the packaged Codex install directory.

### Version 2: App-Server Client

Only after Version 1 works:

- Start one Codex app-server process per account home.
- Show each process as a separate session target.
- Require manual selection.
- Use local-only WebSocket or stdio endpoints.
- Keep capability tokens private and per-profile.

This can become a richer "new window" experience, but app-server is experimental, so it should not be the first dependency.

## Rejected Design: Account Pooler

Do not build a component that:

- Monitors for a 5-hour limit/cooldown.
- Automatically switches to the next account.
- Aggregates three personal accounts into one virtual quota.
- Shares or rotates ChatGPT session tokens/API keys behind the scenes.
- Masks which account is being billed/limited.

Even if technically possible, the purpose would be to bypass usage controls. That creates policy, security, and account-risk problems.

## Better Ways To Get More Usage

Use OpenAI-supported routes instead:

- Buy additional Codex credits if available for your plan.
- Use API-key authentication for usage-based local workflows.
- Move heavy automation to API billing where possible.
- Use a Business/Enterprise workspace if this is work-related.
- Ask OpenAI support/sales about higher limits if your usage is legitimate and sustained.
- Reduce waste with lighter models/profiles for low-risk tasks.

## Prototype Plan

1. Create three empty profile homes under `C:\Users\batty\.codex-accounts\`.
2. Copy a redacted/baseline `config.toml` into each, excluding secrets and account-specific state.
3. Add a PowerShell launcher first; then wrap it in a small desktop UI if it proves useful.
4. Run `codex login` for each profile manually.
5. Verify `codex login status` and `codex doctor` per profile.
6. Test CLI launch per profile.
7. For Windows desktop, use the launcher switch action to stop lingering Codex Desktop processes, sync auth, and relaunch.
8. Decide whether a plugin is needed for workflows; avoid a plugin for auth/session switching.

## Source Links

- Codex authentication: https://developers.openai.com/codex/auth/
- Codex advanced config and `CODEX_HOME`: https://developers.openai.com/codex/config-advanced/
- Codex plugins: https://developers.openai.com/codex/plugins/
- Build Codex plugins: https://developers.openai.com/codex/plugins/build/
- Codex app server: https://developers.openai.com/codex/app-server/
- Codex pricing and additional credits: https://developers.openai.com/codex/pricing/
- Using Codex with your ChatGPT plan: https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan
- OpenAI account sharing policy: https://help.openai.com/en/articles/10471989-openai-account-sharing-policy
- OpenAI terms of use: https://openai.com/policies/row-terms-of-use/
