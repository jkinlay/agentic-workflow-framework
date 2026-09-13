# External review component

This optional component preserves the public repository's split Claude model/publisher adapter inside one maintained core. It is separate from native critics, the offline twelve-gate evaluator and the scheduled Codex amendment loop. Those mechanisms are not interchangeable qualification evidence.

## Boundaries

The [workflow template](../external-review/workflows/awf-review-claude.yml) runs protected base-branch code. Same-repository, non-draft PRs are eligible only after the owner explicitly sets `AWF_ENABLE_CLAUDE_REVIEW=true`. The model job holds a read-only GitHub token and a scoped model credential. It reads PR files as data, never executes them, reviews the diff before seeing developer claims, and emits a schema-validated report. Malformed output is an error, not no findings.

The separate deterministic publisher holds the dedicated reviewer App token, not a model. It validates schema, configured identity, repository and expected/live head, rejects error reports and posts a `COMMENT` review bound to the reviewed commit. It never approves or merges. GitHub App permissions are broader than this code's behavior: the token is not inherently comment-only. Keep it inaccessible to the model and worker.

The [external gate template](../external-review/workflows/awf-review-gate.yml) reads accepted base policy and requires the configured external-engine identity, current head and qualification flags. Optional engine evidence does not satisfy a different required engine. A valid review may contain blocking findings: gate presence alone is not readiness or merge authority. The public repository retains its accepted Copilot-required policy; adding this component does not change it to Claude.

## Adoption and qualification

1. Preserve existing project gate and `.agentic/project.yaml`; do not overwrite them with source examples. The external policy is distinct from core `PROJECT_CONFIG.yaml`. Review engine identities, task/claim paths, model ID, egress route, token/time limits and owner approvals explicitly.
2. Copy the reviewed workflow template to `.github/workflows/awf-review-claude.yml`. Paths assume an installed `.agentic/external-review` directory; a repository hosting source under `core/` must prepend `core/` to the component paths. Keep the policy path project-owned. Verify pinned third-party action commits before activation.
3. Configure protected instruction paths, required checks and bypass restrictions on the server. Configure the dedicated reviewer App for `pull_requests: write, metadata: read`; scope the model credential to the approved data domain. Keep the enable variable unset in production during qualification.
4. In a separately authorized disposable canary, demonstrate effective model-job read-only permissions, publisher identity/permissions, current-head binding and stale-review rejection. Live negative probes can write or merge if a credential is misconfigured; never run them against valuable work. `verify.py` requires an exact disposable repository confirmation for those probes. Offline tests and recorded operator assertions cannot authenticate live permissions.
5. Record code/schema/workflow hashes, secret inventory, App identity, run URLs, positive/negative results and human approval. Test seeded defects and prompt injection through the actual external engine. Native decision-smoke results do not qualify Claude or Copilot. Historical demonstrations from another layout/configuration are provenance, not current qualification.
6. Enable the adapter only after explicit approval of cost, egress and permissions. Qualification flags are changed by a human from observed evidence, never inferred by bootstrap or copied from another project. Requalify after relevant code, credential, action or policy changes. Preserve current-head review and human merge boundaries.

## Offline verification and limits

Run `python -B .agentic/external-review/claude/verify.py unit --out EXTERNAL_REPORT.json`; source/installed self-tests also discover the component tests. Tests replace network calls. No live service or key is required.

Only the Anthropic Messages-compatible route is supplied. No automatic bridge to the native routing ledger, cross-provider attestation service or publisher signature is claimed. The [component record](../external-review/qualification.json) intentionally starts unqualified.
