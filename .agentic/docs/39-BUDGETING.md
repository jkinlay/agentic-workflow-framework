# AWF budgeting

AWF-242: Tier 3, FULL. Fresh accounting/enforcement OFF; dormant limits:
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

Run `workflow.py operating set --instruction "REQUEST" --set
budgeting.accounting_enabled=true`. Valid changes print exact changes/digest,
store a protected proposal, exit 3 `PENDING_OWNER_CONFIRMATION`; settings stay
unchanged. Owner runs `workflow.py operating confirm --digest D` in their
terminal within 15 minutes, typing its first eight characters. Skills never
confirm; verify persistence with `operating show`.

Proposals bind clone/project/repository/governance, prior settings, changes, request/expiry.
Audit retains owner ID/login/digest/time, never tokens. Register journal SHA-256/event ID once
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
Deny worker writes; usage state may use AWF_STATE_HOME.

Budget admission/recovery/controller/routing require protected confirmation;
schema validation supplies none. Latest confirmed values bind even defaults/absent blocks.
Loads never inspect Git/gh executables. Without a store/confirmation, fresh OFF
defaults and absent legacy blocks load; unconfirmed nondefaults refuse budget work. Unavailable profiles, junctions
or missing password-database entries never brick defaults. New protected changes
refuse with the reason. OFF needs no usage ledger.

Binding hashes the Git common directory read from metadata;
project ID cross-checks. Editable IDs cannot hide confirmations. Worktrees share them; moved/fresh clones and copies need fresh
confirmation. Ignore Git environment overrides; gitfiles require registered backlinks outside sandbox write authority. Without Git, protected changes refuse. Deny worker Git-metadata writes/replacement; clone binding requires that isolation.

Older checkouts/mismatches remain inspectable: show/validate-config report differences
from this clone's latest confirmation; unrelated changes work, budget work refuses.
Pending proposals report pending. Explicit terminal confirmation repairs admission preserving audit. Screen/digest bind all differences, including disk edits and no-change reconfirmation. Audit/journals prove nothing; transactions bind governance/prior settings.
Upgrade/import preserve history, never authenticate. Missing legacy fields retain legacy policy
until explicit adoption. From a legacy file, propose `operating set --instruction
"Adopt dormant budget controls" --adopt-budgeting --set budgeting.accounting_enabled=false`.
Optionally attach the external import API JSON with `--budget-import-result RESULT`;
its hash/result appear with the proposal and confirmation, without proving completeness.
Finish through `operating confirm --digest D` in the owner terminal. This adds complete
dormant controls only. Enabling requires a separate ordinary proposal/confirmation
after history import/continuity verification. Declining retains legacy behavior.
See [migration](../../MIGRATION-v1.9.5-to-v1.9.6.md).

Confirm requires interactive consoles; refuses NUL/pipes/files, token variables and foreign `GH_HOST`.
`gh auth status --hostname github.com --active` and
`gh api --hostname github.com --method GET user` verify numeric User ID
against `merge_gate.trusted_owner_ids`; empty yields
`OWNER_GATE_NOT_CONFIGURED` (`.agentic/PROJECT_CONFIG.yaml`). Resolve native `gh` once;
refuse scripts and sandbox-group/capability SID modification/replacement rights.
Inspect ancestors through volume root for ACL/ownership control; replacement needs
parent creation plus DELETE or parent delete-child. Owner/admin rights pass.
Without sandbox principals (POSIX), pass.
Audit path/SHA-256.
`trusted_owner_ids` comes from the working tree:
a process editing it still needs that terminal/login. A process with the
owner's full rights can still get around this.

## Ledger and enrollment

Enrollment binds project/ticket/run/dispatch/session/role; only enrolled AWF sub-agents count.
Exclude controller/independent Codex agents, including ULTRA. Repository/model/ancestry/worker
requests cannot enroll. Protect state outside worktrees; paths supplement isolation.

`RoutingLedger.model_runs` is canonical; `BudgetLedger` adds enrollment. Atomic
checks retain history across days/restarts/policies/worktrees. Bridge legacy
`budget_charges` transactionally; verify sealed archives, deduplicate identities,
refuse conflicts. Separate databases require migration; enforcement refuses until
history is imported. Retain history.

Routed reserve requires trusted registry/binding and matching run/ledger IDs;
settlement retains model/effort/context/independence checks. Budget-only facade cannot settle routed runs. State-store uses that facade without
duplicate reservations. Preserve legacy identities; never relaunch replays.

## Modes and preservation

OFF requires no budget database, telemetry, reservations/events, warnings or
overrun quarantine. Retain security/permissions/ownership/review/merge/host-capacity
and bounded recovery controls; security/context/review mismatches quarantine.
Existing recovery limits and owner authorization to raise them bind in every mode.
Overruns do not block OFF/accounting-only work.
Ledger-free routing proposals never admit/launch; operational admission remains required.
Accounting-only records usage without quota refusal/warnings/quarantine. Unknown
estimates/actuals stay UNKNOWN/null, never zero; independent runs remain outside quotas.
Enforcement atomically counts known usage/outstanding reservations per ticket/lifetime;
unreliable unknown totals refuse. Runs/late settlements retain admission-time bindings/mode.
OFF applies only to subsequent work, never deletes/refunds history, reservations,
failures or unknowns; known overruns stay charged. Reconciliation closes
incidents without refund. Count only attributable tools/retries; enroll retries.

Use versioned 1.9.6 preservation, not in-place backport. Existing installations stay unchanged until authorized preservation/adoption. Never edit pins/receipts or initialize fresh OFF over upgrades. Hosts share ledgers; migration preserves
history/reservations. Live integration requires qualification.
