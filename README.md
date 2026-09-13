# AWF Core

Agentic Workflow 1.7.0, published by Jonathan Kinlay under [Apache-2.0](LICENSE). See [NOTICE](NOTICE).

## Install

Download and extract [the portable distribution](dist/AWF-v1.7-distribution.zip). Verify its [SHA-256](dist/AWF-v1.7-distribution.zip.sha256), then run with Python 3.11+:

```text
python -B install_awf.py --dry-run
python -B install_awf.py
```

The installer upgrades the selected user-level `awf` skill, retains a complete external backup and preserves local settings. Do not uninstall the previous skill first. Other copies/plugins and existing project installations are unchanged. Refresh Codex skill discovery on the next turn or in a new task. A configured update channel, not installation alone, determines later release discovery.

## Use and develop

Start with the [core guide](core/README.md) and [specification](core/.agentic/SPECIFICATION.md). Defaults: three workstreams and three independent reviewers total, one per stream, within actual shared host capacity. Balanced model routing escalates inside project-owned limits; adaptation starts with evidence-backed recommendations.

Source is in `core/`; the verified portable skill is in `.agents/skills/awf/`. Build outputs stay outside `core/`. See [validation](validation/ACCEPTANCE.md) for exact tested scope and limitations.

This is one maintained lineage. The previous public scaffold remains in Git history at [6974271](https://github.com/jkinlay/agentic-workflow-framework/tree/6974271869257f8c33d796d3c85c529277c5691d). [The retirement decision](docs/decisions/0001-core-consolidation.md) records non-carried-forward features; it is not an automatic migration for old installations.

See [architecture](ARCHITECTURE.md), [security](core/SECURITY.md) and [contribution rules](AGENTS.md). No publisher signature or live-provider qualification is claimed.
