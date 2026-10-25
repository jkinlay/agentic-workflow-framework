# AWF 1.9.5 - What's New

**Release-candidate inventory through `7fcaeece364531da65ca1b8293ebd7643bf264ed`, 20 October 2026.**

AWF 1.9.5 adds stronger review and provenance gates, merge-train infrastructure, controller automation controls, safer recovery, backlog planning, and configurable model routing.

This document distinguishes merged changes from pending work and 1.9.6 deferrals. It does **not** establish that 1.9.5 has been published: the cutoff tree still declares version `1.9.4`. (`7fcaeec:VERSION:1`.)

## Scope and evidence

The supplied 520-commit log matches the local Git range `v1.9.4..7fcaeec`. Of the 165 supplied merged PR records, **123** have merge commits in that range; only those PRs are credited below. All six supplied open PRs concern 1.9.5 and appear under **Pending merge**. The Jira snapshot contains **20 tickets labelled `release-1.9.6`**, listed separately below. (`inputs/gitlog-v194-7fcaeec.txt:1`; `inputs/gh-merged.json:1`; `inputs/gh-open.json:1`; `inputs/jira-release-195-196.json:1503`.)

Input citations below are relative to the supplied review directory. Repository citations use `<revision>:<repository-relative path>:<line>`.

**Verification limitation — UNVERIFIED:** this is a documentary release inventory with selected source and test cross-checks. The requested exhaustive review of every changed file, function, caller, callee, and hunk is incomplete. Runtime behavior, live qualification, release publication, and test execution were not independently verified in this review. No tests, edits, commits, pushes, or network operations were performed.

## Install, upgrade and activation

| Change | What it does and why it matters | PRs, tickets and evidence |
|---|---|---|
| More reliable Windows heavy validation | Corrects interpreter selection and keeps the trusted controller interpreter outside the reviewed checkout. This avoids treating a checkout-owned interpreter as independent validation infrastructure. | **#52 — AWF-24.** `inputs/gitlog-v194-7fcaeec.txt:471`; `inputs/jira-release-195-196.json:152`. |
| POSIX and macOS validation support | Supports managed-venv interpreter symlinks and a sealed-artifact fallback where Linux-specific facilities are unavailable. | **#82 — AWF-23, AWF-38.** `inputs/gitlog-v194-7fcaeec.txt:374`; `inputs/jira-release-195-196.json:141`; `inputs/jira-release-195-196.json:282`. |
| Respect repository line-ending settings | Release tooling respects repository-local `core.autocrlf` rather than imposing a Windows setting. This reduces unexpected checkout-byte changes. | **#63 — AWF-18.** `inputs/gitlog-v194-7fcaeec.txt:500`; `inputs/jira-release-195-196.json:81`. |
| Recover interrupted upgrade journal updates | Makes a crash during the managed-after journal update recoverable. Missing parent directories are also handled by the unlink follow-up. | **#88, #98 — AWF-19, AWF-63.** `inputs/gitlog-v194-7fcaeec.txt:315`; `inputs/gitlog-v194-7fcaeec.txt:267`; `inputs/jira-release-195-196.json:93`; `inputs/jira-release-195-196.json:568`. |
| Clearer malformed-recovery handling | Restores invalid-marker classification when recovery markers are malformed, helping distinguish bad recovery evidence from other installation problems. | **#162 — AWF-140**, following AWF-134. `inputs/gitlog-v194-7fcaeec.txt:51`; `inputs/jira-release-195-196.json:1442`. |
| Tolerate stale Python bytecode during upgrade | Prevents stale managed-library bytecode from rejecting an otherwise valid upgrade. | **#72 — AWF-44.** `inputs/gitlog-v194-7fcaeec.txt:444`; `inputs/jira-release-195-196.json:347`. |
| Recognize legitimate historical installs | Addresses unregistered historical 1.9.1 receipts, with explicit acceptance or rejection rather than an unexplained upgrade failure. This is not proof of the pending 1.9.4-to-1.9.5 release path. | **#103 — AWF-28.** `inputs/gitlog-v194-7fcaeec.txt:214`; `inputs/jira-release-195-196.json:174`. |
| Safer AWF source detection | A project’s own root manifest no longer makes it look like the AWF source repository. | **#120 — AWF-20.** `inputs/gitlog-v194-7fcaeec.txt:153`; `inputs/jira-release-195-196.json:105`. |
| AWF can adopt its own framework | Separates shipped template configuration from AWF’s own adopted configuration and adjusts packaging and hygiene around install-owned files. | **#73 — AWF-46**, also carried by batch **#81**. `inputs/gitlog-v194-7fcaeec.txt:413`; `inputs/jira-release-195-196.json:372`; `inputs/gh-merged.json:1`, PR #81. |
| Stronger handoff validation | Rejects incomplete handoff snapshots, corrects host-broker configuration lookup, and sanitizes remote-identity evidence. This makes a reported match more meaningful. | **#61 — AWF-21**, also carried by **#81**. `inputs/gitlog-v194-7fcaeec.txt:414`; `inputs/gitlog-v194-7fcaeec.txt:458`; `inputs/jira-release-195-196.json:117`. |
| Acceptance checks use the installed runtime | AC42 requires resource admission and checks activation through the installed workflow, reducing false acceptance based on the source checkout. | **#75 — AWF-22.** `inputs/gitlog-v194-7fcaeec.txt:432`; `inputs/jira-release-195-196.json:129`. |
| Installed self-tests fit installed projects | Separates source-only checks from installed-runtime checks, supports fresh commitless installations, and fixes the associated diagnostics fixture. | **#76 — AWF-45, AWF-39.** `inputs/gitlog-v194-7fcaeec.txt:395`; `inputs/jira-release-195-196.json:360`; `inputs/jira-release-195-196.json:296`. |
| Project-owned launch declarations | Lets projects declare additional launch surfaces without modifying AWF’s install-pinned launch-surface inventory. | **#113 — AWF-35.** `inputs/gitlog-v194-7fcaeec.txt:182`; `inputs/jira-release-195-196.json:245`. |
| Windows path identity fixes | Normalizes short-path aliases for state containment and pinned-skill identity, avoiding false mismatches between equivalent paths. | **#87, #109 — AWF-61, AWF-70.** `inputs/gitlog-v194-7fcaeec.txt:345`; `inputs/gitlog-v194-7fcaeec.txt:201`; `inputs/jira-release-195-196.json:546`; `inputs/jira-release-195-196.json:645`. |
| Pinned AWF skill selection | Host-launched sessions use the pinned AWF skill and refuse conflicting installed versions. | **#102 — AWF-33.** `inputs/gitlog-v194-7fcaeec.txt:226`; `inputs/jira-release-195-196.json:222`. |
| Additive review-policy migration | Adds review-tier defaults while preserving existing configuration and history; mapping-style support is corrected by the migration follow-up. | **#86, #126 — AWF-16, AWF-59.** `inputs/gitlog-v194-7fcaeec.txt:334`; `inputs/gitlog-v194-7fcaeec.txt:69`; `inputs/jira-release-195-196.json:521`. |
| Installed validation fixture correction | Fixes the environment-dependent validation test without removing the separate missing-dependency exit contract. | **#145 — AWF-100.** `inputs/gitlog-v194-7fcaeec.txt:59`; `inputs/gh-merged.json:1`, PR #145; `7fcaeec:.agentic/scripts/workflow.py:8`. |
| More deterministic PowerShell discovery | Corrects resolution under a sanitized or empty search path and improves diagnosis of process-start failures. | **#144, #159 — AWF-101, AWF-131**, following AWF-92/AWF-124. `inputs/gitlog-v194-7fcaeec.txt:62`; `inputs/gitlog-v194-7fcaeec.txt:48`; `inputs/jira-release-195-196.json:1030`; `inputs/jira-release-195-196.json:1347`. |

