# Agentic Workflow Framework

Release 1.9.1. [Apache-2.0](LICENSE) applies; see [NOTICE](NOTICE).

## Start

Install the portable `awf` skill with its verified distribution launcher. It backs up the selected previous skill and preserves local settings; projects are migrated separately.

Adoption installs files, maps configuration and prepares a draft PR even when default-branch rules are missing or unobserved. Report that warning and offer the shipped ruleset. Live automation retains observed-rule and qualification requirements; [adoption](.agentic/docs/20-NEW-PROJECT-SETUP.md) explains publication restrictions for repositories holding secrets. The first adoption message explains that changing governance files is why the draft PR is being prepared.

Bootstrap derives supported project values, preserves existing configuration, and reports exact unresolved paths/remedies. New Jira can be disabled; absent CI and trusted merge owners warn during adoption. After writing files, bootstrap runs isolated installed `verify-installation`/`validate-config`; CONFIGURED needs successful exits, accepted outputs and bound digests. `workflow.py status` recomputes current checks: INSTALLED, CONFIGURED or independently trusted/observed ACTIVE. ACTIVE verifies adoption and raw AWF bytes on the accepted default branch, allowing unrelated product edits. Report that actual state and next action in the [adoption PR checklist](.agentic/templates/adoption-pr.md); ACTIVE does not enable live automation.

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
- [Qualify an external review engine](.agentic/docs/28-EXTERNAL-REVIEW.md).

Native defaults: three streams within ceiling six, one independent reviewer each; actual host capacity binds. [Operating configuration](.agentic/docs/29-OPERATING-CONFIGURATION.md) supports chat changes within governance. After CONFIGURED, show routes and offer keep, recommend from Epics, or custom. Recommendations require acceptance; caps require a PR. Running work retains reserved routes/hashes.

The offline evaluator never grants execution authority. The reference adapter supports Codex/GitHub.com/Jira; Windows scheduling is optional. Other providers and live qualification are not implied.

Worker COMPLETE leads to a scoped feature-branch push and draft PR, then current validation/requirements, mark-ready and critic review of the observed PR head. Review-ready is not owner-ready: final gates and human merge authority remain separate. Routine publication classification needs accepted identity and fresh live rules evidence; it grants no execution permission or adapter qualification. The controller mirrors the lifecycle into Jira (In Progress, In Review, Done after the observed merge), reading back every write, never retrying a mismatch, never transitioning Epics. Contracts declare a risk tier and closure standard; serious findings name a criterion or boundary; the amendment cap ends in one owner disposition ([review tiers](.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md)).

## Release

See [changes](CHANGELOG.md), [1.9.1 migration](MIGRATION-v1.9.0-to-v1.9.1.md), [previous disposition](RED-TEAM-DISPOSITION-v1.8.7.md), [operating migration](MIGRATION-v1.8.6-to-v1.8.7.md) and [security](SECURITY.md). A release owner sets the canonical repository before publication. Legacy history is preserved. Its split model/publisher adapter remains inside this core. Project-specific qualification remains required.

With Python 3.11+ and hash-locked dependencies, regenerate via `scripts/generate_prompts.py`, build via `scripts/build_release.py`, and validate with `scripts/validate_archive.py --help`. `scripts/build_skill_distribution.py --help` packages the portable skill. Keep outputs/reports outside the source. Builds enforce per-file documentation/version budgets and report totals; acceptance tests verify installation, preserved configuration, Git bytes and reproducibility. Offline fixtures do not qualify live services. The self-reported structured-decision smoke harness checks supplied observations against a public rubric; it runs no model and does not measure prompt quality.

This release is unsigned. Compare downloaded archives with an independently obtained pin; co-delivered hashes alone do not authenticate the publisher. Signing remains deferred until key custody and verification are established.

The shipped `scripts/release_review.py` verifies externally pinned current reviews against a pinned manifest. Coverage defaults to all content; an optional pinned operator scope identifies its required files. `self_test.py --release` or supplied `--reviews` activates this requirement; ordinary checks and legacy `--checks-only` never qualify a release. Archive acceptance and catalog publication still require the pins and matching qualified results. [Migration](MIGRATION-v1.8.5-to-v1.8.6.md) explains adoption changes; [release commands](MIGRATION-v1.8.4-to-v1.8.5.md) remain applicable with current-release pins. A supplement cannot override a stale current claim. The [validation index](.agentic/validation/reference-tests.json) retains historical evidence. Review identity and scope completeness remain operator assertions.

The [actual-model evaluation guide](.agentic/benchmarks/native/README.md) describes bounded fresh runs and versioned decisions. The retained [v1.8.4 protocol](.agentic/benchmarks/native/EVALUATION-v1.8.4.md) records its pre-collection state; external results retain **10/12 strict FAIL**, alongside **5/12 FAIL** and **1/3 FAIL**. Different cases/contracts prevent a controlled causal improvement claim. Local contract checks are independent of rubric; invalid responses retain observed usage without automatic retry. A CLI schema projection does not prove provider enforcement. Historical v1 reproduction requires explicit legacy grading; unit tests are not new model observations.

On slower validation hosts, `validate_archive.py --self-test-timeout-seconds 1800` gives each full source/install test stage a longer bounded window (default 600; allowed 60–3600 seconds). This is an acceptance-run limit. Timed-out stages retain a failed partial report and fixture location; they do not count as passing validation.
