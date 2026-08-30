# Red Team Review -- Addendum: External Reviewer Adapter and Disposition Responses

**Date:** 30 August 2026 (revised the same day after the owner's disposition and the owner's review of the first draft of this addendum)
**Companion to:** `docs/reviews/RED_TEAM_REVIEW_2026-08-30.md` (the original red-team report, now restored to its first-delivered content and treated as immutable) and `docs/RED_TEAM_DISPOSITION_2026-08-30.md` (the owner's disposition).
**Supersedes:** the "Section 7" and "Section 8" text that earlier revisions appended to the original report. That text has been removed from the original and lives here in corrected form.
**Encoding:** this file is deliberately plain ASCII (no em dashes, section signs or curly quotes) because the owner's tooling rendered the UTF-8 punctuation in the original report as mojibake ("a-circumflex-euro-em-dash" for an em dash, "A-circumflex-section" for a section sign). The original report is valid UTF-8 without a byte-order mark; readers must decode it as UTF-8 (for example `open(path, encoding="utf-8")` in Python or `Get-Content -Encoding utf8` in PowerShell). Nothing in it is corrupted.

---

## 1. Response to the owner's review of the first addendum draft

The owner's review of the first draft raised eight concerns and three observations about the implemented Copilot gate. Each is answered below; the resulting corrections are applied in Sections 2 to 6.

| Concern raised | Disposition here | What changed |
|---|---|---|
| The response does not review the implementation; much of the report describes the original 15-file draft, not the current 28-file repository | Agreed. The original report is a review of the draft as it stood and is preserved unchanged for that reason. A verification report of the implemented change is a separate deliverable, not an edit to the original. | Original restored to first-delivered content. Verification report to follow as its own document. |
| Claude is not demonstrably read-only: a custom App with Pull requests: write is broader than comment-only, and Anthropic's custom-App setup guidance asks for read/write on Contents, Issues and Pull requests, so the proposed workflow may not run as written | Agreed. The design in which a model-driven process holds any write-capable GitHub credential is withdrawn. | Replaced by a split architecture in which the model process holds only a read-only `GITHUB_TOKEN` and a fixed, non-model publisher holds the only write credential (Section 3). |
| The Claude gate is unspecified: the AWF gate checks the pull-request reviews endpoint, which works for Copilot's COMMENTED review, but the action cannot submit a formal review | Agreed. | The publisher posts a formal pull-request review with `event: COMMENT` under the reviewer App identity, bound to the head SHA by `commit_id`, so the existing reviews-endpoint gate verifies both engines identically (Section 5). |
| The outage override is inaccurate: a code-owner approval does not override a failing required status check; GitHub requires an authorised ruleset bypass actor | Agreed; the earlier text was wrong. | The exception path is a named ruleset bypass actor whose bypass is recorded by GitHub and reviewed after the fact; never disabling the check and never a plain approval (Section 5). |
| Internal contradiction: Section 7.1 adopted `required_external_engine` while 7.5 had the gate check every engine in `review.engines` | Agreed. | The gate verifies only the required engine; optional engines produce evidence and never block (Sections 2 and 5). |
| Independence is defined too rigidly: "different vendor lineage" cannot be guaranteed for Copilot because GitHub does not promise which model backs a review | Agreed. | Independence is defined by procedural criteria; model and vendor diversity is preferred and recorded when known, not required (Section 2). |
| "Blind to developer rationale" goes too far; the developer report can help locate risk and should be supplied as untrusted claims to verify | Qualified. Anchoring is a real effect, and so is the value of the claims. | Two-phase review: a blind pass on diff, task contract and acceptance criteria, then a second pass in which the developer's report is presented as untrusted claims that the reviewer must mark verified, unverified or contradicted, item by item (Section 2). |
| Reviewer activation before A1 is unnecessary; A1 has no push or PR | Agreed. | External review and repository rules are mandatory before the first A2 pull request. They may be configured earlier at no cost, but they gate A2, not A1 (Section 6). |
| Copilot gate: re-evaluates on `synchronize` and fetches the live head SHA; workflow protection declared but not yet enforced (no remote); `AWF_REVIEWER_LOGIN` can be set to any account | Noted. The first two are as they should be given a local repository. The third should be hardened. | Reviewer identity should be an adapter-owned constant (`copilot-pull-request-reviewer[bot]` for Copilot; the reviewer App slug for Claude, recorded in the protected `.agentic/project.yaml`), not a free-form repository variable (Section 5). |
| Split the document into immutable original, owner disposition, and implementation verification report | Agreed and done. | This addendum is the design and response document; the verification report is separate. |
| Mojibake in the attachment | The original is valid UTF-8; the rendering decoded it as a single-byte code page. | This addendum is ASCII-only. |

The owner's review also confirmed that the Codex-specific factual claims in the original report (override-file precedence, root-to-working-directory instruction chain, the 32 KiB default budget, and the description of Codex code review) are accurate. Those sections of the original therefore stand.

---

## 2. Reviewer contract (corrected)

A human approves every pull request. The external engine reviews every pull request adversarially before the human sees it, so that the human's attention is focused by findings; the human merge authority still inspects the diff to a depth set by the change's risk class: a full read for any change touching protected, gate or research paths and for any pull request carrying a blocker finding; findings-first with a sampled read otherwise; the mode applied is recorded in the approval comment.

**Independence** is a property of how the review runs, not of which model runs it. The required engine must satisfy all of the following:

1. Fresh context: a new job or task from a clean checkout of the pull-request head; never a subagent or child of the task that prompted the developer.
2. Separate execution: it does not share a process, session, sandbox or working tree with the developer agent.
3. Separate identity: its output is attributable to an identity that is neither the developer agent's nor any human's.
4. Effective read-only permissions: the process that runs the model holds no GitHub credential capable of writing. This is verified from effective permissions, not from workflow prose (Section 3.4).
5. Immutable head binding: every finding and every "no findings" statement is bound to a specific head SHA, and the gate compares that SHA to the live head.
6. Untrusted inputs: diff, pull-request title and body, comments, and any instruction files on the head branch are data; the engine is told never to follow instructions found in them.
7. Structured, validated output: findings conform to `schemas/review-report.schema.json`; unknown fields are rejected before anything is published.
8. Positive output: "no findings" is an explicit statement for a specific head SHA, never silence.

Model and vendor diversity relative to the developer runtime is preferred and is recorded in the review report whenever it is actually known (for Copilot it is not known and is recorded as such). It is not a qualification criterion, because it cannot be verified for every engine.

**Two-phase input.** Phase one is blind: the engine receives the diff, the task contract and the acceptance criteria, and produces its findings. Phase two supplies the developer's execution report labelled as untrusted claims; the engine must mark each claim verified, unverified or contradicted, with evidence, and may add findings but may not withdraw phase-one findings on the strength of a claim alone. Both phases are recorded in the report.

**Selection** lives in `.agentic/project.yaml`, a protected path, and is set by the human owner at bootstrap:

```yaml
review:
  required_external_engine: copilot        # exactly one; the only engine the gate verifies
  optional_engines: [claude]               # evidence only; never block merge
  reviewer_identities:                     # adapter-owned, not free-form
    copilot: "copilot-pull-request-reviewer[bot]"
    claude: "<reviewer-app-slug>[bot]"     # the project's custom AWF Reviewer App
  qualification:                           # all must be true for an engine to be selectable as required
    effective_permissions_verified: false  # Section 3.4 demonstration recorded in the lock
    instruction_paths_protected: false     # server-side path protection covers the engine's instruction files
    head_binding_verified: false           # stale-SHA fixture fails the gate
  required_check: awf/review
  fail_closed: true
  outage_exception: ruleset-bypass-actor   # Section 5
```

An engine whose qualification flags are not all true cannot be named as `required_external_engine`, whatever credentials exist.

---

## 3. Claude adapter: a demonstrably read-only design

### 3.1 Why the earlier designs fail the standard

The first design relied on the workflow `permissions:` block and omitted `github_token`. That block scopes only `GITHUB_TOKEN`; when `github_token` is omitted the action authenticates as the official Claude GitHub App, whose installed permission set includes Contents, Workflows, Actions, Repository hooks and Pull requests read-and-write and cannot be installed as a subset. The action's capabilities document states plainly that when triggered on an open pull request Claude "always pushes directly to the existing PR branch" and that it "cannot submit formal GitHub PR reviews" or "approve pull requests". The second design substituted a custom App with Pull requests: write; the owner's review correctly notes that this is broader than comment-only and that Anthropic's custom-App guidance asks for read/write on Contents, Issues and Pull requests, so the action may not run with less. In both designs a model-driven process held a write-capable credential, which is the thing the standard forbids.

### 3.2 Split architecture

Separate the two things that were combined: the process that runs the model, and the process that writes to GitHub.

**Model job.** Runs Claude against the pull-request content and emits a review report as a file. It holds exactly one GitHub credential, the job's `GITHUB_TOKEN`, with `contents: read` and `pull-requests: read`, which GitHub enforces. It holds the Anthropic credential (an API key, an OIDC-federated short-lived token, or a cloud-provider route) and nothing else. It has no GitHub tool that can write, because no write is possible with the credential it holds. Its output is validated against `schemas/review-report.schema.json` before it leaves the job.

**Publisher job.** Fixed code with no model in the loop. It downloads the validated report, validates it again, and posts a formal pull-request review with `event: COMMENT` (an APPROVE or REQUEST_CHANGES event is asserted impossible in the code), with inline comments carrying `commit_id` equal to the reviewed head SHA. It holds the only write credential: a per-run installation token for a custom "AWF Reviewer" GitHub App whose installation has Pull requests: write and Metadata: read and nothing else. Because the publisher is deterministic and lives on a protected path, the write permission is exercised only by reviewed code.

**Trigger and definition.** The workflow runs on `pull_request_target`, so its definition always comes from the base branch and a pull request cannot alter it; the pull-request head is checked out into a subdirectory and is only ever read: no dependency installation, no test execution, no script from the pull request is run. This is the documented safe use of `pull_request_target`; the footgun is executing checked-out content, which this workflow never does. `synchronize` re-runs it on every push, so a stale review can never satisfy a new head.

**Two variants for the model job.**

Variant A (recommended): a small script calls the Claude API or the Claude Agent SDK directly with a fixed prompt, the diff, the changed files' full text, the task contract and the acceptance criteria, and requires JSON conforming to the schema. The model has no tools at all. Read-only is true by construction and visible in the workflow file.

Variant B: `anthropics/claude-code-action` in automation mode with `github_token: ${{ secrets.GITHUB_TOKEN }}` (read-only by the job's permissions), a vendored review skill, `--allowedTools` limited to read tools, and a prompt that writes the report to a file rather than posting. This keeps Claude Code's repository navigation. It must be demonstrated at bootstrap, because the action's documentation directs `github_token` to custom Apps and may expect write permissions; if the action refuses to run read-only, use Variant A.

### 3.3 Workflow sketch

```yaml
# .github/workflows/awf-review-claude.yml
# Definition is taken from the DEFAULT branch (pull_request_target). PR content is checked out to ./pr and only read.
name: awf-review-claude
on:
  pull_request_target:
    types: [opened, synchronize, ready_for_review, reopened]
permissions: {}
concurrency:
  group: awf-review-claude-${{ github.event.pull_request.number }}
  cancel-in-progress: true
jobs:
  model:
    if: github.event.pull_request.draft == false
    runs-on: ubuntu-latest
    timeout-minutes: 20
    permissions:
      contents: read          # the only GitHub credential in this job; GitHub prints the effective set in the job log
      pull-requests: read
    steps:
      - uses: actions/checkout@<commit-sha>               # trusted: base-branch scripts and schema
        with:
          persist-credentials: false
      - uses: actions/checkout@<commit-sha>               # untrusted: PR head, read-only, never executed
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          path: pr
          fetch-depth: 1
          persist-credentials: false
      - name: Build review input (diff, changed files, task contract, acceptance criteria)
        run: python .awf/review/build_input.py --base ${{ github.event.pull_request.base.sha }} --head ${{ github.event.pull_request.head.sha }} --pr-dir pr --out input.json
      - name: Run model (Variant A; no GitHub write credential exists in this job)
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}   # or OIDC federation / cloud-provider route
        run: python .awf/review/run_model.py --in input.json --schema schemas/review-report.schema.json --model <pinned model id> --out report.json
      - uses: actions/upload-artifact@<commit-sha>
        with:
          name: awf-review-report
          path: report.json
  publish:
    needs: model
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read          # GITHUB_TOKEN is not used for any write
    steps:
      - uses: actions/checkout@<commit-sha>
        with:
          persist-credentials: false
      - uses: actions/download-artifact@<commit-sha>
        with:
          name: awf-review-report
      - id: reviewer-app
        uses: actions/create-github-app-token@<commit-sha>   # custom AWF Reviewer App: Pull requests: write, Metadata: read, nothing else
        with:
          app-id: ${{ vars.AWF_REVIEWER_APP_ID }}
          private-key: ${{ secrets.AWF_REVIEWER_APP_PRIVATE_KEY }}
          permission-pull-requests: write
      - name: Publish validated findings as a COMMENT review bound to the head SHA
        env:
          GH_TOKEN: ${{ steps.reviewer-app.outputs.token }}
        run: python .awf/review/publish.py --report report.json --schema schemas/review-report.schema.json --pr ${{ github.event.pull_request.number }} --commit ${{ github.event.pull_request.head.sha }} --event COMMENT
```

Notes. Every action is pinned by commit SHA; mutable tags are a supply-chain path into the reviewer. The scripts under `.awf/review/` and the schema are protected paths. No `id-token: write` is needed unless Anthropic OIDC federation is used, in which case it is added to the model job only. The Anthropic credential is available to this base-defined workflow only; other workflows that run from head branches should be secret-less for agent branches (original report, C2). Hold Anthropic credentials per confidentiality domain. Draft pull requests are skipped. If Variant B is used, `allowed_bots` must name the developer agent's App login or the action never runs on agent-authored pull requests.

### 3.4 What "demonstrably read-only" means, and how it is recorded

The adapter's `effective_permissions_verified` flag is set only when all of the following are recorded in the bootstrap report and the lock:

1. The model job's effective `GITHUB_TOKEN` permissions as printed by GitHub in the job log (`Contents: read, PullRequests: read`), with the run URL.
2. An inventory of every secret and variable the model job references, taken from the workflow file on the protected path: at most the Anthropic credential.
3. A negative test executed in the model job during bootstrap: an attempted write (for example posting an issue comment through the API with the job's token) must fail with 403, and the response is recorded.
4. The reviewer App's installation permissions read through the GitHub API with the App's JWT, equal to `{ pull_requests: write, metadata: read }` and nothing else, recorded in the lock and re-checked periodically for drift.
5. The hash of the publisher script and of the schema, recorded in the lock; the publisher refuses reports with unknown fields and asserts `event == COMMENT`.
6. A stale-SHA fixture: a review posted for a previous head must fail `awf/review` for the current head.

Until these are recorded the adapter is not selectable as the required engine. This is the standard the disposition set, made operational.

### 3.5 Residual risks

Prompt injection reaches the model through the diff and instruction files; its consequences are bounded because the model cannot write anywhere, the report is schema-validated, and the publisher posts only schema fields. Injection can still produce misleading findings or a false "no findings", which is why the human reads the diff at the depth the risk class requires and why the seeded-defect benchmark includes injection fixtures. Cost abuse by a pull request that inflates the diff is bounded by input limits in `build_input.py` and by the job timeout. Vendor and data-egress approval for Anthropic (or a cloud-provider route) is a per-domain decision recorded in `project.yaml`; nothing in this design changes that.

---

## 4. Copilot adapter

Unchanged from the original addendum in substance: a branch ruleset requests Copilot review automatically with "Review new pushes"; AWF-managed instruction files prime it; Copilot only ever leaves a COMMENT review, so conversation resolution plus the presence gate make it binding; its instruction files are read from the head branch, so path protection is mandatory; its model is unknown and is recorded as such. It introduces no repository secret and needs no workflow for the review itself, which is why it is often the safer first integration on GitHub-hosted repositories. Reviewer identity is the adapter-owned constant `copilot-pull-request-reviewer[bot]`, not a free-form variable.

---

## 5. Gate corrections

The `awf/review` check verifies only the `required_external_engine`. It reads the pull-request reviews endpoint and passes when a review by the configured reviewer identity exists whose `commit_id` equals the live head SHA. Copilot satisfies this with its native COMMENTED review; Claude satisfies it through the publisher's COMMENT review (Section 3.2), so one verifier serves both. Optional engines' reviews are linked from the evidence map and never block.

The gate workflow runs from the base branch definition (`pull_request_target` and `pull_request_review`), never checks out or executes pull-request content, holds `pull-requests: read` and `checks: write` only, and re-evaluates on every push and review event. Its own definition is on a protected path.

The outage exception: when the required engine is unavailable and a merge cannot wait, the only path is a ruleset bypass by a named bypass actor configured on the ruleset. GitHub records the bypass; the project's incident log records the reason; the check is never disabled and a code-owner approval never substitutes for it. The bypass list must not include any agent identity.

---

## 6. Recommendation given the stated preference for Claude

The owner of the review request prefers Claude to Copilot. The corrected framework supports that without conflict, because the required engine is a per-project choice.

For the personal instance: adopt Section 3, run the Section 3.4 demonstration at bootstrap, and set `required_external_engine: claude`. Copilot can be an optional engine if licensed; it is not needed for qualification.

For the corporate instance: Copilot is the required engine now, because it needs no vendor approval and introduces no repository secret. Claude is added as an optional engine once its vendor route (direct API or a cloud-provider route) is approved for the confidentiality domain, and is benchmarked against Copilot on the seeded-defect fixtures. If it passes the Section 3.4 demonstration and the benchmark, the owner may switch the required engine to Claude; nothing else in the framework changes.

Sequence: (1) create the custom AWF Reviewer App with Pull requests: write and Metadata: read; (2) vendor the review scripts and schema on a protected path; (3) add the workflow from Section 3.3 on the default branch; (4) run the Section 3.4 demonstration and record it; (5) set the identity and qualification flags in `project.yaml`; (6) enable the `awf/review` required check with the reviewer App as its expected source. All of this precedes the first A2 pull request; none of it is required for A1.

---

## 7. Open items carried forward

- A verification report of the implemented change (review policy, review-report schema, Copilot gate, adapters, protected-path scaffold, generated lock, state machine) as a separate document.
- Supply-chain pinning of actions by commit SHA and vendoring of any review skill, in the Claude adapter guidance.
- Per-domain Anthropic credentials or Console workspaces so an organisation-wide key does not span confidentiality domains.
- Hardening of the Copilot gate's reviewer identity to an adapter-owned constant.
- The provenance, schema, benchmark, incident-response, identity and upstream-CI items accepted in the disposition, before downstream bootstrap or A2 activation.

---

## Appendix: facts relied on in this addendum

From Anthropic's `claude-code-action` capabilities document: "Submit PR Reviews: Claude cannot submit formal GitHub PR reviews"; "Approve PRs: For security reasons, Claude cannot approve pull requests"; "When triggered on an open PR: Always pushes directly to the existing PR branch"; "Perform Branch Operations: Cannot merge branches, rebase, or perform other git operations beyond pushing commits"; "Run Arbitrary Bash Commands: By default, Claude cannot execute Bash commands unless explicitly allowed". From the action's input reference: `github_token` is "GitHub token for Claude to operate with. Only include this if you're connecting a custom GitHub app of your own!"; `allowed_bots` is a comma-separated list of bot logins including the `[bot]` suffix. From the Claude Code GitHub Actions documentation: the official Claude GitHub App is installed with Actions, Checks, Contents, Discussions, Issues, Pull requests, Repository hooks and Workflows read-and-write plus Members and Metadata read, and "GitHub doesn't let you accept a subset"; organisations needing fewer permissions are directed to a custom App; the workflow `permissions:` block scopes `GITHUB_TOKEN`. From GitHub's documentation: Copilot "always leaves a 'Comment' review, not an 'Approve' or 'Request changes' review"; required status checks may name an expected source app; ruleset bypass is granted to configured bypass actors; `pull_request_target` workflows run from the base branch's definition; secrets are withheld from fork pull requests on public repositories. Anthropic's custom-App setup guidance and the action's security document were cited by the owner and are relied on as cited; they were not re-fetched for this revision.
