# AWF budgeting

Tier 3, FULL. Accounting/enforcement default OFF; dormant limits:
4,000,000 tokens/ticket, 1,000,000,000/project lifetime. Limits enable neither
switch; enforcement requires accounting; accounting OFF disables enforcement.

## Upgrade and adoption

Upgrading 1.9.5 keeps an absent budgeting block absent: LEGACY_RETAINED,
original engine and reviewed limits. It adds no accounting, enforcement or budget
gate. Supported 1.9.5 operating records remain verified without confirmation.
Unsupported 1.9.6 pre-release confirmation records refuse plainly; retain them and
restore a supported baseline before upgrading.

When the owner asks in chat, the skill adds the complete dormant OFF block to
OPERATING_CONFIG.yaml on an upgrade/settings PR branch, preserving text/comments.
The upgrade adoption PR may carry it. The optional agent helper
`operating set --adopt-budgeting --instruction WORDS` prepares this file change
only; no --set choices, enabling or owner terminal commands. Git/PR history audits
it. Owner merge and fetch make it effective. Non-OFF choices require a subsequent
settings PR. Dormant adoption needs no ledger/import.

Before enabling, stop legacy writers, snapshot/pin every state-store/routing/host
ledger and explicitly call `agentic.upgrade.import_budget_history` into one
protected shared ledger outside worktrees. Supply exact project ID, complete
worktree roots and source-label mappings `{path, sha256}`. Independently capture
`prepare_budget_import` before and `check_budget_import` afterwards. Verify
charges/reservations/UNKNOWN values/deduplication; retain immutable sources and
results externally. Conflicts require reconciliation; migration cannot approve
its own lost history. See [migration](../../MIGRATION-v1.9.5-to-v1.9.6.md).

## Settings through PRs

In AWF Codex chat, read-only `workflow.py operating show` needs no approval.
Present switches/limits, source/commit, working differences and APPLIED/MISSING/
UNOBSERVED repository rules as warnings. Last PR/merger is optional information.

Settings: accounting_enabled, enforcement_enabled, max_tokens_per_ticket and
max_tokens_per_project under budgeting. Limits are integers 1..9223372036854775807;
switches are booleans. No Epic overrides. Validate proposed bytes with
`operating validate --file PROPOSED`; invalid combinations refuse before preparing
a PR. Branch from the fetched default, edit requested values preserving comments,
and show before/after under existing publication authority. Restricted pushes
prepare branch/body for owner publication. The owner merges; agents never merge.
Protected `operating set` otherwise refuses without writes. Unprotected flow stays.

Effective protected settings come from the locally fetched default-branch commit.
Only refs/remotes/origin/HEAD identifies it; configuration is never an identity
input. Show reports disagreement with github.base_branch. Working edits and PR
branches cannot change admission/recovery/controller/routing, including disabling
enforcement. Older checkouts read the same policy. Merge then fetch applies a
change; update a stale checkout before proposing another.

An absent default-branch file is accepted: fresh OFF or legacy; working values
have no effect. Nondefault checks compare normalized values.
With origin configured but origin/HEAD unavailable, show/validate/install warn;
budget admission tries refresh and refuses unresolved identity except for fresh
OFF/legacy installs with no tracking refs, no default ref and only default
protected values. Non-repository fresh OFF/legacy continue offline.
Malformed/unreadable policy refuses plainly. Git metadata must remain
outside worker write authority. Repository rules and owner merge rights govern
acceptance; direct default-branch push/full owner rights can bypass this control.
Offer the shipped ruleset. Reader digests do not authenticate merges.
The controller's own checkout is not a worker worktree. Deny worker Git metadata,
update-ref and fetch-refspec writes; gitfile backlinks must match. `show` reports rules UNOBSERVED
without a fresh observation; warnings never block.

`show` reports commit and FETCH_HEAD time as the fetch attempt of any
ref, never evidence of policy refresh. New merges remain invisible until fetched.
The controller's own checkout requires repository-local remote.origin.url;
relative/inside-checkout paths refuse. Git 2.29+ ([notes](https://git-scm.com/docs/RelNotes/2.29.0));
older-binary execution unproven. Lock before 30-second discovery/fetch,
advertised-commit checking and publication. Mismatches retry next cycle.
UTF-8 names use check-ref-format. Preserve unverified tracking HEAD when identity
is absent; delete conflicts, update target, then origin/HEAD. Hierarchical renames recover.
Failures retain policy/unverified refusal with remedies. Readers ignore locks;
unlocked locks older than ten minutes are replaced/reported. Locking prevents
clock-jump takeover. Shared filtering preserves Git helpers/home/PATH/SSH-agent.
`show` never fetches. The skill fetches after owner merge, then shows settings.

## Accounting and recovery

OFF opens no ledger/telemetry/reservations. Usage ledgers and atomic enforcement
remain unchanged: enroll supported producers, reserve before launch, settle
observed usage, retain UNKNOWN/overruns and never refund when turning OFF.
Independent agents cannot be attributed by guesswork. Import retains original
charges and blocks enabled admission without complete proofs. Startup failures
remain counted. Routing proposals alone never admit or launch work. Local
operating journals still govern unprotected transactions; protected settings use
Git/PR history, without a protected owner store or terminal confirmation.
