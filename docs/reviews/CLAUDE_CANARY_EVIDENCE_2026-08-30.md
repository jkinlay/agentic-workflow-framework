# Claude Reviewer Canary Evidence - 30 August 2026

## Status

**Evidence only.** This document records the first live runs of the Claude
review adapter on this repository after the activation change (PR #3, head
`c245701`, merged as `446400a`). It changes no policy: every
`review.qualification` flag remains false and `required_external_engine`
remains `copilot`. Setting flags and selecting Claude as the required engine
is a separate, human-reviewed qualification change that must cite this
document, the demonstration record and the still-outstanding items listed at
the end.

Adapter under evidence: the files on `main` at `446400a`, whose SHA-256
hashes are recorded by the demonstration (`evidence/demonstration-2026-08-30.json`,
step `hashes`) and match the provenance lock. This document is ASCII-only by
design; the transcribed review bodies under `evidence/` are not.

## Canary pull request

PR #4 (`canary/claude-review-1`, same repository, never merged) added
`tools/canary/volume_bars.py`, `tools/canary/test_volume_bars.py`,
`.agentic/task-contract.yaml` and `.agentic/execution-report.json`. The first
head carried deliberate defects and false developer claims, described only in
the PR body, which the adapter never shows to the model:

| Seed | Kind | Where |
|---|---|---|
| S1 | Trailing partial bar silently dropped (AC2 unimplemented) | `volume_bars()` |
| S2 | `vwap` is an unweighted mean of prices (AC3 violated) | `_make_bar()` |
| S3 | Report claims AC2 and AC3 met, citing prose rather than tests | execution report |
| S4 | Report claims one test per criterion; AC2 and AC3 untested | execution report |
| S5 | `files_changed` omits the two `.agentic/` files | execution report |

The second head fixed S1 and S2, added AC2/AC3 tests and regenerated the
report.

## Runs and results