Activation remains evidence-based. **INSTALLED** establishes receipt consistency; **CONFIGURED** adds accepted configuration; **ACTIVE** requires independently trusted release bytes and observed acceptance of the adoption on the default branch. ACTIVE alone does not enable adapters. This is the applicable activation contract, not a claim that the pending AWF-55 project report has shipped. (`7fcaeec:.agentic/SPECIFICATION.md:49`; `7fcaeec:.agentic/docs/20-NEW-PROJECT-SETUP.md:48`; `7fcaeec:.agentic/docs/32-ACTIVATION-STATUS.md:19`.)

## Merge train and gates

| Change | What it does and why it matters | PRs, tickets and evidence |
|---|---|---|
| Risk-based review tiers | Contracts declare a risk tier and closure standard. Serious findings require an acceptance criterion or mandatory-boundary basis, making review and closeout requirements explicit. | **#86 — AWF-16.** `inputs/gitlog-v194-7fcaeec.txt:334`; `7fcaeec:.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md:7`. |
| Audited intake assignments | Assigns tier, review cap, and stream at intake; supports audited owner changes and later stream segmentation. | **#112, #129 — AWF-48.** `inputs/gitlog-v194-7fcaeec.txt:145`; `inputs/gitlog-v194-7fcaeec.txt:123`; `7fcaeec:.agentic/docs/20-NEW-PROJECT-SETUP.md:34`. |
| Per-ticket review-cap enforcement | Uses the recorded assignment cap rather than substituting the global amendment limit. | **#125 — AWF-74.** `inputs/gitlog-v194-7fcaeec.txt:92`; `inputs/jira-release-195-196.json:688`. |
| Owner-approved over-cap evidence | Adds canonical Tier 3 approval handling and production collection of those approvals. Wrong-head, late, untrusted, or malformed approval evidence keeps the gate closed. | **#142, #149 — AWF-81, AWF-110.** `inputs/gitlog-v194-7fcaeec.txt:58`; `inputs/gitlog-v194-7fcaeec.txt:54`; `7fcaeec:.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md:11`. |
| Provider-verified owner review | Verifies Tier 3 owner review against provider evidence rather than accepting a copied assertion. | **#153 — AWF-41.** `inputs/gitlog-v194-7fcaeec.txt:53`; `inputs/jira-release-195-196.json:309`; `7fcaeec:.agentic/docs/43-MERGE-TRAIN.md:16`. |
| Explicit draft-to-ready authorization | Validation and green CI no longer suffice to change draft state. The controller needs authenticated authorization for the exact PR and head and retains a refusal otherwise. | **#107 — AWF-42.** `inputs/gitlog-v194-7fcaeec.txt:70`; `7fcaeec:.agentic/docs/31-PUBLICATION-SAFETY.md:71`. |
| Governed epic integration queue | Tests ordered PR interactions while retaining task branches as merge sources. Dependency order, prefix builds, validation, and retarget equivalence are bound to retained evidence. | **#118 — AWF-10.** `inputs/gitlog-v194-7fcaeec.txt:143`; `7fcaeec:.agentic/docs/42-PR-INTEGRATION-QUEUE.md:28`. |
| Queue stall, App, and closeout checks | Uses the reviewed stall interval, refreshes enforced-mode App identity, and binds post-merge counts to closeout and Jira reconciliation. Jira changelog completeness remains deferred under AWF-175. | **#130 — AWF-76.** `inputs/gitlog-v194-7fcaeec.txt:119`; `7fcaeec:.agentic/docs/42-PR-INTEGRATION-QUEUE.md:15`; `7fcaeec:.agentic/docs/42-PR-INTEGRATION-QUEUE.md:87`. |
| Workflow-content CI binding | Adds `workflow_blob_sha` so a reviewed workflow can survive different merge shapes without repeated commit-SHA repinning. Missing or mismatched workflow content refuses the gate; legacy configuration retains its existing binding behavior. | **#122, #133, #138 — AWF-14.** `inputs/gitlog-v194-7fcaeec.txt:151`; `inputs/gitlog-v194-7fcaeec.txt:104`; `inputs/gitlog-v194-7fcaeec.txt:82`; `inputs/gh-merged.json:1`, PR #138. |
| Canonical candidate fingerprints | Provides a candidate-fingerprint entry point and clearer validation when a Git commit SHA is incorrectly supplied as a candidate identifier. | **#117, #141 — AWF-37, AWF-73.** `inputs/gitlog-v194-7fcaeec.txt:167`; `inputs/gitlog-v194-7fcaeec.txt:67`; `7fcaeec:.agentic/scripts/workflow.py:29`. |
| Writer and critic provenance | Records run identity, requested/observed model and effort, and CLI provenance; connects host run logs to commit evidence. | **#136, #146 — AWF-80, AWF-93**, contributing to AWF-49. `inputs/gitlog-v194-7fcaeec.txt:100`; `inputs/gitlog-v194-7fcaeec.txt:57`; `7fcaeec:.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md:78`. |
| Strict provenance grammar | Validates Writer trailers, critic carriers, and reserved-key spelling. Separator and spacing lookalikes are refused rather than interpreted ambiguously. | **#155, #181 — AWF-94, AWF-132.** `inputs/gitlog-v194-7fcaeec.txt:52`; `inputs/gitlog-v194-7fcaeec.txt:36`; `7fcaeec:.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md:8`. |
| Safer Git-range refusals | Malformed base locators in shallow repositories produce a named refusal instead of an uncaught failure. | **#165 — AWF-151**, successor to AWF-95. `inputs/gitlog-v194-7fcaeec.txt:44`; `7fcaeec:.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md:32`. |
| Gate-valid owner dispositions | Binds owner dispositions to native verdict records, issued requests, and trusted owner identities. Automation and owner identities must remain separate. | **#164, #188 — AWF-148, AWF-156**, following AWF-98. `inputs/gitlog-v194-7fcaeec.txt:29`; `inputs/gitlog-v194-7fcaeec.txt:24`; `7fcaeec:.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md:25`. |
| Versioned provenance overrides | Retains covered commits, predecessor evidence, and owner authority in versioned override records. A sealed override does not depend on a fallback label. | **#177, #185 — AWF-79, AWF-96.** `inputs/gitlog-v194-7fcaeec.txt:41`; `inputs/gitlog-v194-7fcaeec.txt:28`; `7fcaeec:.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md:107`. |
| Provider-observed PR inputs | Tightens repository/PR/head/base/ref identity, bounded bodies and comments, pagination, and before/after consistency checks. | **#196 — AWF-96.** Its “tests only” title is stale: the merge includes production changes. `inputs/gitlog-v194-7fcaeec.txt:7`; `7e5a7259:.agentic/lib/agentic/providers/github_review_host.py:1006`; `7fcaeec:.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md:101`. |
| Safer first-draft publication | Validates first-draft title, body, tree, and provenance inputs before publication; recovery reconciles retained effects instead of blindly recreating them. | **#66, #170 — AWF-30, AWF-96.** `inputs/gitlog-v194-7fcaeec.txt:438`; `inputs/gitlog-v194-7fcaeec.txt:45`; `7fcaeec:.agentic/docs/22-AUTOMATED-REVIEW-LOOP.md:29`; `7fcaeec:.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md:124`. |
| Commit provenance reaches the final gate | Recomputes provenance from Git and registered runtime inputs, refusing untagged code and falsely mechanical script commits. This delivers AWF-117’s AC2/AC3 slice, not the whole ticket. | **#202 — AWF-117.** `inputs/gitlog-v194-7fcaeec.txt:18`; `f9667fc0:.agentic/lib/agentic/gates.py:1270`; `7fcaeec:.agentic/docs/45-AWF117-PROVENANCE-GATE-TEST-PLAN.md:18`. |
| Deterministic batch construction | Adds merge-train admission, dependency ordering, prefix construction, invalidation, and batch provenance. A batch is a separate candidate requiring its own evidence. | **#174 — AWF-54**; design groundwork **#169, #180**. `inputs/gitlog-v194-7fcaeec.txt:33`; `inputs/gitlog-v194-7fcaeec.txt:47`; `inputs/gitlog-v194-7fcaeec.txt:34`; `7fcaeec:.agentic/docs/43-MERGE-TRAIN.md:28`. |
| Gated batch publication | Routes publication through registered observers and publishers with freeze rechecks and durable intent. It exposes no merge or target-push authority. | **#200 — AWF-54.** `inputs/gitlog-v194-7fcaeec.txt:22`; `7fcaeec:.agentic/scripts/merge_train.py:2`; `7fcaeec:.agentic/docs/47-AWF54B-TEST-PLAN.md:17`. |
| Observation-only orphan recovery | Reconciles uncertain batch publication after observation expiry without replaying push or PR creation. | **#204 — AWF-54.** `inputs/gitlog-v194-7fcaeec.txt:19`; `7fcaeec:.agentic/docs/47-AWF54B-TEST-PLAN.md:19`. |
| Exact-object batch transfer | Transfers the locally validated commit objects and checks the resulting remote candidate, avoiding a ref-only substitute for the tested batch. | **#210 — AWF-54.** `inputs/gitlog-v194-7fcaeec.txt:12`; `7fcaeec:.agentic/scripts/merge_train.py:193`. |
| Independent batch gates | Requires full exact-head CI, frozen batch reviews, authenticated mark-ready authorization, and separate batch merge authorization. Source-PR review cannot authorize the batch. | **#189 — AWF-54.** `inputs/gitlog-v194-7fcaeec.txt:27`; `7fcaeec:.agentic/docs/47-AWF54B-TEST-PLAN.md:20`. |
| Immutable batch audit | Adds candidate-bound immutable batch audit records. Legacy audit migration is deferred. | **#213 — AWF-54.** `inputs/gitlog-v194-7fcaeec.txt:10`; `7fcaeec:.agentic/docs/47-AWF54B-TEST-PLAN.md:24`; `7fcaeec:.agentic/docs/49-AWF54-R2-W1-W2-INTERFACE.md:11`. |
| Controller-only constituent reconciliation | Adds batch constituent Jira reconciliation, receipt validation, and legacy-recovery hardening. AWF-54 remains in progress, with the canonical-key successor pending. | **#215, #221 — AWF-54.** `inputs/gitlog-v194-7fcaeec.txt:5`; `inputs/gitlog-v194-7fcaeec.txt:1`; `7fcaeec:.agentic/docs/49-AWF54-R2-W1-W2-INTERFACE.md:31`; `inputs/jira-release-195-196.json:470`. |
| Provenance-gate design record | Records the remaining acceptance tests and contract questions; the document itself adds no runtime enforcement. | **#176 — AWF-117.** `inputs/gitlog-v194-7fcaeec.txt:37`; `7fcaeec:.agentic/docs/45-AWF117-PROVENANCE-GATE-TEST-PLAN.md:3`. |

