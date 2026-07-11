# Claude Account Setup

AI Account Hub supports two different Claude authentication surfaces. They are
owned by the official Anthropic applications and do not share login state:

- **Claude Code Login** authenticates the isolated Claude Code CLI profile used
  by the Hub for status, limits, and local model analytics.
- **Desktop Login** authenticates the Claude Desktop application.

A paid account that will use both Claude Code and Claude Desktop must complete
both one-time login flows.

## Add A Paid Claude Account

1. Open **Accounts** and select **Add**.
2. Choose **Claude Code (paid)**.
3. Enter a unique display name. The optional email is a local label that helps
   distinguish accounts.
4. Keep a unique profile path for this account, then select **Add profile**.
5. Select the new account card and press **Login**.
6. Complete the official Claude Code browser login using this account.
7. Return to the Hub and press **Refresh**. The account should expose its
   Claude Code identity before Desktop enrollment.
8. Press **Desktop Login**.
9. Claude Desktop opens with a clean local login screen. Sign into the same
   Claude account.
10. Leave the Hub running. It detects the completed login, briefly restarts
    Claude Desktop, verifies the session and account identity, and marks the
    card **Ready**.

There is no separate Save Desktop step.

## Add A Second Paid Account

Repeat the complete process with another **Claude Code (paid)** profile:

1. Add the second profile with its own name and profile path.
2. Press **Login** and authenticate its Claude Code CLI profile.
3. Press **Desktop Login** and authenticate the same account in Claude Desktop.
4. Wait for the Hub to capture it and mark the card Ready.

Starting Desktop Login for the second account does not send a provider logout
for the first account. The Hub stops Claude Desktop, preserves the current
profile state, clears only the active local copy, and opens a clean login
screen. After both accounts are enrolled, their CLI homes, account identities,
and Desktop sessions remain separate.

## Switch Accounts

- Select an account in **Accounts** and press **Open CLI** to use its isolated
  Claude Code profile.
- Press **Open Desktop** to restore that account's saved Claude Desktop session.

Switch through the Hub. Do not use Claude's in-app **Log out** command merely
to change accounts. A provider logout can revoke that session and require
Desktop Login again.

## Troubleshooting

### Card remains Login

Desktop capture is not complete. Keep the Hub open and allow Claude Desktop to
restart automatically. If the login was replaced before it could be captured,
run **Desktop Login** again.

### No saved Desktop login

Run **Desktop Login**, finish the official login, and wait for the account card
to become Ready before closing the Hub.

### Account mismatch

Claude Code and Claude Desktop were authenticated with different accounts.
Run **Desktop Login** again and choose the account that belongs to the selected
Claude Code profile.

### Revoked or expired credentials

- Use **Login** again if Claude Code authentication fails.
- Use **Desktop Login** again if Claude Desktop opens on its login screen.

Restoring local files cannot recover a token that the provider has revoked.

## Local Security

Profiles and captured Desktop state are stored under the machine-local AI
Account Hub data directory, outside the repository. They may contain
authentication material. Do not copy them into the repository, commit them,
upload them, or share them.
