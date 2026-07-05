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
- `pyproject.toml`
- `main.py`
- `Start-AI-Account-Hub.bat`
- `Docs/`
- `ai_account_hub/` (the app package: `ui/`, `core/`, `harness/`)
- `ai_account_hub/demo_data.py`
- `tests/`
- `scripts/`
- `screenshots/`

## Private Or Local Only

These paths are ignored and should not be uploaded:

- `local-docs/`
- `work/`
- `.claude/`
- `.codex-account-launcher/`
- `.codex-accounts/`
- `.ai-account-hub/`
- `provider-discovery.json`
- `ai_account_hub/assets/*.png`

The ignored folders keep only `.gitkeep` placeholders in the public tree. Put
machine-specific audits, provider probes, private screenshots, exported
sessions, and temporary schemas under `local-docs/` instead of committing them.

## Before Publishing

Run the release audit from the repository root:

```powershell
git status --short --ignored
git ls-files -co --exclude-standard
rg -n --hidden --glob '!.git/**' --glob '!local-docs/**' --glob '!work/**' -i "(refresh[_-]?token|access[_-]?token|authorization|bearer|cookie|session|auth\.json|profiles\.json|C:\\\\Users\\\\)"
python -m compileall -q ai_account_hub
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
```

The current Git history may still contain older research artifacts from local
development. For a first public release, prefer creating a fresh GitHub
repository from the cleaned working tree or rewriting history before pushing.
