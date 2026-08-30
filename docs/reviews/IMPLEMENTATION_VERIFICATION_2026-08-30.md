# Implementation Verification - 30 August 2026

## Scope

This report verifies the repository changes made in response to the original
red-team review and the reviewer-adapter addendum, including the Claude adapter
merged to `main` at `24ecd82`. The merge tree is byte-identical to
`claude-review-adapter` at `fef9f27`. This report does not rewrite either review
input and does not claim that local scaffolds are active server-side controls.

## Reviewer adapter verification

| Property | Repository evidence | Result |
|---|---|---|
| Required reviewer identity | Engine-aware gate retains the Copilot constant and reads a protected App identity for Claude | Implemented |
| Current head binding | Gate fetches live PR and matches review `commit_id` to `head.sha` | Implemented |
| Re-evaluation | `synchronize` plus submitted/edited/dismissed review triggers | Implemented |
| Base-defined gate | API-only `pull_request_target`; no checkout or PR execution | Implemented |
| Gate token | `contents: read` and `pull-requests: read` only | Implemented |
| Review pagination | Up to ten pages of 100 reviews | Implemented |
| Expected status source | Policy says GitHub Actions | Configuration pending remote |
| Protected workflow | `.github/**` is in scaffold, manifest, and CODEOWNERS floor | Server enforcement pending remote |
| Copilot automatic review | Adapter requires `Review new pushes` | Activation pending remote/licence |
| Claude review | Split model/publisher scaffold, deterministic COMMENT publisher, tests, and demonstration workflow | Implemented; qualification pending and remediation required before activation |

## Claude adapter verification

| Property | Verification result | Result |
|---|---|---|
| Merged scope | The 20-file merge at `24ecd82` is tree-identical to `fef9f27` | Pass |
| Credential separation | Static workflow inspection shows the model job has read-only GitHub permissions and the Anthropic credential; the publisher has no model and mints a reviewer-App token | Implemented; live permission evidence pending |
| Untrusted head handling | Base-branch adapter code is executed; the same-repository PR head is checked out under `pr/` and read as data | Implemented |
| Structured model result | Messages API calls force one schema-shaped tool and final reports are validated before publication | Implemented |
| Deterministic publication | Publisher revalidates repository, engine, identity, event head and live head, rejects `error`, and can emit only a `COMMENT` review | Implemented |
| Gate head binding | Gate requires the configured identity, non-dismissed review, current `commit_id`, and Claude status marker | Implemented and covered by offline tests |
| Qualification enforcement | The gate checks three named flags, not every flag present under `review.qualification`; an added false `seeded_benchmark_verified` flag still produced exit 0 | Partial; fail-closed contract not met for future flags |
| Missing-policy behavior | HTTP 404 for `.agentic/project.yaml` defaults to Copilot and can pass on a matching review without any explicit qualification evidence | Not fail-closed; do not make the check required in this state |
| Claims phase | The second Messages API call receives the developer report and a summary of blind findings, but not the diff or file contents. Because the call is stateless, it cannot verify claims against the evidence it is instructed to use | Not conformant; remediate before activation |
| Offline tests | 27 tests were discovered on Windows: 24 passed, two errored because the test fixture unconditionally creates a privileged Unix-style symlink, and one OpenSSL-dependent test skipped; module compilation passed | Partial; Linux result not independently reproduced and suite is not Windows-portable |
| Publisher permission floor | The App permission check requires exactly `pull_requests: write, metadata: read`; documentation correctly notes that GitHub may still allow operations such as labels | Designed; live negative demonstration pending |
| Live qualification | No App, secrets, same-repository demonstration, stale-head run, protected-path proof, or seeded benchmark is present | Pending |

The first six rows justify describing the adapter as implemented. The remaining
rows prevent describing it as qualified, available, or ready to be a required
engine.

## Structural validation of `main`

| Check | Result |
|---|---|
| Root/scaffold review-gate hash parity | Pass |
| Placeholder inventory | Pass: 22 used, 22 declared, none missing or unused |
| Distribution-manifest sources | Pass: 25 file sources, none missing |
| Action references | Pass: all 16 `uses:` references are pinned to 40-hex commits |
| Action tag resolution | Pass: official repository tags resolve to checkout v7.0.1 `3d3c42e...`, upload-artifact v7.0.1 `043fb46...`, download-artifact v8.0.1 `3e5f45b...`, and create-github-app-token v3.2.0 `bcd2ba4...` |
| Relative Markdown links | Pass: no broken repository-relative links found |
| Tracked UTF-8 files | Pass |
| Local absolute-path scan | Pass: no hits outside the preserved reviewer inputs |
| Credential-pattern scan | Pass: no credential values or private-key blocks found |
| JSON syntax | Pass |
| `git diff --check` before documentation edits | Pass |

No YAML parser or Actions linter is installed on the verification host, so the
previously reported YAML parse result was not independently reproduced in this
pass. The embedded gate script was executed directly by its tests and by the
additional false-flag probe above.

## Original finding status

| Finding group | Status after processing |
|---|---|
| C1 protected control paths | Non-empty protected floor and CODEOWNERS/ruleset contract implemented; server rules pending |
| C2 push-time secret exposure | Secret-less A2 requirement implemented in policy; CI configuration pending |
| H1 authority laundering | Protected project policy and human-only readiness/plan transitions implemented |
| H2 reviewer independence | Procedural contract and Copilot current-head gate implemented; live qualification pending |
| H3 autonomy/state consistency | A1 default and one actor-labelled state machine implemented |
| H4 provenance/integrity | Generated lock declared; remote/tag/hash automation still blocks downstream upgrade |
| H5 confidentiality domains | Required separation documented; automated pre-flight still pending |
| H6 instruction mechanics | Override/config inventory and protection documented; instruction-budget CI pending |
| H7 research evidence | Clean QA and methodology requirements documented; evidence schemas/harnesses pending |
| H8 identity/incidents | Minimum contracts implemented; project principals and external audit configuration pending |
| M/L distribution and hygiene | Placeholder inventory, delimited guidance, review schema and link/path checks implemented; full schemas, licence decision, upstream CI and benchmarks pending |

## Corrections to the addendum

The required check's expected source is GitHub Actions, not the reviewer App.
Two-phase input control is not claimed for Copilot. Claude publication is
accepted only through deterministic mediation. Executable Claude code now
exists, but the adapter remains unavailable until the verification defects
above are remediated and live permission, protection, stale-head, and benchmark
evidence exists.

## Validation boundary

Local validation checks file/link consistency, whitespace, JSON syntax,
placeholder inventory, source/target synchronization, and prohibited content.
It cannot prove GitHub rulesets, App permissions, provider availability, review
identity, stale-SHA behavior, or secret isolation without a remote and live
test pull request. Those items remain explicit A2 activation blockers.
