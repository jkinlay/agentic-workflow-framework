# Agentic Workflow Framework

Release 1.9.6. [Apache-2.0](LICENSE) applies; see [NOTICE](NOTICE).

## Start

Install the portable `awf` skill with its verified distribution launcher. It backs up the selected previous skill and preserves local settings; projects are migrated separately.

Adoption installs files, maps configuration and prepares a draft PR even when default-branch rules are missing or unobserved. Report that warning and offer the shipped ruleset. Live automation retains observed-rule and qualification requirements; [adoption](.agentic/docs/20-NEW-PROJECT-SETUP.md) explains publication restrictions for repositories holding secrets. The first adoption message explains that changing governance files is why the draft PR is being prepared.

Bootstrap preserves reviewed configuration and explicit Jira opt-outs. Fresh Jira defaults on but remains unbound and unscoped; adoption warnings grant no live authority. New projects use token-only budgets. Absent CI/owners warn. Preflight warns when Ruff/flake8 includes managed `.agentic`. Installed `verify-installation`/`validate-config` need successful exits, accepted outputs and bound digests for CONFIGURED. `workflow.py status` recomputes INSTALLED, CONFIGURED or independently trusted/observed ACTIVE. ACTIVE verifies adoption and raw AWF bytes on the accepted default branch while allowing unrelated product edits; record state/next action in the [PR checklist](.agentic/templates/adoption-pr.md). It enables no automation.

On a verified extraction with Python 3.11+ and locked dependencies, run ordinary component checks without review credentials:

```text
python -B scripts/self_test.py --report ABS_NEW_COMPONENT_REPORT.json
```

This reports `release_qualified: false` and review `NOT_PROVIDED`. Release qualification is a separate explicit operation.

Read the [specification](.agentic/SPECIFICATION.md), then one guide:

- [Adopt or upgrade a project](.agentic/docs/20-NEW-PROJECT-SETUP.md).
- [Coordinate native workstreams](.agentic/docs/24-STREAM-STARTUP.md).
- [Follow a ticket from implementation to PR review and reconciliation](.agentic/docs/23-TICKET-LIFECYCLE.md).
- [Route models and account for runs](.agentic/docs/27-MODEL-ROUTING.md).
- [Operate the scheduled PR adapter](.agentic/docs/22-AUTOMATED-REVIEW-LOOP.md).
- [Run independent Codex reviews](.agentic/docs/28-EXTERNAL-REVIEW.md).

Fresh defaults: twelve workers, one shared independent critic within ceiling twelve (fourteen slots with controller); host capacity binds. Older omitted caps remain six. [Operating configuration](.agentic/docs/29-OPERATING-CONFIGURATION.md) supports chat changes within governance. After CONFIGURED, show routes and offer keep, recommend from Epics, or custom. Recommendations require acceptance; caps require a PR. Running work retains reserved routes/hashes.

The offline evaluator never grants execution authority. The reference adapter supports Codex/GitHub.com/Jira; Windows scheduling is optional. Other providers and live qualification are not implied.

Worker COMPLETE leads to branch/draft-PR publication, validation, mark-ready and critic review of the observed head. A Git-metadata-blocked worker may hand its tested uncommitted tree to the publisher for exact commit/tree verification. Review-ready is not owner-ready or merge authority. Routine publication needs accepted identity/live rules evidence and grants no execution authority. The controller mirrors Jira lifecycle states, reads back every write, never retries mismatches or transitions Epics. Contracts declare risk tier/closure; serious findings name a criterion/boundary; the amendment cap ends in an owner disposition ([review tiers](.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md)).

## Release

See [changes](CHANGELOG.md), [direct 1.9.4 migration](MIGRATION-to-v1.9.4.md), [1.9.3 step notes](MIGRATION-v1.9.3-to-v1.9.4.md), [previous disposition](RED-TEAM-DISPOSITION-v1.8.7.md), [operating migration](MIGRATION-v1.8.6-to-v1.8.7.md) and [security](SECURITY.md). A release owner sets the canonical repository before publication. Legacy history is preserved. Its split model/publisher adapter remains inside this core. Project-specific qualification remains required.

[1.9.5 changes](docs/AWF-1.9.5-Whats-New.md) [UI](docs/AWF-1.9.5-User-Interface.md)

With Python 3.11+ and hash-locked dependencies, regenerate via `scripts/generate_prompts.py`, build via `scripts/build_release.py`, and validate with `scripts/validate_archive.py --help`. `scripts/build_skill_distribution.py --help` packages the portable skill. Publication and verification commands appear in [release publication](.agentic/docs/32-RELEASE-PUBLISHING.md); see also [consumer-byte guidance](docs/release-consumer-bytes.md). Keep outputs/reports outside the source. Builds enforce per-file documentation/version budgets and report totals; acceptance tests verify installation, preserved configuration, Git bytes and reproducibility. Offline fixtures do not qualify live services. The self-reported structured-decision smoke harness checks supplied observations against a public rubric; it runs no model and does not measure prompt quality.

This release is unsigned. Compare downloaded archives with an independently obtained pin; co-delivered hashes alone do not authenticate the publisher. Signing remains deferred until key custody and verification are established.

`scripts/release_review.py` verifies pinned current reviews against a pinned manifest. Coverage includes all content unless scoped. Standalone `.agentic/scripts/self_test.py --release` (also via `scripts/self_test.py`) runs component checks and validates review pins before and after tests; supplied `--reviews` also requires pins. It always reports `release_qualified: false` with a reason, even on PASS. Only sharded `scripts/release_self_test.py` via `scripts/validate_archive.py` or `scripts/publish_release.py` can qualify, with deferral and reconciliation. The archive's standalone `--release` call tests missing-pins refusal. Catalog publication requires matching qualified results. Supplements cannot override stale claims. Review identity and scope completeness remain operator assertions.

The [actual-model evaluation guide](.agentic/benchmarks/native/README.md) describes bounded fresh runs and versioned decisions. The retained [v1.8.4 protocol](.agentic/benchmarks/native/EVALUATION-v1.8.4.md) records its pre-collection state; external results retain **10/12 strict FAIL**, alongside **5/12 FAIL** and **1/3 FAIL**. Different cases/contracts prevent a controlled causal improvement claim. Local contract checks are independent of rubric; invalid responses retain observed usage without automatic retry. A CLI schema projection does not prove provider enforcement. Historical v1 reproduction requires explicit legacy grading; unit tests are not new model observations.

On slower validation hosts, `validate_archive.py --self-test-timeout-seconds 1800` gives each full source/install test stage a longer bounded window (default 600; allowed 60–3600 seconds). This is an acceptance-run limit. Timed-out stages retain a failed partial report and fixture location; they do not count as passing validation.
