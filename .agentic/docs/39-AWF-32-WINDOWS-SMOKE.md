# AWF-32 Windows smoke procedure

Version 1.9.4. **Operator-run; requires Jonathan.** This procedure is not
executed by CI or by the AWF-32 writer. Run it only on `CCLi9RTX4090` after
AWF-36 has landed and the owner has enabled the controller switches.

Use a disposable ticket and PR in the configured repository. Before starting,
record the exact adapter SHA-256, Codex and `gh` executable SHA-256 values,
repository ID, Jira cloud/project/actor IDs, controller head/tree, and the
owner's authorization to test. Create a scratch directory outside every
worktree and call its absolute path `<STATE_DIR>`; this means a fresh,
nonce-named child of an operator-approved scratch parent. Place the protected
state SQLite database, adapter JSON, inventory/binding files, contract/facts,
run records and JSONL outbox there. Never use the installed AWF runtime or a
production state database.

1. On the named machine, verify the pinned native executables and adapter
   bytes with `Get-FileHash -Algorithm SHA256`. Set only the configured Jira
   token environment variable for the disposable account. Confirm
   `GH_TOKEN`, `GITHUB_TOKEN`, and unrelated Jira credential variables are not
   passed to child processes.
2. Put one disposable, eligible ticket in the GitHub inventory endpoint and
   prepare a matching inventory binding. Use the real repository head/tree and
   an empty or disposable stream set appropriate to the smoke.
3. Run one `cycle` using the exact command shape in
   `34-CONTINUOUS-CONTROLLER.md`. Confirm JSON has complete inventory,
   repository-bound publication readiness, one nonce-bound dispatch receipt,
   `status_delivery.status` equal to `DELIVERED`, and
   `execution_authority: false`. Confirm the outbox record can be read back.
4. Interrupt one disposable Codex launch only if Jonathan explicitly wants
   the recovery case. Restart with the same scratch state and observe the
   durable run record, its nonce-bound handoff, and the independent terminal
   proof. Confirm the adapter performs no second launch, returns an
   observation-bound receipt, or remains unknown and stops safely.
5. Run one `jira-lifecycle` operation. Confirm a current-status read precedes
   at most one transition write and an independent status readback follows it.
   Then run `merge-observed` for the disposable merged PR. Confirm Jira
   reconciliation precedes stable, complete pagination and that incomplete or
   mismatched pages do not produce counts.
6. Record the owner stop, remove the disposable PR/ticket according to the
   owner's repository procedure, and retain only the smoke evidence required
   by the owner. Do not merge product changes from this procedure.

Record template:

```text
operator: Jonathan / __________________
machine: CCLi9RTX4090
started_at_utc: __________________
scratch_path: __________________
adapter_sha256: __________________
codex_sha256: __________________
gh_sha256: __________________
repository_id: __________________  jira_cloud_id: __________________
ticket_and_pr: __________________
controller_head_tree: __________________ / __________________
cycle_json_path: __________________
jira_json_path: __________________
merge_json_path: __________________
dispatch_replayed: YES / NO
outbox_readback: PASS / FAIL
read_before_write_readback: PASS / FAIL / NOT_RUN
pagination_complete: PASS / FAIL / NOT_RUN
owner_stop_recorded_at_utc: __________________
unexpected_observation_or_secret_exposure: __________________
owner_disposition: __________________
```
