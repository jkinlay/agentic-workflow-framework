# Routing migration: projects originating at AWF 1.5

The 1.6 transition introduced Balanced model defaults, explicit per-task routing and an external usage ledger. Evidence records remained revision 3; routing has its own policy/ledger version. This guide maps those differences when adopting the current release; it does not ask you to install an intermediate version.

Preserve repository/Jira/CI identities, owner boundaries, explicit models, writer caps and historical records. Add `execution.model_routing` from the verified source only after reviewing its allowed models, effort floors, budgets and actual host capabilities. Align `execution.roles` deliberately. A legacy configuration without routing keeps static roles; it gains no implied automatic escalation.

Use one coordinator-owned durable ledger outside worker checkouts. Reserve before launch, enforce actual limits at the host, and settle observed identity, usage and outcome. Unknown usage is unresolved, not zero. A reservation never launches a model. Escalation within configured limits uses the existing task authority; pins cannot exceed allowed models or budgets. Independent reviews retain separate contexts and risk floors.

Begin adaptive operation in shadow mode. Collect distinct independently reviewed tickets with exact policy/model/effort and outcome evidence; evaluate recommendations on held-out work before accepting new policy. Synthetic routing tests demonstrate rule/accounting behavior, not model quality. The scheduled PR loop uses its separate configuration and is not enrolled by native routing.

Use the current [migration procedure](MIGRATION-v1.7-to-v1.8.md) for dry-run, backed-up cross-version install, expected-version mapping, validation and rollback. Upgrading the user skill alone does not migrate a project. Preserve catalog settings; prepare the bundled release explicitly if the channel is stale.
