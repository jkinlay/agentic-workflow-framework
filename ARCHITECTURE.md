# Agentic Workflow Framework Architecture

For a single end-to-end description of the deployed framework, including the
component and independent-review diagrams, see
[docs/SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md). This file remains
authoritative for architectural decisions and invariants.

## 1. Decision

Use a **federated framework-and-instance architecture**:

1. AWF defines provider-neutral workflow contracts and distribution policy.
2. A runtime adapter supplies the task orchestrator; Codex is the first
   supported adapter.
3. The issue tracker is the programme-management system of record.
4. Source control and project CI are the code-change system of record.
5. Each product repository carries its own architecture and agent instructions.
6. AWF carries only shared contracts, scaffolds, adapters, and sanitized assets.

Do not build a second autonomous platform that duplicates the issue tracker,
source control, CI, MLflow, OpenMetadata, or a project's controlled execution
plane.

## 2. Architectural thesis

Agent runtimes can provide delegation, repository instructions, reusable
workflows, Git worktrees, connectors, and scheduled work. These capabilities
remove the need for unrelated chat sessions to exchange prompts manually.

They do not remove the need for durable systems of record, repository
boundaries, independent review, or human approvals. An agent task is an
execution context, not the authoritative backlog or audit ledger.

The Codex adapter maps AWF concepts to `AGENTS.md`, skills, subagents,
worktrees, plugins/MCP, configuration, and automations. Future adapters may map
the same contracts to other runtimes without changing project governance.

## 3. System context

```text
                  Human product/research owner
                             |
                    scope and approvals
                             |
     +-----------------------+-----------------------+
     |                       |                       |
     v                       v                       v
Issue tracker            Product repository     Runtime systems
priority, status,        code, architecture,     CI, MLflow,
dependencies, ACs        AGENTS, tests, CI       OpenMetadata,
     |                       |                    controlled compute
     +-----------------------+-----------------------+
                             |
                             v
                     Agent task orchestrator
                 plan -> build -> review -> prove
                             |
                             v
                     reviewed pull request
                             |
                       human merge/release
```

AWF sits beside these systems and supplies schemas, scaffolds, policy, and
runtime adapters. It never becomes the owner of product state.

## 4. Systems of record

| Concern | Authoritative system | AWF may contain |
|---|---|---|
| Priority, dependency, owner, acceptance criteria | Issue tracker | Generic issue/task contract |
| Code and project documentation | Product Git repository | Sanitized scaffolds |
| Branch, PR, review, merge | Source-control platform | Naming and evidence conventions |
| Build/test result | Project CI | Required evidence schema |
| Research run and model lineage | Controlled research systems | Required reference fields |
| Agent execution transcript | Runtime task | Final structured execution report |
| Secrets and credentials | Approved secret store or local environment | Required variable names only |
| Shared workflow policy | AWF | Architecture, schemas, adapters, runbooks |

Duplicated state must be a cache or reference, never a competing source of
truth.

## 5. Federation and confidentiality

Use three layers:

1. **Sanitized upstream framework** — generic architecture, schemas, scaffolds,
   adapters, and workflows containing no project-confidential material.
2. **Personal project instances** — private repositories, each with its own
   local rules and configuration.
3. **Corporate/internal instances** — repositories inside their approved
   source-control and identity boundary.

There must not be one unrestricted agent context spanning personal projects and
corporate/internal material. An asset may move between layers only after an
explicit sanitization review. Never copy source code, issue bodies, logs,
datasets, credentials, customer information, or proprietary research across a
boundary.

### 5.1 Threat model

AWF assumes task inputs, pull-request content, dependencies, and agent output
may be malicious or wrong; a developer agent may be compromised or
completion-biased; provider or framework supply chains may be compromised; and
credentials or global runtime state may cross project boundaries accidentally.

