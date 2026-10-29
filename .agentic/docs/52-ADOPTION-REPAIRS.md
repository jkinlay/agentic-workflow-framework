# Adoption repairs and fresh defaults

The source repair completes native A-L identity handling in model selection,
routing-ledger draining, planning, owner publication and host handoff. Existing
capacity, independent-review, reservation and authorization checks still apply.
Twelve configured workers require fourteen observed slots with the controller
and one shared critic; additional simultaneous specialist work needs capacity.

New project templates select twelve streams. Ordinary workers, controller,
critic and specialists use `gpt-6.1-sol/high`; qualifying simple tasks use
`gpt-6-luna/high`. Tier 1 alone does not select the simple route. Every simple
eligibility predicate still applies, and review/risk floors remain Sol/high.
Missing host support refuses without fallback. The canonical release defaults
are immutable `.agentic/defaults/routing.yaml`; an adopter's `.agentic/routing.yaml`
remains project-owned and is preserved through upgrades.

Fresh bootstrap consumes the packaged operating template. Existing operating
bytes, audit history, pins and reviewer allocation are retained. Missing
operating files on older upgrades retain the conservative three-stream
initialization, and omitted older `execution.max_lanes` still means six. Fresh
governance explicitly supplies twelve rather than widening an old implicit cap.
The optional five-slot acceleration contract remains a separate topology.

Project shell launchers belong in `PROJECT_LAUNCH_SURFACES.json`; do not edit the
pinned framework inventory. Calls under production script roots must still use
`child_env(...)` directly, retain necessary application variables, and read the
intended project's exclusions. Validate actual credential stripping and error
handling; a declaration does not prove runtime behavior or grant authority.

Legacy routing algorithm tests use an explicit immutable fixture, so an
adopter's accepted custom models do not change synthetic test assumptions.
Fresh-default tests separately check the requested release policy and templates.

This repair does not replace an already published release. Release acceptance,
a new version identity, independently verified distribution pins, adoption
review and current ACTIVE evidence remain required before live operation.
Installation remains quiescent; automation switches are enabled only after
bootstrap under accepted project governance. Jira identities and ticket scope
are supplied at project startup; an unbound Jira target permits no writes.
