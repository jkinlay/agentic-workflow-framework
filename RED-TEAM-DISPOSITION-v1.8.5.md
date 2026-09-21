# Disposition of the v1.8.4 review and adoption request

Version 1.8.5 addresses the two reported usability failures while retaining release-review and live-enablement controls. The complete supplied review and change request remain external evidence; their recommendations are evaluated against actual interfaces and GitHub behavior, not treated as proof that every proposed claim is correct.

| Finding | Correction and boundary |
| --- | --- |
| S1: ordinary self-test demands release-review pins | Default source self-test runs component checks and reports `release_qualified: false` with review `NOT_PROVIDED`. `--release` or supplied review inputs activates the current-review requirement. Archive acceptance and catalog publication still require independent pins and matching release-qualified evidence. Existing report/input preservation and final-byte checks remain necessary. |
| S2: adoption inherits live-enablement preconditions | Specification, AGENTS, controller and both skills distinguish installing/mapping/preparing a draft PR from enabling live automation. Missing default-branch rules produce a warning and a concrete owner application step. The first message explains the governance-file reason for using a draft PR. Existing secret-repository push restrictions still apply: prepare local changes/body for owner publication or observe rules first, without refusing local adoption or claiming an uncreated PR. |
| Missing usable ownership/ruleset artifacts | The default-branch template ships with no bypass actors, zero required human approvals, squash/rebase and an initially empty required-check list. CODEOWNERS defaults new installations to `@maintainer`, accepts `--codeowner @handle`, remains project-owned and preserves existing contents. Bootstrap never applies server rules. Required code-owner approval needs actual eligible non-author reviewers; two arbitrary names are insufficient. |
| Rules-state reporting lacks evidence distinctions | The read-only helper discovers the actual default branch and observes applicable rules plus ruleset/bypass details. Adequate baseline is APPLIED; observed inadequate baseline is MISSING; unavailable, malformed, stale or incomplete evidence is UNOBSERVED. Synthetic observations are labelled fixtures and cannot enable live work. APPLIED alone does not establish a qualified review engine or App-bound required check. |

## Corrections to supplied rationale

The [GitHub rules API](https://docs.github.com/en/rest/repos/rules) says strict up-to-date status-check policy has no effect until at least one check is configured. The shipped empty list therefore does not guarantee a current merge base. Populate observed required checks, including the actual `awf/review` App identity, before enabling the relevant automation.

Dismissal concerns approving reviews, not arbitrary COMMENT freshness. Resolving review threads does not authenticate a bot, establish current-head evidence or close AWF findings. AWF's candidate binding, independent review and gate remain necessary. Allowed squash/rebase methods must also be supported by repository settings; this ruleset is not evidence that every server-side behavior has been tested. [Available GitHub rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

## Evidence and remaining qualification

The prior pilot remains **10/12 strict FAIL**, with two independently sealed substantive assessments agreeing on **6 PASS, 1 FAIL out of seven**. NATIVE-29 selected an explicitly forbidden affected-work continuation despite cautious prose; NATIVE-30 selected an extra continuation code outside its allowed set. The earlier **5/12 FAIL** and **1/3 FAIL** remain unchanged. Because the later pilot used new cases and a changed contract, the numerical difference is not a controlled causal estimate of improvement. No actual project actions were measured, so no unsafe-action execution rate is inferred.

This patch adds no new model observations. Focused regressions, current independent review and final source/installed/portable acceptance are recorded in the external release validation artifacts; this document does not manufacture passing totals. Disposable GitHub validation of rule application, direct/force-push rejection and merge-method behavior remains a separate live task requiring an identified disposable repository and applicable authorization. No live probe or target activation is claimed here.

See [migration](MIGRATION-v1.8.4-to-v1.8.5.md), [adoption](.agentic/docs/20-NEW-PROJECT-SETUP.md) and [external qualification](.agentic/docs/28-EXTERNAL-REVIEW.md). Archive consistency, observed repository rules and actual engine/host qualification are separate conclusions.
