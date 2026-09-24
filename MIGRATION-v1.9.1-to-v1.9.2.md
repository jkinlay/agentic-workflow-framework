# Upgrade AWF 1.9.1 to 1.9.2

AWF 1.9.2 makes the controlled rehearsal path usable on token-reporting hosts without weakening review, merge, reconciliation or Jira boundaries.

New adoptions use token-only budgets: `execution.max_cost_microusd_per_ticket` and `execution.daily_project_cost_microusd` are `null`, `execution.max_tokens_per_ticket` and routing's ticket token cap are 1,000,000, and the routing project-day token cap is 5,000,000. Null means no monetary ceiling. If any effective monetary ceiling is an integer, verified reservation and actual cost remain required exactly as before. Token ceilings, run ceilings, duplicate guards and overrun quarantine remain active.

Run `python -B scripts/bootstrap_project.py --mode upgrade --dest PROJECT --expected-manifest-sha256 SHA256` from the verified 1.9.2 source. A verified 1.9.1 receipt is accepted directly. The upgrade changes only `template.expected_workflow_version` from 1.9.1 to 1.9.2 in `PROJECT_CONFIG.yaml`; every other byte is preserved, including comments, ordering, line endings, and existing monetary and token limits. To opt in to the new defaults, an owner reviews a separate governance PR that changes both execution monetary limits to `null` and adjusts token limits deliberately. Do not silently rewrite accepted project budgets.

A worker whose sandbox cannot write Git metadata now records `commit_route: PUBLISHER`, leaves the validated tree uncommitted and reports BLOCKED only for the commit. The assigned publisher commits exactly that worktree, verifies the committed tree equals the tested tree, then publishes the draft PR. The publisher must not repair or change content; any mismatch returns to the worker. Missing `commit_route` retains the legacy `WORKER` meaning.

Host preflight adds `project_lint_scope`. Ruff or flake8 configuration that includes managed `.agentic` files produces a non-blocking WARN. Add `extend-exclude = [".agentic"]` under `[tool.ruff]`, or the equivalent `.agentic` flake8 exclusion, in project-owned configuration.

The repository now contains the portable skill source at `global/awf-portable/`; build it with `scripts/build_skill_distribution.py`. Verify the 1.9.2 source and distribution pins independently, preserve project ownership/instructions/evidence, run the normal reviewed upgrade flow and rerun project tests after adoption.
