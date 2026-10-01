# External review component

This optional Claude/publisher component ships inert/unqualified. Nothing here is a precondition for adopting AWF; requirements apply only when enabling it. Native critics/scheduled amendments remain separate.

## Execution and evidence

The [Claude workflow](../external-review/workflows/awf-review-claude.yml) requires enablement, a non-draft same-repository PR, and execution ref/PR target on the protected default branch. Immutable `github.sha` supplies adapter/policy code. PR files are data: never execute commands or install dependencies from `pr/`.

GitHub documents default-branch `pull_request_target`/`workflow_run` context; candidate-context `pull_request_review` is excluded ([event semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)).

Phase one independently reviews candidate data; phase two assesses untrusted developer claims, retaining findings. Head contracts are proposed criteria. Controlled failures retain evidence; runner loss remains failure.

The publisher validates schema, engine, App identity, repository and expected/live head before COMMENT. Its token is not comment-only; isolate it from models/workers. The [gate](../external-review/workflows/awf-review-gate.yml) requires engine, current-head review and qualification, including `environment_secret_boundaries_verified` for secret-bearing engines. Copilot ignores only that inapplicable flag; other checks remain strict. Review presence never resolves blockers or authorizes merge.

Successful Claude completion wakes a job without model/App secrets. It verifies workflow IDs/paths/events, numeric repository, single PR association, current head/default target, protected source ancestry and unchanged workflow bytes. Both model/publisher jobs must complete on the verified [producer attempt](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt). It selects the latest verified gate run, rechecks producer/head/attempt, then reruns only that `pull_request_target` run's failed jobs. Serialization and attempt checks bound duplicates; they are not atomic. Candidate artifacts/status names grant no authority. Its `github.token` Actions-write scope also permits [reruns/cancellation](https://docs.github.com/en/rest/actions/workflow-runs#cancel-a-workflow-run) and [workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event); trusted code narrows usage, not permissions. Missing associations, skipped jobs, changed state or exhausted reruns require coordinator reconciliation. Copilot/manual reviews need a trusted coordinator rerun. Requesting a rerun proves no acceptance.

## Mandatory server-side secret boundaries

Pre-create separate environments:

| Environment | Exclusive secret |
| --- | --- |
| `awf-review-model` | `ANTHROPIC_API_KEY` |
| `awf-review-publisher` | `AWF_REVIEWER_APP_PRIVATE_KEY` |

Select deployment branches matching only the exact protected default branch: no agent branches, broad patterns, merge refs or tags; “Protected branches only” admits everything if protections are absent. Rules match the run's `GITHUB_REF`, not arbitrary checkouts; retain PR-target guards. Environment secrets arrive after rules pass. See [environment rules](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments).

Keep these secrets out of repository/organization scope; agent-controlled branches are secret-less and only publisher/wake tokens write. Protect workflow/adapter/policy paths, default writes and environment administration, including bypasses. Verify required-check provenance, not name alone. An unknown environment can auto-create without protection; verify settings and plan support, and remain disabled if unavailable ([environment setup](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)).

## Adoption and qualification

Installation/mapping/PR preparation permits missing rules and empty CI with warnings; CONFIGURED/ACTIVE adoption never enables this adapter. Live enablement needs configured required CI, actual trusted merge owners, observed default-branch rules and qualification. Empty checks establish no `awf/review` provenance; operators enforce prerequisites first ([adoption](20-NEW-PROJECT-SETUP.md)).

1. Preserve the accepted engine and `.agentic/project.yaml`. Merge reviewed [project.example.yaml](../external-review/project.example.yaml) fields; this inert policy is separate from core `PROJECT_CONFIG.yaml`. Resolve identity/model placeholders from approved values. All example qualification flags remain false.
2. Review/copy templates into `.github/workflows/`, protecting code/dependencies. Prepend `core/` to paths for source-hosting layouts. Verify action pins. Configure the App for `pull_requests: write, metadata: read`; approve model, egress, paths and cost. Keep `AWF_ENABLE_CLAUDE_REVIEW` unset during preparation.
3. In an authorized disposable canary, demonstrate environment separation, agent/unprotected-target rejection, model read-only permissions, publisher identity, head/stale-review binding and automatic gate wake/check provenance. Negative probes can mutate over-permissioned targets. Require exact repository confirmation and `--canary-marker ABSOLUTE_EXTERNAL_JSON --canary-marker-sha256 PINNED_SHA256`, independently provisioned outside checkout. The marker binds repository/numeric ID, PR/head, expiry within 24 hours, `disposable: true` and allowed probes: an operator assertion, not authentication.
4. Retain inventory without secret values, environment/branch settings, code/schema/workflow hashes, App identity, run URLs and probe evidence. Test seeded defects/injection through the actual engine. Offline/historical/native smoke evidence cannot qualify it.
5. Owners observe required review/CI checks and bypass restrictions, set qualification flags from evidence and approve activation. Requalify material code/policy/action/credential/environment changes; preserve findings and human merge requirements.

## History-verifying checks

A required check declared `verifies_history: true` in `validation.required_ci_checks[]` must record `checkout_depth: full` (`actions/checkout` with `fetch-depth: 0`) or `provenance` fails; ship it with the long-path step in one governance PR. The shipped `awf-review-claude.yml` checks out shallowly and is never declared `verifies_history`.

## Limits and offline verification

Remote model endpoints require reviewed HTTPS without userinfo, query or fragment. Explicit `--allow-loopback-http` accepts numeric loopback fixtures only, with synthetic credentials; parent/child enforce this and bypass proxies for loopback.

UTF-8 input/request/diff caps are 1,000,000/1,500,000/400,000 bytes; oversize fails without silent omissions. Output is capped at 8,000 tokens. Two phases each get one 300-second supervised attempt plus two-second cleanup (604 seconds, excluding OS startup/report I/O). Thirty-minute model and ten/five-minute publisher/gate jobs are outer bounds. Local termination does not prove remote cancellation. For transient 429/5xx or unknown outcomes the coordinator reconciles provider outcome/usage before an authorized rerun; no automatic retry.

Run `python -B .agentic/external-review/claude/verify.py unit --out EXTERNAL_REPORT.json`; fake-network tests need no live key. Only the Anthropic Messages-compatible route ships; the [record](../external-review/qualification.json) remains unqualified.