## Recovery and safety

| Change | What it does and why it matters | PRs, tickets and evidence |
|---|---|---|
| Task-bound external-resource admission | Requires resource evidence for the actual task, principal, and session. Distinguishes sandbox refusal, process-start failure, missing mapping, access denial, and verified read access. | **#79 — AWF-8.** `inputs/gitlog-v194-7fcaeec.txt:129`; `7fcaeec:.agentic/docs/40-EXTERNAL-RESOURCES.md:53`. |
| Safer unpublished-history rewriting | Checks stale refs and pseudorefs before compare-and-swap, requires explicit local commit identity, and retains recovery evidence for uncertain outcomes. | **#99 — AWF-52.** `inputs/gitlog-v194-7fcaeec.txt:258`; `7fcaeec:.agentic/docs/31-PUBLICATION-SAFETY.md:98`. |
| Publication scan before release mutation | Refuses tagging, pushing, or release creation until the exact release history has a matching passing scan. | **#104 — AWF-65.** `inputs/gitlog-v194-7fcaeec.txt:223`; `7fcaeec:.agentic/docs/32-RELEASE-PUBLISHING.md:9`. |
| Correct sync-merge baseline handling | Treats inherited deletions from synchronization merges as pre-existing, including synchronization merges earlier in the candidate range. | **#108, #119 — AWF-69, AWF-72.** `inputs/gitlog-v194-7fcaeec.txt:188`; `inputs/gitlog-v194-7fcaeec.txt:163`; `inputs/jira-release-195-196.json:635`; `inputs/jira-release-195-196.json:666`. |
| Recorded large-file baseline allowances | Permits exact existing oversized artifacts through reviewed path-and-digest allowances while retaining refusal for new, changed, or mismatched artifacts. | **#131, #199 — AWF-34, AWF-154.** `inputs/gitlog-v194-7fcaeec.txt:133`; `inputs/gitlog-v194-7fcaeec.txt:6`; `7fcaeec:.agentic/docs/31-PUBLICATION-SAFETY.md:36`; `7fcaeec:.agentic/tests/test_awf154_large_file_allowances.py:105`. |
| Separate supplemental-configuration evidence | Binds tracked configuration and supplemental bytes separately. Supplemental configuration can add detectors but cannot grant allowance authority. | **#199 — AWF-154.** The merged production implementation supersedes its stale “tests only” description. `98899852:.agentic/lib/agentic/publication.py:1128`; `7fcaeec:.agentic/docs/31-PUBLICATION-SAFETY.md:43`; `7fcaeec:.agentic/tests/test_awf154_large_file_allowances.py:169`. |
| Allowance closure regressions | Tests multiple baseline allowances, a new oversized file after an earlier pass, and an allowed file changed then restored. Restoring final bytes does not erase unsafe intermediate history. | **#218 — AWF-34.** Tests-only change. `inputs/gitlog-v194-7fcaeec.txt:3`; `7fcaeec:.agentic/tests/test_awf34_configured_allowance_closure.py:58`; `7fcaeec:.agentic/tests/test_awf34_configured_allowance_closure.py:95`. |
| Broader diagnostic redaction | Covers prefixed secret names and network-share paths containing spaces, reducing disclosure through rejection messages. | **#183 — AWF-155**, under AWF-97. `inputs/gitlog-v194-7fcaeec.txt:32`; `inputs/jira-release-195-196.json:1598`. |
| Bounded subprocess callers | Moves publication and review-host callers onto bounded execution paths. Remaining capture routes are explicitly deferred under AWF-172. | **#186 — AWF-97.** `inputs/gitlog-v194-7fcaeec.txt:25`; `7fcaeec:.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md:87`; `inputs/jira-release-195-196.json:1782`. |
| Durable publication records | Adds publication-record durability and crash recovery, retaining evidence across interrupted operations. | **#195 — AWF-97.** `inputs/gitlog-v194-7fcaeec.txt:21`; `inputs/gh-merged.json:1`, PR #195; `7fcaeec:.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md:118`. |
| Bounded diagnostic output | Adds a fixed 4 KiB diagnostic helper, safe review-loop fields, and redacted/escaped CLI rejection output. | **#207, #209, #214 — AWF-97.** `inputs/gitlog-v194-7fcaeec.txt:17`; `inputs/gitlog-v194-7fcaeec.txt:15`; `inputs/gitlog-v194-7fcaeec.txt:9`. Detailed implementation audit: **UNVERIFIED**. |
| Bounded pinned input reads | Bounds canonical loading and CLI inputs, including deterministic refusal when files grow during reads. | **#208, #211 — AWF-97.** `inputs/gitlog-v194-7fcaeec.txt:13`; `inputs/gitlog-v194-7fcaeec.txt:14`. Detailed implementation audit: **UNVERIFIED**. |
| Correct Windows file-error translation | Preserves native error classification and filenames so missing paths are handled as missing paths rather than unrelated process errors. | **#216 — AWF-97.** `inputs/gitlog-v194-7fcaeec.txt:8`; `inputs/gh-merged.json:1`, PR #216. |
| Complete-envelope publication cap | Caps the complete serialized publication record at an inclusive 1,048,576 bytes, including framing, digests, and escaping. Oversized records refuse before reservation. | **#217 — AWF-97.** `inputs/gitlog-v194-7fcaeec.txt:4`; `7fcaeec:.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md:118`. |
| Complete-envelope host run-log cap | Applies the corresponding byte cap to host commit-run logs before retained-record inspection or writing; oversized attempts preserve existing evidence. | **#220 — AWF-97.** `inputs/gitlog-v194-7fcaeec.txt:2`; `7fcaeec:.agentic/tests/test_awf97_r12_core.py:72`; `7fcaeec:.agentic/tests/test_awf97_r12_core.py:89`. |

