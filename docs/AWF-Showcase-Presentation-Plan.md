# AWF Showcase Presentation Plan

## Purpose

Present the verified supplied AWF 1.8.9 release as a governed framework for AI-assisted software delivery. The audience should understand its delivery purpose, its explicit limits, how to adopt it in new and established repositories, how its operating choices work, and where GitHub and Jira sit in the authority model.

## Audience and duration

- Audience: Quant, engineering, delivery, risk and technology stakeholders.
- Core presentation: 27 minutes.
- Discussion margin: 3 minutes.
- Total: 30 minutes, within the requested 20 to 30 minute limit.

## Narrative

The deck starts with AWF's release identity and the delivery controls it provides. It then distinguishes installation, adoption, live operation and optional adapters; follows a ticket through its controlled lifecycle; explains adoption for new and established projects; closes the integration story with GitHub, Jira and optional components; compares a clearly-labelled Quant Team delivery proxy with a user-supplied capacity-planning assumption; and ends with an evidence-led pilot.

## Slide plan

| # | Slide | Main message | Visual treatment | Minutes |
| --- | --- | --- | --- | ---: |
| 1 | Release identity and purpose | The verified supplied AWF 1.8.9 release provides a governed operating model for AI-assisted software delivery. | Minimal title with release identity, canonical repository and restrained workflow motif. | 1.0 |
| 2 | Delivery problem and explicit non-goals | AWF makes work ownership, evidence and authority visible. It does not independently launch agents, merge code or mirror Jira workflow states. | Exact contrast between controlled delivery records and retained human or host authority. | 1.5 |
| 3 | Four layers and adoption states | Skill installation, release preparation, project adoption and live operation are separate. `INSTALLED`, `CONFIGURED` and `ACTIVE` describe different evidence states and do not grant live authority. | Four-layer lifecycle paired with a state ladder and a clear live-enablement boundary. | 2.0 |
| 4 | Ticket lifecycle, roles and authority gates | A bounded ticket moves from readiness and reservation through implementation, independent review, final gate, candidate-bound human authorisation and human merge. Amendments, `MERGE_UNKNOWN` reconciliation and optional post-merge Jira are explicit branches. | End-to-end lifecycle with role labels, visible amendment/reconciliation branches and warm human-authority gates. | 2.5 |
| 5 | Candidate-bound evidence and security boundary | Evidence is tied to repository, PR number, head and base SHA, target branch, merge method, requirements and policy. State and secrets remain outside worker worktrees, and privileged review never executes candidate-controlled code. | Candidate-to-evidence map with explicit protected boundary. | 1.5 |
| 6 | Release trust, prerequisites and skill installation | The 1.8.9 distribution is unsigned. An independently trusted delivery pin and byte verification establish supplied bytes, without proving publisher identity or that a release is latest. | Trust sequence that separates release-cache preparation from portable-skill installation, plus a concise prerequisite strip. | 2.0 |
| 7 | New-repository adoption sequence | New-project adoption locks the runtime before both bootstrap calls, then uses dry run, backed-up non-dry-run bootstrap, verification and a draft owner-review PR. `INSTALLED_UNCONFIGURED` stops and requires remedy and rerun. | Seven-stage setup path, including same-filesystem backup semantics and bootstrap-owned checks. | 2.0 |
| 8 | Existing-repository preservation and upgrade | AWF separates byte-exact managed material from protected project-owned values while preserving project instructions, active work, configuration, ownership and history. | Preservation view with distinct project-owned and AWF-managed columns, plus `install` for cross-version adoption and `upgrade` for same-version changes. | 2.0 |
| 9 | Governance versus operating choices | Governance protects budgets, ceilings, allowlists and risk floors. Operating choices select stream count and routes; a trusted host authenticates the actual dispatch instruction and a ceiling is not free host capacity. | Capacity comparison: structural maximum, project ceiling, enabled broker cap and observed slots. | 2.0 |
| 10 | Routing, reservation and fail-closed recovery | AWF reserves before launch and settles observed outcomes. Unknown usage is not zero; reconciliation is optional and disabled by default. The default reconciled-incident ceiling is two per ticket; a third blocks admission unless a trusted host applies a one-time, time-bounded approval. | Ledger path with distinct fail-closed recovery branches and incident-ceiling boundary. | 2.0 |
| 11 | GitHub candidate, CI, PR and merge path | Rules observation is read-only and optional for adoption. AWF prepares a draft PR, binds CI evidence to the candidate and leaves merge to authorised humans. | Candidate-bound PR path with evidence invalidation on head, base or policy change. | 2.0 |
| 12 | Jira's single post-merge boundary | AWF permits at most one explicitly authorised Done transition after a confirmed merge, with read-before and read-after observation. Disabled, unscoped, mismatched or unknown Jira means no further write. | Post-merge decision tree, including the retained non-`DONE` state and the fact that Epics are never transition targets. | 1.5 |
| 13 | Scheduled loop and external review adapter | The scheduled Codex and GitHub PR loop and the separately qualified external-review adapter are distinct optional components with different prerequisites and authority limits. The shipped adapter route is Anthropic Messages-compatible; its publisher and wake credentials are not comment-only. | Focused comparison with purpose, qualification, credentials and non-authority rows. | 2.0 |
| 14 | Quant Team delivery: observed throughput versus planning assumption | Compare a Jira `Fixed`-work-item delivery proxy with the user-supplied 9 PRs per analyst per project day assumption. Preserve the non-equivalence of work items and merged PRs; do not claim an observed AWF uplift. | Two clearly-labelled capacity cards with source caveat and an illustrative, non-causal ratio. | 1.5 |
| 15 | Evidence-led pilot | Start with one owner-approved repository, a bounded ticket set and a matched baseline. Expand only after owner merge, fresh default-branch adoption evidence, ACTIVE, observed capacity and any mode-specific qualification. | Four-step pilot with entry evidence, predeclared measures, guardrails and owner decisions. | 1.5 |

