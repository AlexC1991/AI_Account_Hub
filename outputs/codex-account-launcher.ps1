param(
    [switch]$SelfTest,
    [switch]$SmokeTestStatus
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$launcherRoot = Join-Path $env:USERPROFILE ".codex-account-launcher"
$profilesFile = Join-Path $launcherRoot "profiles.json"
$desktopDefaultBackupRoot = Join-Path $launcherRoot "desktop-default-backup"
$desktopActiveProfilePath = Join-Path $launcherRoot "desktop-active-profile.json"
$defaultAccountsRoot = Join-Path $env:USERPROFILE ".codex-accounts"
$defaultCodexHome = Join-Path $env:USERPROFILE ".codex"
$defaultWorkspace = "C:\Users\batty\Documents\Codex"
$limitsHelperPath = Join-Path $PSScriptRoot "codex-account-limits-helper.mjs"

function Get-CodexCliPath {
    if ($env:CODEX_CLI_PATH -and (Test-Path -LiteralPath $env:CODEX_CLI_PATH)) {
        return (Resolve-Path -LiteralPath $env:CODEX_CLI_PATH).Path
    }

    $localBin = Join-Path $env:LOCALAPPDATA "OpenAI\Codex\bin"
    if (Test-Path -LiteralPath $localBin) {
        $candidate = Get-ChildItem -LiteralPath $localBin -Filter "codex.exe" -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($candidate) {
            return $candidate.FullName
        }
    }

    $command = Get-Command "codex.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "Could not find codex.exe. Start Codex once, then retry."
}

function Get-NodePath {
    $command = Get-Command "node.exe" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $command = Get-Command "node" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    throw "Could not find node.exe. Install Node.js or start from a shell where node is on PATH."
}

function New-DefaultProfiles {
    return @(
        [pscustomobject]@{
            name = "Account 1"
            codexHome = Join-Path $defaultAccountsRoot "account-1"
            workspace = $defaultWorkspace
            cooldownUntilUtc = ""
            shortLimitUsedPercent = ""
            shortLimitResetUtc = ""
            shortLimitLabel = "5h"
            weeklyLimitUsedPercent = ""
            weeklyLimitResetUtc = ""
            weeklyResetEstimateUtc = ""
            weeklyResetEstimateSource = ""
            weeklyLimitLabel = "Weekly"
            limitReachedType = ""
            resetCreditsAvailable = ""
            lastResetOutcome = ""
            lastResetUtc = ""
            lastLimitsRefreshUtc = ""
            lastLimitsError = ""
        },
        [pscustomobject]@{
            name = "Account 2"
            codexHome = Join-Path $defaultAccountsRoot "account-2"
            workspace = $defaultWorkspace
            cooldownUntilUtc = ""
            shortLimitUsedPercent = ""
            shortLimitResetUtc = ""
            shortLimitLabel = "5h"
            weeklyLimitUsedPercent = ""
            weeklyLimitResetUtc = ""
            weeklyResetEstimateUtc = ""
            weeklyResetEstimateSource = ""
            weeklyLimitLabel = "Weekly"
            limitReachedType = ""
            resetCreditsAvailable = ""
            lastResetOutcome = ""
            lastResetUtc = ""
            lastLimitsRefreshUtc = ""
            lastLimitsError = ""
        },
        [pscustomobject]@{
            name = "Account 3"
            codexHome = Join-Path $defaultAccountsRoot "account-3"
            workspace = $defaultWorkspace
            cooldownUntilUtc = ""
            shortLimitUsedPercent = ""
            shortLimitResetUtc = ""
            shortLimitLabel = "5h"
            weeklyLimitUsedPercent = ""
            weeklyLimitResetUtc = ""
            weeklyResetEstimateUtc = ""
            weeklyResetEstimateSource = ""
            weeklyLimitLabel = "Weekly"
            limitReachedType = ""
            resetCreditsAvailable = ""
            lastResetOutcome = ""
            lastResetUtc = ""
            lastLimitsRefreshUtc = ""
            lastLimitsError = ""
        }
    )
}

function Get-ProfileString($profile, [string]$propertyName, [string]$fallback) {
    if ($null -ne $profile -and $profile.PSObject.Properties.Name -contains $propertyName) {
        $value = $profile.$propertyName
        if ($null -ne $value) {
            if ($value -is [DateTime]) {
                return ([DateTimeOffset]$value).ToUniversalTime().ToString("o")
            }
            if ($value -is [DateTimeOffset]) {
                return $value.ToUniversalTime().ToString("o")
            }
            return [string]$value
        }
    }
    return $fallback
}

function Normalize-Profile($profile) {
    return [pscustomobject]@{
        name = Get-ProfileString $profile "name" "Account"
        codexHome = Get-ProfileString $profile "codexHome" (Join-Path $defaultAccountsRoot "account")
        workspace = Get-ProfileString $profile "workspace" $defaultWorkspace
        cooldownUntilUtc = Get-ProfileString $profile "cooldownUntilUtc" ""
        shortLimitUsedPercent = Get-ProfileString $profile "shortLimitUsedPercent" ""
        shortLimitResetUtc = Get-ProfileString $profile "shortLimitResetUtc" ""
        shortLimitLabel = Get-ProfileString $profile "shortLimitLabel" "5h"
        weeklyLimitUsedPercent = Get-ProfileString $profile "weeklyLimitUsedPercent" ""
        weeklyLimitResetUtc = Get-ProfileString $profile "weeklyLimitResetUtc" ""
        weeklyResetEstimateUtc = Get-ProfileString $profile "weeklyResetEstimateUtc" ""
        weeklyResetEstimateSource = Get-ProfileString $profile "weeklyResetEstimateSource" ""
        weeklyLimitLabel = Get-ProfileString $profile "weeklyLimitLabel" "Weekly"
        limitReachedType = Get-ProfileString $profile "limitReachedType" ""
        resetCreditsAvailable = Get-ProfileString $profile "resetCreditsAvailable" ""
        lastResetOutcome = Get-ProfileString $profile "lastResetOutcome" ""
        lastResetUtc = Get-ProfileString $profile "lastResetUtc" ""
        lastLimitsRefreshUtc = Get-ProfileString $profile "lastLimitsRefreshUtc" ""
        lastLimitsError = Get-ProfileString $profile "lastLimitsError" ""
    }
}

function Save-Profiles($profiles) {
    New-Item -ItemType Directory -Force -Path $launcherRoot | Out-Null
    $cleanProfiles = @()
    foreach ($profile in $profiles) {
        $normal = Normalize-Profile $profile
        $cleanProfiles += [pscustomobject]@{
            name = [string]$normal.name
            codexHome = [string]$normal.codexHome
            workspace = [string]$normal.workspace
            cooldownUntilUtc = [string]$normal.cooldownUntilUtc
            shortLimitUsedPercent = [string]$normal.shortLimitUsedPercent
            shortLimitResetUtc = [string]$normal.shortLimitResetUtc
            shortLimitLabel = [string]$normal.shortLimitLabel
            weeklyLimitUsedPercent = [string]$normal.weeklyLimitUsedPercent
            weeklyLimitResetUtc = [string]$normal.weeklyLimitResetUtc
            weeklyResetEstimateUtc = [string]$normal.weeklyResetEstimateUtc
            weeklyResetEstimateSource = [string]$normal.weeklyResetEstimateSource
            weeklyLimitLabel = [string]$normal.weeklyLimitLabel
            limitReachedType = [string]$normal.limitReachedType
            resetCreditsAvailable = [string]$normal.resetCreditsAvailable
            lastResetOutcome = [string]$normal.lastResetOutcome
            lastResetUtc = [string]$normal.lastResetUtc
            lastLimitsRefreshUtc = [string]$normal.lastLimitsRefreshUtc
            lastLimitsError = [string]$normal.lastLimitsError
        }
    }
    $cleanProfiles | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $profilesFile -Encoding UTF8
}

function Load-Profiles {
    if (-not (Test-Path -LiteralPath $profilesFile)) {
        $profiles = New-DefaultProfiles
        Save-Profiles $profiles
        return $profiles
    }

    $raw = Get-Content -LiteralPath $profilesFile -Raw
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $profiles = New-DefaultProfiles
        Save-Profiles $profiles
        return $profiles
    }

    $loaded = $raw | ConvertFrom-Json
    if ($null -eq $loaded) {
        $profiles = New-DefaultProfiles
        Save-Profiles $profiles
        return $profiles
    }

    $items = @()
    if ($loaded -is [array]) {
        $items = $loaded
    } else {
        $items = @($loaded)
    }

    return @($items | ForEach-Object { Normalize-Profile $_ })
}

