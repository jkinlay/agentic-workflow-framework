# Design-first trial for hard Tier 3 tickets

**Status:** trial in progress. Merging records its rules and decision criteria. It changes no cap, gate, critic authority, lane order or merge control. **Dates:** the owner approved the trial on 12 October 2026; this record was written on 13 October 2026.

## Context

Seventeen of the last 19 reviews in 1.9.5 needed another fix round, and AWF-10 passed only at round 8. The hypothesis is that many rounds trace to specification gaps that are catchable before any code exists.

[30-REVIEW-TIERS-AND-CLOSEOUT](.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md) makes Tier 3's three-round cap unextendable, and the configuration sets `tier3_review.max_rounds: 3`. Tier 3 round counts above three therefore need reconciling (see "Open items").

The AWF project bot proposed the trial. The owner approved it for AWF-76 and AWF-14 phase 2, before either started coding.

## Decision

### Method

1. One Codex writer drafts a design: the files to change, the acceptance tests and the invariants.
2. A separate Codex run reviews the design adversarially. It writes and reviews no code, and it reports blocking, important and minor gaps.
3. One accountable Codex writer implements the work and addresses the blocking and important findings.
4. The normal independent critic keeps PASS/FAIL authority. Caps, gates, lane order and merge controls are unchanged.

The design phase is time-boxed to about 30 minutes. It touches no candidate files, so it can run while implementation lanes wait.

### Rules

* **Not a review round.** A design review counts towards no cap and is never a PASS or FAIL.
* **Off-branch records.** The design and its findings go on the ticket or in the PR description, never in a branch commit. A commit would move the head and invalidate evidence.
* **Recorded runs.** Each run records its id, model, effort and Codex CLI version, per the companion `ADR-LANE-MODEL-ROUTING.md`. The design reviewer is never weaker than the design writer.
* **Findings carry ids** (for example `AWF-76-D-001`) and a severity.
* **Verified dispositions.** Before the first code review, the controller records each blocking and important finding as implemented, superseded or rejected with a reason. The critic receives that list as context and still judges the code independently.

### Measures

* **Baseline:** the median and distribution of implementation rounds to PASS for Tier 3 tickets merged in 1.9.5 before the trial, excluding VOID rounds.
* **Primary measure:** implementation rounds to PASS for each trial ticket.
* **Mechanism:** each P1 in a failed round is classified as one of:
  * *spec gap caught but unresolved*: the design review raised it;
  * *spec gap missed*: the design review didn't catch it;
  * *implementation error*: the specification was adequate and the code was wrong.
* **Cost:** the design phase's time and usage, and cap dispositions per ticket.

### Selection and decision

* **Selection:** the controller marks a ticket "hard Tier 3" with a reason **before work starts**. Tickets are never added after a result is known.
* **Sample:** at least 6 tickets. The result after AWF-14 phase 2 is only an interim check.
* **Adopt** for hard Tier 3 if all three hold:
  * median rounds are at least one below the baseline median;
  * the share of spec-gap-missed P1s falls;
  * cap dispositions per ticket do not rise.
* **Stop** if, after 6 tickets, the median is no better than baseline.

## Evidence so far

* **Design phase:** about 17 minutes. Design review found 5 blocking issues on AWF-76 and 4 on AWF-14 phase 2. These are leading indicators only.
* **AWF-76** (PR #130) passed on round 4 (`critic-c195-r4-027`, exact-head CI green) and merged as `d3116ba`. Its input was already precise: findings routed from AWF-10 critic rounds. It is not compared with AWF-10, a larger parent ticket; the baseline replaces that comparison.
* **AWF-14 phase 2:** awaiting code review.

## Open items

* Cite the primary record of the owner's 12 October approval.
* Reconcile AWF-76's round 4 and AWF-10's round 8 with the Tier 3 cap: evidence-only or VOID rounds, or the owner disposition used. This record does not excuse them.
* Clarify whether "replaces parallel writers" means several writers on one ticket, which `one_writer_per_ticket` forbids, or parallel lanes on different tickets.
* Compute the baseline. Classify AWF-76's failed-round P1s.
* Record the trial tickets' designs and findings, with ids, on their tickets.