## Content guardrails

- Use the verified supplied AWF 1.8.9 release as the controlling source. Describe it as supplied and verified, not latest. Include the release date (16 September 2026), publisher (Jonathan Kinlay), Apache-2.0 licence and canonical repository (`jkinlay/agentic-workflow-framework`).
- Treat the older 1.4.1 attachment as historical only. Do not use the published Confluence hierarchy as an evidentiary source unless separately cited.
- State that AWF is a governed framework, not an autonomous delivery service. Do not imply automatic agent launches, code merges, Jira workflow mirroring, server-rule changes or external authority from local validation.
- Explain that a digest verifies bytes against an independently trusted pin. It does not authenticate the publisher or prove that a release is latest.
- State that the supplied 1.8.9 distribution is unsigned. Separate release-cache preparation from portable-skill installation.
- Distinguish `INSTALLED`, `CONFIGURED`, `ACTIVE`, live enablement and optional-adapter qualification. `CONFIGURED` requires accepted checks with bound source and policy digests. `ACTIVE` also requires independent release trust, receipt-changing adoption merge and accepted raw AWF, configuration, receipt and provenance bytes on the fresh default branch; it does not authorise a model launch, adapter, code merge or Jira write.
- Include the Publisher role as distinct from Worker, Host/operator and Human owner. Describe the core review as an independent critic in a separate context; actual model-route availability is host-dependent. Do not imply a universal physical separation of role actors; apply the separation and credential constraints required by the selected mode.
- State that human merge authorisation binds the exact candidate, expires and is consumed once. Critic review and final gate never confer merge authority. Preserve the amendment path and `MERGE_UNKNOWN` reconciliation branch.
- Explain native streams accurately: the structural maximum is six, the template starts with three active streams and one independent reviewer per stream, one writer owns a ticket or path at a time, a trusted host authenticates the actual instruction before dispatch, and a governance ceiling is not free host capacity.
- Describe reconciliation accurately: it is disabled by default, requires named authorised operators and protected host controls, records assertions rather than authenticating them, and never turns a reconciled run into positive evidence. State the default ticket-wide ceiling of two reconciled incidents and the trusted-host, one-time, time-bounded approval required to raise it.
- Describe GitHub as observed and explicitly authorised. Rules observation is read-only; bootstrap does not change server rules; CI evidence is candidate-bound; human merge control remains explicit.
- Describe Jira exactly: after confirmed merge, at most one expressly authorised Done transition may be attempted, with read-before/read-after observation. The shipped reference adapter performs no Jira writes. Disabled or unscoped Jira permits no write; an unknown or mismatched result stops all further Jira writes without retry; Epics are never transition targets.
- Compare optional components rather than conflating them. The scheduled review loop is a separately qualified host adapter for one enrolled existing PR. The external-review adapter is inert and separately qualified; the shipped route is Anthropic Messages-compatible and its publisher `pull_requests:write` credential and secretless wake `actions:write` token are not comment-only. Neither is a prerequisite for core adoption.
- Keep security controls visible: candidate-controlled content does not grant authority, worktrees are not isolation boundaries, secrets and protected state remain outside worker worktrees, and privileged review does not execute candidate-controlled code.
- Treat the Quant Team comparison as capacity planning. Use `project = QA AND resolution = Fixed` work items as an observed Jira proxy only, state that accessible Jira fields did not expose a reliable merged-PR count, label the 9 PRs per analyst per project day figure as Jonathan Kinlay's 18 September 2026 planning assumption, and explicitly reject a causal productivity claim or direct ratio interpretation. Predeclare an aligned merged-PR, quality, rework, lead-time, human-effort, cost and recovery cohort before testing an AWF effect.
- Give every slide speaker notes with the relevant AWF 1.8.9 manual section reference. Add visible source text for trust-sensitive claims.

