# AWF Core

Agentic Workflow 1.8.0, published by Jonathan Kinlay under [Apache-2.0](LICENSE). See [NOTICE](NOTICE).

## Install

Download and extract [the portable distribution](dist/AWF-v1.8-distribution.zip). Compare its [SHA-256](dist/AWF-v1.8-distribution.zip.sha256) with an independently obtained pin, then run with Python 3.11+:

```text
python -B install_awf.py --dry-run
python -B install_awf.py
```

The installer upgrades the selected user-level `awf` skill, retains a complete external backup and preserves local settings. Do not uninstall the previous skill first. Other copies/plugins and existing project installations are unchanged. Refresh Codex skill discovery on the next turn or in a new task. A configured update channel, not installation alone, determines later release discovery.

## Use and develop

Start with the [core guide](core/README.md) and [specification](core/.agentic/SPECIFICATION.md). Defaults: three workstreams and three independent reviewers total, one per stream, within actual shared host capacity. Balanced model routing escalates inside project-owned limits; adaptation starts with evidence-backed recommendations.

Source is in `core/`; the verified portable skill is in `.agents/skills/awf/`. Build outputs stay outside `core/`. See [validation](validation/ACCEPTANCE.md) for exact tested scope and limitations.

This is one maintained lineage. The previous public scaffold remains in Git history at [6974271](https://github.com/jkinlay/agentic-workflow-framework/tree/6974271869257f8c33d796d3c85c529277c5691d). The [consolidation decision](docs/decisions/0001-core-consolidation.md) is refined by the [external-review restoration decision](docs/decisions/0002-review-and-evidence.md). Old installations are not automatically migrated.

See [architecture](ARCHITECTURE.md), [security](core/SECURITY.md) and [contribution rules](AGENTS.md). This release is unsigned: co-delivered hashes do not authenticate its publisher. External workflows are opt-in and require project-specific live qualification. Native decision smoke is not model-quality or host-action proof.