function Ensure-ProfileHome($profile) {
    New-Item -ItemType Directory -Force -Path $profile.codexHome | Out-Null
    if ($profile.workspace -and -not (Test-Path -LiteralPath $profile.workspace)) {
        New-Item -ItemType Directory -Force -Path $profile.workspace | Out-Null
    }
}

function Get-AuthJsonPath([string]$codexHome) {
    return (Join-Path $codexHome "auth.json")
}

function Get-CooldownUntilLocal($profile) {
    $raw = Get-ProfileString $profile "cooldownUntilUtc" ""
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }

    try {
        return ([DateTimeOffset]::Parse($raw)).ToLocalTime()
    } catch {
        return $null
    }
}

function Get-CooldownRemaining($profile) {
    $until = Get-CooldownUntilLocal $profile
    if ($null -eq $until) {
        return [TimeSpan]::Zero
    }

    $remaining = $until - [DateTimeOffset]::Now
    if ($remaining.TotalSeconds -le 0) {
        return [TimeSpan]::Zero
    }

    return $remaining
}

function Format-Remaining([TimeSpan]$remaining) {
    if ($remaining.TotalSeconds -le 0) {
        return "-"
    }

    return "{0:00}:{1:00}:{2:00}" -f [int][Math]::Floor($remaining.TotalHours), $remaining.Minutes, $remaining.Seconds
}

function Get-PoolState($profile) {
    $remaining = Get-CooldownRemaining $profile
    if ($remaining.TotalSeconds -gt 0) {
        return "Not Ready"
    }

    return "Ready"
}

function Set-Cooldown($profile, [TimeSpan]$duration) {
    $profile.cooldownUntilUtc = ([DateTimeOffset]::UtcNow.Add($duration)).ToString("o")
}

function Clear-Cooldown($profile) {
    $profile.cooldownUntilUtc = ""
}

function Confirm-OpenIfNotReady($profile) {
    $remaining = Get-CooldownRemaining $profile
    if ($remaining.TotalSeconds -le 0) {
        return $true
    }

    $message = "$($profile.name) is Not Ready because the local 5h timer has $(Format-Remaining $remaining) remaining. Open it anyway?"
    $result = [System.Windows.Forms.MessageBox]::Show(
        $message,
        "Not Ready timer active",
        [System.Windows.Forms.MessageBoxButtons]::OKCancel,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
    return ($result -eq [System.Windows.Forms.DialogResult]::OK)
}

function Quote-ForSingleQuotedPowerShell([string]$value) {
    return "'" + ($value -replace "'", "''") + "'"
}

function Quote-ForProcessArgument([string]$value) {
    if ($null -eq $value -or $value.Length -eq 0) {
        return '""'
    }

    if ($value -notmatch '[\s"]') {
        return $value
    }

    return '"' + ($value -replace '\\(?=\\*")', '$0$0' -replace '"', '\"') + '"'
}

function Invoke-CodexCapture([string]$codexPath, [string]$codexHome, [string[]]$arguments, [string]$workingDirectory) {
    Ensure-ProfileHome ([pscustomobject]@{ codexHome = $codexHome; workspace = $workingDirectory })

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $codexPath
    $psi.Arguments = (($arguments | ForEach-Object { Quote-ForProcessArgument $_ }) -join " ")
    $psi.WorkingDirectory = $workingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.Environment["CODEX_HOME"] = $codexHome

    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    $text = @()
    $text += "Exit code: $($process.ExitCode)"
    if (-not [string]::IsNullOrWhiteSpace($stdout)) {
        $text += ""
        $text += $stdout.TrimEnd()
    }
    if (-not [string]::IsNullOrWhiteSpace($stderr)) {
        $text += ""
        $text += $stderr.TrimEnd()
    }
    return ($text -join [Environment]::NewLine)
}

function Invoke-ProcessCapture([string]$fileName, [string[]]$arguments, [string]$workingDirectory) {
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $fileName
    $psi.Arguments = (($arguments | ForEach-Object { Quote-ForProcessArgument $_ }) -join " ")
    $psi.WorkingDirectory = $workingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $process = [System.Diagnostics.Process]::Start($psi)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()

    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Stdout = $stdout
        Stderr = $stderr
    }
}

function Format-PercentValue([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) {
        return "-"
    }

    $number = 0.0
    if ([double]::TryParse($value, [ref]$number)) {
        return "{0:0.#}%" -f $number
    }

    return $value
}

function Get-PercentNumber([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $null
    }

    $number = 0.0
    if ([double]::TryParse($value, [ref]$number)) {
        return $number
    }

    return $null
}

function Format-LeftPercent([string]$usedValue) {
    $used = Get-PercentNumber $usedValue
    if ($null -eq $used) {
        return "-"
    }

    $left = 100.0 - [double]$used
    if ($left -lt 0) { $left = 0 }
    if ($left -gt 100) { $left = 100 }
    return "{0:0.#}%" -f $left
}

function Test-LimitExhausted([string]$usedValue) {
    $used = Get-PercentNumber $usedValue
    return ($null -ne $used -and [double]$used -ge 100.0)
}

function Format-ResetCountdown([string]$resetRaw) {
    if ([string]::IsNullOrWhiteSpace($resetRaw)) {
        return "-"
    }

    try {
        $reset = ([DateTimeOffset]::Parse($resetRaw)).ToLocalTime()
        $remaining = $reset - [DateTimeOffset]::Now
        if ($remaining.TotalDays -ge 1) {
            return "{0}d {1:00}h" -f [int][Math]::Floor($remaining.TotalDays), $remaining.Hours
        }
        if ($remaining.TotalSeconds -le 0) {
            return "now"
        }
        if ($remaining.TotalHours -ge 1) {
            return "{0}h {1:00}m" -f [int][Math]::Floor($remaining.TotalHours), $remaining.Minutes
        }
        if ($remaining.TotalMinutes -ge 1) {
            return "{0}m" -f [int][Math]::Ceiling($remaining.TotalMinutes)
        }
        return "<1m"
    } catch {
        return "-"
    }
}

