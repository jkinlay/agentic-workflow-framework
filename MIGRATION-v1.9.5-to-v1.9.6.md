# Upgrade from 1.9.5 to 1.9.6

Use the separately verified release and its manifest pin. Run a non-writing upgrade plan first. Stop active dispatch and establish exclusive ownership before applying the transactional upgrade; retain backups and recovery records until verification succeeds.

The upgrade recognizes the published 1.9.5 receipt and changes the expected workflow version. Existing project routing, operating choices, reviewer allocation, pins, budgets and accounting remain. Existing projects do not automatically adopt the new twelve-stream or model defaults: request those operating changes separately within reviewed governance ceilings.

Fresh projects default to twelve logical workers. Controller, critic, specialist and ordinary worker defaults are gpt-6.1-sol/high; qualifying simple work uses gpt-6-luna/high. Host capacity still limits actual concurrent dispatch.

Unknown state remains present and receives a read-only archive copy for inspection under the selected target release. All existing archive subtrees are retained without recursively archiving them. A conflicting archive destination refuses the upgrade before writes. Never reset accounting or edit managed receipts to force acceptance.

Verify installed integrity and accepted configuration through the installed isolated interpreter. Human-reviewed adoption acceptance and independent release trust are required for ACTIVE. ACTIVE alone does not enable Jira or start a controller. Automatic activation reporting remains deferred.

Follow the [release qualification checklist](.agentic/docs/38-RELEASE-196-QUALIFICATION.md)
for exact candidate bindings, external preservation reports, recovery and the
Windows Python 3.12 installed cohort. The new preservation runner only snapshots,
compares and makes a non-writing plan; it never applies an upgrade. Its synthetic
owner customization evidence is not CMC evidence. An actual adopter baseline and
disposable-copy proof require separate authorized access.

If installation is interrupted, keep the transaction marker, journal and recovery
objects intact. Invoke the verified release's `scripts/bootstrap_project.py
--dest DISPOSABLE_PROJECT --recover` in a fresh process, retain its actual result,
then repeat recovery to prove a stable endpoint. A missing/tampered recovery
object or unknown outcome requires investigation; do not remove diagnostics,
rehash corruption, edit receipts or reinstall over the installation. Re-run both
canonical installed checks after recovery. Never infer CONFIGURED/ACTIVE from a
rollback result or a successful library-only check.

Budget adoption is explicit. Missing `budgeting` settings retain the legacy
engine and reviewed limits; upgrading never silently switches budgeting OFF.
Existing explicit settings remain unchanged. The upgrade report and
`.agentic-state/upgrade/budgeting-1.9.6.json` record `LEGACY_RETAINED` or
`EXPLICIT_SETTINGS_RETAINED`, prior operating/policy hashes and outstanding import
requirements. Neither disposition certifies history completeness or owner adoption.

Before adopting the new controls:

1. Stop legacy dispatch/settlement across every host and worktree. Inventory all
   state-store, routing and per-host budget databases. Checkpoint WALs, retain
   immutable snapshots and pin their SHA-256 digests. Never remove reservations,
   UNKNOWN usage or archives to make admission pass.
2. Select one protected canonical project ledger outside worker/reviewer
   worktrees; configure both controller and review host to use that same path.
   Have the trusted operator call installed `agentic.upgrade.import_budget_history`
   with that destination, exact project ID, complete worktree roots and a mapping
   of source labels to `{path, sha256}`. This is an explicit host API, not an
   automatically enabled adapter. Import every old per-host ledger, even if its
   rows appear duplicated. Exact identities/rows deduplicate with retained proofs;
   conflicts refuse for reconciliation. Retain original databases unchanged.
3. Capture `prepare_budget_import` evidence before import and run
   `check_budget_import` from `scripts/qualify_upgrade.py` afterwards. These
   independent query-only checks compare source continuity, actual canonical
   charges and deduplication proofs. Keep private baseline rows outside upstream;
   publish only sanitized hashes/counts. A receipt alone cannot excuse lost usage.
4. If interrupted, replay the identical pinned sources and destination. Import
   charges/proofs commit together; replay never refunds or double-charges.
   Changed sources, unknown usage or conflicting recovery state require
   investigation. Preserve UNKNOWN as unknown, never a retrospective zero.
5. Through the authenticated owner/host operating boundary, explicitly adopt
   accounting/enforcement settings. New dormant limits are 4,000,000 tokens per
   ticket and 1,000,000,000 per project lifetime. A limit alone enables neither
   switch; enforcement requires accounting. Verify persisted settings and totals
   before resuming. Legacy state-store enforcement retains its refusal until its
   runtime integration validates the canonical import; never bypass that guard.

Jira identity also requires an explicit upgraded-project adoption step. Using the
controller's identity-adoption operation, the operator states the expected
project ID and repository ID and verifies the observed binding. Jira writes
refuse until that step succeeds; retaining an old project key, configuration or
upgrade receipt does not authorize writes. Disabled Jira stays disabled. This
identity step does not enable an adapter or authorize a controller launch.

Final integrated qualification, independent review and owner release/adopter
acceptance remain outstanding until actually observed. This guide authorizes no
live upgrade or activation.
