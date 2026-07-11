param(
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

if ($InstallDependencies) {
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt -r requirements-build.txt
}

# Keep the release name in sync with pyproject.toml rather than duplicating it
# in the workflow or hand-editing it for every tag.
$Version = python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
if (-not $Version) {
    throw "Could not read the package version from pyproject.toml"
}

$BuildRoot = Join-Path $RepoRoot "build"
$DistRoot = Join-Path $RepoRoot "dist"
$Bundle = Join-Path $DistRoot "AI-Account-Hub"
$Artifact = Join-Path $DistRoot "AI-Account-Hub-$Version-windows-x64.zip"
$Checksum = Join-Path $DistRoot "AI-Account-Hub-$Version-windows-x64.sha256.txt"

New-Item -ItemType Directory -Path $BuildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $DistRoot -Force | Out-Null

# Generate native Windows Explorer version metadata from the project version.
$Parts = @($Version.Split('.') | ForEach-Object { [int]$_ })
while ($Parts.Count -lt 4) { $Parts += 0 }
$VersionTuple = "$($Parts[0]), $($Parts[1]), $($Parts[2]), $($Parts[3])"
$VersionInfo = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($VersionTuple),
    prodvers=($VersionTuple),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'AlexC1991'),
        StringStruct('FileDescription', 'AI Account Hub'),
        StringStruct('FileVersion', '$Version'),
        StringStruct('InternalName', 'AI-Account-Hub'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 AlexC1991'),
        StringStruct('OriginalFilename', 'AI-Account-Hub.exe'),
        StringStruct('ProductName', 'AI Account Hub'),
        StringStruct('ProductVersion', '$Version'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
Set-Content -LiteralPath (Join-Path $BuildRoot "windows-version-info.txt") -Value $VersionInfo -Encoding UTF8

python -m compileall -q ai_account_hub
if ($LASTEXITCODE -ne 0) { throw "Python compilation failed" }

python -m PyInstaller --clean --noconfirm `
    --distpath $DistRoot `
    --workpath (Join-Path $BuildRoot "pyinstaller") `
    packaging\AI-Account-Hub.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$Executable = Join-Path $Bundle "AI-Account-Hub.exe"
if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) {
    throw "PyInstaller did not create $Executable"
}

# The windowed executable emits no console output, so the exit code is the
# contract for imports, version metadata, and bundled-resource validation.
$env:AI_HUB_EXPECTED_VERSION = $Version
& $Executable --smoke-test
$env:AI_HUB_EXPECTED_VERSION = $null
if ($LASTEXITCODE -ne 0) {
    throw "Frozen executable smoke test failed with exit code $LASTEXITCODE"
}

# Keep human-readable release files beside the executable as well as inside the
# PyInstaller resource directory used by Help menu actions.
Copy-Item README.md, RELEASE_NOTES.md, LICENSE -Destination $Bundle -Force

if (Test-Path -LiteralPath $Artifact) {
    Remove-Item -LiteralPath $Artifact -Force
}
Compress-Archive -LiteralPath $Bundle -DestinationPath $Artifact -CompressionLevel Optimal

$Hash = (Get-FileHash -LiteralPath $Artifact -Algorithm SHA256).Hash
$ArtifactName = Split-Path $Artifact -Leaf
Set-Content -LiteralPath $Checksum -Value "$Hash  $ArtifactName" -Encoding ASCII
Write-Output "Built: $Artifact"
Write-Output "SHA256: $Hash"
