# Claude Review Adapter - Implementation Note (30 August 2026)

## Purpose

The addendum disposition kept Claude "unavailable until a split model/publisher
implementation and its negative permission tests exist". This change supplies
that implementation so the only remaining step is to run its demonstration
against the live repository. It does not mark Claude available: the
`review.qualification` flags stay false until a human records the evidence.

This note is ASCII-only by design (see the addendum's encoding note).

## What was added

Scaffold sources under `scaffolds/providers/claude/`, mapped by the
distribution manifest to the same relative targets in a project:

| Target in a project | Role |
|---|---|
| `.github/workflows/awf-review-claude.yml` | Split model/publisher workflow on `pull_request_target`, same-repository branches only |
| `.github/workflows/awf-review-claude-demonstrate.yml` | Manual demonstration: permission-negative tests, App permission read, offline tests, hashes, merged record |
| `.agentic/review/claude/awf_review_common.py` | Stdlib HTTP client, JSON-schema subset validator, minimal YAML reader for policy |
| `.agentic/review/claude/policy.py` | Reads engine, identity, pinned model and paths from protected policy; refuses placeholders |
| `.agentic/review/claude/build_input.py` | Diff via API, changed files as data, task contract, execution report; excludes PR title/body/comments |
| `.agentic/review/claude/run_model.py` | Anthropic Messages API with a forced schema-shaped tool call; blind phase then claims phase; error reports fail closed |
| `.agentic/review/claude/publish.py` | Non-model publisher: revalidates, binds to live head, posts one COMMENT review; APPROVE unreachable |
| `.agentic/review/claude/verify.py` | `model-negative`, `publisher-negative`, `app-permissions` (JWT via openssl), `unit`, `hashes`, `record` |
| `.agentic/review/claude/tests/` | 27 offline tests, including the gate script extracted from the workflow file |

Changed framework files:

- `.github/workflows/awf-review-gate.yml` and its scaffold copy: the gate is
  now engine-aware. It reads `review.required_external_engine` and the
  reviewer identity from the base-branch policy through the API (no checkout),
  keeps the Copilot constant for `copilot`, requires the publisher's
  `awf-review-status` marker for `claude`, and still binds to the live head,
  excludes dismissed reviews, paginates, and re-evaluates on push and review
  events. It also enforces the review policy's rule that every
  `review.qualification` flag must be true for the required engine, failing
  closed otherwise, so a half-configured engine cannot pass. A repository
  without a policy file falls back to Copilot and says so. The gate token
  gains `contents: read` for the policy read.
- `.agentic-workflow/distribution-manifest.yaml`: Claude artifacts added with
  `requires: review.required_external_engine equals claude or
  review.optional_engines contains claude`; the gate entry is unconditional.
- `scaffolds/project/.agentic/project.yaml.template`: `review.claude` block
  (pinned model, base URL, input paths); `scaffolds/project/placeholders.yaml`
  gains `__AWF_CLAUDE_MODEL__`.
- `docs/providers/claude-code-action.md`, `docs/REVIEW_POLICY.md`,
  `docs/BOOTSTRAPPING.md`: status and activation text updated.

No third-party Python package is used anywhere in the adapter. Every GitHub
Action is pinned to a commit SHA resolved from the official repositories on
2026-08-30; a human should re-verify the pins before activation.

## Security properties, and how each is demonstrated

| Property | Mechanism | Evidence |
|---|---|---|
| Model process cannot write to GitHub | Model job holds only `GITHUB_TOKEN` with `contents: read`, `pull-requests: read`; no App token exists in that job | `verify.py model-negative`: refused issue comment, review, ref and check-run creation (403) |
| Model has no tools | `run_model.py` sends one forced tool definition whose only effect is a structured answer | Code review; `test_wrong_tool_is_an_error` |
| Publisher credential is minimal and never model-accessible | Separate job; per-run App token requested with `permission-pull-requests: write`; App installed with Pull requests + Metadata only | `verify.py app-permissions` (exact match), `verify.py publisher-negative` |
| Only COMMENT reviews | `EVENT_COMMENT` constant, asserted before send; `post_review` refuses anything else | `test_post_review_refuses_non_comment_event` |
| Head binding | Report head must equal event head and live head; review `commit_id` is the head; gate compares to live head; marker required | `test_stale_head_posts_nothing`, `GateTests` |
| Error reports never satisfy the gate | Publisher refuses `status: error`; gate requires the marker only the publisher writes | `test_error_report_posts_nothing`, gate marker test |
| PR text cannot steer the blind phase | Title/body/comments excluded; instruction files never read as instructions | `test_build_excludes_pr_text_and_records_limitations` |
| Definition cannot be altered by a PR | `pull_request_target` runs the base-branch workflow; adapter code and schema are protected paths | Policy plus server-side rules (activation item) |
| Forks cannot spend the credential | `head.repo.full_name == github.repository` guard on the model job | Workflow condition |

## Activation on the public repository

The repository is public at `github.com/jkinlay/agentic-workflow-framework`.
To take Claude to "available" for a project (or for this repository itself):

1. Create a GitHub App named for the project, for example "AWF Reviewer":
   permissions Pull requests: Read and write, Metadata: Read; no other
   permission, no webhook. Install it on the repository. Note its App ID and
   generate a private key. The reviewer identity is `<app-slug>[bot]`.
2. Add repository variable `AWF_REVIEWER_APP_ID` and secret
   `AWF_REVIEWER_APP_PRIVATE_KEY`.
3. Add secret `ANTHROPIC_API_KEY` (or set `review.claude.base_url` to an
   approved gateway and use `ANTHROPIC_AUTH_TOKEN`). Keep it scoped to the
   confidentiality domain.
4. Create `.agentic/project.yaml` from the template with
   `review.required_external_engine: copilot` (or `disabled` while testing),
   `optional_engines: [claude]`, `reviewer_identities.claude: <app-slug>[bot]`,
   and `review.claude.model` pinned. Copy the schema to
   `.agentic/schemas/review-report.schema.json` and the Claude artifacts to the
   targets in the manifest.
5. Apply the branch ruleset (required check `awf/review` from GitHub Actions,
   non-author approval, code-owner review, conversation resolution, stale
   approval dismissal, most-recent-push approval) and the protected-path rules
   (push ruleset where the plan allows; otherwise CODEOWNERS plus the
   secret-less A2 constraint from the review policy).
6. Open a same-repository test pull request. Confirm the model and publisher
   jobs run, a COMMENT review by `<app-slug>[bot]` appears with
   `awf-review-status`, and `awf/review` passes. Push another commit; confirm
   `awf/review` fails until the new head is reviewed. Record that run URL.
7. Run "AWF Claude adapter demonstration" from the Actions tab with the pull
   request number and the run URL from step 6. Download `demonstration.json`.
8. Run the seeded-defect benchmark.
9. Set `review.qualification` flags from the evidence and, if every flag is
   true, set `required_external_engine: claude`. Until then the gate fails
   closed for the required engine (or verifies Copilot if Copilot is required
   and its own flags are already true).

## What this change does not do

- It does not run the demonstration; that needs the App, the credentials and
  a live pull request, which only the repository owner can create.
- It does not implement Bedrock, Vertex or Foundry transport; an approved
  gateway that speaks the Anthropic Messages API can be used through
  `review.claude.base_url`.
- It does not change the Copilot adapter's behaviour beyond the shared gate.
- It does not add release provenance, upstream CI or benchmark fixtures, which
  remain open items from the dispositions.

## Local validation performed

`python3 -m py_compile` on every module; 26 offline tests passing with
resource warnings treated as errors; YAML parse of all four workflows, the
manifest and the template; every `uses:` reference matches
`owner/repo@<40-hex>`; an end-to-end dry run of build_input, run_model and
publish with fake GitHub and Anthropic responses; manifest sources exist;
placeholder tokens declared; no absolute machine paths; no credentials.
