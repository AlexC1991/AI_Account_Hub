# Codex Account Launcher Prototype

Run:

```powershell
C:\Users\batty\Documents\Codex\2026-06-30\s\outputs\runcodex.bat
```

What it does:

- Creates three local account profiles by default.
- Stores profile metadata at `C:\Users\batty\.codex-account-launcher\profiles.json`.
- Uses isolated Codex homes under `C:\Users\batty\.codex-accounts\`.
- Switches the Codex desktop app to the selected account by stopping lingering Codex Desktop processes, syncing the selected profile's `auth.json` into the default desktop Codex home, then relaunching.
- Lets you seed a minimal config, log in, check status, run doctor, switch the desktop account, and optionally open CLI.
- Shows a visual account pool with total accounts, ready accounts, not-ready accounts, and countdown timers.
- Lets you manually start or clear a 5-hour cooldown timer for each account.
- Can pull Codex account rate-limit windows through Codex app-server with `Refresh Limits` or `Refresh All`.
- Displays pulled limit data as separate `Left` and `Reset` columns instead of cramming both values into one cell.
- Can use one earned reset credit when `Refresh Limits` reports at least one available credit, with confirmation.

What it deliberately does not do:

- It does not read, parse, or display `auth.json`.
- For Windows desktop switching only, it copies `auth.json` as an opaque file. It saves the active desktop auth back to the previous launcher profile, then copies the selected profile auth into `C:\Users\batty\.codex\auth.json`.
- It makes a one-time backup of the previous default auth under `C:\Users\batty\.codex-account-launcher\desktop-default-backup\`.
- It does not pool accounts into one quota.
- It does not detect rate limits and auto-switch accounts.
- It displays live limit data only when you click `Refresh Limits` or `Refresh All`.
- It will not try to use a reset credit until refreshed limit data reports at least one available reset credit.
- It does not edit packaged Codex app files.

Suggested first use:

1. Open the launcher.
2. Select `Account 1`.
3. Click `Seed config`.
4. Click `Login` and complete the Codex login.
5. Click `Status` to verify.
6. Repeat for `Account 2` and `Account 3`.
7. Click `Refresh Limits` for the selected account or `Refresh All` for all profiles.
8. When an account hits a cooldown manually, select it and click `Set 5h Timer` if the pulled reset window is unavailable.
9. Use the pool table to see local timers plus pulled 5h/weekly usage windows.
   - `5h Left` / `Weekly Left` = percent of that limit window still available.
   - `5h Reset` = `0` while the 5h bucket is still ready; counts down when that bucket is exhausted.
   - `Weekly Reset` = estimated from recent daily usage when available. If a reset credit was used, the launcher uses Codex's API reset window.
   - `Local Timer` = only the manual timer you set in this launcher.
   - `Reset Credits` = account-reported earned reset credits available.
10. Use `Switch Desktop Account` for the selected account.

Notes:

- `Switch Desktop Account` stops Codex Desktop processes that remain in the background, saves the previous active desktop auth back to its profile, syncs the selected profile auth into the default Codex desktop home, then runs `codex app <workspace>`.
- Because it stops all Codex Desktop processes, it can close any currently open Codex Desktop window on this machine.
- The Codex CLI and app-server paths continue to use isolated `CODEX_HOME` directories directly.
- Cooldown timers are local notes saved in the launcher profile file. They are not official account-limit data.
- Pulled limit data comes from Codex app-server methods `account/rateLimits/read` and `account/usage/read`; the profile must be logged in first.
- `Use 1 Reset Credit` uses Codex app-server method `account/rateLimitResetCredit/consume`; it consumes a real account reset credit if Codex accepts the request.
- The weekly reset timestamp is a bucket boundary. The launcher hides that countdown as `0` until it becomes actionable.
- The CLI path is auto-detected from the installed Codex app bundle under `AppData\Local\OpenAI\Codex\bin`.
