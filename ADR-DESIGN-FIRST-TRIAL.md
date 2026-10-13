# Design-first trial for hard Tier 3 tickets

**Status:** trial in progress. Its rules and decision criteria are accepted by owner rulings of 13 October 2026 and bind once this PR merges. The trial changes no cap, gate, critic authority, lane order or merge control.

## Context

Seventeen of the last 19 reviews in 1.9.5 needed another fix round, and AWF-10 passed only at round 8. The hypothesis is that many rounds trace to specification gaps that are catchable before any code exists.

The owner launched the trial at 11:10 on 12 October 2026 for AWF-76 and AWF-14 phase 2, before either started coding. The launch was relayed and recorded in #130's description, and the owner confirmed it directly on 13 October 2026.

## Decision

### Method

1. One Codex writer drafts a design: the files to change, the acceptance tests and the invariants.
2. A separate Codex run reviews the design adversarially. It writes no code and reports blocking, important and minor gaps.
3. One accountable Codex writer implements the work and addresses the blocking and important findings.
4. The normal independent critic keeps PASS/FAIL authority. Caps, gates and merge controls are unchanged.

The design phase is time-boxed to about 30 minutes and touches no candidate files.

### Rules

* **Not a review round.** A design review counts towards no cap and is never a PASS or FAIL.
* **Off-branch records.** The design and its findings are stored on the ticket, in the PR description, or in operator state referenced from the PR description. They are never committed to the PR branch, because a commit would move the head.
* **Recorded runs.** Each run records its id, model, effort and Codex CLI version, per [ADR-LANE-MODEL-ROUTING](ADR-LANE-MODEL-ROUTING.md). The design reviewer is never weaker than the design writer.
* **Findings carry ids** (for example `AWF-76-D-001`) and a severity.
* **Verified dispositions.** Before the first code review, the controller records each blocking and important finding as implemented, superseded or rejected with a reason. The critic receives that list as context and judges the code independently.

### Measures

* **Baseline:** the median and distribution of implementation rounds to PASS for Tier 3 tickets merged in 1.9.5 before the trial, excluding VOID rounds.
* **Primary measure:** implementation rounds to PASS for each trial ticket.
* **Mechanism:** each P1 in a failed round is classified as one of:
  * *spec gap caught but unresolved*;
  * *spec gap missed*;
  * *implementation error*.
* **Cost:** the design phase's time and usage, and cap dispositions per ticket.

### Selection and decision (owner ruling: sample of 4)

* **Selection:** the controller marks a ticket "hard Tier 3" with a reason **before work starts**. Tickets are never added after a result is known.
* **Sample:** **4 tickets**, including AWF-76 and AWF-14 phase 2. The release has few hard Tier 3 tickets left.
* **The 4-ticket result decides whether design-first continues** for hard Tier 3 work in 1.9.5. Continue if all three hold:
  * median rounds are at least one below the baseline median;
  * the share of spec-gap-missed P1s falls;
  * cap dispositions per ticket do not rise.

  Otherwise stop.
* **Making it standard practice** is confirmed against further tickets in the next release, because four tickets are a small sample.

## Evidence so far

* **Design phase:** about 17 minutes. Design review found 5 blocking issues on AWF-76 and 4 on AWF-14 phase 2. These are leading indicators only.
* **AWF-76** (#130) passed on round 4 (`critic-c195-r4-027`, exact-head CI green) and merged as `d3116ba`.
  * Its round 4 was over the Tier 3 cap. The owner approved it at 21:30 on 12 October 2026, and it is recorded in #130's description. [ADR-TIER3-CAP-EXTENSION](ADR-TIER3-CAP-EXTENSION.md) ratifies one Tier 3 extension going forward and records this round as an exception.
  * Its input was already precise. It is not compared with AWF-10, a larger parent ticket; the baseline replaces that comparison.
* **AWF-14 phase 2:** awaiting code review.

## Open items

* Clarify whether "replaces parallel writers" means several writers on one ticket, which `one_writer_per_ticket` forbids, or parallel lanes on different tickets.
* Compute the baseline. Classify AWF-76's failed-round P1s.
* Give the trial tickets' design findings ids and record their dispositions.
