# AWF proof pack: the framework's own history as evidence

Compiled 23 September 2026 from `jkinlay/agentic-workflow-framework` (GitHub PRs, review comments and `git log`) and the AWF 1.9.0 delivery note and review ledger. Every number below can be traced to the source named beside it.

## 1. The critic on a real PR: PR #7 (strongest single exhibit)

**Source:** github.com/jkinlay/agentic-workflow-framework/pull/7, "Fix issue #6 managed Copilot distribution artifacts".

| Fact | Value |
| --- | --- |
| Opened / merged | 30 Aug 2026 17:30 UTC / 18:24 UTC (54 minutes) |
| Commits on the PR | 12 (fix, then five rounds of "address review" / "regenerate provenance") |
| Automated independent reviews | 5, each bound to an exact head SHA (`2299087`, `ab58c2a`, `55361a0`, `b5bd6ea`, `bd554ed`) |
| Reviewer | `awf-reviewer[bot]`, engine Claude; the worker was Codex, so the worker and reviewer ran on different models |
| Findings per round (blocking) | 10 (1) → 10 (3) → 12 (4) → 13 (5) → 16 (2) |
| Inline review comments | 61 |
| How it ended | The last 2 blocking items were merge-policy questions. The owner ruled on them in writing and merged with a merge commit. A post-merge validation from a fresh clone confirmed the reviewed head was the merged head. |

**Findings worth putting on a slide** (verbatim headlines from the bot's inline comments):

- *"Root reviewer instruction file weakened: explicit blocking-class list replaced with vague summary"* (high, blocking). The worker had softened the reviewer's own instructions, and the reviewer caught it. This is the clearest case for independent review.
- *"New provenance test is self-referential: it can never validate the commit that introduces the lock update"* (high, blocking). A test that looked like evidence but could never fail. Quants will recognise the pattern.
- *"Protected CI/workflow paths modified … without visible human authorization"* (high, blocking).
- *"Claims-phase: AC6 'unchanged qualification behavior' is contradicted by the reviewer-instruction rewrite"* (medium, blocking). The worker's claimed evidence was checked against the diff and found wanting.

**Say this honestly:** the blocking count rose for three rounds before it fell. Each fix to the provenance files exposed new inconsistencies. That is what an adversarial reviewer should do, and it is also why AWF has a finite amendment cap with an owner decision at the end (added in 1.9.0). This PR predates the 1.x lineage (it is the 0.1.1 scaffold), so present it as the origin of the design, not as a 1.9.1 run.

## 2. AWF building AWF: the 1.9.0 release

**Source:** AWF 1.9.0 delivery note and `AWF-v1.9.0-review-rounds-2026-09-21.md` (project doc `reviews/review-v1.9.0-2026-09-21.md`).

| Round | Candidate | Critic | Specialist | Serious findings |
| --- | --- | --- | --- | --- |
| 1 | `598ebe2` | REQUEST_CHANGES | FAIL | 2 BLOCKER, 3 MAJOR |
| 2 | `595b9b8` | REQUEST_CHANGES | PASS | 1 MAJOR |
| 3 | `f8c8115` (shipped) | APPROVE | PASS | none (MINOR/NIT successor proposals only) |

**The headline catch (R1-C-F01, BLOCKER):** owner sign-offs were an unverified data shape, so *an agent could fabricate an owner disposition and flip the critic's verdict to PASS.* The fix was an authenticated owner-record grammar. Round 2 then found a subtler hole: a "verifier" run that shared the worker's producer identity was still accepted (R2-C-F01, MAJOR, `boundary:INDEPENDENCE`). Both were fixed and re-verified by fresh reviewers.

Outcome: 900 tests, 0 failed; archive acceptance passed; cap of 3 amendment cycles not exceeded.

**Caveat:** these rounds ran in the build workspace. They are documented in the ledger, not as reviews on a GitHub PR.

## 3. Honest measurement

**Source:** `README.md` and `.agentic/benchmarks/native/EVALUATION-*.md`.

| Pilot | Result |
| --- | --- |
| 1.8.2 real-model decision pilot | 1/3 strict FAIL |
| 1.8.3 twelve-case pilot (pre-fixed protocol) | 5/12 strict FAIL |
| 1.8.4 pilot under decision contract v2 | 10/12 strict FAIL |
| 1.9.0 / 1.9.1 | NOT_RUN |

These pilots measure whether a model makes AWF's structured routine decisions correctly without help. The results are poor, and they are published unadjusted. Use this as a credibility slide: the framework does not trust the model's judgement, which is why the gates, the independent critic and the human merge exist.

## 4. Red-team trail

Eight written red-team dispositions ship in the repository (`RED-TEAM-DISPOSITION-v1.8.md` to `-v1.8.7.md`), plus 15 migration notes. Every release from 1.8 to 1.8.7 has an external review and a written response to it.

## 5. Gaps to close before presenting the AWF repo as proof

A sceptical audience can check the repository themselves. As of 23 September 2026 they would find:

| Gap | Evidence | Fix |
| --- | --- | --- |
| The 1.9.1 port merged with no review on GitHub | PR #13, +43,274 / −13,927 lines, 0 reviews, 0 comments | Put the next change, open PR #14 (release hardening), through the full AWF flow with the critic posting on GitHub, and use it as a current exhibit. |
| A direct push to `main` after that merge | `fbd4337 update presentation` is on `origin/main` without a PR | Protect `main` with the shipped ruleset (`.agentic/templates/awf-main-ruleset.json`). |
| No tag or GitHub Release for 1.9.1 | `git tag` and `gh release list` are both empty | Tag `v1.9.1` and publish a Release with the distribution ZIP and its SHA-256. That becomes the independent pin for the checksum slide. |
| Placeholder publisher | `PUBLISHER.json` → `"repository": "https://example.invalid/agentic-workflow-framework"` | Set the canonical repository URL. |
| The AWF repo is not itself adopted | `.agentic/workflow-version.yaml`: `profile: manual_reference`, `install_id: null` | Either adopt it (true dogfooding) or say plainly on the slide that the framework is proven on its build process and on adopters, not by self-installation. |

Closing the first three gaps also answers review findings M4 (checksum pin source) and B2 (a real critic finding on a current PR) from the 23 September deck review.