function Format-ActionableReset([string]$usedValue, [string]$resetRaw) {
    if (-not (Test-LimitExhausted $usedValue)) {
        return "0"
    }

    return Format-ResetCountdown $resetRaw
}

function Get-ParsedDateTimeOffset([string]$raw) {
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }

    try {
        return [DateTimeOffset]::Parse($raw)
    } catch {
        return $null
    }
}

function Convert-ToIsoDateString($value) {
    if ($null -eq $value) {
        return ""
    }

    try {
        if ($value -is [DateTime]) {
            return ([DateTimeOffset]$value).ToUniversalTime().ToString("o")
        }

        return ([DateTimeOffset]::Parse([string]$value)).ToUniversalTime().ToString("o")
    } catch {
        return [string]$value
    }
}

function Get-WeeklyResetEstimate($profile, $result) {
    $apiReset = ""
    if ($null -ne $result.rateLimits.weeklyWindow) {
        $apiReset = Convert-ToIsoDateString $result.rateLimits.weeklyWindow.resetsAtIso
    }

    $lastReset = Get-ParsedDateTimeOffset (Get-ProfileString $profile "lastResetUtc" "")
    if ($null -ne $lastReset -and $lastReset.UtcDateTime -ge [DateTime]::UtcNow.AddDays(-7)) {
        return [pscustomobject]@{ Iso = $apiReset; Source = "api" }
    }

    $buckets = @()
    if ($null -ne $result.usage -and $null -ne $result.usage.dailyUsageBuckets) {
        $buckets = @($result.usage.dailyUsageBuckets)
    }

    $todayUtc = [DateTime]::UtcNow.Date
    $cutoffUtc = $todayUtc.AddDays(-6)
    $usedDates = @()
    foreach ($bucket in $buckets) {
        $tokens = 0.0
        if (-not [double]::TryParse([string]$bucket.tokens, [ref]$tokens) -or $tokens -le 0) {
            continue
        }

        try {
            $bucketDate = [DateTime]::ParseExact([string]$bucket.startDate, "yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture)
        } catch {
            continue
        }

        if ($bucketDate -ge $cutoffUtc -and $bucketDate -le $todayUtc) {
            $usedDates += $bucketDate
        }
    }

    if ($usedDates.Count -gt 0) {
        $earliest = @($usedDates | Sort-Object | Select-Object -First 1)[0]
        $estimate = [DateTimeOffset]::new($earliest.Year, $earliest.Month, $earliest.Day, 0, 0, 0, [TimeSpan]::Zero).AddDays(7)
        return [pscustomobject]@{ Iso = $estimate.ToString("o"); Source = "usage" }
    }

    return [pscustomobject]@{ Iso = $apiReset; Source = "api" }
}

function Format-WeeklyReset($profile) {
    $estimateRaw = Get-ProfileString $profile "weeklyResetEstimateUtc" ""
    if (-not [string]::IsNullOrWhiteSpace($estimateRaw)) {
        return Format-ResetCountdown $estimateRaw
    }

    return Format-ResetCountdown (Get-ProfileString $profile "weeklyLimitResetUtc" "")
}

function Set-ProfileLimitsFromResult($profile, $result) {
    $profile.lastLimitsRefreshUtc = [DateTimeOffset]::UtcNow.ToString("o")
    if (-not $result.ok) {
        $profile.lastLimitsError = [string]$result.error
        return
    }

    $profile.lastLimitsError = ""
    $profile.limitReachedType = [string]$result.rateLimits.rateLimitReachedType
    if ($null -ne $result.resetOutcome) {
        $profile.lastResetOutcome = [string]$result.resetOutcome
        $profile.lastResetUtc = [DateTimeOffset]::UtcNow.ToString("o")
        if ([string]$result.resetOutcome -eq "reset") {
            Clear-Cooldown $profile
        }
    }

    if ($null -ne $result.rateLimits.rateLimitResetCredits) {
        $profile.resetCreditsAvailable = [string]$result.rateLimits.rateLimitResetCredits.availableCount
    }

    if ($null -ne $result.rateLimits.shortWindow) {
        $profile.shortLimitLabel = [string]$result.rateLimits.shortWindow.label
        $profile.shortLimitUsedPercent = [string]$result.rateLimits.shortWindow.usedPercent
        $profile.shortLimitResetUtc = Convert-ToIsoDateString $result.rateLimits.shortWindow.resetsAtIso
    }

    if ($null -ne $result.rateLimits.weeklyWindow) {
        $profile.weeklyLimitLabel = [string]$result.rateLimits.weeklyWindow.label
        $profile.weeklyLimitUsedPercent = [string]$result.rateLimits.weeklyWindow.usedPercent
        $profile.weeklyLimitResetUtc = Convert-ToIsoDateString $result.rateLimits.weeklyWindow.resetsAtIso
    }

    $weeklyEstimate = Get-WeeklyResetEstimate $profile $result
    $profile.weeklyResetEstimateUtc = [string]$weeklyEstimate.Iso
    $profile.weeklyResetEstimateSource = [string]$weeklyEstimate.Source
}

function Invoke-LimitsRefresh($profile, [string]$nodePath, [string]$codexPath, [string]$action = "read") {
    if ([string]::IsNullOrWhiteSpace($nodePath)) {
        throw "Node.js was not found; cannot call the limits helper."
    }
    if (-not (Test-Path -LiteralPath $limitsHelperPath)) {
        throw "Missing limits helper: $limitsHelperPath"
    }

    Ensure-ProfileHome $profile
    $capture = Invoke-ProcessCapture $nodePath @($limitsHelperPath, $codexPath, $profile.codexHome, $profile.workspace, $action) $profile.workspace
    $json = $capture.Stdout.Trim()
    if ([string]::IsNullOrWhiteSpace($json)) {
        $err = $capture.Stderr.Trim()
        if ([string]::IsNullOrWhiteSpace($err)) {
            $err = "No output from limits helper."
        }
        throw $err
    }

    return ($json | ConvertFrom-Json)
}

function Invoke-ResetCredit($profile, [string]$nodePath, [string]$codexPath) {
    return Invoke-LimitsRefresh $profile $nodePath $codexPath "consume-reset"
}

function Get-SeedConfigText {
    $sourceConfig = Join-Path $env:USERPROFILE ".codex\config.toml"
    $lines = @(
        "# Minimal Codex config created by codex-account-launcher.ps1",
        "# Account credentials are stored separately in this CODEX_HOME.",
        'cli_auth_credentials_store = "file"'
    )

    if (Test-Path -LiteralPath $sourceConfig) {
        $safeKeys = @("model", "model_reasoning_effort", "service_tier", "approval_policy", "sandbox_mode")
        foreach ($line in Get-Content -LiteralPath $sourceConfig) {
            if ($line -match "^\s*\[") {
                break
            }
            if ($line -match "^\s*([A-Za-z0-9_]+)\s*=") {
                $key = $Matches[1]
                if ($safeKeys -contains $key -and $line -notmatch "(?i)(token|secret|password|credential|authorization|api_key|bearer)") {
                    $lines += $line
                }
            }
        }
    }

    return (($lines | Select-Object -Unique) -join [Environment]::NewLine) + [Environment]::NewLine
}

function Ensure-ProfileFileCredentialStore($profile) {
    Ensure-ProfileHome $profile
    $target = Join-Path $profile.codexHome "config.toml"

    if (-not (Test-Path -LiteralPath $target)) {
        Get-SeedConfigText | Set-Content -LiteralPath $target -Encoding UTF8
        return "Created config with file-backed credentials: $target"
    }

    $lines = @(Get-Content -LiteralPath $target)
    $firstSection = $lines.Count
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match '^\s*\[') {
            $firstSection = $index
            break
        }
    }

    for ($index = 0; $index -lt $firstSection; $index++) {
        if ($lines[$index] -match '^\s*cli_auth_credentials_store\s*=') {
            if ($lines[$index] -match '"file"') {
                return $null
            }

            $lines[$index] = 'cli_auth_credentials_store = "file"'
            $lines | Set-Content -LiteralPath $target -Encoding UTF8
            return "Updated config to use file-backed credentials: $target"
        }
    }

    if ($firstSection -eq 0) {
        $lines = @('cli_auth_credentials_store = "file"', "") + $lines
    } elseif ($firstSection -lt $lines.Count) {
        $before = @($lines[0..($firstSection - 1)])
        $after = @($lines[$firstSection..($lines.Count - 1)])
        $lines = $before + @('cli_auth_credentials_store = "file"', "") + $after
    } else {
        $lines += 'cli_auth_credentials_store = "file"'
    }

    $lines | Set-Content -LiteralPath $target -Encoding UTF8
    return "Added file-backed credentials to config: $target"
}