## Controller and planning

| Change | What it does and why it matters | PRs, tickets and evidence |
|---|---|---|
| Continuous execution and reporting | Adds cadence controls and readiness reporting, retains uncertain dispatches, and clears matching UNKNOWN blockers after successful reconciliation. | **#53, #93 — AWF-5, AWF-66.** `inputs/gitlog-v194-7fcaeec.txt:302`; `inputs/gitlog-v194-7fcaeec.txt:291`; `inputs/jira-release-195-196.json:3`; `inputs/jira-release-195-196.json:602`. |
| Shipped reference controller adapter | At the historical cutoff, the reference adapter lacked `observe_host_resources` and `verify_host_observation` required by `cycle`; the same gap was observed in the supplied snapshot. Post-#249, the reference adapter supplies the cycle CLI's planning operations and passes its adapter check on fresh installs and upgrades from 1.9.4 (#249 tests: `.agentic/tests/test_reference_controller_adapter.py:1652`, `.agentic/tests/test_reference_controller_adapter_install.py:133`, `:143`; Task 55 dry run, 25 October 2026 16:33 GMT). Successful current-controller operation remains **UNVERIFIED**. | **Historical attribution: #78, #83, #106 - AWF-32, AWF-57, AWF-68.** `inputs/gitlog-v194-7fcaeec.txt:370`; `inputs/gitlog-v194-7fcaeec.txt:359`; `inputs/gitlog-v194-7fcaeec.txt:219`; `7fcaeec:.agentic/docs/34-CONTINUOUS-CONTROLLER.md:78`. Supplied-snapshot observations: `main/.agentic/scripts/continuous_controller.py:46`; `main/.agentic/scripts/continuous_controller.py:217`; `main/.agentic/adapters/reference_controller_adapter.py:1451`. |
| Controller operating guidance | Adds controller operating examples. The cutoff document labels its displayed adapter fragment non-executable, so it should not be copied as a complete launch procedure. | **#92 — AWF-58.** `inputs/gitlog-v194-7fcaeec.txt:288`; `7fcaeec:.agentic/docs/34-CONTINUOUS-CONTROLLER.md:80`. |
| Isolated agent launches | Separates AWF-launched sessions from ambient application configuration, plugins, and notification hooks. Isolation still requires host qualification evidence. | **#96 — AWF-51.** `inputs/gitlog-v194-7fcaeec.txt:247`; `7fcaeec:.agentic/docs/22-AUTOMATED-REVIEW-LOOP.md:21`. |
| Evidence-backed qualification procedure | Replaces bare qualification assertions with a retained procedure, artifact pins, host binding, and expiry. Preparing the procedure is not evidence that live qualification ran. | **#80 — AWF-31.** `inputs/gitlog-v194-7fcaeec.txt:318`; `7fcaeec:.agentic/docs/22-AUTOMATED-REVIEW-LOOP.md:15`. |
| Portable stream output | Removes host-local absolute paths from committed stream-planning output and supports approved ownership-path redaction. | **#74 — AWF-17.** `inputs/gitlog-v194-7fcaeec.txt:425`; `inputs/jira-release-195-196.json:70`. |
| Backlog-based planning primitives | Adds dependency/file-overlap planning and lane recommendations. The original broad AWF-50 brief was narrowed; it does not establish that every requested controller or schema capability shipped. | **#127 — AWF-50**; remaining-scope design **#182**. `inputs/gitlog-v194-7fcaeec.txt:132`; `inputs/gitlog-v194-7fcaeec.txt:31`; `7fcaeec:.agentic/docs/48-AWF50-DESIGN-PLAN.md:3`. |
| Trusted capacity and zero-capacity planning | Uses authenticated resource observations, allows a safe lane count of zero, seals capacity before owner overrides, and rejects invalid resource ceilings or operator-authored capacity substitutes. | **#168, #179, #184, #201, #203 — AWF-88.** `inputs/gitlog-v194-7fcaeec.txt:46`; `inputs/gitlog-v194-7fcaeec.txt:39`; `inputs/gitlog-v194-7fcaeec.txt:30`; `inputs/gitlog-v194-7fcaeec.txt:23`; `inputs/gitlog-v194-7fcaeec.txt:20`; `7fcaeec:.agentic/docs/29-OPERATING-CONFIGURATION.md:13`. |
| Per-ticket writer or critic selection | Allows mixed roles in one inventory, with role authority from authenticated lifecycle evidence and fresh, distinct writer/critic sessions. | **#147 — AWF-89.** `inputs/gitlog-v194-7fcaeec.txt:55`; `inputs/gh-merged.json:1`, PR #147. |
| Safer overlap and synchronization | Re-observes lane-start ownership, binds allowances to current PR heads, and uses head-bound conflict evidence. | **#166 — AWF-150**, successor to AWF-90/AWF-127. `inputs/gitlog-v194-7fcaeec.txt:43`; `7fcaeec:.agentic/docs/34-CONTINUOUS-CONTROLLER.md:60`. |
| Dispatch migration and round-boundary recovery | Migrates only unambiguous legacy state with retained inventory binding, preserves non-terminal reservations, and requires authenticated boundary evidence before refill. | **#178 — AWF-91**; design **#173**. `inputs/gitlog-v194-7fcaeec.txt:38`; `inputs/gitlog-v194-7fcaeec.txt:42`; `7fcaeec:.agentic/docs/34-CONTINUOUS-CONTROLLER.md:14`. |
| Deep dependency graphs | Carries ticket-planning work and replaces recursive traversal so deep chains and cycles receive bounded handling rather than an uncaught recursion failure. | **#172 — AWF-152**, carrying AWF-115. `inputs/gitlog-v194-7fcaeec.txt:40`; `inputs/gh-merged.json:1`, PR #172; `inputs/jira-release-195-196.json:1568`. |
| Durable planning history | Persists digest-checked plan records through the protected state store, with restart, duplicate-identity, replay, and tamper handling. | **#206 — AWF-50.** `inputs/gitlog-v194-7fcaeec.txt:16`; `inputs/gh-merged.json:1`, PR #206; `7fcaeec:.agentic/docs/48-AWF50-DESIGN-PLAN.md:23`. |
| Authenticated backlog snapshots | Adds a read-only backlog reader and operating guidance for ceilings, overrides, and migration. This does not establish completed production trigger wiring. | **#212 — AWF-50.** `inputs/gitlog-v194-7fcaeec.txt:11`; `7fcaeec:.agentic/docs/48-AWF50-DESIGN-PLAN.md:19`; `7fcaeec:.agentic/docs/29-OPERATING-CONFIGURATION.md:39`. |
| Four functional automation switches | Makes implementation dispatch, critic requests, and amendment resumption obey a master switch plus their individual switches. Shipped values remain false; install and upgrade still require quiescence. | **#190 — AWF-36.** `inputs/gitlog-v194-7fcaeec.txt:26`; `7fcaeec:.agentic/docs/34-CONTINUOUS-CONTROLLER.md:121`. |
| Project status-report design | Records plans for outstanding-ticket counts, lane capacity, routing, merge-train state, and ETA reporting. This is design delivery; the production report is not established at the cutoff. | **#175 — AWF-55.** `inputs/gitlog-v194-7fcaeec.txt:35`; `7fcaeec:.agentic/docs/46-AWF55-STATUS-REPORT-TEST-PLAN.md:4`; `inputs/jira-release-195-196.json:483`. |

