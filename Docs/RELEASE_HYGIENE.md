# Release Hygiene

AI Account Hub keeps public project documentation in `Docs/` and local private
notes in `local-docs/`.

Windows treats `Docs/` and `docs/` as the same directory on the default
case-insensitive filesystem, so the private folder deliberately uses a distinct
name instead of relying on letter case.

## Public

These paths are intended to be safe for GitHub:

- `README.md`
- `LICENSE`
- `Start-AI-Account-Hub.bat`
- `Docs/`
- `outputs/ai-hub-calendar-gui/`
- `outputs/ai-hub-qt/`
- `outputs/ai-hub-calendar-gui/test_*.py`
- `outputs/ai-hub-qt/test_*.py`

## Private Or Local Only

These paths are ignored and should not be uploaded:

- `local-docs/`
- `work/`
- `.claude/`
- `.codex-account-launcher/`
- `.codex-accounts/`
- `.ai-account-hub/`
- `provider-discovery.json`
- `outputs/ai-hub-calendar-gui/qa-*`
- `outputs/ai-hub-calendar-gui/assets/*.png`
- `outputs/ai-hub-qt/demo_data.py`

The ignored folders keep only `.gitkeep` placeholders in the public tree. Put
machine-specific audits, provider probes, screenshots, exported sessions, and
temporary schemas under `local-docs/` instead of committing them.

## Before Publishing

Run the release audit from the repository root:

```powershell
git status --short --ignored
git ls-files -co --exclude-standard
rg -n --hidden --glob '!.git/**' --glob '!local-docs/**' --glob '!work/**' -i "(refresh[_-]?token|access[_-]?token|authorization|bearer|cookie|session|auth\.json|profiles\.json|C:\\\\Users\\\\)"
python -m compileall -q outputs\ai-hub-calendar-gui outputs\ai-hub-qt
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest outputs\ai-hub-calendar-gui outputs\ai-hub-qt -q
```

The current Git history may still contain older research artifacts from local
development. For a first public release, prefer creating a fresh GitHub
repository from the cleaned working tree or rewriting history before pushing.
