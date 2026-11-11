### Upgrade commands

For the verified 1.9.6 candidate, rehearse on disposable copies of authentic
published 1.9.5. Live rollout requires separate target authorization. Retain an
untouched baseline; snapshot before apply and retain external preservation reports; see the
[migration guide](../../MIGRATION-v1.9.5-to-v1.9.6.md).

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

Add `--github-repo OWNER/REPO --repository-id ID --default-branch BRANCH` with the
established identity to both commands. Wheels must match the target lock. Backups
require `--on-conflict backup`; the default creates none.

**Preview acceptance:** inspect detected/target versions, steps, complete configuration
diff, managed additions/changes/removals, state actions and required settings.
Unresolved validation commands refuse. Incomplete enabled Jira identity can change
from REJECTED to accepted configuration with JIRA_IDENTITY_UNBOUND; the plan
reports this explicitly. Settings remain unchanged and Jira writes stay refused
until identity adoption. Other rejected settings await review. No test commands are invented. Accepted YAML
retains its comments/format. Unexpected import files must be preserved and moved
outside `.agentic/lib`/`.agentic/scripts`. Unsafe retained state names its path:
preserve it externally and reconcile references before repeating the plan. Product
directories outside upgrade inputs are not traversed. Managed containment remains
mandatory. Preserve unsafe targets externally and restore contained regular unlinked
content before repeating the plan. Comparison proves preservation only since BEFORE.

**Application acceptance:** CONFIGURED requires actual isolated installed subprocess
checks and matching digests. `UPGRADED` alone is only the transaction result.

**Budgeting:** an upgraded 1.9.5 project keeps the block absent and reports
LEGACY_RETAINED; original accounting/limits continue. Upgrade enables nothing.
If requested in chat, prepare the dormant block on the upgrade adoption PR branch
with accounting/enforcement false and limits 4,000,000/ticket, 1,000,000,000/project
lifetime, keeping text/comments. The optional agent helper `operating set
--adopt-budgeting --instruction WORDS` prepares only that file change, without
--set choices. Owner merge and fetch apply it; later non-OFF values need another
settings PR and verified legacy history import. Working edits have no effect.
No owner terminal commands or confirmation step. Supported 1.9.5 audit records
remain valid; unsupported pre-release confirmation records refuse plainly and
must be retained while restoring a supported baseline.

**Failure:** retain diagnostics, original state and recovery material. Immutable-file
mismatches refuse; never rehash modified files into a valid-looking receipt.

**Related guidance:** [Starting-installation checks](15-runbook-supported-starting-installations.md); [migration behavior](18-runbook-migration-behavior.md); [journal recovery](19-runbook-journal-recovery.md); [post-upgrade verification](20-runbook-post-upgrade-verification-and-reactivation.md).

[Runbook index](INDEX.md)
