# Lane model routing: Luna-first writers, stronger reviewers, early escalation

**Status:** Owner rulings 2026-10-13 05:56 BST (Jonathan, relayed by DevHead): D1 option D, D2, D3, D5, D6 approved; accepted on merge. Merging changes no configuration, code or lane behaviour by itself.

## Context

[27-MODEL-ROUTING](.agentic/docs/27-MODEL-ROUTING.md) defaults: controller `gpt-5.6-sol`/high, workers `gpt-5.6-luna`/medium, critic `gpt-5.6-sol`/high; two escalations per ticket.

Observations, 13 October 2026:

* Lane scripts hard-code every run to `gpt-5.6-sol`/high, bypassing routing, floors, escalation and ledger.
* Runs omit provenance; review comments cite Codex CLI 0.149.0 while 0.162.0 is installed. AWF-80 removes the hard-coded string.
* Host offers `gpt-6-luna`, `gpt-6.1-sol` and `gpt-6-astra` through ultra under a weekly allowance.

`gpt-6-luna` matched quality faster and cheaper in owner simple-task tests.

| Option | Why not chosen |
| --- | --- |
| A. Raise everything to `gpt-6.1-sol`/high | Unproven cost. |
| B. Escalate after two failed rounds | Too late for caps (AWF-81). |
| C. A plus B | Both. |

## Decision (option D)

### Owner rulings, 13 October 2026

| Ruling | Decision |
| --- | --- |
| D1 | Adopt option D. |
| D2 | Approve provenance and the reviewer rules first. |
| D3 | Luna starts on Tier 1 only. |
| D5 | The provenance change lands in 1.9.5 now. The routing switch (follow-ups 3 and 4) follows the open Tier 3 core. |
| D6 | The owner records the Luna test results as benchmark evidence. |

### 1. Provenance

Every lane run records:

* role, run id, ticket, PR and head;
* tier and escalation step;
* requested and observed model and effort;
* the Codex CLI version read at launch;
* tokens, or `unknown` (never zero);
* outcome.

Critic comments carry observed model, effort and CLI version beside `Critic run:`. The ledger reserves requested routes and settles observed ones. A one-week baseline precedes the routing switch.

### 2. Review rules

* **R-1:** the reviewer is never weaker than the writer.
* **R-2:** a reviewer change mid-PR forces a full fresh review.

Routes rank by model (`gpt-6-luna` < `gpt-6.1-sol` < `gpt-6-astra`), then by effort.

### 3. Defaults

| Work | Writer | Reviewer | Design / spec review |
| --- | --- | --- | --- |
| Tier 1 | `gpt-6-luna`/medium | `gpt-6.1-sol`/high | — |
| Tier 2 | `gpt-6.1-sol`/high | `gpt-6.1-sol`/high | — |
| Tier 3 and high-risk | `gpt-6.1-sol`/high | `gpt-6.1-sol`/high | `gpt-6.1-sol`/xhigh |

**Tier 2 moves to `gpt-6-luna`/medium** by owner ruling once about 10 Tier 1 Luna tickets pass cleanly, meaning a PASS with no escalation and no cap disposition. Specialist routes and mandatory floors are unchanged.

### 4. Escalation

Ladder: `gpt-6-luna`/medium → `gpt-6.1-sol`/high → `gpt-6.1-sol`/xhigh → `gpt-6-astra`/xhigh.

The last step applies only to an owner extension beyond the cap: Tier 2 `EXTEND_ONE_CYCLE`, or Tier 3 under [ADR-TIER3-CAP-EXTENSION](ADR-TIER3-CAP-EXTENSION.md). The owner-signed `review-cap-disposition` remains the gate record; an `AWF-OVERCAP-APPROVAL` comment may echo it, to be settled in AWF-81 and [Chapter 30](.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md#the-amendment-cap). Each of these moves a ticket up one step:

* a failed local validation, before the critic, so no round is spent;
* a critic P1;
* a durable reasoning or implementation failure.

Further rules:

* Escalation is monotonic, within the configured two escalations.
* The reviewer rises with the writer.
* Max and ultra effort need an owner ruling per ticket.
* Infrastructure and unknown failures never change the model.

### 5. Rollout and revert

**Measure per tier:**

* first-round PASS rate;
* rounds per merge;
* escalations;
* cap dispositions;
* usage per merge, critics included;
* escaped defects.

Review after 15 tickets per tier, or two weeks.

**Revert triggers:**

| Trigger | Action |
| --- | --- |
| Luna's first-round PASS rate falls more than 10 points below baseline over 15 tickets | Writer → `gpt-6.1-sol`/high |
| An escaped defect attributable to Luna | Owner review |
| Allowance above 80% with more than two days to reset | Escalation beyond `gpt-6.1-sol`/high pauses except for release-critical work |

## Consequences

**Follow-up Tier 3 tickets** (Codex writes, independent critic, exact-head CI, owner merge):

1. Per-run provenance (AWF-80; relates to AWF-49).
2. R-1 and R-2 in the critic launch path (AWF-80).
3. Add `gpt-6.1-sol` to `model_order`, allowlists and efforts; confirm it against `gpt-6-sol` (AWF-82).
4. Dispatch through `suggest` → `reserve` → `settle` in the controller adapter (AWF-36) and lane scripts (AWF-82); depends on AWF-48/AWF-74.

Changes 1 and 2—provenance, R-1/R-2 and R-3—ship in 1.9.5 now. Changes 3 and 4 follow the 1.9.5 Tier 3 core without delaying release. R-3 permits no routing switch before AWF-80 merges and the baseline week ends. AWF-83 measures rollout; AWF-81 corrects cap handling.

**Effects:** unservable routes still return `unavailable`; model mismatches are reported. Budgets, pins, floors, review caps (subject to AWF-81) and owner merge authority are unchanged.
