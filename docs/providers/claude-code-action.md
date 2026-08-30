# Claude Review Adapter

## Status

Unavailable in AWF v0.1.0. This document defines the qualification design; it
does not claim an executable adapter has passed it.

Anthropic documents that Claude Code Action can push to an open pull-request
branch when permitted and cannot itself submit a formal pull-request review or
approval. Anthropic's documented custom-App setup currently requests read/write
Contents, Issues, and Pull Requests. Running the model-driven action with that
credential does not meet AWF's effective read-only contract.

Official documentation:

- [Setup and custom-App permissions](https://github.com/anthropics/claude-code-action/blob/main/docs/setup.md)
- [Capabilities and limitations](https://github.com/anthropics/claude-code-action/blob/main/docs/capabilities-and-limitations.md)
- [Security guidance](https://github.com/anthropics/claude-code-action/blob/main/docs/security.md)

## Required split architecture

The model and GitHub publisher are separate jobs and principals.

### Model job

- Uses the base-branch workflow and protected review scripts/schema.
- Reads the pull-request head without running its scripts, dependencies, hooks,
  package managers, or executables.
- Holds `contents: read` and `pull-requests: read` only.
- Holds the approved, confidentiality-domain-scoped Anthropic or cloud-provider
  credential.
- Has no GitHub write tool or credential.
- Preferably calls the Claude API or Agent SDK directly with no tools and
  schema-constrained output.
- Performs blind and developer-claim phases and emits a validated report.

Using Claude Code Action for the model job is an experimental fallback. It must
first prove that it runs with a read-only `GITHUB_TOKEN`, emits the report
without posting or pushing, and exposes no write-capable implicit App token.

### Publisher job

- Contains no model and consumes only a size-bounded, schema-valid report.
- Revalidates the report and live head SHA.
- Uses a short-lived, dedicated AWF Reviewer App token with only
  `pull_requests: write` and metadata access.
- Posts only a `COMMENT` review whose `commit_id` is the reviewed head.
- Rejects `APPROVE`, `REQUEST_CHANGES`, unknown fields, stale SHAs, and invalid
  inline locations.
- Runs protected, commit-pinned code; the reviewer App is distinct from the
  developer App.

The publisher credential is write-capable, but it is never model-accessible.
This deterministic mediation—not calling the credential "comment-only"—is the
security boundary.

## Trigger safety

Use `pull_request_target` only for a base-defined workflow that never executes
pull-request content. If the head is checked out, place it in a separate
directory and treat every file as data. Pin every third-party action to a
reviewed commit SHA. Bound input size, turns, duration, and cost.

The `awf/review` status remains a GitHub Actions check. A formal `COMMENT`
review from the publisher lets the common gate verify reviewer identity and
`commit_id` through the pull-request reviews API.

## Qualification evidence

Claude cannot be selected as required until bootstrap records:

1. effective model-job GitHub permissions and a failed write attempt;
2. the complete model-job secret/variable inventory;
3. publisher App installation permissions and drift checks;
4. hashes of protected publisher code and schema;
5. successful schema, invalid-event, invalid-location, and stale-SHA tests;
6. approved vendor/data-egress route and domain-scoped credential;
7. seeded-defect benchmark results;
8. correct behavior for developer-App-authored pull requests.

Until the scripts, workflow, pinned actions, and tests exist, keep Claude
optional or disabled and use a qualified provider such as Copilot for A2.
