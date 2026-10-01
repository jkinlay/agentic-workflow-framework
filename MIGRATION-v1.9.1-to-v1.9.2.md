# Upgrade AWF 1.9.1 to 1.9.2

AWF 1.9.2 makes the controlled rehearsal path usable on token-reporting hosts without weakening review, merge, reconciliation or Jira boundaries.

New adoptions use token-only budgets: execution and routing allow 2,000,000 tokens and 12 runs per ticket, while routing allows 30,000,000 tokens and 250 runs per project day; both execution monetary ceilings and both routing monetary ceilings remain `null`. The increase is based on an owner workload of 10–20 tickets a day and measured runs: signal-lab SL-1 used 282,975 tokens in 3 runs, the 77-file AWF 1.9.2 PR used 1,402,537 tokens in 6 runs across three critic rounds, and a full ticket takes 8–9 runs. Null means no monetary ceiling. If any effective monetary ceiling is an integer, verified reservation and actual cost remain required exactly as before. Token ceilings, run ceilings, duplicate guards and overrun quarantine remain active.

Run `python -B scripts/bootstrap_project.py --mode upgrade --dest PROJECT --expected-manifest-sha256 SHA256` from the verified 1.9.2 source. A verified 1.9.1 receipt is accepted directly. The upgrade changes only `template.expected_workflow_version` from 1.9.1 to 1.9.2 in `PROJECT_CONFIG.yaml`; every other byte is preserved, including comments, ordering, line endings, and every owner-set budget. Existing configurations are never rewritten to these defaults; owners opt in only through a separately reviewed governance PR. Do not silently rewrite accepted project budgets.

A worker whose sandbox cannot write Git metadata now records `commit_route: PUBLISHER`, leaves the validated tree uncommitted and reports BLOCKED only for the commit. The assigned publisher commits exactly that worktree, verifies the committed tree equals the tested tree, then publishes the draft PR. The publisher must not repair or change content; any mismatch returns to the worker. Missing `commit_route` retains the legacy `WORKER` meaning.

Host preflight adds `project_lint_scope`. Ruff or flake8 configuration that includes managed `.agentic` files produces a non-blocking WARN. Add `extend-exclude = [".agentic"]` under `[tool.ruff]`, or the equivalent `.agentic` flake8 exclusion, in project-owned configuration.

The repository now contains the portable skill source at `global/awf-portable/`; build it with `scripts/build_skill_distribution.py`. Verify the 1.9.2 source and distribution pins independently, preserve project ownership/instructions/evidence, run the normal reviewed upgrade flow and rerun project tests after adoption.
