# Upgrade AWF 1.9.2 to 1.9.3

This first AWF 1.9.3 change set covers publisher tree equality, observed model routes and settlement outcome templates. Review floors, critic independence, specialist triggers, reconciliation and Jira behavior are unchanged.

Run `python -B scripts/bootstrap_project.py --mode upgrade --dest PROJECT --expected-manifest-sha256 SHA256` from verified 1.9.3 source. A verified 1.9.2 receipt is accepted directly. The upgrade changes only `template.expected_workflow_version` from 1.9.2 to 1.9.3 in `PROJECT_CONFIG.yaml`; every other byte is preserved, including comments, ordering, line endings, budgets and owner-set routes. New-project Sol/high risk and specialist defaults are not written into upgraded projects.

Workers using `commit_route: PUBLISHER` now declare `changes`, `tested_tree` and `ignored_untracked`. `agentic.gittree` computes the base tree plus exactly those changes without writing Git objects or the index. The publisher makes no content repair and verifies `git rev-parse HEAD^{tree}` equals `tested_tree`; any mismatch returns to the worker as a scope violation. Test temporary directories belong outside the repository.

New adoptions include `execution.route_capabilities` with a 30-day observation age and a project-relative observation path. Each model observation records host ID, host software/version, method and time. Missing, refused, unproven or stale configured routes produce the non-blocking `route_models_observed` warning. A listed capability remains a claim until observed.

Use `python -B .agentic/scripts/route_model.py outcome-template --role ROLE` for worker, fix, critic or specialist settlement shapes. Replace observed placeholders from host evidence. Critic and specialist runs always settle with `independent_review_passed: false`; their verdict and independence evidence belong in the separately bound review record.
