# Claude Desktop Login Audit

Date: 2026-07-01

## Scope

This was a local, read-only audit of Claude Desktop and Claude Code login state. Token, cookie, and session values were not printed or copied.

## Installed Surfaces

- Claude Desktop package: `Claude_1.15962.1.0_x64__pzs8sxrjxfjjc`
- Claude Desktop executable: `C:\Program Files\WindowsApps\Claude_1.15962.1.0_x64__pzs8sxrjxfjjc\app\Claude.exe`
- Claude Desktop version: `1.15962.1`
- Claude Code executable: `C:\Users\batty\AppData\Roaming\Claude\claude-code\2.1.187\claude.exe`
- Claude Code version: `2.1.187`

No Claude or Anthropic process was running during the audit.

## Desktop Profile State

Main desktop profile folder:

- `C:\Users\batty\AppData\Roaming\Claude`

Important auth/session storage locations:

- `C:\Users\batty\AppData\Roaming\Claude\config.json`
- `C:\Users\batty\AppData\Roaming\Claude\Network\Cookies`
- `C:\Users\batty\AppData\Roaming\Claude\Local Storage\leveldb`
- `C:\Users\batty\AppData\Roaming\Claude\Session Storage`
- `C:\Users\batty\AppData\Roaming\Claude\IndexedDB\https_claude.ai_0.indexeddb.leveldb`

`config.json` contains OAuth cache keys:

- `oauth:tokenCache`
- `oauth:tokenCacheV2`

The cookie database contains active `claude.ai` cookies. Metadata only:

- `sessionKey` exists and expires `2026-07-27 14:05:34`
- `sessionKeyLC` exists and expires `2026-07-27 14:05:34`
- `cf_clearance` exists and expires `2027-06-27 10:10:42`

This strongly indicates Claude Desktop has a current web/Desktop login profile.

## Claude Code Auth State

Claude Code auth status returned:

```json
{
  "loggedIn": false,
  "authMethod": "none",
  "apiProvider": "firstParty"
}
```

So Claude Desktop login and Claude Code CLI login are not currently the same usable auth state.

Claude Code user state exists at:

- `C:\Users\batty\.claude.json`

It contains account/cache keys such as:

- `oauthAccount`
- `clientDataCache`
- `modelAccessCache`
- `passesEligibilityCache`
- `clientDataCacheSlots`

That file appears to contain cached account metadata, but Claude Code still reports not logged in.

## Integration Notes

Codex is easy to profile because it supports isolated `CODEX_HOME` folders. Claude Desktop does not expose an equivalent simple per-profile environment variable in this audit.

For Claude Desktop account switching, the likely profile boundary is the Electron app data directory:

- `C:\Users\batty\AppData\Roaming\Claude`

A safe switcher would need to:

1. Close Claude Desktop and related helper processes.
2. Snapshot the current `Roaming\Claude` profile to a named account backup.
3. Restore another named `Roaming\Claude` profile snapshot.
4. Relaunch Claude Desktop.
5. Never call a real `claude auth logout` during account switching, because logout may invalidate server-side auth.

This is higher risk than Codex switching because Chromium/Electron profile files can be open, locked, or partially updated. The switcher must use backups and avoid copying while Claude is running.

## GUI Wiring Recommendation

For the account hub, Claude should start with these supported features:

- Detect Claude Desktop installed/not installed.
- Detect Desktop login metadata by checking `config.json` OAuth cache and `Network\Cookies` cookie metadata.
- Show Desktop profile status as `Ready`, `Login`, or `Unknown`.
- Launch Claude Desktop.
- Open Claude Desktop profile folder.
- Add named Claude profile snapshots only after adding a safe backup/restore flow.

Do not mark Claude Code as ready from Desktop login alone. Claude Code should use its own `claude auth status`, which currently reports not logged in.