function Seed-ProfileConfig($profile) {
    $message = Ensure-ProfileFileCredentialStore $profile
    if ([string]::IsNullOrWhiteSpace($message)) {
        $target = Join-Path $profile.codexHome "config.toml"
        return "Config already has file-backed credentials: $target"
    }

    return $message
}

function Start-VisiblePowerShell([string]$title, [string]$scriptText, [string]$workingDirectory) {
    $fullScript = @"
`$Host.UI.RawUI.WindowTitle = $(Quote-ForSingleQuotedPowerShell $title)
Set-Location -LiteralPath $(Quote-ForSingleQuotedPowerShell $workingDirectory)
$scriptText
"@

    Start-Process -FilePath "powershell.exe" -WorkingDirectory $workingDirectory -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-NoExit",
        "-Command",
        $fullScript
    ) | Out-Null
}

function Start-HiddenPowerShell([string]$scriptText, [string]$workingDirectory) {
    Start-Process -WindowStyle Hidden -FilePath "powershell.exe" -WorkingDirectory $workingDirectory -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        $scriptText
    ) | Out-Null
}

function Start-Login($profile, [string]$codexPath, [switch]$DeviceCode) {
    Ensure-ProfileFileCredentialStore $profile | Out-Null
    $deviceArg = ""
    if ($DeviceCode) {
        $deviceArg = " --device-auth"
    }

    $script = @"
`$env:CODEX_HOME = $(Quote-ForSingleQuotedPowerShell $profile.codexHome)
Write-Host "CODEX_HOME=`$env:CODEX_HOME"
& $(Quote-ForSingleQuotedPowerShell $codexPath) login$deviceArg
Write-Host ""
Write-Host "Login command finished. Run Status in the launcher to verify."
"@

    Start-VisiblePowerShell "Codex login - $($profile.name)" $script $profile.workspace
}

function Start-CodexCli($profile, [string]$codexPath) {
    Ensure-ProfileFileCredentialStore $profile | Out-Null
    $script = @"
`$env:CODEX_HOME = $(Quote-ForSingleQuotedPowerShell $profile.codexHome)
Write-Host "CODEX_HOME=`$env:CODEX_HOME"
& $(Quote-ForSingleQuotedPowerShell $codexPath)
"@

    Start-VisiblePowerShell "Codex CLI - $($profile.name)" $script $profile.workspace
}