## Tooling

| Change | What it does and why it matters | PRs, tickets and evidence |
|---|---|---|
| Configurable model routing | Moves the model catalogue and routes into operator-owned `.agentic/routing.yaml`, with a loader that refuses invalid models, efforts, tiers, roles, or floors. | **#154 — AWF-107.** `inputs/gitlog-v194-7fcaeec.txt:50`; `inputs/gh-merged.json:1`, PR #154; `7fcaeec:.agentic/routing.yaml:1`. AWF-107 is absent from the supplied Jira snapshot. |
| Additional approved model | Adds `gpt-6.1-sol` at medium/high effort. The cutoff routing file provides the concrete shipped route values; earlier design decisions are not a substitute for that configuration. | **#148 — AWF-82.** `inputs/gitlog-v194-7fcaeec.txt:56`; `7fcaeec:.agentic/routing.yaml:6`. |
| Routing and cap decision records | Records owner decisions and later clarifications around routing, provenance, and Tier 3 extension policy. These documentation PRs do not independently change runtime behavior. | **#134, #137 — AWF-80, AWF-81, AWF-82**, with AWF-83 referenced as benchmark follow-up. `inputs/gitlog-v194-7fcaeec.txt:108`; `inputs/gitlog-v194-7fcaeec.txt:68`; `inputs/gh-merged.json:1`, PR #137. |
| Design-first trial | Records a design/adversarial-design-review trial while preserving independent critic authority and existing gates. Related AWF-76 context is documented; a dedicated ticket mapping is **UNVERIFIED**. | **#135.** `inputs/gitlog-v194-7fcaeec.txt:81`; `inputs/gh-merged.json:1`, PR #135. |
| Manifest synchronization tooling | Adds deterministic manifest merge support and CI rehearsal, reducing conflicts between independent changes. A later fix supplies command-scoped identity for merge rehearsal. | **#84, #132 — AWF-53, AWF-78.** `inputs/gitlog-v194-7fcaeec.txt:138`; `inputs/gitlog-v194-7fcaeec.txt:125`; `7fcaeec:scripts/manifest_merge_driver.py:143`; `7fcaeec:.github/workflows/manifest-check.yml:43`. |
| Safer release credential routing | Gives release tag publication an explicit credential route and rejects conflicting header configuration without retaining credentials in output. | **#62 — AWF-26.** `inputs/gitlog-v194-7fcaeec.txt:306`; `7fcaeec:.agentic/docs/32-RELEASE-PUBLISHING.md:17`. |
| Broader CI coverage | Adds portable-skill coverage and Windows installed-runtime checks, then adjusts when Windows checks run. | **#71, #85, #111 — AWF-43, AWF-47, AWF-71.** `inputs/gitlog-v194-7fcaeec.txt:384`; `inputs/gitlog-v194-7fcaeec.txt:353`; `inputs/gitlog-v194-7fcaeec.txt:195`. |
| Final-candidate Windows CI | Runs the Windows installed-runtime check for final non-draft candidates and applicable branch pushes; intermediate draft rounds use Linux CI. This scheduling change does not waive the final gate. | **#163 — AWF-145.** `inputs/gitlog-v194-7fcaeec.txt:49`; `inputs/gh-merged.json:1`, PR #163. |
| More robust install-test timing | Adjusts subprocess timeout handling in install tests and improves timeout diagnostics without changing their acceptance assertions. | **#139 — AWF-87.** `inputs/gitlog-v194-7fcaeec.txt:74`; `inputs/gh-merged.json:1`, PR #139. |
| Standalone test imports | Makes test modules import cleanly without relying on another test’s path setup. | **#123, #124 — AWF-75, AWF-77.** `inputs/gitlog-v194-7fcaeec.txt:149`; `inputs/gitlog-v194-7fcaeec.txt:147`; `inputs/jira-release-195-196.json:698`; `inputs/jira-release-195-196.json:718`. |
| Catalogue-derived parity checks | Derives expected schema counts from the generator catalogue rather than a hard-coded total. | **#121 — AWF-62.** `inputs/gitlog-v194-7fcaeec.txt:158`; `inputs/jira-release-195-196.json:557`. |
| Portable acceptance scripts | Replaces host-specific paths in acceptance tooling. | **#97 — AWF-64.** `inputs/gitlog-v194-7fcaeec.txt:238`; `inputs/jira-release-195-196.json:579`. |

### Jira-only completion claims

The following supplied tickets describe additional work, but their completion cannot be promoted to a confirmed AWF release feature solely from Jira’s Done status. Matching implementation, release membership, or complete second-source verification remains **UNVERIFIED**.

| Area | Tickets and reported scope | Evidence |
|---|---|---|
| Publication and freeze controls | AWF-84, AWF-103, AWF-104, AWF-105, AWF-142, AWF-144: immediate HOLD/freeze checks, pre-push synchronization and validation. | `inputs/jira-release-195-196.json:804`; `:1056`; `:1068`; `:1081`; `:1463`; `:1483`. |
| Model, review, and retry provenance | AWF-85, AWF-86, AWF-102, AWF-106, AWF-111, AWF-112, AWF-122, AWF-125, AWF-137, AWF-138. | `inputs/jira-release-195-196.json:817`; `:829`; `:1043`; `:1093`; `:1118`; `:1130`; `:1254`; `:1287`; `:1410`; `:1421`. |
| Test-preservation gates | AWF-99, AWF-113, AWF-121, AWF-133, AWF-139, AWF-147, AWF-153: empty-diff, deleted-test, parameterized-test, decorator, and alias handling. | `inputs/jira-release-195-196.json:1005`; `:1143`; `:1242`; `:1367`; `:1431`; `:1513`; `:1578`. |
| Controller ownership and scheduling | AWF-114, AWF-116, AWF-118, AWF-119, AWF-123, AWF-126, AWF-128, AWF-129, AWF-143. | `inputs/jira-release-195-196.json:1155`; `:1181`; `:1208`; `:1219`; `:1265`; `:1297`; `:1317`; `:1327`; `:1473`. |
| Regression-test maintenance | AWF-120, AWF-124, AWF-130, AWF-135, AWF-136, AWF-141: rollback cleanup, timing, process-start classification, and concurrent test-state handling. | `inputs/jira-release-195-196.json:1230`; `:1277`; `:1337`; `:1389`; `:1400`; `:1453`. |
| Compact installer recovery journal | AWF-134 reports change-sized recovery journals. Complete release attribution and implementation verification remain UNVERIFIED here. | `inputs/jira-release-195-196.json:1377`. |
| Benchmark follow-up | AWF-83 is marked Done **and `wont-do`**; it is not evidence of a shipped benchmark result. | `inputs/jira-release-195-196.json:790`. |

