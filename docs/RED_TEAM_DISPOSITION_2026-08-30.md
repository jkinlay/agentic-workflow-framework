# Red-Team Review Disposition — 30 August 2026

The later reviewer-adapter addendum supersedes the original Claude permission
design. The processed result is recorded in
`docs/RED_TEAM_ADDENDUM_DISPOSITION_2026-08-30.md`; the original report and
addendum remain immutable review inputs under `docs/reviews/`.

## Overall decision

Accept the central conclusion: AWF v0.1.0 relied too heavily on prose inside an
agent-writable checkout. Independent external PR review is adopted, together
with the protected-path, human-authorization, and push-time controls required to
make that review meaningful.

## Accepted findings

- **C1:** protected governance, runtime, CI, ownership, reviewer, and gate paths
  require a non-empty deny floor plus server-side enforcement.
- **C2:** push is the first external-effect boundary; merge-only controls do not
  protect CI secrets.
- **H1:** issue text cannot grant scope, tools, autonomy, or approver authority.
- **H2:** a subagent in the developer lineage is not an independent reviewer.
- **H3:** autonomy must have one protected source and the operating model must be
  part of the documentation gate.
- **H4:** release provenance, hashes, and a generated lock are required before
  automated upgrades.
- **H6-H8:** instruction discovery, independent research evidence, identity,
  retention, and incident controls need mechanical definitions.
- **M1-M7 and L1-L3:** the consistency, distribution, placeholder, validation,
  provenance, and upstream-hygiene findings are accepted as implementation
  backlog unless superseded below.

## Qualified agreement

- **H5:** confidentiality domains must be mechanically separated. A distinct
  runtime home, OS account, or machine is a valid control. Administratively
  managed and domain-specific global instructions may still be permitted; the
  requirement is no unapproved cross-domain state, not a universal ban on all
  global configuration.
- **C2 fallback:** a base-branch PR gate can block merge but cannot undo a
  malicious push-triggered workflow. Without server-side push restrictions,
  the safe alternatives are secret-less agent branches/forks or remaining at
  A1.
- **Reviewer benchmark:** adopt it as a standing control, but let each project
  set cadence and thresholds from risk rather than imposing monthly execution
  universally.

## Disagreements with the proposed reviewer implementation

1. **No universal Claude-first ordering.** AWF requires one qualified external
   engine and lets the project owner select it. Copilot is often the safer first
   GitHub integration because it does not introduce a repository API secret;
   Claude may provide stronger model separation where its vendor route and App
   permissions are approved.
2. **Claude is not read-only merely because workflow permissions say so.** The
   Claude GitHub App can use its own installation permissions and may push to an
   open PR branch. AWF therefore requires contents read-only at the App level
   and treats the adapter as unavailable until that is verified.
3. **Claude output is not a formal approving review.** Current official action
   documentation says it cannot submit formal reviews or approve. Its output is
   evidence for a required check and a human, not a GitHub approval.
4. **Humans must not read findings instead of diffs.** The external engine
   reduces review load and focuses attention, but the human merge authority
   still performs risk-appropriate diff inspection.
5. **The example `review.engines` list was ambiguous.** It said "Claude where
   available, otherwise Copilot" but made every listed engine mandatory. AWF
   instead records one `required_external_engine`, optional additional engines,
   and the effective reviewed head SHA.

## Implementation status in this change

- Added a provider-neutral review policy and structured review-report schema.
- Added Copilot and Claude Code Action adapter guidance.
- Added protected-path and external-review configuration to the project
  scaffold.
- Added Copilot adversarial review instructions and a CODEOWNERS fragment.
- Added a secretless, read-only Copilot presence gate that binds the configured
  reviewer identity to the current pull-request head SHA.
- Made human readiness/plan approval and the A1 default explicit.
- Added a single state machine with actors, failure states, and definitions.
- Added reviewer and push-time checks to onboarding and implementation gates.

Server-side rulesets, credentials, App permissions, required checks, and actual
review execution cannot be activated in a local repository with no remote. They
remain mandatory project-bootstrap actions, not assumed completed controls.
