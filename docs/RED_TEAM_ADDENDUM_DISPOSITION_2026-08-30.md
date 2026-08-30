# Reviewer-Adapter Addendum Disposition - 30 August 2026

## Decision

Accept the addendum's corrected separation-of-duties direction and harden the
working Copilot adapter now. Retain Claude as an unavailable design until a
split model/publisher implementation and its negative permission tests exist.

The original review and reviewer addendum under `docs/reviews/` are preserved
unchanged as review inputs.

## Accepted and implemented

- Independence is procedural; vendor/model diversity is preferred and recorded
  only when known.
- Reviewer identity is adapter-owned. The Copilot gate now requires
  `copilot-pull-request-reviewer[bot]` rather than a free-form repository
  variable.
- The gate checks only the one required engine and binds its review
  `commit_id` to the live pull-request head.
- The API-only gate runs from the base-branch definition with
  `pull_request_target`, never checks out pull-request content, and reruns on
  every push and review event.
- External review gates A2 pull requests, not A0/A1 local work.
- Urgent outage handling uses a named, non-agent ruleset bypass actor plus an
  incident record; an approval alone cannot replace a required check.
- A split Claude model/publisher architecture is the only accepted direction:
  the model has no GitHub write credential and deterministic protected code
  performs any publication.
- Threat, identity/audit, and incident-response contracts are now explicit.

## Qualified or corrected

- Two-phase blind/claims review is mandatory only when an adapter controls the
  model inputs. Copilot reads provider-selected pull-request context and cannot
  prove that separation; its limitation is recorded rather than disguised.
- `pull_request_target` is accepted only for base-defined, API-only/read-only
  handling that never executes pull-request content.
- The expected source of the supplied `awf/review` required status is GitHub
  Actions. The reviewer App posts review evidence but does not emit the workflow
  job status.
- A Claude publisher App's `pull_requests: write` permission is not called
  comment-only. Its risk is bounded by keeping the credential away from the
  model and constraining deterministic publisher code to `COMMENT` reviews.
- Use protected `.agentic/` or project-approved runtime paths for future review
  scripts; introducing `.awf/` is optional, though it remains in the protected
  floor for compatibility with the addendum.

## Deferred with an activation block

- Executable Claude workflow, model and publisher scripts, pinned action SHAs,
  permission-negative tests, App installation, and vendor credentials.
- Seeded review benchmark fixtures and measured thresholds.
- Framework remote, immutable release commit/tag, per-artifact hashes, generated
  lock implementation, and project/manifest/lock schemas.
- Upstream CI and secret scanning.
- Server-side rulesets, expected-source configuration, App identities, and
  actual reviewer execution, which require the future GitHub remote.

Claude and all A2 writes remain unavailable until their respective deferred
controls are demonstrated. Deferred items are not represented as completed
security properties.
