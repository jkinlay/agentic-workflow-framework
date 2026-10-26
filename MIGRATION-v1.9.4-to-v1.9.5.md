# Upgrade AWF 1.9.4 to 1.9.5

Use this guide only with a verified integrated 1.9.5 distribution, its independently obtained manifest pin, and a registered byte-exact 1.9.4 installation. The final 1.9.5 release date and publication acceptance remain UNVERIFIED.

## Before upgrading

Complete the release publication checkpoint. Record the accepted version, receipt, provenance, project identity, configuration, operating settings, current state, and pending recovery artifacts. Pause controller activity, arrange exclusive installation ownership, and preserve an independent recoverable backup including ignored state.

Use a new dedicated release cache and retain rejected caches or diagnostics. Do not rehash locally modified managed files into an apparently valid receipt.

## Preview and apply

Resolve every placeholder to a verified local path and the independently approved release-manifest digest.

macOS/Linux:

```sh
'<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --mode upgrade --on-conflict backup --dry-run
'<install-dir>/bootstrap-venv/bin/python' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --runtime-wheelhouse '<install-dir>/wheelhouse' --mode upgrade --on-conflict backup
```

Windows PowerShell:

```powershell
& '<install-dir>/bootstrap-venv/Scripts/python.exe' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --mode upgrade --on-conflict backup --dry-run
& '<install-dir>/bootstrap-venv/Scripts/python.exe' -B '<install-dir>/release-cache/source/scripts/bootstrap_project.py' --dest '<repo-path>' --expected-manifest-sha256 '<release-manifest-sha256>' --runtime-wheelhouse '<install-dir>/wheelhouse' --mode upgrade --on-conflict backup
```

Before applying, inspect detected and target versions, every migration step, the full configuration diff, managed-file changes, state actions, and new required settings.

## Owner-file migration

The adjacent migration changes `.agentic/PROJECT_CONFIG.yaml` at `template.expected_workflow_version` from `1.9.4` to `1.9.5`. It adds `execution.risk_tiers.tier3_review` only when absent, with roles `[critic, specialist]`, blocking findings, and `max_rounds: 3`; an existing `tier3_review` mapping is retained.

YAML retains surrounding bytes and verifies the parsed result; JSON is reserialized. `OPERATING_CONFIG.yaml` is carried forward without authorizing a capacity increase. This adjacent step does not add Jira identity fields: that insertion belongs to the earlier 1.9.2→1.9.3 step.

The `.gitignore` installer creates or appends one AWF block after the existing bytes, with one LF only if required, when no complete block is recognized. Otherwise it replaces the first complete header-to-terminal block in place at its original position and removes later complete blocks. It preserves every byte outside the blocks in order; refuses partial, nested, or incomplete boundaries; and `added_lines` counts the lines actually inserted.

The retained Codex machine-log credential detector refuses only high-confidence secret formats and exact environment credential values.

## Controller adapter requirements

The controller loads a reviewed absolute adapter module, checks its SHA-256 pin, calls `build_adapters(config)`, and rejects every required operation that is missing or non-callable. The supplied source does not establish a `workflow.py -I` form; place Python isolation flags only according to the verified installed wrapper or interpreter command.

| Subcommand | Required adapter operations | Conditional or optional operations | Shipped reference adapter |
|---|---|---|---|
| `planning-state prune` | None | None | N/A; no adapter is loaded |
| `snapshot` | None | None | N/A; no adapter is loaded |
| `cycle` | `observe_inventory,observe_reservations,dispatch_ticket,observe_dispatch,deliver_status,observe_publication,observe_host_resources,verify_host_observation,observe_planning_backlog,verify_planning_backlog,observe_planning_events,verify_planning_events,verify_planning_owner` | Required with `--overlap-allowances`: `observe_overlap_allowance`; required with `--replan-request`: `observe_replan_trigger`. Optional when callable: `observe_overlap_conflict,observe_overlap_sync_inventory,sync_writer,reconcile_overlap_sync,observe_dispatch_quiescence` | Supported |
| `jira-lifecycle` | `observe_provider_identity,observe_issue_identity,read_current_status,write_transition,read_transition` | None | Supported |
| `merge-observed` | `reconcile_merged_ticket,fetch_scope_page,observe_accepted_project_config,observe_closeout_record` | When Jira is enabled: `observe_jira_reconciliation` | Supported |
| `owner-publication-prepare` | `authorize_owner_publication` | None | Unsupported |
| `owner-publication-resume` | `observe_identity,observe_remote_heads,create_draft_pr,observe_draft_pr` | None | Unsupported |
| `owner-publication-status` | None | None | N/A; no adapter is loaded |
| `batch-operation` | `batch_coordinator` | None | Unsupported |
| `batch-closeout` | `batch_coordinator,observe_batch_merge,observe_issue_identity,observe_issue_policy,observe_provider_identity,read_current_status,write_transition,read_transition` | None | Unsupported: missing `batch_coordinator,observe_batch_merge,observe_issue_policy` |

Adapter loading alone grants no live execution, publication, review, push, merge, or Jira-write authority.

## Verification and rollback

`UPGRADED` alone does not establish CONFIGURED. Require successful installed verification and configuration evidence with matching digests; review the complete upgrade diff and preserved owner bytes; run the installed self-test with a fresh report path and run project tests separately.

ACTIVE additionally requires acceptance of the receipt-changing upgrade and current governed bytes on the observed default branch. Before durable commit, authenticated recovery can restore the old managed/runtime state; afterward it completes the committed endpoint and is not a downgrade.

For a completed upgrade, use an owner-reviewed restoration or revert plan based on preserved backups and accepted history. Exact restore commands and subsequent ACTIVE acceptance remain UNVERIFIED.

Final integrated platform, CI, archive, and publication results remain UNVERIFIED.
