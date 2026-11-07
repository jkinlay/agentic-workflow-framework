# Upgrade from 1.9.5 to 1.9.6

Use the separately verified release/pin. Stop dispatch/settlement and establish exclusive ownership. Rehearse on disposable copies; retain an untouched authentic baseline and external reports. Never run this rehearsal on a real installation.

Projects not yet on 1.9.5 should first follow the [adoption guide](.agentic/docs/20-NEW-PROJECT-SETUP.md).

`PYTHON` is the absolute interpreter in a separate bootstrap virtual environment, created with a supported Python using `python -m venv BOOTSTRAP`. Install the verified release dependencies there with `PYTHON -m pip install --require-hashes --no-index --find-links WHEELS -r RELEASE/.agentic/requirements.lock`. WHEELS must satisfy the release lock. Replace uppercase arguments; use absolute paths.

Snapshot **before apply**; copy that same quiescent project to BASELINE and leave BASELINE untouched:

```text
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output BEFORE
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup --dry-run
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --runtime-wheelhouse WHEELS --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output AFTER
PYTHON -B RELEASE/scripts/qualify_upgrade.py compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON
```

Compare proves preservation only **since BEFORE**, never absence of earlier damage. Backup acceptance requires this upgrade's exact replaced/deleted originals and expected metadata; arbitrary backup-folder additions fail. Default conflict handling creates no backups; missing replaced receipt/provenance originals fail preservation.

Identity must match existing configuration. Inspect the plan before applying. Unresolved validation commands refuse; rejected settings remain pending owner review, with one compatibility change: incomplete enabled Jira identity formerly REJECTED can become accepted configuration with JIRA_IDENTITY_UNBOUND. The plan reports this status change; settings stay unchanged and writes remain refused until explicit identity adoption. Disabled Jira stays disabled; adoption enables no adapter/controller.

Accepted YAML retains comments/format; only its version changes. Refusals name paths/remedies. Preserve unexpected import/schema files externally; correct or move them outside their managed directories. For managed/state links, preserve targets externally, restore contained independent regular files/directories and reconcile references before repeating the plan. Do not discard history or weaken containment. Snapshot/upgrade do not walk unrelated product directories.

Routing, operating choices, reviewers, pins, budgets and accounting remain. Unknown state gets a read-only archive copy; prior archives remain without recursive archival. Conflicting destinations refuse. Never reset accounting or edit receipts.

`RUNTIME` is the absolute installed `.agentic/.venv/Scripts/python.exe` on Windows or `.agentic/.venv/bin/python` elsewhere. `WORKFLOW` is the absolute installed `.agentic/scripts/workflow.py`:

```text
RUNTIME -B -I WORKFLOW --root DISPOSABLE verify-installation
RUNTIME -B -I WORKFLOW --root DISPOSABLE validate-config
RUNTIME -B -I WORKFLOW --root DISPOSABLE status
```

CONFIGURED requires actual zero exits, integrity true, ACCEPTED configuration and matching digests. ACTIVE additionally needs independent release trust and observed human-reviewed adoption acceptance; it enables no adapter/controller. See [qualification](.agentic/docs/38-RELEASE-196-QUALIFICATION.md).

Interrupted installation: retain marker/journal/recovery objects. Use the verified release's bootstrap script with `--dest DISPOSABLE --recover` in a fresh process, then repeat recovery for a stable endpoint. Unknown/tampered recovery requires investigation; never reinstall over it. Repeat canonical checks; rollback alone proves no CONFIGURED/ACTIVE state.

Budgeting stays LEGACY_RETAINED when absent, with original engine/limits; explicit settings remain. The upgrade report/state disposition records the prior hash and EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED, never completeness or adoption.

For owner adoption, ask in AWF Codex chat. The skill prepares an upgrade/settings
branch from the fetched default branch and adds the budgeting block to
OPERATING_CONFIG.yaml, preserving its existing text/comments: accounting false,
enforcement false, 4,000,000 tokens/ticket and 1,000,000,000/project lifetime.
It can travel in the upgrade adoption PR when requested. The optional agent helper
`operating set --adopt-budgeting --instruction "Adopt dormant budget controls"`
only prepares this file change on that branch; it needs no explicit --set choices
and cannot enable anything. Restricted publication prepares branch/body for the
owner. The owner reviews and merges the PR on GitHub; fetch makes it effective.
No terminal command is required of the owner. Working edits and unmerged branches
change no effective settings. Ask for non-OFF settings in a subsequent settings PR.
Git/PR history replaces confirmation transactions.

Before enabling, stop every legacy writer. Inventory state-store/routing/per-host
databases, checkpoint WALs and retain immutable snapshots/SHA-256 pins. The trusted
host explicitly calls installed `agentic.upgrade.import_budget_history` with one
protected canonical ledger outside worktrees, exact project ID, complete worktree
roots and source-label mappings `{path, sha256}`. Both hosts select that ledger.
Capture `prepare_budget_import` before and `check_budget_import` afterwards using
`scripts/qualify_upgrade.py` APIs. Preserve sources/reservations/UNKNOWN usage and
retain results externally. Exact replay deduplicates; conflicts need reconciliation.
Enabled admission retains exact legacy import/charge checks. OFF adoption needs
no import; declining adoption retains legacy behavior.

Supported 1.9.5 operating records continue to read and verify without confirmation.
Unsupported 1.9.6 pre-release records carrying confirmation fields refuse with a
plain reason; preserve them and restore a supported 1.9.5 baseline before upgrade.
See [budgeting](.agentic/docs/39-BUDGETING.md).

Final release qualification/review/acceptance and separately authorized adopter rollout remain outstanding. This guide authorizes neither.