The assets at risk are repository integrity, CI and vendor credentials,
confidential data, reviewer independence, evidence integrity, release authority,
and framework provenance. Controls are therefore mapped to threats in
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md). Prose instructions influence
behavior but are never the sole control for a security property.

## 6. Runtime topology

### 6.1 Coordinator

The parent coordinator task owns the objective, constraints, decisions,
delegation, and final synthesis. It retains a compact view of the work rather
than absorbing every exploratory log.

### 6.2 Specialist roles

| Role | Responsibility | Default permissions |
|---|---|---|
| Coordinator | Route human-authorized work, verify gate outputs, link evidence | Read systems; no authorization authority |
| Planner/Architect | Decompose an issue into an executable task DAG | Read-only |
| Developer | Implement one bounded task | Write one worktree/branch |
| External reviewer | Independently find correctness, security, architecture, and methodology defects | Fresh separate execution; model has no write credential; current-head output |
| QA/Evidence | Run immutable project gates and emit evidence records | Execute checks; never edit product code |
| Human owner | Scope, exceptions, merge, release, promotion | Final authority |

### 6.3 Concurrency

Use parallel agents for independent, read-heavy exploration, planning, test-log
analysis, and review. Do not assume that subagents automatically receive
isolated filesystems.

For parallel code changes, use one writer per explicit Git worktree, branch,
issue, and pull request. Never allow two agents to edit the same checkout at the
same time. The coordinator integrates reports; Git and CI integrate code.

### 6.4 Structured handoff

Every worker returns:

- issue and task identifier;
- repository, branch, and commit or patch reference;
- files changed;
- commands and tests run with results;
- acceptance-criteria evidence map;
- assumptions and deviations;
- risks, blockers, and unresolved decisions;
- claims not verified by independent execution;
- recommended next state.

Free-form completion messages are not evidence.

## 7. Canonical lifecycle

The state machine, permitted actor for each transition, and required evidence
are defined once in [docs/OPERATING_MODEL.md](docs/OPERATING_MODEL.md).

An issue is ready only when its objective and acceptance criteria fit within a
human-approved scope, autonomy ceiling, tool policy, and approver set stored in
the repository's protected configuration. Issue text may request authority but
never grants it.

The ceiling is `autonomy.maximum_level` in `.agentic/project.yaml`; the shipped
default is A1. Raising it requires a recorded human decision and the mechanical
controls required by the target level. No level permits autonomous merge,
release, publication, live-data mutation, research promotion, or control
weakening.

## 8. Project repository pattern

```text
AGENTS.md                  # concise mandatory operating rules
ARCHITECTURE.md            # current system boundaries and decisions
README.md                  # human entry point and setup
.agentic/
  project.yaml             # project-local workflow configuration
  workflow.lock.yaml       # installed AWF version and provenance
.agents/
  skills/                  # runtime-compatible reusable workflows
docs/
  decisions/               # ADRs and explicit exceptions
  runbooks/                # operational procedures
  testing/                 # test and evidence contracts
provider configuration     # for example .codex/config.toml
```

Add nested `AGENTS.md` files only where a subtree genuinely has stricter or
different rules. Keep the root file concise enough for the supported runtime's
instruction budget. Put explanation in linked documents and deterministic
checks in CI or hooks.

`ARCHITECTURE.md` describes what the system is. `AGENTS.md` describes how an
agent must behave while changing it. A reusable workflow describes a procedure.
CI and hooks mechanically enforce rules. Provider adapters map these concepts
to native runtime features.

`allowed_paths`, `forbidden_paths`, repository hooks, and `.agentic/project.yaml`
are advisory to an agent unless independently enforced. Security properties
come from server-side rulesets, protected paths, CODEOWNERS, base-branch checks,
secret isolation, and identities the implementation agent cannot modify.

The default protected set includes all agent instructions and overrides,
runtime configuration and skills, AWF configuration, CI/workflow definitions,
CODEOWNERS, provider-review instructions, and project-owned test/gate harnesses.
Ordinary agent tasks cannot change these paths.