## HTML implementation

Create a standalone 16:9 HTML deck with local or inline assets only, no runtime network dependency, keyboard navigation, touch controls, an accessible table of contents, slide counter, presenter-notes toggle and one-slide-per-page print styling. Use a dark navy background, blue and teal accents, restrained warm highlighting for human authority gates, clear sans-serif typography and high-contrast text. Build diagrams as editable HTML/SVG structures, use labels or icons in addition to colour, honour reduced-motion preferences and keep visible slide copy concise. Add semantic slide landmarks, visible keyboard focus and text equivalents for diagrams; put supporting explanation and source references in speaker notes.

## Acceptance criteria

- Fifteen slides, with a 27-minute core runtime and three minutes discussion margin.
- Professional visual hierarchy, varied visual forms and readable content at presentation size.
- Covers release identity and trust, purpose, non-goals, capabilities, roles, candidate-bound evidence, security, setup for new and existing projects, adoption states, configuration options, capacity, routing, GitHub, Jira, optional components and a pilot route.
- Explicitly distinguishes framework controls from host and human authority, including the conditions that do and do not grant live authority.
- Contains exact Jira and reconciliation boundaries, rather than implying automated or routine external action.
- Is independently reviewed before implementation, amended for accepted findings and includes slide-level source traceability in notes.

## Independent review outcome

An independent Sol/Max reviewer compared the draft plan with the verified AWF 1.8.9 manual. The review identified material omissions in release trust, adoption states, ticket lifecycle, capacity, reconciliation, Jira, optional components, security, timing and accessibility. All findings were incorporated. The reviewer then returned **FIT TO IMPLEMENT**, with the final timing corrected to a 25.5-minute core and 3-minute discussion margin.

## Red Team amendment outcome

A second independent Sol/Max Red Team review of the completed deck returned an **AMBER** verdict. Its high-severity recommendations were adopted: stronger `INSTALLED`/`CONFIGURED`/`ACTIVE` evidence definitions; an exact merge candidate and expiring single-use owner authority; amendment and `MERGE_UNKNOWN` lifecycle branches; an unsigned-release disclosure; corrected bootstrap order and `INSTALLED_UNCONFIGURED` stop condition; explicit separation of managed bytes from project-owned values; reviewed-governance rather than operating-budget language; host-authenticated dispatch and reconciled-incident ceiling; optional-adapter credential disclosures; an owner merge and fresh-default-branch pilot step; and protection against Space advancing the deck while a control has focus.

The Red Team also required the Quant Team productivity comparison to distinguish source units. The amended slide shows the available QA Jira `Fixed`-work-item proxy and the user-supplied merged-PR planning assumption separately, identifies the 21.1× calculation as illustrative only, and states that it is not an observed or causal AWF productivity outcome.
