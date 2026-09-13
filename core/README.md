# AWF Core

Release 1.8.0. Publisher: Jonathan Kinlay. Core and supporting distribution files are licensed under [Apache-2.0](LICENSE); see [NOTICE](NOTICE).

## Start

Install the portable `awf` skill with its verified distribution launcher. It backs up the selected previous skill and preserves local settings; projects are migrated separately.

Read the [specification](.agentic/SPECIFICATION.md), then one guide:

- [Adopt or upgrade a project](.agentic/docs/20-NEW-PROJECT-SETUP.md).
- [Coordinate native workstreams](.agentic/docs/24-STREAM-STARTUP.md).
- [Route models and account for runs](.agentic/docs/27-MODEL-ROUTING.md).
- [Operate the scheduled PR adapter](.agentic/docs/22-AUTOMATED-REVIEW-LOOP.md).
- [Qualify an external review engine](.agentic/docs/28-EXTERNAL-REVIEW.md).

Native coordination is guidance, not an AWF dispatcher/attestation service. Defaults are three workstreams and three independent reviewers total, one per stream, bounded by shared host capacity. Balanced routing uses configurable host-supported models and automatic escalation within human-owned limits. Shadow recommendations do not rewrite policy.

The offline evaluator never grants execution authority. The reference adapter supports Codex/GitHub.com/Jira; Windows scheduling is optional. Other providers and live qualification are not implied.

## Release

See [changes](CHANGELOG.md), [review disposition](RED-TEAM-DISPOSITION-v1.8.md), [migration](MIGRATION-v1.7-to-v1.8.md) and [security](SECURITY.md). Canonical repository: jkinlay/agentic-workflow-framework. Legacy Git history is preserved. Its split model/publisher adapter is maintained inside this core, not as a second framework. Project-specific qualification remains required.

With Python 3.11+ and hash-locked dependencies, regenerate via `scripts/generate_prompts.py`, build via `scripts/build_release.py`, and validate with `scripts/validate_archive.py --help`. `scripts/build_skill_distribution.py --help` packages the portable skill. Keep outputs/reports outside the source. Builds enforce per-file documentation/version budgets and report totals; acceptance tests verify installation, preserved configuration, Git bytes and reproducibility. Offline fixtures do not qualify live services. The native decision benchmark supplies repeatable seeded-defect and prompt-injection cases, not a claim of model-quality equivalence.

This release is unsigned. Compare downloaded archives with an independently obtained pin; co-delivered hashes alone do not authenticate the publisher. Signing remains deferred until key custody and verification are established.