function Start-CodexDesktop($profile, [string]$codexPath) {
    Ensure-ProfileFileCredentialStore $profile | Out-Null
    $script = @"
`$env:CODEX_HOME = $(Quote-ForSingleQuotedPowerShell $profile.codexHome)
& $(Quote-ForSingleQuotedPowerShell $codexPath) app $(Quote-ForSingleQuotedPowerShell $profile.workspace)
"@

    Start-HiddenPowerShell $script $profile.workspace
}

function Get-CodexDesktopProcesses {
    $matches = @()
    foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
        $path = ""
        try {
            $path = [string]$process.Path
        } catch {
            $path = ""
        }

        if ($path -match '\\WindowsApps\\OpenAI\.Codex_' -and $path -match '\\app\\(Codex|resources\\codex)\.exe$') {
            $matches += $process
        }
    }

    return @($matches)
}

function Stop-CodexDesktopForAccountSwitch {
    $processes = @(Get-CodexDesktopProcesses)
    if ($processes.Count -eq 0) {
        return "No Codex Desktop background processes were running."
    }

    $initialIds = @($processes | ForEach-Object { $_.Id })
    $closed = 0
    foreach ($process in $processes) {
        try {
            if ($process.ProcessName -eq "Codex" -and $process.MainWindowHandle -ne [IntPtr]::Zero) {
                if ($process.CloseMainWindow()) {
                    $closed++
                }
            }
        } catch {
        }
    }

    $deadline = [DateTime]::Now.AddSeconds(8)
    do {
        Start-Sleep -Milliseconds 250
        $remaining = @(Get-CodexDesktopProcesses | Where-Object { $initialIds -contains $_.Id })
    } while ($remaining.Count -gt 0 -and [DateTime]::Now -lt $deadline)

    $killed = 0
    foreach ($process in $remaining) {
        try {
            $process.Kill()
            $killed++
        } catch {
        }
    }

    if ($killed -gt 0) {
        Start-Sleep -Milliseconds 800
    }

    return "Stopped $($processes.Count) Codex Desktop process(es). Graceful close requested: $closed. Force-stopped: $killed."
}

function Sync-ActiveDesktopAuthBackToProfile {
    if (-not (Test-Path -LiteralPath $desktopActiveProfilePath)) {
        return $null
    }

    $defaultAuth = Get-AuthJsonPath $defaultCodexHome
    if (-not (Test-Path -LiteralPath $defaultAuth)) {
        return "No default desktop auth was available to save back to the active profile."
    }

    try {
        $marker = Get-Content -LiteralPath $desktopActiveProfilePath -Raw | ConvertFrom-Json
    } catch {
        return "Could not read the previous desktop active-profile marker; skipping auth save-back."
    }

    $activeHome = Get-ProfileString $marker "codexHome" ""
    $activeName = Get-ProfileString $marker "name" "previous profile"
    if ([string]::IsNullOrWhiteSpace($activeHome)) {
        return "Previous desktop active-profile marker did not include a CODEX_HOME; skipping auth save-back."
    }

    New-Item -ItemType Directory -Force -Path $activeHome | Out-Null
    Copy-Item -LiteralPath $defaultAuth -Destination (Get-AuthJsonPath $activeHome) -Force
    return "Saved current desktop auth back to $activeName."
}

function Sync-ProfileAuthToDesktopDefault($profile) {
    Ensure-ProfileFileCredentialStore $profile | Out-Null
    Ensure-ProfileFileCredentialStore ([pscustomobject]@{ codexHome = $defaultCodexHome; workspace = $profile.workspace }) | Out-Null
    $profileAuth = Get-AuthJsonPath $profile.codexHome
    if (-not (Test-Path -LiteralPath $profileAuth)) {
        throw "No auth.json found for $($profile.name). Run Login for this profile first, then retry Switch Desktop Account."
    }

    New-Item -ItemType Directory -Force -Path $defaultCodexHome | Out-Null
    New-Item -ItemType Directory -Force -Path $desktopDefaultBackupRoot | Out-Null

    $defaultAuth = Get-AuthJsonPath $defaultCodexHome
    $backupAuth = Join-Path $desktopDefaultBackupRoot "auth.json"
    if ((Test-Path -LiteralPath $defaultAuth) -and -not (Test-Path -LiteralPath $backupAuth)) {
        Copy-Item -LiteralPath $defaultAuth -Destination $backupAuth -Force
    }

    Copy-Item -LiteralPath $profileAuth -Destination $defaultAuth -Force
    $marker = [pscustomobject]@{
        name = [string]$profile.name
        codexHome = [string]$profile.codexHome
        syncedAtUtc = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $marker | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $desktopActiveProfilePath -Encoding UTF8

    return "Synced $($profile.name) auth into default Codex desktop home. Original default auth backup: $backupAuth"
}

if ($SelfTest) {
    $path = Get-CodexCliPath
    Write-Output "Codex CLI: $path"
    try {
        Write-Output "Node: $(Get-NodePath)"
    } catch {
        Write-Output "Node: not found"
    }
    Write-Output "Profiles file: $profilesFile"
    Write-Output "Default accounts root: $defaultAccountsRoot"
    exit 0
}

if ($SmokeTestStatus) {
    $path = Get-CodexCliPath
    $testHome = Join-Path $launcherRoot "smoke-test-home"
    New-Item -ItemType Directory -Force -Path $testHome | Out-Null
    Write-Output "Codex CLI: $path"
    Write-Output "Smoke CODEX_HOME: $testHome"
    Invoke-CodexCapture $path $testHome @("login", "status") $defaultWorkspace
    exit 0
}

$codexCliPath = Get-CodexCliPath
$nodePath = ""
try {
    $nodePath = Get-NodePath
} catch {
    $nodePath = ""
}
$profiles = @(Load-Profiles)

[System.Windows.Forms.Application]::EnableVisualStyles()

$form = [System.Windows.Forms.Form]::new()
$form.Text = "Codex Account Pool"
$form.StartPosition = "CenterScreen"
$form.Size = [System.Drawing.Size]::new(1260, 780)
$form.MinimumSize = [System.Drawing.Size]::new(1180, 700)

$main = [System.Windows.Forms.TableLayoutPanel]::new()
$main.Dock = "Fill"
$main.ColumnCount = 1
$main.RowCount = 5
$main.Padding = [System.Windows.Forms.Padding]::new(14)
$main.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Absolute, 42)) | Out-Null
$main.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Absolute, 245)) | Out-Null
$main.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Absolute, 230)) | Out-Null
$main.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Percent, 100)) | Out-Null
$main.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Absolute, 28)) | Out-Null
$form.Controls.Add($main)

$header = [System.Windows.Forms.TableLayoutPanel]::new()
$header.Dock = "Fill"
$header.ColumnCount = 2
$header.ColumnStyles.Add([System.Windows.Forms.ColumnStyle]::new([System.Windows.Forms.SizeType]::Absolute, 260)) | Out-Null
$header.ColumnStyles.Add([System.Windows.Forms.ColumnStyle]::new([System.Windows.Forms.SizeType]::Percent, 100)) | Out-Null
$main.Controls.Add($header, 0, 0)

$title = [System.Windows.Forms.Label]::new()
$title.Text = "Codex Account Pool"
$title.Dock = "Fill"
$title.TextAlign = "MiddleLeft"
$title.Font = [System.Drawing.Font]::new("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
$header.Controls.Add($title, 0, 0)

$cliLabel = [System.Windows.Forms.Label]::new()
$cliLabel.Text = "Desktop launch: $codexCliPath app <workspace>"
$cliLabel.Dock = "Fill"
$cliLabel.AutoEllipsis = $true
$cliLabel.TextAlign = "MiddleLeft"
$header.Controls.Add($cliLabel, 1, 0)

$profileList = [System.Windows.Forms.ListBox]::new()
$profileList.DisplayMember = "name"
foreach ($profile in $profiles) {
    [void]$profileList.Items.Add($profile)
}
if ($profileList.Items.Count -gt 0) {
    $profileList.SelectedIndex = 0
}

$poolGroup = [System.Windows.Forms.GroupBox]::new()
$poolGroup.Text = "Account pool"
$poolGroup.Dock = "Fill"
$main.Controls.Add($poolGroup, 0, 1)

$poolLayout = [System.Windows.Forms.TableLayoutPanel]::new()
$poolLayout.Dock = "Fill"
$poolLayout.ColumnCount = 1
$poolLayout.RowCount = 2
$poolLayout.Padding = [System.Windows.Forms.Padding]::new(10, 6, 10, 10)
$poolLayout.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Absolute, 28)) | Out-Null
$poolLayout.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Percent, 100)) | Out-Null
$poolGroup.Controls.Add($poolLayout)

$poolSummaryLabel = [System.Windows.Forms.Label]::new()
$poolSummaryLabel.Dock = "Fill"
$poolSummaryLabel.TextAlign = "MiddleLeft"
$poolSummaryLabel.Font = [System.Drawing.Font]::new("Segoe UI", 9, [System.Drawing.FontStyle]::Bold)
$poolLayout.Controls.Add($poolSummaryLabel, 0, 0)

$poolGrid = [System.Windows.Forms.DataGridView]::new()
$poolGrid.Dock = "Fill"
$poolGrid.ReadOnly = $true
$poolGrid.AllowUserToAddRows = $false
$poolGrid.AllowUserToDeleteRows = $false
$poolGrid.AllowUserToResizeRows = $false
$poolGrid.RowHeadersVisible = $false
$poolGrid.MultiSelect = $false
$poolGrid.SelectionMode = "FullRowSelect"
$poolGrid.AutoSizeColumnsMode = "Fill"
$poolGrid.BackgroundColor = [System.Drawing.SystemColors]::Window
$poolGrid.BorderStyle = "FixedSingle"
$poolGrid.ColumnHeadersHeightSizeMode = "AutoSize"
$poolGrid.AutoSizeRowsMode = "None"
$poolGrid.RowTemplate.Height = 26
[void]$poolGrid.Columns.Add("Account", "Account")
[void]$poolGrid.Columns.Add("State", "State")
[void]$poolGrid.Columns.Add("Local", "Local Timer")
[void]$poolGrid.Columns.Add("ShortLeft", "5h Left")
[void]$poolGrid.Columns.Add("ShortReset", "5h Reset")
[void]$poolGrid.Columns.Add("WeeklyLeft", "Weekly Left")
[void]$poolGrid.Columns.Add("WeeklyReset", "Weekly Reset")
[void]$poolGrid.Columns.Add("ResetCredits", "Reset Credits")
$poolGrid.Columns["Account"].FillWeight = 26
$poolGrid.Columns["State"].FillWeight = 12
$poolGrid.Columns["Local"].FillWeight = 12
$poolGrid.Columns["ShortLeft"].FillWeight = 10
$poolGrid.Columns["ShortReset"].FillWeight = 14
$poolGrid.Columns["WeeklyLeft"].FillWeight = 12
$poolGrid.Columns["WeeklyReset"].FillWeight = 14
$poolGrid.Columns["ResetCredits"].FillWeight = 10
$poolLayout.Controls.Add($poolGrid, 0, 1)

$detailsGroup = [System.Windows.Forms.GroupBox]::new()
$detailsGroup.Text = "Selected account"
$detailsGroup.Dock = "Fill"
$main.Controls.Add($detailsGroup, 0, 2)

$detailsRoot = [System.Windows.Forms.TableLayoutPanel]::new()
$detailsRoot.Dock = "Fill"
$detailsRoot.ColumnCount = 2
$detailsRoot.RowCount = 1
$detailsRoot.Padding = [System.Windows.Forms.Padding]::new(10, 8, 10, 10)
$detailsRoot.ColumnStyles.Add([System.Windows.Forms.ColumnStyle]::new([System.Windows.Forms.SizeType]::Percent, 48)) | Out-Null
$detailsRoot.ColumnStyles.Add([System.Windows.Forms.ColumnStyle]::new([System.Windows.Forms.SizeType]::Percent, 52)) | Out-Null
$detailsGroup.Controls.Add($detailsRoot)

$fields = [System.Windows.Forms.TableLayoutPanel]::new()
$fields.Dock = "Fill"
$fields.ColumnCount = 2
$fields.RowCount = 4
$fields.ColumnStyles.Add([System.Windows.Forms.ColumnStyle]::new([System.Windows.Forms.SizeType]::Absolute, 110)) | Out-Null
$fields.ColumnStyles.Add([System.Windows.Forms.ColumnStyle]::new([System.Windows.Forms.SizeType]::Percent, 100)) | Out-Null
$fields.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Absolute, 34)) | Out-Null
$fields.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Absolute, 34)) | Out-Null
$fields.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Absolute, 34)) | Out-Null
$fields.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Percent, 100)) | Out-Null
$detailsRoot.Controls.Add($fields, 0, 0)

function New-FieldLabel([string]$text) {
    $label = [System.Windows.Forms.Label]::new()
    $label.Text = $text
    $label.Dock = "Fill"
    $label.TextAlign = "MiddleLeft"
    return $label
}

$nameBox = [System.Windows.Forms.TextBox]::new()
$nameBox.Dock = "Fill"
$homeBox = [System.Windows.Forms.TextBox]::new()
$homeBox.Dock = "Fill"
$workspaceBox = [System.Windows.Forms.TextBox]::new()
$workspaceBox.Dock = "Fill"

$fields.Controls.Add((New-FieldLabel "Name"), 0, 0)
$fields.Controls.Add($nameBox, 1, 0)
$fields.Controls.Add((New-FieldLabel "CODEX_HOME"), 0, 1)
$fields.Controls.Add($homeBox, 1, 1)
$fields.Controls.Add((New-FieldLabel "Workspace"), 0, 2)
$fields.Controls.Add($workspaceBox, 1, 2)

$note = [System.Windows.Forms.TextBox]::new()
$note.Dock = "Fill"
$note.Multiline = $true
$note.ReadOnly = $true
$note.BorderStyle = "FixedSingle"
$note.Text = "Format: Left columns show remaining capacity. Resets use one style: days/hours or hours/minutes. Weekly Reset uses usage history when available. Switch Desktop Account stops lingering Codex Desktop processes, syncs the selected account, then relaunches."
$fields.Controls.Add($note, 0, 3)
$fields.SetColumnSpan($note, 2)

$actions = [System.Windows.Forms.TableLayoutPanel]::new()
$actions.Dock = "Fill"
$actions.ColumnCount = 4
$actions.RowCount = 4
$actions.ColumnStyles.Add([System.Windows.Forms.ColumnStyle]::new([System.Windows.Forms.SizeType]::Percent, 25)) | Out-Null
$actions.ColumnStyles.Add([System.Windows.Forms.ColumnStyle]::new([System.Windows.Forms.SizeType]::Percent, 25)) | Out-Null
$actions.ColumnStyles.Add([System.Windows.Forms.ColumnStyle]::new([System.Windows.Forms.SizeType]::Percent, 25)) | Out-Null
$actions.ColumnStyles.Add([System.Windows.Forms.ColumnStyle]::new([System.Windows.Forms.SizeType]::Percent, 25)) | Out-Null
for ($row = 0; $row -lt 4; $row++) {
    $actions.RowStyles.Add([System.Windows.Forms.RowStyle]::new([System.Windows.Forms.SizeType]::Percent, 25)) | Out-Null
}
$detailsRoot.Controls.Add($actions, 1, 0)

function New-ActionButton([string]$text) {
    $button = [System.Windows.Forms.Button]::new()
    $button.Text = $text
    $button.Dock = "Fill"
    $button.Margin = [System.Windows.Forms.Padding]::new(5)
    return $button
}

$appButton = New-ActionButton "Switch Desktop Account"
$statusButton = New-ActionButton "Status"
$doctorButton = New-ActionButton "Doctor"
$seedButton = New-ActionButton "Seed config"
$loginButton = New-ActionButton "Login"
$deviceLoginButton = New-ActionButton "Device login"
$setCooldownButton = New-ActionButton "Set 5h Timer"
$clearCooldownButton = New-ActionButton "Clear Timer"
$refreshLimitsButton = New-ActionButton "Refresh Limits"
$refreshLimitsButton.Enabled = -not [string]::IsNullOrWhiteSpace($nodePath)
$refreshAllLimitsButton = New-ActionButton "Refresh All"
$refreshAllLimitsButton.Enabled = -not [string]::IsNullOrWhiteSpace($nodePath)
$useResetCreditButton = New-ActionButton "Use 1 Reset Credit"
$useResetCreditButton.Enabled = -not [string]::IsNullOrWhiteSpace($nodePath)
$cliButton = New-ActionButton "Open CLI"
$saveButton = New-ActionButton "Save profile"
$openFolderButton = New-ActionButton "Open home"

$actions.Controls.Add($appButton, 0, 0)
$actions.Controls.Add($refreshLimitsButton, 1, 0)
$actions.Controls.Add($refreshAllLimitsButton, 2, 0)
$actions.Controls.Add($statusButton, 3, 0)
$actions.Controls.Add($loginButton, 0, 1)
$actions.Controls.Add($deviceLoginButton, 1, 1)
$actions.Controls.Add($useResetCreditButton, 2, 1)
$actions.Controls.Add($setCooldownButton, 3, 1)
$actions.Controls.Add($doctorButton, 0, 2)
$actions.Controls.Add($seedButton, 1, 2)
$actions.Controls.Add($saveButton, 2, 2)
$actions.Controls.Add($clearCooldownButton, 3, 2)
$actions.Controls.Add($cliButton, 0, 3)
$actions.Controls.Add($openFolderButton, 1, 3)

$logGroup = [System.Windows.Forms.GroupBox]::new()
$logGroup.Text = "Log"
$logGroup.Dock = "Fill"
$main.Controls.Add($logGroup, 0, 3)

$outputBox = [System.Windows.Forms.TextBox]::new()
$outputBox.Dock = "Fill"
$outputBox.Multiline = $true
$outputBox.ScrollBars = "Both"
$outputBox.ReadOnly = $true
$outputBox.Font = [System.Drawing.Font]::new("Consolas", 9)
$logGroup.Controls.Add($outputBox)

$footer = [System.Windows.Forms.Label]::new()
$footer.Dock = "Fill"
$footer.AutoEllipsis = $true
$footer.TextAlign = "MiddleLeft"
$footer.Text = "Profiles: $profilesFile"
$main.Controls.Add($footer, 0, 4)

function Get-SelectedProfile {
    if ($null -eq $profileList.SelectedItem) {
        throw "Select a profile first."
    }
    return $profileList.SelectedItem
}

function Sync-FieldsFromSelection {
    if ($null -eq $profileList.SelectedItem) {
        return
    }
    $selected = Get-SelectedProfile
    $nameBox.Text = $selected.name
    $homeBox.Text = $selected.codexHome
    $workspaceBox.Text = $selected.workspace
}

function Sync-SelectionFromFields {
    $selected = Get-SelectedProfile
    $selected.name = $nameBox.Text.Trim()
    $selected.codexHome = $homeBox.Text.Trim()
    $selected.workspace = $workspaceBox.Text.Trim()
    $profileList.DisplayMember = ""
    $profileList.DisplayMember = "name"
}

function Append-Output([string]$text) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $outputBox.AppendText("[$stamp] $text$([Environment]::NewLine)$([Environment]::NewLine)")
}

function Refresh-PoolView {
    if ($null -eq $poolGrid -or $null -eq $poolSummaryLabel) {
        return
    }

    $selectedIndex = $profileList.SelectedIndex
    $total = $profileList.Items.Count
    $ready = 0
    $notReady = 0

    $poolGrid.Rows.Clear()
    for ($index = 0; $index -lt $profileList.Items.Count; $index++) {
        $profile = $profileList.Items[$index]
        $state = Get-PoolState $profile
        $remaining = Get-CooldownRemaining $profile
        $localText = Format-Remaining $remaining
        $shortUsedRaw = Get-ProfileString $profile "shortLimitUsedPercent" ""
        $shortResetRaw = Get-ProfileString $profile "shortLimitResetUtc" ""
        $weeklyUsedRaw = Get-ProfileString $profile "weeklyLimitUsedPercent" ""
        $shortLeftText = Format-LeftPercent $shortUsedRaw
        $weeklyLeftText = Format-LeftPercent $weeklyUsedRaw
        $resetCreditsText = Get-ProfileString $profile "resetCreditsAvailable" "-"
        if ([string]::IsNullOrWhiteSpace($resetCreditsText)) {
            $resetCreditsText = "-"
        }
        $limitError = Get-ProfileString $profile "lastLimitsError" ""
        $reachedType = Get-ProfileString $profile "limitReachedType" ""
        $isExhausted = (Test-LimitExhausted $shortUsedRaw) -or (Test-LimitExhausted $weeklyUsedRaw)
        $shortResetText = Format-ActionableReset $shortUsedRaw $shortResetRaw
        $weeklyResetText = Format-WeeklyReset $profile

        if (-not [string]::IsNullOrWhiteSpace($limitError)) {
            $state = "Error"
        } elseif (-not [string]::IsNullOrWhiteSpace($reachedType) -or $isExhausted) {
            $state = "Not Ready"
        }

        if ($state -eq "Not Ready" -or $state -eq "Error") {
            $notReady++
        } else {
            $ready++
        }

        $rowIndex = $poolGrid.Rows.Add($profile.name, $state, $localText, $shortLeftText, $shortResetText, $weeklyLeftText, $weeklyResetText, $resetCreditsText)
        $row = $poolGrid.Rows[$rowIndex]
        if ($state -eq "Error") {
            $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(255, 235, 235)
            $row.DefaultCellStyle.ForeColor = [System.Drawing.Color]::FromArgb(130, 0, 0)
        } elseif ($state -eq "Not Ready") {
            $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(255, 210, 210)
            $row.DefaultCellStyle.ForeColor = [System.Drawing.Color]::FromArgb(120, 0, 0)
        } else {
            $row.DefaultCellStyle.BackColor = [System.Drawing.Color]::FromArgb(235, 255, 235)
            $row.DefaultCellStyle.ForeColor = [System.Drawing.Color]::FromArgb(0, 90, 0)
        }
    }

    $poolSummaryLabel.Text = "Total accounts: $total    Ready: $ready    Not Ready: $notReady"
    if ($selectedIndex -ge 0 -and $selectedIndex -lt $poolGrid.Rows.Count) {
        $poolGrid.ClearSelection()
        $poolGrid.Rows[$selectedIndex].Selected = $true
    }
}

$profileList.Add_SelectedIndexChanged({
    Sync-FieldsFromSelection
    Refresh-PoolView
})

$poolGrid.Add_CellClick({
    param($sender, $eventArgs)
    if ($eventArgs.RowIndex -ge 0 -and $eventArgs.RowIndex -lt $profileList.Items.Count) {
        $profileList.SelectedIndex = $eventArgs.RowIndex
    }
})

$saveButton.Add_Click({
    try {
        Sync-SelectionFromFields
        Save-Profiles @($profileList.Items)
        Refresh-PoolView
        Append-Output "Saved profiles."
    } catch {
        Append-Output $_.Exception.Message
    }
})

$statusButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        Ensure-ProfileFileCredentialStore $profile | Out-Null
        $result = Invoke-CodexCapture $codexCliPath $profile.codexHome @("login", "status") $profile.workspace
        Append-Output "Status for $($profile.name):`r`n$result"
    } catch {
        Append-Output $_.Exception.Message
    }
})

$doctorButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        Ensure-ProfileFileCredentialStore $profile | Out-Null
        $result = Invoke-CodexCapture $codexCliPath $profile.codexHome @("doctor", "--summary") $profile.workspace
        Append-Output "Doctor for $($profile.name):`r`n$result"
    } catch {
        Append-Output $_.Exception.Message
    }
})

$seedButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        $message = Seed-ProfileConfig $profile
        Append-Output $message
    } catch {
        Append-Output $_.Exception.Message
    }
})

$loginButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        Save-Profiles @($profileList.Items)
        Start-Login $profile $codexCliPath
        Append-Output "Opened login window for $($profile.name)."
    } catch {
        Append-Output $_.Exception.Message
    }
})

$deviceLoginButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        Save-Profiles @($profileList.Items)
        Start-Login $profile $codexCliPath -DeviceCode
        Append-Output "Opened device login window for $($profile.name)."
    } catch {
        Append-Output $_.Exception.Message
    }
})

$setCooldownButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        Set-Cooldown $profile ([TimeSpan]::FromHours(5))
        Save-Profiles @($profileList.Items)
        Refresh-PoolView
        $until = Get-CooldownUntilLocal $profile
        Append-Output "Started 5-hour local cooldown timer for $($profile.name). Ready at $($until.ToString('yyyy-MM-dd HH:mm:ss'))."
    } catch {
        Append-Output $_.Exception.Message
    }
})

$clearCooldownButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        Clear-Cooldown $profile
        Save-Profiles @($profileList.Items)
        Refresh-PoolView
        Append-Output "Cleared local cooldown timer for $($profile.name)."
    } catch {
        Append-Output $_.Exception.Message
    }
})

$refreshLimitsButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        Append-Output "Refreshing Codex limits for $($profile.name)..."
        $result = Invoke-LimitsRefresh $profile $nodePath $codexCliPath
        Set-ProfileLimitsFromResult $profile $result
        Save-Profiles @($profileList.Items)
        Refresh-PoolView
        if ($result.ok) {
            Append-Output "Refreshed limits for $($profile.name)."
        } else {
            Append-Output "Could not refresh limits for $($profile.name): $($result.error)"
        }
    } catch {
        Append-Output $_.Exception.Message
    }
})

$refreshAllLimitsButton.Add_Click({
    try {
        Sync-SelectionFromFields
        for ($index = 0; $index -lt $profileList.Items.Count; $index++) {
            $profile = $profileList.Items[$index]
            Append-Output "Refreshing Codex limits for $($profile.name)..."
            try {
                $result = Invoke-LimitsRefresh $profile $nodePath $codexCliPath
                Set-ProfileLimitsFromResult $profile $result
                if ($result.ok) {
                    Append-Output "Refreshed limits for $($profile.name)."
                } else {
                    Append-Output "Could not refresh limits for $($profile.name): $($result.error)"
                }
            } catch {
                $profile.lastLimitsRefreshUtc = [DateTimeOffset]::UtcNow.ToString("o")
                $profile.lastLimitsError = $_.Exception.Message
                Append-Output "Could not refresh limits for $($profile.name): $($_.Exception.Message)"
            }
        }
        Save-Profiles @($profileList.Items)
        Refresh-PoolView
    } catch {
        Append-Output $_.Exception.Message
    }
})

$useResetCreditButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        $available = Get-ProfileString $profile "resetCreditsAvailable" "unknown"
        $availableNumber = 0
        if (-not [int]::TryParse($available, [ref]$availableNumber)) {
            [System.Windows.Forms.MessageBox]::Show(
                "Refresh Limits first so the launcher can confirm this account has a reset credit available.",
                "Reset credit availability unknown",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Information
            ) | Out-Null
            Append-Output "Did not use reset credit for $($profile.name): availability is unknown. Click Refresh Limits first."
            return
        }

        if ($availableNumber -lt 1) {
            [System.Windows.Forms.MessageBox]::Show(
                "$($profile.name) does not currently report any available reset credits.",
                "No reset credit available",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Information
            ) | Out-Null
            Append-Output "Did not use reset credit for $($profile.name): no reset credits available."
            return
        }

        $message = "Use one rate-limit reset credit for $($profile.name)?`r`n`r`nReset credits available: $availableNumber`r`n`r`nThis consumes a real account reset credit."
        $answer = [System.Windows.Forms.MessageBox]::Show(
            $message,
            "Use reset credit",
            [System.Windows.Forms.MessageBoxButtons]::OKCancel,
            [System.Windows.Forms.MessageBoxIcon]::Warning
        )
        if ($answer -ne [System.Windows.Forms.DialogResult]::OK) {
            Append-Output "Cancelled reset-credit use for $($profile.name)."
            return
        }

        Append-Output "Requesting rate-limit reset for $($profile.name)..."
        $result = Invoke-ResetCredit $profile $nodePath $codexCliPath
        Set-ProfileLimitsFromResult $profile $result
        Save-Profiles @($profileList.Items)
        Refresh-PoolView

        if (-not $result.ok) {
            Append-Output "Could not use reset credit for $($profile.name): $($result.error)"
            return
        }

        switch ([string]$result.resetOutcome) {
            "reset" { Append-Output "Reset credit consumed for $($profile.name). Eligible rate-limit windows were reset." }
            "nothingToReset" { Append-Output "No reset credit consumed for $($profile.name): no current rate-limit window is eligible for reset." }
            "noCredit" { Append-Output "No reset credit consumed for $($profile.name): no earned reset credits are available." }
            "alreadyRedeemed" { Append-Output "Reset request was already redeemed for $($profile.name). Refreshed limits." }
            default { Append-Output "Reset request completed for $($profile.name). Outcome: $($result.resetOutcome)" }
        }
    } catch {
        Append-Output $_.Exception.Message
    }
})

$cliButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        if (-not (Confirm-OpenIfNotReady $profile)) {
            Append-Output "Cancelled CLI launch for $($profile.name)."
            return
        }
        Save-Profiles @($profileList.Items)
        Start-CodexCli $profile $codexCliPath
        Append-Output "Opened CLI for $($profile.name)."
    } catch {
        Append-Output $_.Exception.Message
    }
})

$appButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        if (-not (Confirm-OpenIfNotReady $profile)) {
            Append-Output "Cancelled desktop app launch for $($profile.name)."
            return
        }
        Save-Profiles @($profileList.Items)
        Append-Output (Stop-CodexDesktopForAccountSwitch)
        $saveBackMessage = Sync-ActiveDesktopAuthBackToProfile
        if (-not [string]::IsNullOrWhiteSpace($saveBackMessage)) {
            Append-Output $saveBackMessage
        }
        $syncMessage = Sync-ProfileAuthToDesktopDefault $profile
        Append-Output $syncMessage
        Start-CodexDesktop $profile $codexCliPath
        Append-Output "Switched Codex Desktop to $($profile.name) and requested relaunch."
    } catch {
        Append-Output $_.Exception.Message
    }
})

$openFolderButton.Add_Click({
    try {
        Sync-SelectionFromFields
        $profile = Get-SelectedProfile
        Ensure-ProfileHome $profile
        Start-Process -FilePath "explorer.exe" -ArgumentList @($profile.codexHome) | Out-Null
        Append-Output "Opened $($profile.codexHome)."
    } catch {
        Append-Output $_.Exception.Message
    }
})

$refreshTimer = [System.Windows.Forms.Timer]::new()
$refreshTimer.Interval = 1000
$refreshTimer.Add_Tick({ Refresh-PoolView })
$refreshTimer.Start()

$form.Add_FormClosed({
    $refreshTimer.Stop()
    $refreshTimer.Dispose()
})

Sync-FieldsFromSelection
Refresh-PoolView
[void]$form.ShowDialog()
