# AWF budgeting

AWF-242: Tier 3, FULL. Fresh accounting/enforcement default OFF; dormant limits:
4,000,000 tokens/ticket, 1,000,000,000/project lifetime. Limits enable neither
switch; enforcement requires accounting; accounting OFF disables enforcement.
Missing fields preserve legacy budgeting/reviewed limits.

## Operating choices and authentication

`OPERATING_CONFIG.yaml` block:

```json
"budgeting": {
  "accounting_enabled": false,
  "enforcement_enabled": false,
  "max_tokens_per_ticket": 4000000,
  "max_tokens_per_project": 1000000000
}
```

Set `budgeting.*` booleans/positive integers within bounds.

Run `workflow.py operating set --instruction "REQUEST" --set
budgeting.accounting_enabled=true`. Valid changes print exact changes/digest,
store a protected proposal, exit 3 `PENDING_OWNER_CONFIRMATION`; settings stay
unchanged. Owner runs `workflow.py operating confirm --digest D` in their
terminal within 15 minutes, typing its first eight characters. Skills never
confirm; verify persistence with `operating show`.

Proposals bind clone/project/repository/governance, prior settings, changes, request/expiry.
Confirmation retains protected digests. Audit: numeric owner ID/login/digest/time, never tokens. Register journal SHA-256/event ID once
in protected storage within 15 minutes, before publication/configuration changes.
Confirmations supersede unapplied transactions. Recovery
finishes the latest registered transaction extending bound prior
settings, or clears its terminal journal without reapplying; it never renews
authority. Before journal publication, unchanged settings permit fresh `set`/`confirm`;
afterwards inspect/pin and recover first. Preserve evidence. Disabling accounting recovers
derived enforcement OFF too.

Owner storage uses the Windows current token's account SID and HKLM ProfileList
ProfileImagePath plus AppData/Local, or POSIX password-database home, outside worker
writes. No override selects it; AWF_STATE_HOME, LOCALAPPDATA, USERPROFILE and HOME cannot.
Usage state may use AWF_STATE_HOME; deny worker writes.

Budget admission/recovery/controller/routing require protected confirmation;
schema validation supplies none. Latest confirmed values bind even defaults/absent blocks.
Loads never inspect Git/gh executables. Without a store/confirmation, fresh OFF
defaults and absent legacy blocks load; unconfirmed nondefaults refuse budget work. Unavailable profiles, junctions
or missing password-database entries never brick defaults. New protected changes
refuse with the reason. OFF needs no usage ledger.

Binding hashes the Git common directory read from metadata;
project ID cross-checks. Editable IDs cannot hide confirmations. Worktrees share them; moved/fresh clones and copies need fresh
confirmation. Ignore Git environment overrides; gitfiles require registered backlinks outside sandbox write authority. Without Git, protected changes refuse. The host must deny worker Git-metadata
writes/replacement: binding protects clone identity under that isolation, not processes
able to replace it.

Older checkouts/mismatches remain inspectable: show/validate-config report differences
from this clone's latest confirmation; unrelated changes work, budget work refuses.
Pending proposals report pending. Explicit budgeting choices/terminal confirmation
repair admission preserving audit. Screen/digest bind every difference from
confirmed values, including prior disk edits; settings appear for
no-change reconfirmation. Audit/journals prove nothing; transactions bind governance/prior settings.
Bootstrap/adoption/upgrade preserve OFF defaults; migration/import preserve
history, never authenticate. Missing legacy fields retain legacy policy
until manual versioned budget adoption.

Confirm requires interactive stdin/stdout (Windows console handles, excluding
NUL/pipes/files); refuses token environment variables and foreign `GH_HOST`.
`gh auth status --hostname github.com --active` and
`gh api --hostname github.com --method GET user` verify a User's numeric ID
against `merge_gate.trusted_owner_ids`; empty refuses
`OWNER_GATE_NOT_CONFIGURED`, naming `.agentic/PROJECT_CONFIG.yaml`. Resolve native `gh` once; refuse scripts/worker-writable locations using sandbox-group/capability SID
file/directory grants; owner/admin token rights are not disqualifying. Without sandbox
principals this check passes, including POSIX. Owner-writable clones pass; volume-root grants alone
cannot establish replacement. Audit path/SHA-256.
`trusted_owner_ids` comes from the working tree:
a process editing it still needs that terminal/login. A process with the
owner's full rights can still get around this. No execution authority.

## Ledger and enrollment

Only explicitly enrolled AWF sub-agents count; enrollment binds project/ticket/run/dispatch/session/role.
Exclude controller/independent Codex agents, including ULTRA. Repository/model/ancestry/worker
requests cannot enroll. Protect state outside worktrees; paths supplement isolation.

`RoutingLedger.model_runs` is canonical; `BudgetLedger` adds enrollment. Atomic
checks retain history across days/restarts/policies/worktrees. Bridge legacy
`budget_charges` transactionally; verify sealed archives, deduplicate identities,
refuse conflicts. Separate databases require migration; enforcement refuses until
history is imported. Never evade history by changing databases.

Routed reserve requires trusted registry/binding and matching run/ledger IDs;
settlement retains model/effort/context/independence checks. Budget-only facade
cannot settle routed runs or bypass checks. State-store uses that facade without
duplicate reservations. Preserve legacy identities; never relaunch replays.

## Modes and preservation

OFF requires no budget database, telemetry, reservations/events, warnings or
overrun quarantine. Retain security/permissions/ownership/review/merge/host-capacity
and bounded recovery controls; security/context/review mismatches quarantine.
Existing recovery limits and owner authorization to raise them bind in every mode.
Historical budget overruns do not block OFF/accounting-only work.
Ledger-free routing proposals never admit/launch; operational admission remains required.
Accounting-only records usage without quota refusal/warnings/quarantine. Unknown
estimates/actuals stay UNKNOWN/null, never zero; independent runs remain outside quotas.
Enforcement atomically counts known usage/outstanding reservations per ticket/lifetime;
unreliable unknown totals refuse. Runs/late settlements retain admission-time bindings/mode.
OFF applies only to subsequent work, never deletes/refunds history, reservations,
failures or unknowns; known overruns stay charged. Reconciliation closes
incidents without refund. Count only attributable tools/retries; enroll retries.

Use versioned 1.9.6 preservation, not in-place backport. Installed CMC Quant Foundry
1.9.5 stays unchanged/enabled. Owner may retain it or authorize qualified preservation
with explicit budget disposition. Never edit integrity-managed bytes/pins/receipts,
or initialize fresh OFF over upgrades. Hosts share ledgers; migration preserves
history/reservations. Live integration requires qualification; release owns manifests.
