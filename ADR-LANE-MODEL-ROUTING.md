# Lane model routing: Luna-first writers, stronger reviewers, early escalation

**Status:** proposed. The owner accepts it by merging this PR; it then binds the 1.9.5 routing work below. Merging changes no configuration, code or lane behaviour by itself. **Date:** 13 October 2026.

## Context

[27-MODEL-ROUTING](.agentic/docs/27-MODEL-ROUTING.md) ships these defaults:

* controller `gpt-5.6-sol`/high;
* workers `gpt-5.6-luna`/medium;
* critic `gpt-5.6-sol`/high;
* at most two escalations per ticket.

The reviewed allowlist already includes `gpt-6-luna`, `gpt-6-sol` and `gpt-6-astra`.

Observed on the owner's lane host, 13 October 2026:

* The operator-local lane scripts hard-code `gpt-5.6-sol`/high for writer, reviewer and design runs. They bypass AWF routing, floors, escalation and the ledger.
* No run records its model or effort.
* Review comments state Codex CLI 0.149.0 while 0.162.0 is installed, so that field is not observed.
* The host offers `gpt-6-luna`, `gpt-6.1-sol` and `gpt-6-astra`, with efforts up to ultra. It shows no prices or limits.
* One weekly allowance covers all runs.

In the owner's simple-task comparison, `gpt-6-luna` was faster, cheaper and as good. Those results are to be recorded as benchmark evidence.

Options considered:

| Option | Why not chosen |
| --- | --- |
| A. Raise everything to `gpt-6.1-sol`/high | Raises the cost of every run without evidence that model weakness causes the failures. |
| B. Escalate after two failed rounds | Too late: Tier 3's three-round cap is unextendable ([30-REVIEW-TIERS-AND-CLOSEOUT](.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md)). |
| C. A plus B | Inherits both problems. |

## Decision (option D)

### 1. Provenance first

Every lane run records:

* role, run id, ticket, PR and head;
* tier and escalation step;
* requested and observed model and effort (observed may be `unobserved`);
* the Codex CLI version read at launch;
* tokens, or `unknown` (never zero);
* outcome.

The critic comment carries the observed model, effort and CLI version beside `Critic run:`. The ledger reserves the requested route and settles the observed one. A one-week baseline precedes any routing switch.

### 2. Review rules

* **R-1:** the reviewer is never weaker than the writer.
* **R-2:** a reviewer change mid-PR forces a full fresh review.

Routes rank by model (`gpt-6-luna` < `gpt-6.1-sol` < `gpt-6-astra`), then by effort.

### 3. Defaults

| Work | Writer | Reviewer | Design / spec review |
| --- | --- | --- | --- |
| Tier 1–2 | `gpt-6-luna`/medium | `gpt-6.1-sol`/high | — |
| Tier 3 | `gpt-6.1-sol`/high | `gpt-6.1-sol`/high | `gpt-6.1-sol`/xhigh |
| High-risk | At least `gpt-6.1-sol`/high | At least `gpt-6.1-sol`/high | `gpt-6.1-sol`/xhigh |

Specialist routes are unchanged. Mandatory floors still apply.

### 4. Escalation

Ladder: `gpt-6-luna`/medium → `gpt-6.1-sol`/high → `gpt-6.1-sol`/xhigh → `gpt-6-astra`/xhigh.

The last step applies only to an owner-authorized round beyond the cap, where policy permits one. Each of these moves a ticket up one step:

* a failed local validation, before the critic, so no round is spent;
* a critic P1;
* a durable reasoning or implementation failure.

Further rules:

* Escalation is monotonic, within the configured two escalations.
* The reviewer rises with the writer.
* Caps never reset.
* Max and ultra effort need an owner ruling per ticket.
* Infrastructure, credential, rate-limit and unknown failures never change the model.

### 5. Rollout and revert

**Measure per tier:**

* first-round PASS rate;
* rounds per merge;
* escalations;
* cap dispositions;
* time and usage per merge, critics included;
* escaped defects.

Review after 15 tickets per tier, or two weeks.

**Revert triggers:**

| Trigger | Action |
| --- | --- |
| A tier's Luna first-round PASS rate falls more than 10 points below baseline over 15 tickets | That tier's writer → `gpt-6.1-sol`/high |
| An escaped defect attributable to Luna | Owner review before continuing |
| Allowance above 80% with more than two days to reset | Escalation beyond `gpt-6.1-sol`/high pauses except for release-critical work |

Tier 2 uses Luna from day one under these triggers. The routing switch never delays the open 1.9.5 Tier 3 core work.

## Consequences

**Follow-up Tier 3 tickets** (Codex writes, independent critic, exact-head CI, owner merge):

1. Per-run provenance. This relates to AWF-49.
2. R-1 and R-2 in the critic launch path.
3. A governance PR adding `gpt-6.1-sol` to `model_order`, the role allowlists and the effort lists, and confirming whether it supersedes `gpt-6-sol`.
4. Dispatch through `suggest` → `reserve` → `settle` with these defaults: in the controller adapter (AWF-36) and, meanwhile, in the operator-local lane scripts. This depends on AWF-48 and AWF-74.

**Effects:**

* Writer cost falls where most runs are, and review cost rises modestly.
* Unservable routes still return `unavailable`, never a silent fallback. Mismatches between requested and observed models are reported.
* The metrics, not the pilot, decide whether Luna stays.

**Unchanged:** budgets, pins, floors, review caps and owner merge authority.
