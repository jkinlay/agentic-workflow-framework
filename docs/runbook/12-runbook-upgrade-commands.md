### Upgrade commands

**[Unmerged d2-006d branch for the registered 1.9.4 predecessor; 1.9.5 publication UNVERIFIED.]**

Windows:

```powershell
& '<install-dir>/bootstrap-venv/Scripts/python.exe' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --mode upgrade --on-conflict backup --dry-run
& '<install-dir>/bootstrap-venv/Scripts/python.exe' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --runtime-wheelhouse '<install-dir>/wheelhouse' --mode upgrade --on-conflict backup
```

macOS/Linux:

```sh
'<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --mode upgrade --on-conflict backup --dry-run
'<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --runtime-wheelhouse '<install-dir>/wheelhouse' --mode upgrade --on-conflict backup
```

Command interface: `7fcaeece:scripts/bootstrap_project.py:141`. Historical upgrade planning: `7fcaeece:.agentic/lib/agentic/installer.py:1738`.

**Preview acceptance:** inspect `detected_version`, `target_version`, each migration step, total configuration diff, added/changed/removed managed files, state actions, and new required settings. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1803`.

**Application acceptance:** CONFIGURED requires the same actual installed subprocess evidence and digest matches as fresh installation. `UPGRADED` alone is only the underlying transaction result. Sources: `7fcaeece:.agentic/lib/agentic/installer.py:1971`; `7fcaeece:.agentic/lib/agentic/adoption_config.py:893`.

**Failure:** retain diagnostics, original state, and recovery material. An immutable-file mismatch reports expected/actual hashes and refuses the historical upgrade. Do not rehash modified files into an apparently valid receipt. Sources: `7fcaeece:.agentic/lib/agentic/upgrade.py:198`; `9f44c2a1:.agentic/tests/test_upgrade_195_path.py:270`.

