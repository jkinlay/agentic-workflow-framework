# Claude Review Adapter

## Status

Implemented as a scaffold in AWF v0.1.0: the split model/publisher workflow,
its scripts, offline tests, and the demonstration workflow that produces the
qualification evidence exist under `scaffolds/providers/claude/`. The adapter
is **unavailable in any project until that demonstration has been run against
a live repository and its record sets the `review.qualification` flags**. A
project may name `claude` as `required_external_engine` only after that.

The adapter does not use Claude Code Action for the model job. Anthropic
documents that the action can push to an open pull-request branch when
permitted, cannot itself submit a formal pull-request review or approval, and
that its documented custom-App setup requests read/write Contents, Issues and
Pull Requests. A model-driven process holding such a credential does not meet
AWF's effective read-only contract. The model job instead calls the Anthropic
Messages API directly with no tools except a forced, schema-shaped answer.

Official documentation:

- [Setup and custom-App permissions](https://github.com/anthropics/claude-code-action/blob/main/docs/setup.md)
- [Capabilities and limitations](https://github.com/anthropics/claude-code-action/blob/main/docs/capabilities-and-limitations.md)
- [Security guidance](https://github.com/anthropics/claude-code-action/blob/main/docs/security.md)

## Split architecture (as implemented)

Workflow: `.github/workflows/awf-review-claude.yml`, run from the base-branch
definition on `pull_request_target`, restricted to same-repository branches so
that a fork can never trigger it with the repository's credential.

### Model job

- Checks out the base branch (trusted adapter code, policy, schema) and the
  pull-request head into `pr/` as data. Nothing under `pr/` is executed: no
  scripts, dependencies, hooks, package managers, or git commands.
- Holds `contents: read` and `pull-requests: read` on `GITHUB_TOKEN` and the
  confidentiality-domain-scoped Anthropic credential (`ANTHROPIC_API_KEY`, or
  `ANTHROPIC_AUTH_TOKEN` for an approved gateway). No GitHub write credential
  exists in the job.
- `policy.py` reads engine, reviewer identity, pinned model, and input paths
  from protected `.agentic/project.yaml` and refuses placeholders.
- `build_input.py` gathers per-file patches through the API, the changed
  files' text from `pr/`, the task contract, and the developer execution
  report. It never includes the pull-request title, body, or comments, and
  records every truncation as a limitation.
- `run_model.py` runs the blind phase, then, when an execution report exists,
  the claims phase as a continuation of the same conversation, so the diff,
  files and task contract remain in the model's context and every claim is
  assessed against them. The model answers only through a forced tool call
  whose input schema mirrors `schemas/review-report.schema.json`; the answer
  is validated against that tool schema before anything is normalised from
  it, and a malformed or self-contradictory answer is an error, never an
  implicit `no_findings`. Requests omit non-default sampling parameters, which
  Claude Opus 5 rejects. The workflow allows 20,000 output tokens and 540
  seconds per phase, within a 30-minute model-job limit, so default adaptive
  thinking and the forced tool answer share adequate headroom without crossing
  the non-streaming request ceiling. Exhaustion without the required tool call
  is a schema-valid `error` report, never `no_findings`. Blind-phase findings
  cannot be withdrawn; the claims phase may only add findings and claim
  assessments. Any failure yields a schema-valid `error` report and a non-zero
  exit, so the publisher never runs and the gate fails closed.
- The report is uploaded as an artifact even when the job fails.

### Publisher job

- Contains no model. Runs `publish.py` from the base checkout with a per-run
  token minted by `actions/create-github-app-token` for the dedicated AWF
  Reviewer App, requested with `permission-pull-requests: write`.
- Refuses the App unless `app-slug[bot]` equals the protected policy identity.
- Revalidates the report against the schema, engine, repository, expected head
  SHA, live head SHA, and configured identity; rejects `error` reports; does
  nothing when a marked review by this identity already covers the head.
- Posts exactly one review with the constant event `COMMENT` and `commit_id`
  equal to the reviewed head. `APPROVE` and `REQUEST_CHANGES` are unreachable
  in code. Findings whose location is not in the diff are placed in the review
  body rather than dropped; a rejected inline position falls back to the body.
- The review body carries `awf-review-status: no_findings|findings` and
  `awf-review-head: <sha>`, which the common gate requires for this engine.

The publisher credential is write-capable, but it is never model-accessible.
That deterministic mediation is the security boundary, not a claim that the
credential is comment-only.

### Gate

`.github/workflows/awf-review-gate.yml` reads `review.required_external_engine`
and `review.reviewer_identities.claude` from the base-branch policy through the
API, requires a non-dismissed review by that identity whose `commit_id` equals
the live head and whose body carries the status marker, and re-evaluates on
every push and review event. It fails closed unless every flag under
`review.qualification` is true and the three baseline flags are present, and
it fails closed when the policy file is absent: no engine is ever assumed.
Only the required engine satisfies the gate; optional engines produce
evidence.

## Qualification evidence

Claude cannot be selected as required until bootstrap records:

1. effective model-job GitHub permissions and refused write attempts
   (`verify.py model-negative`, run inside the model job's permission set);
2. the complete model-job secret/variable inventory (from the workflow file on
   a protected path: at most the Anthropic credential);
3. publisher App installation permissions equal to exactly
   `pull_requests: write, metadata: read` (`verify.py app-permissions`, using
   an App JWT signed with `openssl`), plus refused writes outside pull-request
   reviews with the App token (`verify.py publisher-negative`), and periodic
   drift checks;
4. hashes of protected adapter code and schema (`verify.py hashes`);
5. successful schema, invalid-event, invalid-location, stale-SHA, and gate
   tests (`verify.py unit`, 32 offline tests including the gate script);
6. approved vendor/data-egress route and domain-scoped credential (human);
7. seeded-defect benchmark results (human-run fixtures);
8. correct behavior for developer-App-authored pull requests (the workflow
   does not depend on the pull-request author; confirm on a demonstration PR).

`.github/workflows/awf-review-claude-demonstrate.yml` runs items 1, 3, 4 and 5
against a same-repository demonstration pull request and merges the fragments
into `demonstration.json`. Its `suggested_qualification` block proposes
`effective_permissions_verified`; a human sets `head_binding_verified` only
after observing an `awf/review` failure following a push that made an earlier
review stale, and `instruction_paths_protected` only after server-side path
rules are active.

## Activation checklist

1. Create a dedicated GitHub App ("AWF Reviewer"): permissions Pull requests
   read and write, Metadata read, nothing else; no webhook; install it on the
   repository. Its slug plus `[bot]` is the reviewer identity.
2. Store `vars.AWF_REVIEWER_APP_ID` and `secrets.AWF_REVIEWER_APP_PRIVATE_KEY`
   as repository (or domain-scoped organization) configuration.
3. Store the domain-scoped `secrets.ANTHROPIC_API_KEY` (or an approved gateway
   token) for the model job only.
4. Set `review.reviewer_identities.claude`, `review.claude.model` (pinned id),
   and optionally `review.claude.base_url` in protected `.agentic/project.yaml`;
   add `claude` to `optional_engines` first.
5. Install the managed artifacts listed in the distribution manifest for
   `claude` and re-verify the pinned action SHAs.
6. Open a same-repository demonstration pull request from the developer
   identity; confirm a `COMMENT` review by the reviewer identity appears with
   the status marker, then push a further commit and confirm `awf/review`
   fails until the new head is reviewed.
7. Run the demonstration workflow against that pull request; attach
   `demonstration.json` to the bootstrap report.
8. Run the seeded-defect benchmark.
9. Set the `review.qualification` flags by hand from the evidence; only then
   set `required_external_engine: claude`.

## Trigger safety

`pull_request_target` is used only because the workflow never executes
pull-request content and its definition must come from the base branch. If
the head is checked out, it lives in a separate directory and every file is
data. Every third-party action is pinned to a commit SHA resolved on
2026-08-30 (`actions/checkout` v7.0.1, `actions/upload-artifact` v7.0.1,
`actions/download-artifact` v8.0.1, `actions/create-github-app-token` v3.2.0);
re-verify them before activation. Input size, model tokens, and job duration
are bounded. On a public repository the same-repository condition is what
keeps forks from spending the credential; keep it.

## Known limits

- Cloud-provider routes (Bedrock, Vertex, Foundry) are not implemented in the
  stdlib client; use `review.claude.base_url` with an approved gateway that
  speaks the Anthropic Messages API, or extend `run_model.py` under review.
- The publisher App's `pull_requests: write` may permit more than reviews
  (for example labels). The demonstration records the observed capability;
  the deterministic publisher never exercises it.
- Line placement follows the diff hunks; findings outside the diff are
  reported in the body.
