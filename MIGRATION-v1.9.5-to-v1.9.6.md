# Upgrade from 1.9.5 to 1.9.6

Verify release/pin. Stop dispatch/settlement; establish exclusive ownership. Rehearse on disposable copies; retain untouched authentic baseline/external reports.

See [adoption](.agentic/docs/20-NEW-PROJECT-SETUP.md).

Before committing/checking out managed bytes, merge `.agentic/templates/installed.gitattributes`
into root `.gitattributes`, preserving product rules. This prevents `core.autocrlf=true` corruption. Inspect nested/info overrides; never
renormalize unrelated files. Repeat verification after fresh checkout.

`PYTHON` is the absolute interpreter in a separate bootstrap virtual environment, created with a supported Python using `python -m venv BOOTSTRAP`. Install the verified release dependencies there with `PYTHON -m pip install --require-hashes --no-index --find-links WHEELS -r RELEASE/.agentic/requirements.lock`. WHEELS must satisfy the release lock. Use absolute paths for uppercase arguments.

Snapshot **before apply**; preserve that quiescent copy as untouched BASELINE:

```text
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output BEFORE
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup --dry-run
PYTHON -B RELEASE/scripts/bootstrap_project.py --dest DISPOSABLE --expected-manifest-sha256 PIN --runtime-wheelhouse WHEELS --mode upgrade --github-repo OWNER/REPO --repository-id ID --default-branch BRANCH --on-conflict backup
PYTHON -B RELEASE/scripts/qualify_upgrade.py snapshot --project DISPOSABLE --output AFTER
PYTHON -B RELEASE/scripts/qualify_upgrade.py compare --project DISPOSABLE --before BEFORE --after AFTER --source RELEASE --expected-manifest-sha256 PIN --baseline-project BASELINE --output COMPARISON
```

Compare proves preservation only **since BEFORE**. Backups require this upgrade's exact replaced/deleted originals and metadata; arbitrary additions fail. Default conflict handling creates no backups; missing replaced receipt/provenance originals fail preservation.

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

Retain marker/journal/recovery objects. Run verified bootstrap with `--dest DISPOSABLE --recover` freshly. Automatic deletion remains deferred to [AWF-258](.agentic/docs/38-RELEASE-196-QUALIFICATION.md#awf-258-successor). Rollback requiring runtime/backup removal refuses before managed mutation; retries cannot clear it. Remain quiescent under exclusive ownership. The owner inspects journal/retained paths/original/new bytes, preserves needed data externally and recovers manually before retrying. Never edit evidence or reinstall over unknown/tampered state. Successful commits report COMMITTED_CLEANUP_PENDING backups: inspect exact inventory. Repeat canonical checks; rollback proves no CONFIGURED/ACTIVE state.

Recovery recognises only exact sibling temporary names derived from the journal's
transaction identity, naming scheme and recorded targets. Unrecorded look-alikes
and unknown managed additions refuse with marker retained. Older journals grant
no cleanup authority. Never glob-delete `.awf-*.tmp` or edit recovery records.

Budgeting stays LEGACY_RETAINED when absent, with original engine/limits; explicit settings remain. The upgrade report/state disposition records the prior hash and EXPLICIT_SHARED_LEDGER_IMPORT_REQUIRED, never completeness or adoption.

Request adoption in AWF chat. The skill branches from fetched default
and adds budgeting to OPERATING_CONFIG.yaml, preserving text/comments: accounting false,
enforcement false, 4,000,000 tokens/ticket and 1,000,000,000/project lifetime.
Include it in the upgrade PR when requested. The optional helper
`operating set --adopt-budgeting --instruction "Adopt dormant budget controls"`
only prepares this file change on that branch; it needs no explicit --set choices
and cannot enable anything. Restricted publication prepares branch/body for the
owner. The owner reviews and merges the PR on GitHub; fetch makes it effective.
Unmerged edits change no effective settings. Ask for non-OFF settings in a subsequent settings PR.
Git/PR history replaces confirmation transactions.

Before enabling, stop every legacy writer. Inventory state-store/routing/per-host
databases, checkpoint WALs and retain immutable snapshots/SHA-256 pins. The trusted host calls installed `agentic.upgrade.import_budget_history` with one
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

Rollout remains unauthorized.
