# Cursor Account Setup

AI Account Hub can manage Cursor accounts and switch the active `cursor-agent`
CLI login between them. Cursor's own tooling is more limited than Codex/Claude,
so read the **Usage & Limits** note below for what the Hub can and cannot show.

## Add A Cursor Account

1. Open **Accounts** and select **Add**.
2. Choose **Cursor** as the provider (Plan auto-fills to Pro).
3. Enter a unique display name. The optional email is a local label only.
4. Keep the auto-derived, unique profile path, then select **Add profile**.
5. Select the new account card and press **Login**.
6. Complete the official Cursor sign-in (`cursor-agent` opens the cursor.com
   login) for this account.
7. Return to the Hub and press **Refresh** — the card shows the signed-in
   account identity and status.

Repeat with a new profile for each additional account.

## Switch Accounts

- Press **Login** on an account to make its Cursor login the active one.
- Press **Logout** to sign that account out of the local Cursor CLI.
- Use **Open CLI** to launch a terminal for `cursor-agent`.

## Usage & Limits

Cursor's CLI (`cursor-agent`) provides **login / logout / status / about /
models** but **no usage or rate-limit command**, and Cursor's local tracking DB
only records lines-of-code percentages, not tokens. So for Cursor the Hub shows:

- account identity, plan label, and login/ready status, and
- your Cursor **chat history grouped by project** in the sidebar,

but it **cannot** show token usage or a session-limit bar the way it does for
Codex/Claude — that data isn't exposed anywhere the Hub can read it. Use
**Online** to open your Cursor web dashboard for usage/billing.

## Troubleshooting

### Card stays "Login"

Press **Login** and finish the `cursor-agent` sign-in. If nothing opens, install
the Cursor CLI (`cursor-agent`) and ensure it is on PATH.

### No usage shown

This is expected — see **Usage & Limits** above. Cursor does not expose usage to
the CLI. Use **Online** for the web dashboard.

### Revoked or expired credentials

Press **Login** again to re-authenticate the Cursor CLI.

## Local Security

Per-account Cursor state lives under the machine-local AI Account Hub data
directory, outside this repository. It contains authentication material. Never
copy, commit, upload, or share it.
