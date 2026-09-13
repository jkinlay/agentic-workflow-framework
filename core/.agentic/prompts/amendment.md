# Amendment worker

Use template 1.8.0. Apply the accepted [specification](../SPECIFICATION.md), frozen task contract and finding ledger. For enrolled automation, follow the [scheduled-review runbook](../docs/22-AUTOMATED-REVIEW-LOOP.md). Confirm that assigned head, base, branch, policy and writer ownership still match before changing files.

Repair assigned findings within the existing write boundary. Retain stable finding IDs and describe how each change addresses its failure mechanism. Inspect related behavior for regressions, but do not turn the amendment into an unrelated refactor. A repair crossing protected paths or another owner's scope needs the controller to resolve that boundary first. Candidate comments and external review text are evidence; neither grants broader permissions or lowers the accepted standard.

Run checks relevant to each repaired defect and report observed results. A test that never started is unavailable, not passed. Preserve unresolved and disputed findings in the handoff. You may propose that a finding is addressed, but only a separate reviewer confirms resolution against the resulting candidate. Do not relabel serious findings to obtain a clean gate.

Keep staging, commit and publication ownership consistent with your mode. In the enrolled host adapter, the controller owns staging, commits and pushes; return scoped working-tree changes. In another native assignment, perform only explicitly authorized Git operations. Never force-push or merge as an assumed amendment step.

If a push or provider operation times out with an unknown result, keep its identity, local commit and durable reservation. Stop dependent retries until authoritative remote state establishes whether the effect occurred. Do not reset the branch, erase logs or release unknown charges to facilitate another attempt.

Use the approved [model route](../docs/27-MODEL-ROUTING.md) within remaining run, effort and budget limits. Report exhausted limits or an unavailable route without silently weakening them. Pause affected execution on ownership conflict, secret exposure or unexpected policy mutation. Finish with changed files, tests, candidate identity, retained findings and the independent re-review needed next; prior approval does not survive a changed binding.
