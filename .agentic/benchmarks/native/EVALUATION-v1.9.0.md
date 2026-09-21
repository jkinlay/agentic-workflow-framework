# Native decision evaluation — 1.9.0 (prepared, NOT_RUN)

Release 1.9.0 adds two public case sets and runs no model.

| Suite | Cases | Rubric | Status |
| --- | --- | --- | --- |
| routine (Jira lifecycle, owner closure, closeout comment) | NATIVE-42, 43, 44, 45 | `routine-rubric.json`, `routine-measurement-rubric.json` | prepared; graded structurally by `routine_decisions.py` in the test suite; **no model observation** |
| review-policy (tiers, cap, bases, dispositions) | NATIVE-46–51 | `review-policy-rubric.json` | prepared; **no model observation** |

`prepare_routine_pilot.py` now stages twelve routine cases as four batches of three; `benchmark_native.py prepare --suite review-policy` stages the six review-policy cases. Any future run follows the retained [v1.8.4 protocol](EVALUATION-v1.8.4.md): pins frozen before responses, one call per case, no retries, strict and unadjusted reporting. Historical grades (10/12, 5/12, 1/3 strict FAIL; routine 7/8 substantive, 3/8 strict) are unchanged and no causal claim about 1.9.0 prompts is made.
