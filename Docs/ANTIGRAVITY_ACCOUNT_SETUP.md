# Antigravity Account Setup

AI Account Hub can track Antigravity (Google) accounts, but Antigravity is the
most limited of the four providers: its login is **desktop-only** and its CLI
does not expose account, status, or usage commands. Read the **Limitations**
note below before adding an account.

## Add An Antigravity Account

1. Open **Accounts** and select **Add**.
2. Choose **Antigravity** as the provider (Plan auto-fills to Pro).
3. Enter a unique display name. The optional email is a local label only.
4. Keep the auto-derived, unique profile path, then select **Add profile**.
5. Sign in through the **Antigravity desktop app** using your Google account —
   this is the only supported login surface.
6. Return to the Hub and press **Refresh** to capture whatever local session
   metadata is available.

## Limitations

Antigravity's `agy` CLI provides only `changelog / install / models / plugin /
update` — there is **no** `login`, `status`, `account`, or `usage` command, and
login happens entirely inside the desktop app (Google SSO). As a result the Hub:

- can list the account and open its conversations grouped by project, but
- **cannot** perform an in-Hub login, and **cannot** read token usage or
  rate-limit windows the way it does for Codex/Claude.

Use **Online** to open the Antigravity web dashboard for plan, credits, and
usage. If a provider adds CLI account/usage commands in the future, the Hub's
Antigravity support can be extended to match Codex/Claude.

## Troubleshooting

### Card never becomes Ready

Expected for Antigravity — there is no CLI login/status for the Hub to verify.
Sign in through the Antigravity desktop app and use **Online** for account
details.

### No usage shown

Expected — Antigravity does not expose usage to any local interface the Hub can
read. Use **Online** for the web dashboard.

## Local Security

Any captured Antigravity session metadata lives under the machine-local AI
Account Hub data directory, outside this repository. Never copy, commit, upload,
or share it.