## Breaking changes

These changes can reject inputs or workflows previously accepted:

- **Stricter provenance at the final gate.** Missing commit-provenance runtime inputs, untagged code, and falsely mechanical script commits can prevent readiness. AWF-117 remains partially delivered. **#202 / AWF-117.** (`f9667fc0:.agentic/lib/agentic/gates.py:1270`; `7fcaeec:.agentic/docs/45-AWF117-PROVENANCE-GATE-TEST-PLAN.md:18`.)
- **Explicit draft-state authority.** Passing validation or CI does not authorize marking a PR ready. Exact-PR/head authorization must be authenticated and retained. **#107 / AWF-42.** (`7fcaeec:.agentic/docs/31-PUBLICATION-SAFETY.md:71`.)
- **Legacy qualification booleans are insufficient.** Live review-loop configuration requires a pinned qualification record, host binding, and freshness. **#80 / AWF-31.** (`7fcaeec:.agentic/docs/22-AUTOMATED-REVIEW-LOOP.md:25`.)
- **Legacy dispatch-state migration may refuse.** Non-terminal 1.9.4 state without the required durable inventory binding is refused while preserving its before-image. **#178 / AWF-91.** (`7fcaeec:.agentic/docs/34-CONTINUOUS-CONTROLLER.md:16`.)
- **Complete serialized-record limits apply.** Publication records and host commit-run logs exceeding the inclusive 1 MiB envelope limit refuse; framing and escaping count toward the limit. **#217, #220 / AWF-97.** (`7fcaeec:.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md:118`; `7fcaeec:.agentic/tests/test_awf97_r12_core.py:72`.)
- **Invalid routing configuration refuses.** Missing or malformed routing configuration and unsupported routes do not silently fall back. **#154 / AWF-107.** (`inputs/gh-merged.json:1`, PR #154; `7fcaeec:.agentic/routing.yaml:1`.)
- **Large-file allowances require tracked authority.** Supplemental or working-tree configuration cannot grant a large-file allowance. **#199 / AWF-154.** (`7fcaeec:.agentic/docs/31-PUBLICATION-SAFETY.md:43`.)

## Upgrade notes

Use the supplied [AWF 1.9.5 Install and Adoption Runbook draft](../runbook/INDEX.md). Its final publication path remains **UNVERIFIED**.

The companion runbook is supplied as repository parts through the index above. It describes conditional interfaces and pending work; it does not certify an integrated, published 1.9.5 upgrade. Historically, the runbook was not supplied to the cutoff review and was not found in its inspected `7fcaeec` tree; the pending d2-006d revision was unavailable in that review's permitted checkout. The companion's branch descriptions do not establish integration into the supplied snapshot. Complete 1.9.4-to-1.9.5 upgrade acceptance remains **UNVERIFIED**.

The available sources support these preparation steps:

1. **Quiesce automation before installation or upgrade.** All five automation switches must be false during that operation. Enable dispatch switches afterwards only through reviewed configuration. **#190 / AWF-36.** (`7fcaeec:.agentic/docs/34-CONTINUOUS-CONTROLLER.md:135`.)
2. **Preserve configuration, ownership, and recovery evidence.** Retain project-owned instructions, existing configuration, backups, and journals; recover unfinished installation first. **#88 / AWF-19; #73 / AWF-46.** (`7fcaeec:.agentic/docs/20-NEW-PROJECT-SETUP.md:12`; `7fcaeec:.agentic/docs/20-NEW-PROJECT-SETUP.md:21`.)
3. **Review new policy and routing inputs.** Check risk-tier defaults, real trusted-owner identities, required CI bindings, and routing configuration. Existing reviewer allocation and operating choices should not be silently replaced. **#86 / AWF-16; #126 / AWF-59; #154 / AWF-107.** (`7fcaeec:.agentic/docs/20-NEW-PROJECT-SETUP.md:34`; `7fcaeec:.agentic/docs/20-NEW-PROJECT-SETUP.md:36`; `7fcaeec:.agentic/routing.yaml:8`.)
4. **Retain installed-runtime verification evidence.** CONFIGURED requires successful installed verification and configuration validation, matching digests, and accepted results. A dry run does not establish installation. (`7fcaeec:.agentic/docs/20-NEW-PROJECT-SETUP.md:44`.)
5. **Re-observe activation after adoption acceptance.** Use absolute installed canonical runtime/script paths and an explicit repository root: `<installed-canonical-runtime> -B -I <installed-workflow.py> --root <repo-path> status --require-active --release-source <verified-release-source> --expected-manifest-sha256 <independently-approved-pin>`. The verified release source must be outside the project. Add `--adoption-pr <receipt-changing-pr-number>` when needed. Exit 4 means an observed state below ACTIVE; exit 2 means invalid input or no established project state. Inspect capability results separately: ACTIVE does not establish adapter readiness. The shorter command, omitting both explicit source/pin arguments, is valid when the trusted host AWF skill has a valid, complete, matching installation receipt and verified inventory at `$CODEX_HOME/skills/awf` (default `~/.codex/skills/awf`). This supported fallback does not make an arbitrary project receipt publisher authentication. Historical references: `7fcaeec:.agentic/docs/20-NEW-PROJECT-SETUP.md:54`; `7fcaeec:.agentic/docs/32-ACTIVATION-STATUS.md:11`. Supplied-snapshot observations: `main/.agentic/lib/agentic/cli.py:143`; `main/.agentic/lib/agentic/release_trust.py:63`; `main/.agentic/lib/agentic/release_trust.py:78`; `main/.agentic/docs/32-ACTIVATION-STATUS.md:17`; `main/.agentic/docs/32-ACTIVATION-STATUS.md:19`.
6. **Requalify live review hosts where required.** The documented qualification operation is `review_loop.py --config … qualify --confirm-disposable-pr`; its retained evidence must be inspected and pinned before live use. **#80 / AWF-31.** (`7fcaeec:.agentic/docs/22-AUTOMATED-REVIEW-LOOP.md:15`.)

Do not infer above-six stream support from planning primitives: the cutoff operating guide explicitly says the generated schemas still reject that configuration pending AWF-50 item 1. (`7fcaeec:.agentic/docs/29-OPERATING-CONFIGURATION.md:19`.)

## Known limitations

Release publishing is not supported on macOS in 1.9.5. The release step fails closed because the host lacks atomic no-follow release materialization primitives. Publish from Windows or Linux.

## Known issues

