# Continuous controller contract

Version 1.9.4. Continue until complete, blocked or owner-stopped.

## Production cycle

Use only the pinned entry point:

```text
python .agentic/scripts/workflow.py controller --state ABSOLUTE_PROTECTED_DB --stream A --stream B --stream C --worktree-root ABSOLUTE_WORKTREE --project-config PROJECT_CONFIG --adapter-module REVIEWED_ADAPTER.py --adapter-sha256 PIN --adapter-config ADAPTER_CONFIG cycle --inventory-binding INVENTORY_BINDING --repository-root ABSOLUTE_REPOSITORY --repository-head-sha FULL_HEAD --repository-tree-sha FULL_TREE --now TIME --host-capacity N
```

State stays outside worktrees. Corrupt identity blocks. Each cycle pins
worktree/head/tree. Persist intent before dispatch; never reissue an unresolved
mutation.

## Exact overlap allowances

`--cycle-id` and `--overlap-allowances` accept only an unedited canonical
`AWF-OVERLAP-ALLOWANCE 1` comment by `merge_gate.trusted_owner_ids`, for at
most 24 hours. `github.expected_actor_id` grants none. Plan and lane-start
Date/ETag observations bind repository, PRs, heads, files and branches.
Before either lane is scheduled, re-observe inventory and validate exact
ticket/path ownership. Sample controller UTC after every
provider read; five seconds of leading provider Date is tolerated only for
clock skew. Paths are exact case-sensitive NFC Git paths without globs.

Expiry persists a pair gate. Accepted dispatches need terminal proof before a
conflict decision; otherwise `PENDING` survives restart. Decide conflict by a
head-bound three-way merge of observed merge-base/file bytes, not blob
inequality. Mergeable different blobs clear without a sync writer. Resolve a
true text conflict onto the second branch by bounded union merge and GitHub's
expected-head commit mutation. Its sorted, hash-matched diff is capped at
512 KiB and carried to the next critic. `SYNCED` binds the receipt;
`EXECUTED` also binds fresh post-read UTC, provider Date/ETag heads and
inventory tuples matching live 40-hex SHAs. `UNKNOWN` reads only its retained
`COMPLETED` receipt.

## Jira and review

Disabled Jira performs no access. Otherwise bind provider, actor and issue on
each read/write/readback; reconcile without reissuing. After merge, reconcile
before counting. Final submission requires the
[review barrier](33-REVIEW-COMPLETION-BARRIER.md).

## Reference adapter (AWF-32)

For AWF-32, use PowerShell 7, disposable issues, `AWF_JIRA_TOKEN`, and an
isolated `AWF_CODEX_HOME` per
[provider identity](38-PROVIDER-IDENTITY-AND-CREDENTIALS.md). Copy
`.agentic/examples/reference-controller-adapter.json` to controller-owned
state. Fill it from fresh observations: executable paths/hashes, numeric
repository/project/actor IDs, base branch, inventory digest, isolated home,
detached exact-head worktree, run/outbox paths and Jira transition IDs. Pin the
configured adapter; invoke the command above with current head/tree and
strictly advancing UTC; require nonce-bound dispatch receipts and no errors.

```powershell
$STATE_DIR=Join-Path $env:TEMP ("awf-controller-"+[guid]::NewGuid().ToString("N"))
$WORKTREE_ROOT=Join-Path $STATE_DIR "worktree"
$NOW=[DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
$HEAD=(git -C $WORKTREE_ROOT rev-parse HEAD).Trim()
$TREE=(git -C $WORKTREE_ROOT rev-parse "HEAD^{tree}").Trim()
$ADAPTER_PIN=(Get-FileHash $ADAPTER_MODULE -Algorithm SHA256).Hash.ToLower()
$CONTROLLER_ACTOR=Read-Host "Jira controller account ID"
$TRANSITION_ID=Read-Host "Owner-approved transition ID"
$AWF_CODEX_HOME=(Resolve-Path (Read-Host "Provisioned AWF Codex home")).Path
$adapter.codex.isolated_home=$AWF_CODEX_HOME
$adapter.codex.windows_sandbox='elevated'
```

The adapter strips credentials and caps stderr at 1 MiB. Stop by atomically
replacing `OWNER-STOP.json.tmp` with `OWNER-STOP.json`; let the cycle finish.
Output has `execution_authority: false`; missing evidence fails closed.
