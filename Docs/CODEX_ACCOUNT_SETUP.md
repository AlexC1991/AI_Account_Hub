# Codex Account Setup

AI Account Hub manages multiple OpenAI Codex accounts side by side. Each account
gets its own isolated Codex home (under the machine-local Hub data directory)
so their logins, session history, and rate-limit windows stay separate.

- **Login** authenticates the Codex CLI / `codex app-server` for an account and
  is what the Hub's usage/limits reads depend on.
- **Open Desktop** loads a selected account into the shared Codex Desktop app
  (`~/.codex`), which is the account whose history the sidebar mirrors.

## Add A Codex Account

1. Open **Accounts** and select **Add**.
2. Choose **Codex** as the provider (Plan auto-fills to Plus; change it if
   your account is Pro/Team).
3. Enter a unique display name. The optional email is a local label only.
4. Keep the auto-derived, unique profile path, then select **Add profile**.
5. Select the new account card and press **Login**.
6. Complete the official Codex browser sign-in for this account.
7. Return to the Hub and press **Refresh**. The card should show the account's
   plan and its weekly / 5-hour limit bars.

For a second or third account, repeat with a new profile name and path — each
account keeps its own Codex home, so logging into one never signs out another.

## Switch Which Account Codex Desktop Uses

- Select an account in **Accounts** and press **Open Desktop**. The Hub syncs
  that account's credentials into the shared Codex home and opens Codex Desktop
  signed in as that account. The account currently loaded there is marked
  **In use**.
- Use **Open CLI** to launch a terminal already pointed at that account's Codex
  home.

Switch accounts through the Hub. Signing out inside Codex Desktop to change
accounts can revoke that session and require **Login** again.

## Usage & Limits

- Codex exposes **daily token totals** and the **weekly** + **5-hour** rate
  limit windows, which the Hub reads via `codex app-server`.
- Codex does **not** report active-minutes, so "Month active" is blank ("—")
  for Codex accounts — that figure only exists for providers that record it.
- The calendar and stat cards are per-account; the shared `~/.codex` history is
  attributed to whichever account is currently signed into Codex Desktop.

## Troubleshooting

### Card stays "Login" or "Not ready"

The account has no valid Codex login yet. Press **Login**, finish the browser
sign-in, then **Refresh**.

### Limits show blank after login

Press **Refresh**. If it still fails, the Codex CLI / `app-server` may not be
installed or on PATH — install the Codex CLI and retry.

### Revoked or expired credentials

Press **Login** again. Restoring local files cannot recover a token the provider
has revoked.

## Local Security

Per-account Codex homes and captured Desktop state live under the machine-local
AI Account Hub data directory, outside this repository. They contain
authentication material. Never copy, commit, upload, or share them.