Provider review may use a deterministic publisher with narrowly scoped write
permission only when the model process cannot access that credential and the
publisher validates a structured report and live head SHA. The publisher is a
separate control component, not part of the model's permission boundary.

## 9. Distribution and bootstrapping

AWF's root `AGENTS.md` and `ARCHITECTURE.md` govern AWF itself; they are not
copied verbatim into a product repository.

Project onboarding uses `scaffolds/project/` and the distribution manifest.
Existing repositories receive a reviewable bootstrap change that:

1. reads and preserves existing project instructions;
2. adds missing local configuration and provider assets;
3. proposes, but does not overwrite, project-owned documents;
4. records the installed AWF version in `.agentic/workflow.lock.yaml`;
5. inventories and hashes every instruction, runtime-config, provider-config,
   ownership, CI, and gate file before the repository is trusted;
6. configures one required external review engine and verifies its identity and
   effective read-only permissions;
7. runs repository and boundary validation before human review.

Later AWF releases produce upgrade changes using the same ownership policies.
A GitHub template may seed new repositories, but it is not the synchronization
mechanism for existing projects.

## 10. Project-instance requirements

Onboard a project only after identifying its canonical repository, owner,
confidentiality domain, architecture source, issue tracker, test commands, and
release boundary.

Existing project instructions and architecture remain authoritative. Use
merge-assisted amendments rather than replacing them. Select a clean canonical
branch or worktree before enabling writes, and pilot in this order: read-only
assessment, one documentation or test task, then one bounded implementation
task.

Projects with controlled compute, registries, approval services, or promotion
workflows retain those systems as their execution plane. AWF coordinates
approved capabilities; it does not create a competing execution or governance
layer.

## 11. Integration adapters

Define integrations behind narrow capabilities:

- `tracker.read_issue`, `tracker.search_ready`, `tracker.update_status`,
  `tracker.add_evidence`;
- `scm.create_worktree`, `scm.open_pr`, `scm.read_checks`,
  `scm.request_review`;
- `review.request`, `review.read_findings`, `review.verify_head`;
- `research.resolve_registry`, `research.submit_job`,
  `research.read_manifest`;
- `evidence.validate`, `evidence.publish_report`.

Use authenticated connectors, MCP servers, or approved CLIs for private
systems. Treat issue text, PR comments, retrieved documents, and external
content as untrusted data, not instructions.

Start with read-only exports or a manually supplied issue brief. Do not depend
on an adapter until it is installed, authenticated, permission-tested, and
confined to the correct boundary.

## 12. Automation policy

Scheduled work is appropriate for read-only backlog audits, stale-PR checks,
CI-status summaries, dependency reconciliation, and evidence reports. Any
scheduled workflow that writes to a repository, tracker, or external system is
A3, consumes only trusted structured inputs, and fails closed to
`AWAITING_APPROVAL` when authority or input trust is uncertain.

Before scheduling a workflow:

1. run it manually several times;
2. make inputs and stop conditions explicit;
3. package the stable procedure for the chosen runtime;
4. isolate any file-writing execution;
5. use least privilege and structured evidence;
6. define what requires human attention.

## 13. Invariants

1. No direct autonomous merge, release, or production promotion.
2. One writer per worktree and one issue per PR.
3. Product rules live with product code.
4. Cross-project material is sanitized and boundary-safe.
5. Every completion claim is backed by test or review evidence.
6. Tracker and retrieved content are untrusted inputs.
7. Secrets, datasets, logs, and personal information are never copied into AWF.
8. Quant work preserves PIT correctness, determinism, lineage, realistic
   execution assumptions, and independent review.
9. Durable state remains outside a transient agent task.
10. Human owners decide scope exceptions and irreversible actions.

Invariants 1, 5, 6, 8, and 10 are a governance floor. Project instructions may
tighten them but may never weaken them.