- **Release packaging is unfinished or unproven in these sources.** The historical cutoff version remains 1.9.4 (`7fcaeec:VERSION:1`; `inputs/jira-release-195-196.json:1630`); the supplied snapshot also declares 1.9.4 (`main/VERSION:1`; `main/MANIFEST.json:591`). Concrete blockers observed in that snapshot are archive validation's assertion that every controller value is false despite cadence 600 (`main/scripts/validate_archive.py:372`; `main/.agentic/PROJECT_CONFIG.yaml:344`), undefined `source_external_unit` and `installed_external_unit` report inputs (`main/scripts/validate_archive.py:504`), restrictive AC42 preservation acceptance needing the exact intended migration delta (`main/scripts/ac42_e2e.py:408`), and required external-resource admission deliberately recorded as uncovered/NOT_COVERED (`main/scripts/ac42_e2e.py:46`; `main/scripts/ac42_e2e.py:439`). Repair the assertion while preserving cadence 600; resolve both report inputs and demonstrate complete report serialization; accept only the exact intended migration delta; and exercise the required admission check. Integrated root/runtime/manifest/upgrade-target identities identifying 1.9.5, verified byte-exact predecessor registration, reproducible generated outputs, both final manifests regenerated after included changes, independent pins, candidate-bound review, required platform evidence including clean Windows, and publication acceptance remain required. No successful validation of those repairs was observed. A published 1.9.5 bundle and completed integrated upgrade acceptance remain **UNVERIFIED**.
- **Planning is partially delivered.** Above-six schema support remains pending, and production planning-trigger wiring is open in #219. AWF-50 remains In Progress. (`7fcaeec:.agentic/docs/29-OPERATING-CONFIGURATION.md:19`; `inputs/gh-open.json:1`, PR #219; `inputs/jira-release-195-196.json:421`.)
- **The richer project status report is not established as shipped.** #175 is a design plan; #192 contains pending test work. AWF-55 remains In Progress. (`7fcaeec:.agentic/docs/46-AWF55-STATUS-REPORT-TEST-PLAN.md:4`; `inputs/gh-open.json:1`, PR #192.)
- **Merge-train delivery remains incomplete.** AWF-54 is In Progress, the canonical-key successor is pending, and standalone authenticated CLI operation is deferred. The cutoff CLI explicitly refuses direct publication commands. (`inputs/jira-release-195-196.json:470`; `7fcaeec:.agentic/scripts/merge_train.py:323`; `7fcaeec:.agentic/docs/49-AWF54-R2-W1-W2-INTERFACE.md:13`.)
- **Cross-owner isolated Git remains pending.** #205 addresses exact-root trust and archive configuration; it is not part of the merged cutoff. (`inputs/gh-open.json:1`, PR #205; `inputs/jira-release-195-196.json:1620`.)
- **AWF-97 and AWF-117 remain In Progress.** Their merged slices do not establish complete bounded-I/O coverage or completion of all provenance-gate contracts. (`inputs/jira-release-195-196.json:978`; `inputs/jira-release-195-196.json:1195`.)
- **Jira recovery and counting retain known limitations.** UNKNOWN recovery can accept matching status without sufficient operation/actor proof; changelog completeness and pagination remain deferred. (`inputs/jira-release-195-196.json:1792`; `inputs/jira-release-195-196.json:1813`.)
- **Windows filesystem and timeout limitations remain.** Directory pinning, first-use ownership-lock races, backlog-path aliases, and descendant cleanup are tracked for 1.9.6. (`inputs/jira-release-195-196.json:1692`; `:1702`; `:1712`; `:1722`; `:1752`.)
- **One oversized-result timing assertion is flaky under load.** This is a tracked test limitation, not evidence of a completed fix. (`inputs/jira-release-195-196.json:1802`; `inputs/gh-merged.json:1`, PR #220.)

## Deferred to 1.9.6

Every row below has the supplied `release-1.9.6` label. Deferral is verified from that metadata; implementation status beyond the supplied records is **UNVERIFIED**.

| Ticket | Deferred work | Citation |
|---|---|---|
| **AWF-67** | Verify the verdict anchor on the provider’s rendered PR page. | `inputs/jira-release-195-196.json:615` |
| **AWF-146** | Enforce controller pace rules around lifted freezes, local commits under HOLD, and ticket freezes. | `inputs/jira-release-195-196.json:1503` |
| **AWF-149** | Version 1.9.6 tracking epic. | `inputs/jira-release-195-196.json:1535` |
| **AWF-159** | AWF-116 cross-check findings and import cross-check rework. | `inputs/jira-release-195-196.json:1640` |
| **AWF-160** | Migrate legacy batch audits into the AWF-91 store without rewriting history. | `inputs/jira-release-195-196.json:1653`; `7fcaeec:.agentic/docs/49-AWF54-R2-W1-W2-INTERFACE.md:13` |
| **AWF-161** | Standalone authenticated merge-train CLI, or a formal controller-only operating contract. | `inputs/jira-release-195-196.json:1666`; `7fcaeec:.agentic/docs/49-AWF54-R2-W1-W2-INTERFACE.md:13` |
| **AWF-162** | Evidence-backed diagnosis of hosted-CI-only batch failures without automatic culprit attribution. | `inputs/jira-release-195-196.json:1679`; `7fcaeec:.agentic/docs/49-AWF54-R2-W1-W2-INTERFACE.md:13` |
| **AWF-163** | Stronger Windows directory-pin access rights. | `inputs/jira-release-195-196.json:1692` |
| **AWF-164** | First-use issue-ownership lock initialization race. | `inputs/jira-release-195-196.json:1702` |
| **AWF-165** | Reject Windows backlog-path aliases that can bypass overlap detection. | `inputs/jira-release-195-196.json:1712` |
| **AWF-166** | Contain and terminate installed-self-test and AC42 subprocess trees on timeout. | `inputs/jira-release-195-196.json:1722` |
| **AWF-167** | Regression test pinning the publication-record disk-read bound. | `inputs/jira-release-195-196.json:1732` |
| **AWF-168** | Oversized allowed-file rename regression coverage. | `inputs/jira-release-195-196.json:1742` |
| **AWF-169** | AC42 outer/child timeout mismatch and surviving-child cleanup. | `inputs/jira-release-195-196.json:1752` |
| **AWF-170** | d2-006 journal-only hardening. | `inputs/jira-release-195-196.json:1762` |
| **AWF-171** | Complete-envelope cap for controller batch-audit records. | `inputs/jira-release-195-196.json:1772` |
| **AWF-172** | Bounded capture for remaining release-script, PowerShell, and Python routes. | `inputs/jira-release-195-196.json:1782` |
| **AWF-173** | Require operation/actor proof during Jira UNKNOWN recovery. | `inputs/jira-release-195-196.json:1792`; `inputs/gh-merged.json:1`, PR #221 |
| **AWF-174** | Load-sensitive oversized-result timing assertion. | `inputs/jira-release-195-196.json:1802`; `inputs/gh-merged.json:1`, PR #220 |
| **AWF-175** | **AWF-76 follow-up:** positive Jira changelog completeness using pagination metadata, plus the reference-adapter pagination gap. | `inputs/jira-release-195-196.json:1813` |

**AWF-76 split:** #130’s stall policy, live App binding, and closeout/Jira binding are merged 1.9.5 work. The remaining changelog-completeness work belongs to AWF-175 in 1.9.6; the entire AWF-76 feature is not deferred. (`inputs/gitlog-v194-7fcaeec.txt:119`; `inputs/jira-release-195-196.json:708`; `inputs/jira-release-195-196.json:1813`.)

## Pending merge

These items are excluded from the merged feature claims above.

| Item | Pending scope and significance | Evidence |
|---|---|---|
| **#219 — AWF-50 — pending merge** | Production planning triggers, including planning before dispatch and recomputation from controller events. Supplied head: `960a9d6`. | `inputs/gh-open.json:1`; `7fcaeec:.agentic/docs/48-AWF50-DESIGN-PLAN.md:13` |
| **#205 — AWF-157 — pending merge** | Exact resolved-root Git trust with archive/configuration pins, addressing cross-owner checkout failures. Supplied head: `c407bcf`. | `inputs/gh-open.json:1`; `inputs/jira-release-195-196.json:1620` |
| **#198 — AWF-117 — pending merge** | AC1 provenance-gate test slice. Tests-only review evidence, not independently a complete implementation. Supplied head: `d3cd025`. | `inputs/gh-open.json:1`; `7fcaeec:.agentic/docs/45-AWF117-PROVENANCE-GATE-TEST-PLAN.md:17` |
| **#197 — AWF-117 — pending merge** | Critic-run-carrier and fallback-owner-override test slice. Supplied head: `cc46057`. | `inputs/gh-open.json:1`; `7fcaeec:.agentic/docs/45-AWF117-PROVENANCE-GATE-TEST-PLAN.md:20` |
| **#192 — AWF-55 — pending merge** | Project status-report tests; the supplied body retains the production-code hold. Supplied head: `84bf543`. | `inputs/gh-open.json:1`; `7fcaeec:.agentic/docs/46-AWF55-STATUS-REPORT-TEST-PLAN.md:4` |
| **#191 — AWF-117 — pending merge** | AC2/AC3/AC6 tests and generated-contract coverage. #202 already delivers a narrower production gate slice; this open PR is not additional merged functionality. Supplied head: `0247c6c`. | `inputs/gh-open.json:1`; `inputs/gitlog-v194-7fcaeec.txt:18` |
| **AWF-54 :657 successor — pending merge; no PR yet** | Branch `awf/1.9.5-awf54-657-canonical-key-successor`, head `7ddb195`. Changes reconciliation identity handling to accept numeric IDs or canonical keys and compare the corresponding returned identity. Full acceptance verification is **UNVERIFIED**. | User-supplied pending scope; `7ddb195:.agentic/adapters/reference_controller_adapter.py:649`; `7ddb195:.agentic/adapters/reference_controller_adapter.py:658` |
| **d2-006d — pending merge; no PR yet** | Branch `awf/1.9.5-awf195up-upgrade-path-1-9-4`, head `9f44c2a1`. **UNVERIFIED:** that revision was unavailable in the permitted checkout; implementation, ticket mapping, and validation cannot be cited. | User-supplied pending scope only. Related deferred limitation: `inputs/jira-release-195-196.json:1762`. |
| **POSTSUITE-001 — pending merge; successor not yet written** | Planned successor to d2-006d. **UNVERIFIED:** no implementation, revision, PR, or test evidence supplied. | User-supplied pending scope only. |

## Appendix: reading and verification coverage

### Files read in full

Repository files are at `7fcaeec` unless otherwise noted. Line counts include blank lines.

| File | Lines |
|---|---:|
| `inputs/gitlog-v194-7fcaeec.txt` | 520 |
| `inputs/gh-open.json` — all six records and bodies | 1 |
| `inputs/jira-release-195-196.json` — all 156 records and fields | 1,824 |
| `AGENTS.md` — permitted working checkout | 30 |
| `VERSION` | 1 |
| `.agentic/SPECIFICATION.md` | 53 |
| `.agentic/docs/20-NEW-PROJECT-SETUP.md` | 68 |
| `.agentic/docs/22-AUTOMATED-REVIEW-LOOP.md` | 114 |
| `.agentic/docs/23-TICKET-LIFECYCLE.md` | 60 |
| `.agentic/docs/27-MODEL-ROUTING.md` | 83 |
| `.agentic/docs/29-OPERATING-CONFIGURATION.md` | 91 |
| `.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md` | 74 |
| `.agentic/docs/31-PUBLICATION-SAFETY.md` | 126 |
| `.agentic/docs/32-ACTIVATION-STATUS.md` | 25 |
| `.agentic/docs/32-RELEASE-PUBLISHING.md` | 47 |
| `.agentic/docs/34-CONTINUOUS-CONTROLLER.md` | 141 |
| `.agentic/docs/40-EXTERNAL-RESOURCES.md` | 132 |
| `.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md` | 139 |
| `.agentic/docs/42-PR-INTEGRATION-QUEUE.md` | 98 |
| `.agentic/docs/43-MERGE-TRAIN.md` | 100 |
| `.agentic/docs/45-AWF117-PROVENANCE-GATE-TEST-PLAN.md` | 53 |
| `.agentic/docs/46-AWF55-STATUS-REPORT-TEST-PLAN.md` | 76 |
| `.agentic/docs/47-AWF54B-TEST-PLAN.md` | 44 |
| `.agentic/docs/48-AWF50-DESIGN-PLAN.md` | 55 |
| `.agentic/docs/49-AWF54-R2-W1-W2-INTERFACE.md` | 34 |
| `.agentic/routing.yaml` | 22 |
| `.github/workflows/manifest-check.yml` | 111 |
| `scripts/manifest_merge_driver.py` | 226 |
| `.agentic/scripts/merge_train.py` | 330 |
| `.agentic/scripts/workflow.py` | 59 |
| `.agentic/tests/test_awf154_large_file_allowances.py` | 236 |
| `.agentic/tests/test_awf34_configured_allowance_closure.py` | 128 |
| `.agentic/tests/test_awf97_r12_core.py` | 110 |

### Partially inspected files — UNVERIFIED for full-file review

| File | Total lines | Coverage |
|---|---:|---|
| `inputs/gh-merged.json` | 1 | All records parsed for membership; eligible titles and selected complete bodies inspected. Remaining bodies not fully reviewed. |
| `.agentic/schemas/project-config.schema.json` | 1,477 | Selected configuration declarations. |
| `.agentic/lib/agentic/configuration.py` | 331 | Selected declarations and #199 production diff. |
| `.agentic/lib/agentic/publication.py` | 2,811 | Selected declarations and #199 production diff. |
| `.agentic/lib/agentic/model_routing.py` | 1,538 | Selected routing declarations. |
| `.agentic/lib/agentic/upgrade.py` | 903 | Selected migration declarations. |
| `.agentic/lib/agentic/providers/github_review_host.py` | 1,768 | #196 production diff and selected declarations. |
| `.agentic/lib/agentic/gates.py` | 1,420 | #202 production diff. |
| `.agentic/adapters/reference_controller_adapter.py` | 1,454 | Pending-successor identity diff; count is for the cutoff version. |

### Outstanding exhaustive-review requirements

The cutoff diff contains **330 changed files**, approximately **74,927 added lines** and **4,547 removed lines**. The remaining files were not fully read. Complete caller/callee traversal and the requested one-row-per-function, call-site, and hunk tables were **not completed — UNVERIFIED**.

Second-source implementation verification is also incomplete for entries supported only by release metadata or PR descriptions. Historical test totals in PR bodies were not reproduced. The companion install/adoption runbook is supplied as repository parts through [the runbook index](../runbook/INDEX.md); its final publication path remains **UNVERIFIED**. This review does not independently establish the pending d2-006d implementation or a POSTSUITE-001 successor. The companion's branch descriptions do not establish integration into the supplied snapshot. Integrated upgrade acceptance and final 1.9.5 release artifacts remain **UNVERIFIED**.

**STATUS: GAPS**