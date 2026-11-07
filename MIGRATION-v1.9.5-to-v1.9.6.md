# Upgrade from 1.9.5 to 1.9.6

Use the separately verified release/pin. Stop dispatch/settlement and establish exclusive ownership. Rehearse on disposable copies; retain an untouched authentic baseline and external reports. Never run this rehearsal on a real installation.

`PYTHON` is the absolute interpreter in a separate bootstrap virtual environment, created with a supported Python using `python -m venv BOOTSTRAP`. Install the verified release dependencies there with `PYTHON -m pip install --require-hashes --no-index --find-links WHEELS -r RELEASE/.agentic/requirements.lock`. WHEELS must satisfy the release lock. Replace uppercase arguments; use absolute paths.

Snapshot **before apply**; copy that same quiescent project to BASELINE and leave BASELINE untouched:

```text
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output BEFORE
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup --dry-run
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --runtime-wheelhouse WHEELS --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output AFTER
PYTHON -B RELEASE/scripts/qualify_upgrade.py compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON
```

Compare proves preservation only **since BEFORE**, never absence of earlier damage. Backup acceptance requires this upgrade's exact replaced/deleted originals and expected metadata; arbitrary backup-folder additions fail. Default conflict handling creates no backups.

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

For owner adoption:

1. Stop every legacy writer. Inventory state-store/routing/per-host databases, checkpoint WALs, retain immutable snapshots and SHA-256 pins. Preserve reservations and UNKNOWN usage.
2. The trusted host explicitly calls installed `agentic.upgrade.import_budget_history` with one protected canonical ledger outside all worktrees, exact project ID, complete worktree roots and source-label mappings `{path, sha256}`. Both hosts must select that ledger. Retain its JSON result externally as IMPORT_RESULT. This host API grants no adoption authority. Capture `prepare_budget_import` before and `check_budget_import` afterwards using `scripts/qualify_upgrade.py` APIs; retain sources. Exact replay deduplicates; conflicts require reconciliation.
3. Prepare dormant adoption:

```text
RUNTIME -B -I WORKFLOW --root DISPOSABLE operating set --instruction "Adopt dormant budget controls" --adopt-budgeting --set budgeting.accounting_enabled=false --budget-import-result IMPORT_RESULT
RUNTIME -B -I WORKFLOW --root DISPOSABLE operating confirm --digest DIGEST
```

The proposal displays complete OFF settings, exact changes, import result/hash and digest; no settings change until the owner confirms in their own terminal through AWF-242. OFF needs no import (omit its result flag). Then inspect `operating show`; propose enabling separately with ordinary `operating set` and confirm again after continuity verification. Enabled admission retains exact legacy import/charge checks. Declining adoption retains legacy behavior. See [budgeting](.agentic/docs/39-BUDGETING.md).

Final release qualification/review/acceptance and separately authorized adopter rollout remain outstanding. This guide authorizes neither.
