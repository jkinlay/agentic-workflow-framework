# Architecture

AWF supplies versioned project contracts, an offline evaluator, durable local accounting, installation tools and optional host adapters. The tracker, Git/CI and project-owned controlled systems retain their authority; transient agent chats do not replace them.

`core/` is the reproducible source release. `.agents/skills/awf/` is its portable discovery/procedure package. `dist/` contains versioned archives; `validation/` contains sanitized acceptance evidence. The normative [specification](core/.agentic/SPECIFICATION.md) and executable schemas define core behavior.

Native dispatch is host-dependent guidance. Model selection/accounting is separate from agent launch and host-enforced limits. The reference PR adapter targets Codex/GitHub.com, with synthetic Jira planning; portability to other providers is not claimed. Independent reviewers share host slots with writers and coordinators.

Candidate input cannot grant authority, weaken trusted policy or authorize merge. Reviews/gates bind exact candidates. Human merge/release control and confidentiality boundaries remain mandatory. Core configuration does not install server-side branch protection or qualify a review engine.

The [consolidation ADR](docs/decisions/0001-core-consolidation.md) retires the older scaffold without rewriting history or importing private project data. The existing upstream `.agentic/project.yaml` and `awf/review` workflow remain unchanged and fail closed while engine qualification is incomplete. They govern this repository, not installed core projects.

The [restoration ADR](docs/decisions/0002-review-and-evidence.md) retains the split external model/publisher adapter in `core/.agentic/external-review/`. Its root workflow points to that canonical component and is disabled unless explicitly enabled by the owner. A read-only model job produces a report; a separate deterministic publisher posts a current-head COMMENT review. This does not replace the required Copilot engine, authenticate native reviewer identities or qualify a live host. Project-owned qualification remains pending.
