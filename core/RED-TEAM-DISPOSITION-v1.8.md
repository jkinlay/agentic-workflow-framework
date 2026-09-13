# Re-review disposition

AWF 1.8 follows the 13 September 2026 re-review of the prior core release. Implementation and offline evidence are distinct from live qualification.

| Finding | Decision and implemented boundary |
| --- | --- |
| External-review removal | Partially accepted: the public gate and Copilot-required policy survived consolidation, but the Claude adapter did not. Restore that adapter inside the core, with opt-in workflow, tests and explicit project qualification. Preserve public gate/policy unchanged. |
| Documentation overcut | Accept. Remove pointer stubs, restore actionable migrations and substantive native prompts. Use per-file limits and report totals rather than a global cap. |
| Behavioral evidence | Accept. Provide repeatable seeded-defect and prompt-injection decision cases with exact packet/prompt bindings and separate evaluator rubrics. Decision smoke is not proof of host actions or model-quality equivalence. |
| Reconciliation loop | Accept. Add finite ticket-wide reconciled-incident retries, checked at admission across policy/role/phase changes. Safe closure remains possible; charges and audit persist. Infrastructure incidents are not automatically reasoning failures. |
| Authentication wording | Accept. The helper records operator assertions; authentication and observed termination belong to the trusted host/operator. |
| Evidence cohort drift | Accept. Keep prior hashes/cohorts queryable and separate; omission of the new optional retry field does not rewrite policy. |
| Hygiene and CLI gaps | Accept. Check the real source, future major versions and additional text formats; add failure-path/process tests and source/installed coverage. |
| Private security contact | Accept. Publisher-designated private email is in SECURITY.md. No response SLA is implied. |
| Signed releases | Defer key creation until custody and verifier distribution are approved. State unsigned status explicitly; co-delivered hashes are integrity checks, not publisher authentication. |

Duplicate scaffold/framework trees are not restored. Git history preserves retired material. External-engine live review, permission qualification and human merge approval remain separate release gates; local passing tests must not be presented as satisfying them.