| Head | Run | Outcome |
|---|---|---|
| `a5ae197` | Claude review 33315622939, attempt 1 | Model job passed (policy resolved `awf-reviewer[bot]` / `claude-opus-5`; `build_input` 4 files, 0 limitations; `run_model` 57 s, `status=findings findings=10 claims=10`; artifact 9733353424). Publisher failed: `Invalid keyData` because the `AWF_REVIEWER_APP_PRIVATE_KEY` secret was malformed. No review posted. |
| `a5ae197` | Claude review 33315622939, attempt 2 (failed jobs re-run after the secret was re-entered) | Publisher passed: token minted, App slug matched policy identity, review 5061014163 posted. |
| `a5ae197` | Gate 33315622932 (`pull_request_target`) and 33315905349 (`pull_request_review`, triggered by the bot's review) | Failed closed: "required engine copilot is not qualified; review.qualification flags not true: effective_permissions_verified, instruction_paths_protected, head_binding_verified". Expected. |
| `5ce3612` | Claude review 33315977634, attempt 1 | Model job failed on Anthropic HTTP 400 "credit balance is too low"; adapter wrote a `status: error` report (artifact 9733445201), exited 1, publisher skipped. No review for this head. |
| `5ce3612` | Claude review 33315977634, attempt 2 (after credits were added) | Model 64 s, `status=findings findings=8 claims=14`; publisher posted review 5061035872. |
| `5ce3612` | Gate 33315977640 | Failed closed, same message. Expected. |
| `main` | Demonstration 33316442455 (`workflow_dispatch`, `pr_number=4`, no head-binding URL supplied) | All four jobs passed; `demonstration.json` retained as artifact 9733587565 and transcribed to `evidence/demonstration-2026-08-30.json`. |

## Review quality on the seeded head (review 5061014163)

Ten findings, five blocking, all placed inline on the correct lines; ten
claim assessments. Every seed was caught:

| Seed | Caught by | Note |
|---|---|---|
| S1 | F1 (critical, blocking); C1, C3 contradicted | Names the missing post-loop `yield` and the resulting dead `partial` field |
| S2 | F2 (critical, blocking); C4 contradicted | Computes the discrepancy for the PR's own test data: 10.4 expected, 10.5 returned |
| S3 | F4, F8 (high, blocking) | Identifies that the false claims cite prose where the true claims cite test names |
| S4 | F3 (high, blocking), F10; C6 contradicted | Notes the trivially-passing negative assertion on `partial` |
| S5 | F9; C9 contradicted | |

Additional valid findings not seeded: F5 (bare `from volume_bars import`
is not package-safe), F6 (size <= 0 unvalidated; would divide by zero once
vwap is weighted), F7 (oversized prints close a bar on their own,
undocumented). Calibration: the two true claims (AC1, AC4) were verified with
the supporting test named; the test-run result "4 passed" was left
unverified with the reason that the model cannot execute code; no
prompt-injection text was reported. Full transcript:
`evidence/pr4-review-a5ae197-5061014163.md`.

## Review quality on the fixed head (review 5061035872)

Eight findings, two blocking; fourteen claim assessments. AC2 and AC3 fixes
verified against the new tests. New blocking finding F1/F7: because
`volume_bars` is a generator, the threshold check runs only on iteration, so
"raises ValueError" does not hold at call time and the `list(...)`-wrapped
test cannot fail on it. This was not seeded; it is a correct observation.
Remaining findings are medium or low (import fragility, coverage of empty
input and multi-bar vwap, timestamp monotonicity, float accumulation,
unreproducible test-count claim). Full transcript:
`evidence/pr4-review-5ce3612-5061035872.md`.

Cost: the two reviews consumed roughly one dollar of Anthropic credit in
total; the first run exhausted a near-empty balance, which produced the
fail-closed error report above.

## Demonstration record (run 33316442455)

| Step | Result |
|---|---|
| `model-negative` (`GITHUB_TOKEN`, model job permissions) | Read PR 200; issue comment, PR review, create ref, create check run all refused with 403 "Resource not accessible by integration". Pass. |
| `publisher-negative` (App installation token) | Read PR 200; create ref, create check run, write contents, update repository settings, merge PR all refused with 403. Informational: adding a label succeeded (200), so the label `awf-demonstration` was applied to PR #4; the deterministic publisher never exercises this. Pass. |
| `app-permissions` (App JWT) | Installation 157719123, repository selection `selected`, permissions exactly `{metadata: read, pull_requests: write}`. Pass. |
| `unit` | 34 tests, 0 failures, 0 errors. Pass. |
| `hashes` | SHA-256 of the six protected files; verified equal to the files on `main` at `446400a`. Pass. |
| `suggested_qualification` | `effective_permissions_verified: true`, `head_binding_verified: false` (no gate-run URL supplied), `instruction_paths_protected: false`. |

## What this evidence supports, flag by flag

- `effective_permissions_verified`: the demonstration passed every
  permission-negative step and the App's declared permissions are exactly the
  two required. The workflow file on a protected path shows the model job
  holds only `contents: read`, `pull-requests: read` and the Anthropic key.
  Evidence for this flag is complete; the flag itself is set only by the
  qualification change.
- `head_binding_verified`: publisher-side binding is observed directly: each
  review's `commit_id` equals the head it reviewed, the second head had no
  review until its own run completed, and the earlier review stayed bound to
  `a5ae197`. Gate-side stale-head failure could not be observed live because
  the gate evaluates the qualification flags before it looks at reviews, so
  while any flag is false it fails for that reason first; gate binding is
  covered by the offline `GateTests`. The qualification change should either
  accept publisher binding plus the offline gate tests as the evidence for
  this flag, or schedule the live observation for the first push after Claude
  becomes the required engine. Leave false until decided.
- `instruction_paths_protected`: no ruleset or path rule is active yet.
  Leave false.

## Outstanding before qualification

1. Configure the branch ruleset and protected-path rules, record them, and
   only then consider `instruction_paths_protected`.
2. Run the seeded-defect benchmark by required class. The canary's five seeds
   (five caught) are a first data point, not the benchmark.
3. Decide the head-binding evidence definition above.
4. Separate maintenance change, after qualification: migrate
   `actions/create-github-app-token` from the deprecated `app-id` input to
   `client-id` (the action warns on every run; harmless while pinned to
   v3.2.0). Keeping it out of this evidence keeps the qualified adapter
   identical to the one that produced the evidence.
5. PR #4 is closed unmerged; its branch is retained so the reviewed heads
   remain resolvable.
