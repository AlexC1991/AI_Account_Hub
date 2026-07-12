"""PyInstaller one-folder definition for the Windows portable build."""

from pathlib import Path


# PyInstaller exposes SPECPATH as the directory containing this spec file.
ROOT = Path(SPECPATH).resolve().parent
PUBLIC_DOCS = (
    "ANTIGRAVITY_ACCOUNT_SETUP.md",
    "ARCHITECTURE.md",
    "CLAUDE_ACCOUNT_SETUP.md",
    "CODEX_ACCOUNT_SETUP.md",
    "COMMUNITY_TELEMETRY_SECURITY_PLAN.md",
    "CURSOR_ACCOUNT_SETUP.md",
    "PORTING_MACOS_LINUX.md",
    "PROVIDER_DISCOVERY.md",
    "REAL_WORLD_USAGE_ANALYTICS.md",
)
PUBLIC_SCREENSHOTS = (
    "README.md",
    "community-sharing.png",
    "dashboard.png",
    "day-detail.png",
    "signal-rail.png",
    "statistics-compare.png",
    "statistics-community.png",
    "statistics-models.png",
    "statistics-overview.png",
    "statistics-productivity.png",
    "tray-widget.png",
)

datas = [
    (str(ROOT / "ai_account_hub" / "assets"), "ai_account_hub/assets"),
    (str(ROOT / "scripts" / "codex-account-limits-helper.mjs"), "scripts"),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "RELEASE_NOTES.md"), "."),
    (str(ROOT / "LICENSE"), "."),
]
datas.extend((str(ROOT / "docs" / name), "docs") for name in PUBLIC_DOCS)
datas.extend((str(ROOT / "screenshots" / name), "screenshots") for name in PUBLIC_SCREENSHOTS)

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # The Windows artifact embeds Python 3.12, so the Python 3.10-only tomli
    # fallback and its setuptools alias are unreachable release weight.
    excludes=["pkg_resources", "pytest", "setuptools", "tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI-Account-Hub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "ai_account_hub" / "assets" / "hub-icon.png"),
    version=str(ROOT / "build" / "windows-version-info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AI-Account-Hub",
)
