# Agentic Workflow Framework (AWF) 1.8.9

## Complete operational documentation

**Release:** 1.8.9  
**Release date:** 16 September 2026  
**Publisher:** Jonathan Kinlay  
**Licence:** Apache-2.0  
**Canonical repository:** `jkinlay/agentic-workflow-framework`

This is a stand-alone operational manual for the verified AWF 1.8.9 distribution. It covers what AWF is, what it does and does not do, requirements, installation, project adoption, operating options, routing, evidence, review, security, recovery, and command-line use.

AWF is a governed framework, not an autonomous delivery service. It coordinates and records controlled engineering work, but authority to launch agents, access external systems, merge code, change repository rules, or update Jira remains with the configured host and authorised humans.

## Contents

1. [Release identity and verification](#1-release-identity-and-verification)
2. [Purpose and scope](#2-purpose-and-scope)
3. [Core concepts and roles](#3-core-concepts-and-roles)
4. [What AWF does not do](#4-what-awf-does-not-do)
5. [Requirements](#5-requirements)
6. [Installing or upgrading the portable skill](#6-installing-or-upgrading-the-portable-skill)
7. [Preparing and adopting AWF in a project](#7-preparing-and-adopting-awf-in-a-project)
8. [Adoption states and live enablement](#8-adoption-states-and-live-enablement)
9. [Project configuration reference](#9-project-configuration-reference)
10. [Operating configuration and capacity](#10-operating-configuration-and-capacity)
11. [Model routing, reservations and budgets](#11-model-routing-reservations-and-budgets)
12. [Ticket lifecycle, PRs and human merge control](#12-ticket-lifecycle-prs-and-human-merge-control)
13. [Evidence, validation and review](#13-evidence-validation-and-review)
14. [Jira boundary](#14-jira-boundary)
15. [Optional scheduled review loop](#15-optional-scheduled-review-loop)
16. [Optional external review adapter](#16-optional-external-review-adapter)
17. [Security, integrity and recovery](#17-security-integrity-and-recovery)
18. [Command reference](#18-command-reference)
19. [Operational runbooks](#19-operational-runbooks)
20. [Upgrading from 1.8.8 to 1.8.9](#20-upgrading-from-188-to-189)
21. [Troubleshooting](#21-troubleshooting)
22. [Glossary](#22-glossary)

---

## 1. Release identity and verification

The supplied portable distribution identifies itself as AWF 1.8.9 and contains the bundled source archive `agentic-workflow-template-v1.8.9.zip`.

| Item | SHA-256 |
| --- | --- |
| Supplied outer distribution ZIP | `e8a5b4b2305c351a750c38abac2d98a9da90a96a934f311bf631cbb69c34719b` |
| Bundled AWF source archive | `1ecfeff80bd305a937915738988d5b4a026353eb7ba1308e75e8ede5f6639dc2` |
| Bundled source manifest | `f18aaac889455c2d6a0ae3668dd4a0cb11c54970658cd8017594844a50b56dbe` |
| Portable skill manifest | `192c538a8f2f4e25e8e70edb99719c6e048eac9c46242e737f0a918a87b00c7d` |

The release preparation helper successfully verified the archive and source, including 273 source/archive files and 271 manifest entries. The portable-distribution test suite also passed: **92 tests, 0 failures**.

Important trust limitation: AWF 1.8.9 is **unsigned**. Its hashes detect changed bytes when compared with an independently obtained trusted pin; they do not cryptographically prove the publisher's identity. Do not treat a hash copied from the same download channel as independent provenance.

### Verify a received ZIP before running code

On Windows:

```powershell
(Get-FileHash .\AWF-v1.8.9-distribution.zip -Algorithm SHA256).Hash
```

On macOS/Linux:

```text
sha256sum AWF-v1.8.9-distribution.zip
```

Compare the result with a release pin received through a separate trusted channel. Then extract the complete distribution and use its verifier. The following command creates a separate local release cache; its parent must already exist, but the cache directory itself must be new and dedicated:

```text
python -B awf/scripts/prepare_release.py --cache-dir ABSOLUTE_EMPTY_CACHE_DIRECTORY --version 1.8.9
```

This verifies and extracts the bundled source to the cache. It does not install the skill, adopt a project, create a PR, call the network, or overwrite an existing cache.

---

## 2. Purpose and scope

AWF provides a repeatable control framework for AI-assisted software delivery. Its purpose is to make delegated implementation work reviewable, bounded, evidence-led and accountable.

It supplies:

- A portable `awf` skill with verified-release discovery and installation support.
- A project template with a versioned workflow, schemas, protected configuration, prompts, examples and runbooks.
- A governed ticket lifecycle from readiness through implementation, review, final gate, human merge and limited post-merge reconciliation.
- Native multi-stream coordination guidance for hosts that can delegate work to agents.
- Model-routing recommendations, durable reservations, budget accounting, escalation controls and a protected retry ledger.
- Independent critic and specialist-review controls.
- Project adoption tooling that creates governed files and prepares an owner-reviewable draft PR.
- Optional components for a scheduled Codex/GitHub.com PR review loop and an external Claude/publisher review adapter.

AWF deliberately separates four things that are often conflated:

| Layer | Meaning |
| --- | --- |
| Skill installation | Installs or upgrades the portable `awf` skill in a skills location. It does not alter projects. |
| Release preparation | Verifies a pinned archive and creates a local cache for inspection. It does not install the skill or alter projects. |
| Project adoption | Copies/updates AWF-managed governance files, derives configuration and prepares a draft PR. It does not itself merge or enable live adapters. |
| Live operation | Uses an authorised host, observed repository controls and qualified adapters to run selected work. It still preserves human authority. |

---

## 3. Core concepts and roles

### 3.1 Roles

| Role | Primary responsibility | Default route in the template |
| --- | --- | --- |
| Human owner | Defines scope, accepts governance changes, grants any required explicit authority and makes/approves merge decisions. | Not model-routed. |
| Controller | Coordinates tickets, state, evidence, capacity and next actions. | `gpt-5.6-sol` / medium |
| Worker | Implements a bounded ticket or amendment in its assigned scope. | `gpt-5.6-terra` / medium |
| Independent critic | Reviews the observed PR head independently of the worker. | `gpt-5.6-sol` / high |
| Specialist reviewer | Reviews triggered security, data-migration, public-API or operations risk. | `gpt-6-astra` / high |
| Host/operator | Authenticates host facts, enforces resource limits, launches processes, preserves state and establishes observed identity/termination facts. | Host responsibility |
| Publisher | Publishes a scoped feature branch and draft PR where authorised. It may be a designated human, service or host component. | Project-specific |

Configured handles, CODEOWNERS entries and model labels do not prove reviewer eligibility, independence, access rights or human authorisation. Those must be observed separately.

### 3.2 Candidate

A candidate is the exact change being evaluated. AWF binds evidence to the current repository, PR, head SHA, base SHA, target branch, merge method, policy and requirements. A local worktree alone is not a candidate ready for PR review.

Changing a candidate—such as changing the head, base, target, merge method, requirements or policy—invalidates the affected review, gate or authorisation evidence. This is why AWF requires a critic to review the observed PR head and requires re-review after material change.

### 3.3 Evidence and records

AWF uses versioned JSON/YAML contracts for worker results, critic reviews, CI evidence, final gates, authorisations, recovery, Jira snapshots and related records. Canonicalisation rejects ambiguous data. The reference evaluator defines twelve gates; records are evidence of current facts rather than a substitute for them.

### 3.4 Native streams

Native streams are independent implementation workstreams labelled A–F. The template begins with three active streams and one independent reviewer per stream. The structural maximum is six streams, subject to project governance and actual host capacity.

One writer owns each ticket/path at a time. A coordinator who edits code is a writer. Reviewers and queued proposals are not active writers, but they still consume host capacity.

---

## 4. What AWF does not do

AWF is intentionally conservative. By itself it does **not**:

- Create a project migration merely because the portable skill was installed.
- Prove that a release is the latest available release when an owner channel is unavailable.
- Prove publisher identity from a digest.
- Launch native agents, enforce a provider's usage limit, or attest that a model actually ran.
- Treat a configured stream ceiling as evidence of available host slots.
- Turn a local worktree or a review report into a hosted PR.
- Merge code automatically under the standard template.
- Treat review readiness as owner readiness or merge authority.
- Create tickets, update Epics, or mirror AWF workflow states into Jira.
- Retry an uncertain merge, Jira write or external provider outcome blindly.
- Qualify an external review engine, host isolation, scheduler, credential boundary or live service from offline fixtures.
- Enable the scheduled loop or external review adapter merely because a project is `CONFIGURED` or `ACTIVE`.

The offline evaluator always reports `execution_authority: false`. It validates local structure and logic; it cannot establish remote permissions, human intent, live host enforcement or model quality.

---

## 5. Requirements

### 5.1 Baseline requirements

| Area | Requirement |
| --- | --- |
| Python | Python 3.11 or later. AWF 1.8.9 was checked with Python 3.12 during documentation verification. |
| Archive trust | An independently obtained SHA-256 pin for the outer distribution and/or release manifest. |
| Filesystem | A writable skills directory; a backup location on the same filesystem; a separate, dedicated release cache; and protected state outside worker worktrees. |
| Git | Required for project adoption and repository work. Fresh checkout verification is required for `ACTIVE`. |
| Runtime dependencies | Install the locked `.agentic/requirements.lock` with `--require-hashes --only-binary=:all:` before using project runtime commands that need YAML/schema validation. |
| Project authority | A human owner who may accept the draft adoption PR and approve any governance changes. |

### 5.2 Requirements for optional live capabilities

| Capability | Additional requirements |
| --- | --- |
| Native multi-stream work | A host with delegation capability, observed free capacity, a protected routing ledger, non-overlapping ownership and independent reviewer contexts. |
| Agent pushes to repositories containing secrets | Observed repository rules and existing qualification for the selected mode; otherwise prepare changes for owner publication. |
| Scheduled review loop | A qualified host, separate runtime/state/worker/critic directories, exclusive branch ownership, configured CI, actual trusted merge owners and observed rules. |
| External review adapter | A separately qualified engine, protected environments/secrets, validated App identity and workflow provenance, plus owner approval. |
| Jira post-merge transition | Jira enabled and scoped, a configured Done mapping, explicit authorisation, confirmed merge and read-before/read-after observation. |

### 5.3 Locked runtime dependencies

The project lock pins PyYAML, `jsonschema` and its transitive dependencies. Do not use an unconstrained runtime for an adoption claim. A typical project-local setup is:

```text
py -3 -m venv .venv
.venv\Scripts\python -m pip install --require-hashes --only-binary=:all: -r .agentic\requirements.lock
```

On macOS/Linux, use the analogous interpreter and virtual-environment paths. Keep the environment outside candidate worktrees where practical and do not install dependencies from an untrusted PR.

---

## 6. Installing or upgrading the portable skill

### 6.1 Normal installation

Extract `AWF-v1.8.9-distribution.zip`, open the extracted `AWF-v1.8.9-distribution` folder and run a dry run first:

```text
python -B install_awf.py --dry-run
python -B install_awf.py
```

Windows users may use:

```text
py -3 -B install_awf.py --dry-run
py -3 -B install_awf.py
```

Use an absolute Python path if Python is not on `PATH`.

The launcher contains the expected package pins and verifies the installer before it runs. It locates the existing user-level `awf` skill by default. To install into a specific location, supply a destination ending in `awf`:

```text
python -B install_awf.py --dest ABSOLUTE_SKILLS_DIRECTORY/awf --dry-run
python -B install_awf.py --dest ABSOLUTE_SKILLS_DIRECTORY/awf
```

### 6.2 Installer behaviour

The installer is rollback-capable and fail-closed:

- It verifies the expected inventory and manifest before replacement.
- It stages a replacement rather than overwriting a known skill in place.
- It preserves local catalog, update-channel and selected local settings.
- It retains the previous complete folder in an external backup on the same filesystem.
- It does not remove duplicate skills or plugins elsewhere.
- A repeat of an identical installation verifies without replacement.
- Unknown, conflicting, modified or newer installed versions fail rather than being silently overwritten or downgraded.

Do **not** uninstall the existing skill first. If an install is interrupted, retain the lock/staging/backup material and follow its recovery result; do not delete it blindly. A failed replacement should leave the previous folder intact, but inspect the reported backup if rollback itself failed.

### 6.3 Advanced installer options

`install_awf.py` invokes the verified `install_skill.py` helper. Its supported controls are:

| Option | Purpose |
| --- | --- |
| `--source PATH` | Select a portable skill source when calling the helper directly. |
| `--expected-manifest-sha256 SHA256` | Mandatory trusted manifest pin for direct helper invocation. |
| `--dest PATH` | Select the installed `awf` skill path. |
| `--backup-root PATH` | Select a backup root on the same filesystem as the destination. |
| `--project PATH` | Supply a project context for preserved project-local settings where applicable. |
| `--preserve-relative PATH` | Preserve an approved relative local file; retained selections are recorded in receipts. |
| `--dry-run` | Validate and report the planned operation without writing. |

Use the supplied `install_awf.py` launcher for normal work. Direct helper use is for controlled operations where the independent manifest pin is available.

### 6.4 After installation

Skill discovery refreshes on the next turn or in a new Codex task. Invoke `$awf` and ask it to adopt AWF 1.8.9 in a specific project. Installing the skill does not upgrade every project automatically.

---

## 7. Preparing and adopting AWF in a project

### 7.1 Adoption principles

Adoption changes repository governance files. The first adoption message should explicitly say that AWF will prepare a **draft PR for owner review and merge**. Adoption has no requirement for pre-existing repository rules, CI checks or Jira configuration; missing items are reported as warnings, not invented as facts.

Before making changes, inspect:

- The accepted AWF version and any explicit version selector.
- The target repository's current working tree, worktrees, instructions, CODEOWNERS and active PRs.
- Existing `.agentic` configuration, history, receipts and operating choices.
- Actual repository metadata, default branch and rules when read-only observation is available.
- Jira scope and availability, without assuming that Jira can be mutated.

Preserve project-owned instructions in `PROJECT_INSTRUCTIONS.md`. Keep AWF-managed `AGENTS.md` byte-exact. Preserve project-specific configuration, ownership, history and partial work rather than replacing them with template defaults.

### 7.2 Recommended adoption sequence

1. Verify the AWF 1.8.9 distribution and prepare a separate cache.
2. Create an isolated adoption branch/worktree with exclusive ownership.
3. Optionally observe the actual default-branch rules using a trusted GitHub CLI.
4. Run the project bootstrap in dry-run mode and review its proposed derivation/residue report.
5. Run bootstrap in `install` mode with backup handling for cross-version adoption.
6. Install locked dependencies and execute the installed verification/configuration commands.
7. Inspect `workflow.py status` and `workflow.py operating show`.
8. Commit the governed adoption changes and prepare a draft PR. Record observed rule state, residue, commands and next action in the adoption checklist.
9. Have the owner review and merge the adoption PR. Then perform fresh checkout/status evidence before claiming `ACTIVE`.

### 7.3 Optional rules observation

Rules observation is read-only and does not block adoption:

```text
python -B .agentic/scripts/repository_rules.py --repository OWNER/REPO --observe --save-observation ABSOLUTE_OBSERVATION.json
```

The output classifies relevant repository rules as:

| Result | Meaning |
| --- | --- |
| `APPLIED` | Adequate observed baseline, including a visible no-bypass policy. |
| `MISSING` | Repository rules were observed but are inadequate. |
| `UNOBSERVED` | Rules could not be observed or evidence is unavailable, stale, malformed or incomplete. |

`APPLIED` is evidence of an observed baseline, not a grant of model, GitHub, Jira or merge authority.

### 7.4 Bootstrap commands

From the verified AWF source, use controlled absolute paths and a trusted manifest pin:

```text
python -B scripts/bootstrap_project.py --dest TARGET --expected-manifest-sha256 TRUSTED_SHA256 --codeowner '@handle' --dry-run
python -B scripts/bootstrap_project.py --dest TARGET --expected-manifest-sha256 TRUSTED_SHA256 --codeowner '@handle' --on-conflict backup
```

Useful bootstrap options are:

| Option | Meaning |
| --- | --- |
| `--dest PATH` | Target repository. Required. |
| `--expected-manifest-sha256 SHA256` | Required source-manifest trust pin in a controlled adoption. |
| `--mode install` | Use for cross-version adoption; backed-up installation semantics apply. |
| `--mode upgrade` | Same-version upgrade mode only. Do not use it to cross versions. |
| `--on-conflict error|backup` | Refuse conflicts or preserve them in backup material. |
| `--recover` | Recover an unfinished bootstrap operation deliberately. |
| `--dry-run` | Report intended changes without writing. |
| `--project-name` / `--project-short-name` | Override values derived from the repository name. |
| `--github-repo OWNER/REPO` | Supply repository identity when origin cannot be used. |
| `--repository-id NUMBER` | Supply the numeric GitHub ID when it cannot be observed. Never invent one. |
| `--test-command COMMAND` | Record a project validation command; bootstrap does not run it. |
| `--jira-site URL` and `--jira-key KEY` | Enable Jira only when both are supplied for a fresh configuration. |
| `--codeowner HANDLE_OR_TEAM` | Seed a new CODEOWNERS entry only when the project has none. The default is `@jkinlay`. |
| `--rules-observation PATH` and `--expected-rules-observation-sha256 SHA256` | Supply pinned read-only rules evidence. |
| `--default-branch NAME` | Assert an expected branch; live observation still discovers the actual default. |
| `--review-app-id ID` | Record the expected App identity for an `awf/review` prerequisite. It does not qualify an adapter. |
| `--propose-operating-capacity` | Stage a governance proposal for a ceiling of at least six and derived per-stream reviewers. It requires owner review/merge. |

For a new project, bootstrap derives a real UUID4 once. It should derive the repository identity from an authenticated source where possible; do not substitute a guessed numeric ID. Existing values are retained unless an explicit supported migration changes them.

### 7.5 Verify the installed project

With exclusive ownership and a quiescent adoption:

```text
python -B -I ABS_TARGET/.agentic/scripts/workflow.py verify-installation
python -B -I ABS_TARGET/.agentic/scripts/workflow.py validate-config
python -B -I ABS_TARGET/.agentic/scripts/workflow.py status
python -B ABS_TARGET/.agentic/scripts/workflow.py operating show
```

`CONFIGURED` requires more than a copied file: the installed verification must exit 0 with `integrity_valid: true`; configuration validation must exit 0 with `status: ACCEPTED`; and source, policy and operating digests must match. Dry-run output and source-library inspection are not substitutes for those installed commands.

---

## 8. Adoption states and live enablement

| State | What it proves | What it does not prove |
| --- | --- | --- |
| `UNVERIFIED` / failed verification | The installation cannot yet be trusted. | Nothing about safe operation. |
| `INSTALLED` | Managed bytes match the installation receipt. | Valid configuration, independent release provenance, accepted merge or live enablement. |
| `CONFIGURED` | Installed verification/configuration passed and policy/operating digests are consistent. | Independent release trust, observed adoption merge, adapter qualification or automatic authority. |
| `ACTIVE` | Independent release trust, observed receipt-changing adoption merge, fresh default-branch evidence and accepted raw AWF/configuration/receipt/provenance bytes. | Adapter qualification, scheduler enrollment, model launch, automatic merge or Jira authority. |

`workflow.py status` recomputes current checks rather than relying on a past bootstrap output. To establish `ACTIVE`, it needs independent release evidence—either a trusted installed host skill or an external release source with an independently approved manifest pin—plus observed accepted adoption evidence on the fresh default branch. Unrelated product edits may coexist with the adoption change.

Live enablement is intentionally separate. Before live external review, scheduled amendments or agent pushes into a secrets-bearing repository, the relevant mode needs observed rules, configured CI, actual trusted merge owners and its own qualification evidence.

---

## 9. Project configuration reference

`PROJECT_CONFIG.yaml` is the protected governance layer. The release uses schema version 3 and expects workflow version 1.8.9. Treat changes as governance changes requiring reviewed project control.

### 9.1 Top-level configuration

| Section | Key controls | Notes |
| --- | --- | --- |
| `template` | `expected_workflow_version` | Must remain `1.8.9` for this release. |
| `project` | Stable UUID, name, short name | Generate the UUID once for a new project; retain it thereafter. |
| `jira` | Enablement, site/key, scope, status map and mutation owner | Jira can be disabled. Disabled Jira permits no writes. |
| `github` | Host, repository/numeric ID, base branch, branch pattern, merge method, draft-PR-first | Must be grounded in observed repository metadata. |
| `execution` | Stream ceilings, roles, broker, routing, budgets and retry limits | Protected operational policy. |
| `validation` | Commands, candidate-bound CI policy, required checks, evidence age and timeout | CI evidence must be current and correctly bound. |
| `scope` | Protected paths, generated paths and documentation classes | Keep governance paths protected. |
| `critic` | Critic requirement, independence, blocking severities and re-review rules | Default blocking severities are `BLOCKER` and `MAJOR`. |
| `specialist_reviews` | Security, data migration, public API and operations triggers | Triggered review is in addition to the independent critic. |
| `merge_gate` | Human authorisation, CI, review/thread requirements, TTL and trusted owners | Automatic merge is disabled by default. |
| `controller` | Dispatch/review/amendment/Jira automation toggles | All automatic toggles are false in the template. |
| `audit` | State location policy, retention and secret redaction | Store state outside worktrees. |
| `portfolio` | Cross-project limitations | Default is read-only with no cross-project dispatch. |

### 9.2 Template execution defaults

| Setting | Template value | Meaning |
| --- | --- | --- |
| `max_parallel_tickets` | 6 | Governance ceiling before an enabled broker is considered. |
| `max_parallel_tickets_per_stream` | 1 | One ticket per active stream. |
| `native_streams.enabled` | true | Native coordination is allowed as host-dependent guidance. |
| `independent_reviewers.allocation` | `one_per_stream` | Each active stream requires one independent reviewer. |
| `max_agent_runs_per_ticket` | 8 | Ticket-level routing/run budget. |
| `max_spawn_depth` | 1 | Prevents unbounded recursive delegation. |
| `max_amendment_cycles` | 3 | Template default; an accepted project may set a different reviewed ceiling. |
| `transient_retry_limit` | 2 | Separate from the protected reconciled-incident retry ceiling. |
| `max_run_seconds` | 3600 | Per-run reference limit. |
| `max_tool_calls_per_run` | 100 | Per-run reference limit. |
| `max_tokens_per_ticket` | 100,000 | Ticket token cap. |
| `daily_project_cost_microusd` | 50,000,000 | Project daily cost cap in micro-USD. |
| `one_writer_per_ticket` | true | Prevents overlapping implementation ownership. |
| `host_broker.enabled` | false | Stored broker limits do not bind until enabled. |

### 9.3 Role and routing defaults

The template allowlist is `gpt-5.6-luna`, `gpt-5.6-terra`, `gpt-5.6-sol` and `gpt-6-astra`. Actual availability is host-dependent.

| Role/condition | Default model/effort | Floor or special rule |
| --- | --- | --- |
| Controller | Sol / medium | May only use an allowed host-supported route. |
| Worker | Terra / medium | The unchanged unpinned default may use Luna / low only for qualifying simple work. |
| Independent critic | Sol / high | Review floor. Context must differ from the worker. |
| Specialist | Astra / high | Used for specialist triggers and high-risk/complex/uncertain work. |
| High risk | Astra / high | Mandatory risk floor applies after ordinary overrides. |

High-risk flags in the template are `security`, `permissions`, `schema_or_migration`, `data_loss`, `concurrency`, `production`, `public_api` and `architecture`.

### 9.4 Specialist-review triggers

The template provides four specialist classes. Review the actual project configuration rather than relying only on examples.

| Specialist | Typical triggering paths/keywords/risk flags |
| --- | --- |
| Security | `auth/**`, `security/**`, credentials, authentication, secrets, `security`. |
| Data migration | `migrations/**`, `schema/**`, migration/schema language, `schema_or_migration`, `data_loss`. |
| Public API | `api/**`, `openapi/**`, breaking interfaces, public API, `public_api`. |
| Operations | `infra/**`, `deploy/**`, production/race-condition language, `concurrency`, `production`. |

### 9.5 Merge-gate defaults

The standard template requires human authorisation, current critic/specialist evidence, green required CI, no unresolved blocking threads, and revalidation after head or target-base change. It sets:

- `automatic_merge_enabled: false`
- `execution_after_authorization: owner_manual`
- A 900-second authorisation TTL
- Empty trusted-owner IDs until the project records actual owners
- High-risk owner quorum of 1 by default

Empty trusted owners or CI checks are adoption warnings. They are not identities, approvals or live-ready policy.

---

## 10. Operating configuration and capacity

AWF uses two configuration layers:

| Layer | File | Change control | Purpose |
| --- | --- | --- | --- |
| Governance | `.agentic/PROJECT_CONFIG.yaml` | Reviewed PR | Ceilings, budgets, allowlists, floors, escalation, broker policy and one-reviewer-per-stream policy. |
| Operating choices | `OPERATING_CONFIG.yaml` | Direct authorised user instruction within governance | Active stream count and selected routes for streams/roles. |

The root operating file is mutable and project-owned. Bootstrap seeds:

- Three streams, A–C.
- Terra / medium workers.
- Sol / high reviewers.
- Sol / medium controller.
- Astra / high specialist.
- Luna / low simple-worker route enabled.

### 10.1 Effective ceiling

The effective stream ceiling is the minimum of:

1. The structural maximum of six streams.
2. `execution.max_parallel_tickets`.
3. `execution.host_broker.max_workers` **only if** `execution.host_broker.enabled` is true.

`workflow.py operating show` reports:

- `effective_ceiling`
- `effective_ceiling_governance_path`
- `effective_ceiling_sources`

If only the structural maximum of six binds, the governance path is null. If project and broker limits tie, the project path is preferred while all tied sources remain listed. Heavy/GPU broker limits are not stream counts.

A configured ceiling is not a claim that sufficient host slots, independent reviewers, leases or writer ownership exist. Invalid operating counts are refused; AWF does not silently clamp them.

### 10.2 Changing operating choices

Direct user instructions can change operating choices within governance. The controller should translate the request, echo the result, apply it and show the result. Example:

```text
python -B .agentic/scripts/workflow.py operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
```

User-selected routes are pinned against optional escalation, but mandatory risk/review floors still apply. Mixed valid/invalid changes are rejected as a whole; they are not partially applied.

An Epic-scoped route requires the exact observed Epic ID and does not broaden to global policy:

```text
python -B .agentic/scripts/workflow.py operating set --instruction "Stream B's worker on Astra/xhigh for this Epic" --epic PROJ-123 --set streams.B.worker=gpt-6-astra/xhigh
```

Scoped counts, simple-worker switches and project recommendations with `--epic` are unsupported. Existing complete routes are required to unpin an override.

### 10.3 Recommendations from observed Epics

AWF can recommend—not autonomously adopt—routes from observed scoped Epics and optional inventory:

```text
python -B .agentic/scripts/workflow.py operating recommend --epics EPICS.json --inventory INVENTORY.json
```

Present the recommendation table and wait for acceptance. Apply a fresh accepted recommendation with:

```text
python -B .agentic/scripts/workflow.py operating set --instruction "adopt all" --recommendation ID --accept all
```

Stale input, operating or governance hashes require a new recommendation. Unknown/overlapping file scopes are grouped conservatively. Recommendation evidence does not authorise dispatch or change protected ceilings.

### 10.4 Audit and recovery

Commit `OPERATING_CONFIG.yaml` with `.agentic-state/operating/changes/`. The audit records instructions, source, hashes and changes. Interrupted operating transactions, locks and temporary files block dispatch until they are inspected and recovered. A recovery action requires an independently inspected journal SHA-256; AWF fails closed when required atomic filesystem primitives are unavailable.

Running work retains the route and `operating_hash` that were reserved for it. Reducing streams drains surplus streams after their current tickets; it does not silently cancel them or rewrite their reservations.

---

## 11. Model routing, reservations and budgets

### 11.1 Routing model

`route_model.py` proposes routes and records observed outcomes. The host authenticates capability facts, launches models and enforces real limits.

Route precedence is:

1. Role default.
2. Simple-worker or high-risk rule.
3. Stream override.
4. Matching Epic-scoped override.
5. Agent override.
6. Ticket override.
7. Mandatory risk and review floors.

The final route must be supported by the host and present in the intersection of the routing allowlist and role-specific approved models. If it is unavailable, AWF returns `unavailable`; it does not silently fall back to a weaker route.

Simple work requires low complexity, risk and uncertainty, strong verification, and no risk flags. Jira priority alone is insufficient. A pinned route blocks optional escalation, not mandatory floors.

### 11.2 Escalation

Escalation occurs between runs on durable reasoning, implementation or validation failures. Model rank and effort are monotonic: they do not decrease after escalation or reclassification. Defaults permit two escalations per ticket and three reasoning failures per phase, within the configured effort ceiling.

If a ceiling, pin or host capability conflicts with monotonic escalation, admission is blocked. Credentials, infrastructure, rate limits, cancellation and unknown outcomes require investigation; `resolve-failure` records verified recovery before a same-model retry.

### 11.3 Reserve before launch; settle after observation

The normal sequence is:

1. `suggest` a route without side effects.
2. `reserve` a bounded run in one protected durable SQLite ledger outside all worker checkouts.
3. Launch only through the host after reservation.
4. `settle` the actual model, effort, context, usage and outcome once observed.

Reservations are hard upper bounds the host must enforce. Effective budgets are the lower of routing budgets and pre-existing execution caps. Unknown usage is not zero. Outstanding work retains its charge across midnight and policy changes; there is no timeout refund.

Settlement mismatch or overrun quarantines the project and prevents positive evidence. Operating and policy hashes are stored separately, and settlement keeps the hashes of the original reservation rather than the current configuration.

### 11.4 Reconciliation and retry ceilings

Reconciliation is disabled by default. Enabling it needs named authorised operators and protected host controls. It records operator assertions about authorisation, termination and remediation; it does not authenticate those assertions.

The reconciled-incident retry ceiling defaults to two per ticket across roles, phases and policy changes. A third reconciled incident blocks new admission but does not prevent safe closure. The protected ledger initialises the retry ceiling to the lower of two and the reviewed configured cap.

Increasing this ceiling cannot be done through ordinary configuration or the routing CLI. Only a trusted host may invoke the protected ledger operation after authenticating explicit human direction and a one-time, time-bounded approval/evidence pair. Required approval timestamps obey:

```text
approved_at <= now < expires_at <= approved_at + 24 hours
```

Workers must not have ledger-write rights or access to that increase operation. A copied database does not create global replay protection.

### 11.5 Shadow evidence

Adaptive routing starts in `shadow` mode. It may propose a controlled experiment only after 20 independently reviewed low-risk tickets, a 95% acceptance rate and no known escaped defects in the observed cohort. It never automatically downgrades models or proves model quality.

### 11.6 Routing commands

```text
python .agentic/scripts/route_model.py defaults
python .agentic/scripts/route_model.py suggest --config PROJECT/.agentic/PROJECT_CONFIG.yaml --request request.json --capabilities observed-host.json
python .agentic/scripts/route_model.py reserve --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --request request.json --capabilities observed-host.json --ledger STATE/routing.sqlite
python .agentic/scripts/route_model.py settle --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --ledger STATE/routing.sqlite --run-id RUN_ID --outcome outcome.json
python .agentic/scripts/route_model.py reconcile --config PROJECT/.agentic/PROJECT_CONFIG.yaml --project-root PROJECT --ledger STATE/routing.sqlite --run-id RUN_ID --observation operator-reconciliation.json
```

Blocked, unavailable, quarantined or invalid routing results return exit code 2. Exit 0 is not evidence that a model was launched.

---

## 12. Ticket lifecycle, PRs and human merge control

### 12.1 Normal lifecycle

| Stage | Outcome and essential evidence |
| --- | --- |
| `BACKLOG` → `READY` | Valid configuration, scoped contract, ownership and dependencies. |
| `READY` → `DISPATCHED` | Permitted dispatch, current lease/ownership and reserved budget. |
| `DISPATCHED` → `IN_PROGRESS` | Registered run and verified worktree. |
| `IN_PROGRESS` → `PR_DRAFT` | Valid worker result plus observed `branch_pushed` and `pr_exists`. |
| `PR_DRAFT` → `READY_FOR_CRITIC` | Current validation/requirements and observed `draft_cleared`. |
| `READY_FOR_CRITIC` → `CHANGES_REQUESTED` | Critic rejects current PR head. |
| `CHANGES_REQUESTED` → `AMENDING` → `READY_FOR_CRITIC` | Bounded amendment, current validation and registered new head. |
| `READY_FOR_CRITIC` → `SPECIALIST_REVIEW` or `FINAL_REVIEW` | Current critic; specialists when triggered. |
| `SPECIALIST_REVIEW` → `FINAL_REVIEW` | Current specialist and critic evidence. |
| `FINAL_REVIEW` → `READY_FOR_OWNER_AUTHORIZATION` | Derived final gate and current requirements. |
| `READY_FOR_OWNER_AUTHORIZATION` → `OWNER_AUTHORIZED` | Verified, unused, current authorisation. |
| `OWNER_AUTHORIZED` → `MERGING` → `MERGED` | Certified executor, atomic candidate protocol, consumed permit, confirmed matched merge. |
| `MERGED` → `DONE` | Confirmed merge and, where Jira is enabled and explicitly authorised, confirmed Done reconciliation. |

`READY_FOR_CRITIC` is not `READY_FOR_OWNER_AUTHORIZATION`. Neither a critic approval nor a passing final gate authorises a merge.

### 12.2 PR discipline

When a worker reports completion, the assigned publisher must push the scoped feature branch and open a **draft** PR against `github.base_branch`. AWF then observes repository, PR number, head/base SHA and target before it completes candidate-bound records.

Current validation and requirements are required before marking the PR ready. The independent critic reviews the observed head after the draft is cleared. Any head/base/requirements/policy change requires the appropriate fresh evidence and review.

Routine-publication classification can describe whether a PR is suitable for a routine handoff. It requires accepted repository/default/ref identity and fresh matching `APPLIED` rules evidence, but it never grants execution authority or removes platform, secret, protected-ref, scope or adapter gates.

### 12.3 Final gate and authorisation

The human authorisation record binds ordered, case-sensitive AWF fields, a nonce, gate hash, expiry and raw unedited source. A trusted executor must authenticate the human source/quorum, reject replay, re-check the candidate/base and consume the authorisation once.

Offline helpers validate consistency only. They cannot authenticate the person, prove source time or authorise a merge.

### 12.4 Invalidation and recovery

The following invalidate affected evidence: authorisation revoked, base changed/retargeted, blocking thread reopened, CI failure/unavailability, head changed, merge-method change, policy change and requirements change. Unspecified events are rejected.

During `MERGING` or `MERGE_UNKNOWN`, disruptive events create uncertainty. Reconcile the external state before retrying. Do not replay an uncertain merge. Confirmed manual merges must match the candidate and authorisation; confirmed reverts reopen merged work rather than simply cancelling it.

---

## 13. Evidence, validation and review

### 13.1 Validation

The project configuration can require local validation commands and candidate-bound CI evidence. Required CI must be fresh, bound to the current candidate, and have the configured application/workflow provenance. A green check name alone is insufficient.

The template separates:

| Check type | What it establishes |
| --- | --- |
| `verify-installation` | Installed managed bytes match the receipt. |
| `validate-config` | Configuration conforms to AWF policy/schema. With `--require-enablement`, it also checks CI/owner prerequisites; it still does not qualify an adapter. |
| `status` | Current adoption state, proofs and the next action. |
| `validate-record` | Shape and local semantics of a single record, not cross-record/live-source truth. |
| `evaluate` | Offline candidate-gate evaluation; optionally renders a requested authorisation record. |
| `verify-authorization` | Consistency of authorisation, request, gate and config, not human authentication. |
| `self_test.py --checks-only` | Component checks without release qualification. |
| `self_test.py --release` | Explicit current independent-review requirement for source-release acceptance. |

Ordinary component checks may be green while release qualification remains false or review is `NOT_PROVIDED`. Offline fixtures do not prove live service integration or model quality.

### 13.2 Independent critic and specialists

The critic is required by default, uses an independent context and blocks on unresolved `BLOCKER` or `MAJOR` findings. The project may require re-review after every head or target-base change and a full final review. Critics retain finding identities; resolved findings require resolution evidence.

Specialists are triggered by configured paths, keywords and risk flags. An approving critic does not replace a required specialist review.

### 13.3 Evidence that must remain durable

Keep durable, candidate-bound records for at least:

- Requirements and policy hashes.
- Ticket/stream/ownership and non-overlap evidence.
- PR repository, number, head, base, target and merge-method identity.
- Validation and CI outputs with provenance and freshness.
- Critic and specialist findings, resolutions and currentness.
- Final-gate result and human-authorisation record.
- Routing reservations, settlement, usage/outcome, quarantine/reconciliation history and operating/policy hashes.
- Merge observation and, where applicable, Jira read-before/read-after reconciliation evidence.

The audit store must be outside worker worktrees. Preserve unknown charges/outcomes and failed evidence; never turn missing facts into green evidence.

---

## 14. Jira boundary

AWF workflow states are not Jira statuses. The standard framework models only one possible Jira status mutation:

> After a confirmed merge, make one explicitly authorised ticket transition to `jira.status_map.done`.

Rules:

- Epics are scope/grouping selectors and are never transition targets.
- `Backlog`, `Ready`, `In Progress` and `In Review` mappings are reserved; AWF does not use them for automatic workflow mirroring.
- Read the issue before attempting the Done transition, avoiding a duplicate write if already Done.
- Read it again afterward and record requested/observed status, issue/operation identity and actor/time only when observed.
- If the result is mismatched, unavailable or unknown, stop all further Jira writes and report a suspected external-automation conflict. Do not retry the mutation.
- Continue unaffected streams and read-only reconciliation.
- If Jira is disabled or unscoped, perform no Jira writes and do not invent tickets, approvals or `jira_done_confirmed` evidence.

The shipped reference adapter itself performs no Jira writes.

---

## 15. Optional scheduled review loop

The scheduled review loop is a separate, optional host adapter for one explicitly enrolled existing PR. It supports Codex CLI and same-repository GitHub.com PRs. It does not create the initial PR, merge it or write Jira.

### 15.1 Preconditions

Before live enrollment, prepare four physically separate directories:

1. Trusted runtime.
2. Protected state.
3. Clean worker clone.
4. Independent critic clone.

Both clones must have the exact GitHub origin. The worker clone must already match the owned PR branch/head. Existing writer ownership must be transferred before enrollment.

Copy `host-config.example.json` to protected state and fill it with observed/pinned values: repository/PR/branch identity, executable hashes for Git/gh/Codex, runtime manifest hash, frozen contract path/hash, worker/critic routes, allowed files, initial finding ledger, CI App/workflow pins, limits and qualification record.

Changing requirements, binaries or configuration requires a reviewed replacement enrollment. Never silently update pins.

### 15.2 Commands

```text
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json check
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json enroll
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json tick
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json status
```

`check` reads GitHub and prepares the critic clone; it does not qualify the model or isolation. `enroll` records ownership. A `tick` performs one review, amendment or CI observation and may make a normal non-force amendment push. A single protected database serialises ticks; another native writer must not concurrently own the branch.

Defaults are three amendment attempts, ten agent runs and 24 CI waits. Attempts remain consumed across failure/resume. CI must match the current head, pinned App identity, Actions workflow path/content and a successful conclusion.

### 15.3 Scheduling and recovery

Windows Task Scheduler registration is optional. Preview registration first, then use `-Create` only under existing host authority. The standard schedule is every five minutes and requires a logged-in user.

Before maintenance, disable wakeups and pause the loop. Disabling a scheduled task does not terminate an active process. After a timeout/crash/uncertain push, preserve retained run inputs, results and logs; inspect remote state, local commits and surviving processes; then reconcile before resuming. Never replay an uncertain push or create another database to reset limits.

---

## 16. Optional external review adapter

AWF includes an optional Claude/publisher component. It ships inert and unqualified. Native critic review and the scheduled review loop are separate; this adapter is not a prerequisite for installing or adopting AWF.

If organisational policy does not permit this adapter, leave it disabled.

### 16.1 Core constraints

- It operates only on a non-draft same-repository PR with protected-default-branch context.
- PR files are untrusted data: do not execute PR commands or install PR dependencies in the adapter.
- The model performs a read-only review phase; the publisher validates schema, engine, App/repository identity and current head before it comments.
- Review presence never resolves blockers or authorises a merge.
- Model and publisher credentials must be isolated from workers and from each other.
- Current-head, workflow provenance and qualification evidence are required before its review can serve as a gate input.

### 16.2 Mandatory secret boundaries

| GitHub environment | Exclusive secret |
| --- | --- |
| `awf-review-model` | `ANTHROPIC_API_KEY` |
| `awf-review-publisher` | `AWF_REVIEWER_APP_PRIVATE_KEY` |

Restrict both environments to the exact protected default branch. Do not use broad patterns, tags, merge refs or agent branches. Keep equivalent credentials out of organisation/repository scope, leave agent-controlled branches secret-less, and protect workflow/adapter/policy paths and environment administration.

### 16.3 Qualification

Qualification needs a separately authorised disposable canary that demonstrates environment separation, rejection of agent/unprotected targets, read-only model permissions, publisher identity, stale-head binding and gate/check provenance. Retain inventory without secret values, workflow/policy hashes, App identity, run URLs and probe evidence.

Offline/historical/native smoke results do not qualify this component. It must be requalified after material code, policy, action, credential or environment changes.

---

## 17. Security, integrity and recovery

### 17.1 Security principles

- Candidate, ticket and agent content never grants authority.
- Keep secrets, credentials and protected state outside worker worktrees.
- Worktrees are not an isolation boundary.
- Store state with restrictive permissions; the source defaults to POSIX `0600`. Shared access needs reviewed policy.
- Never execute candidate-controlled code in privileged review/publisher contexts.
- Protected paths include AWF governance, workflow configuration, CODEOWNERS and CI workflow paths by default.
- No force-push, branch deletion or server-rule mutation should be inferred from bootstrap or routine publication status.
- Digests identify bytes but are not publisher signatures.

### 17.2 Repository rules

The supplied main ruleset is an **offer** for separately authorised owner application; bootstrap never changes server rules. The sample ruleset follows the actual default branch and requires PRs without requiring a sole maintainer to self-approve. It permits squash/rebase only if repository settings also permit them.

Before relevant live work, configure real required checks and actual trusted owner identities. An empty required-check list cannot establish CI/review provenance. Approval dismissal does not refresh comment evidence, and resolved threads do not authenticate a review.

### 17.3 Recovery principles

| Situation | Required response |
| --- | --- |
| Incomplete skill install/cache | Preserve it; use the reported recovery procedure or a new dedicated cache. Do not overwrite/delete blindly. |
| Invalid operating transaction | Inspect the journal; preserve temporary material; recover with an independently inspected journal digest. |
| Unknown model use/outcome | Treat usage as unknown, not zero; reconcile through approved host process. |
| Candidate/head/base changed | Invalidate stale evidence and obtain fresh validation/review/gate evidence. |
| Unknown merge result | Reconcile external state before retrying. |
| Jira mismatch/unknown | Stop further Jira writes; report suspected automation conflict; do not retry. |
| Host crash or uncertain push | Preserve state/logs/commits; reconcile processes and remote state before resume. |

### 17.4 Vulnerability reporting

Report vulnerabilities privately to the publisher-designated contact at `jkinlay@gmail.com`. Include the release hash, affected component and a redacted reproduction. Do not open a public issue containing exploit details or secrets. Pause affected automation and preserve evidence. No response-time or cryptographic identity guarantee is claimed.

---

## 18. Command reference

Commands below require reviewed absolute paths and appropriate local authority. `--help` on the installed release is the definitive source for optional arguments.

### 18.1 Portable skill and release commands

| Command | Purpose |
| --- | --- |
| `python -B install_awf.py --dry-run` | Verify and preview portable skill install/upgrade. |
| `python -B install_awf.py` | Install/upgrade the portable skill. |
| `python -B awf/scripts/prepare_release.py --cache-dir ABS_PATH --version 1.8.9` | Verify and prepare an isolated local source cache. |
| `python -B awf/scripts/awf.py --catalog CATALOG locate …` | Read-only locate a pinned release in the local catalog. |
| `python -B awf/scripts/awf.py --catalog CATALOG inspect …` | Read-only inspect a pinned local release. |
| `python -B awf/scripts/check_updates.py --project ABS_PROJECT --channel CHANNEL --version 1.8.9` | Check an owner-controlled channel; it does not download/install a release. |

### 18.2 Project bootstrap and status commands

| Command | Purpose |
| --- | --- |
| `bootstrap_project.py --dry-run` | Preview project adoption. |
| `bootstrap_project.py --on-conflict backup` | Perform backed-up adoption/installation. |
| `workflow.py verify-installation` | Verify installed managed bytes. |
| `workflow.py validate-config` | Validate project policy/configuration. |
| `workflow.py validate-config --require-enablement` | Also require CI/owner enablement prerequisites; no adapter qualification implied. |
| `workflow.py status [--json]` | Recompute adoption state, proofs and next action. |
| `workflow.py status --adoption-pr N --gh PATH` | Bind status observation to the receipt-changing adoption PR using a trusted GitHub CLI. |
| `workflow.py status --release-source PATH --expected-manifest-sha256 SHA256` | Supply independently trusted release source evidence for `ACTIVE`. |
| `workflow.py capabilities` | Describe reference-tool capabilities and boundaries. |

### 18.3 Evidence and authorisation commands

| Command | Purpose |
| --- | --- |
| `workflow.py validate-record TYPE FILE` | Validate a single record schema and local semantics. |
| `workflow.py evaluate BUNDLE [--request]` | Evaluate gates; `--request` renders an authorisation request from a current gate. |
| `workflow.py verify-authorization --record R --request Q --gate G --config C` | Verify local authorisation consistency. |

### 18.4 Operating, routing and planning commands

| Command | Purpose |
| --- | --- |
| `workflow.py operating show` | Show current operating choices and effective ceiling. |
| `workflow.py operating set --instruction TEXT --set PATH=VALUE` | Apply a direct authorised operating choice inside governance. |
| `workflow.py operating set --epic EPIC-ID …` | Apply a precise Epic-scoped route override. |
| `workflow.py operating recommend --epics FILE [--inventory FILE]` | Produce a recommendation from observed scoped Epics. |
| `plan_streams.py` | Produce project-owned stream plan material from verified inventory. It does not launch agents. |
| `route_model.py suggest` | Return a route proposal without side effects. |
| `route_model.py reserve` | Reserve one bounded run in the protected routing ledger. |
| `route_model.py settle` | Record observed execution outcome once. |
| `route_model.py reconcile` | Record operator reconciliation for an exact run where the feature is enabled. |

### 18.5 Repository, review and release-validation commands

| Command | Purpose |
| --- | --- |
| `repository_rules.py --observe` | Read actual GitHub default-branch rule state. |
| `repository_rules.py --require-enablement` | Fail unless observed rules meet repository-rule prerequisites; no adapter qualification implied. |
| `review_loop.py check|enroll|tick|status|pause|resume` | Manage the optional qualified single-PR scheduled review loop. |
| `scripts/self_test.py --checks-only --report FILE` | Run component checks without release qualification. |
| `scripts/self_test.py --release …` | Require externally pinned current independent review records for release acceptance. |
| `scripts/validate_archive.py --archive ZIP --expected-zip-sha256 SHA --report FILE …` | Full trusted archive acceptance flow, including extraction/testing/install/rebuild, with external review pins. |

---

## 19. Operational runbooks

### 19.1 First-time skill installation

1. Obtain an independent outer ZIP hash pin.
2. Verify the ZIP hash and extract the full distribution.
3. Run `install_awf.py --dry-run`.
4. Review the selected destination, previous version, backup location, preserved settings and duplicate report.
5. Run `install_awf.py`.
6. Start a new Codex task/turn if necessary for skill discovery.
7. Do not infer that any project was upgraded.

### 19.2 First project adoption

1. Confirm the target project, owner and intended AWF version.
2. State that adoption changes governance files and will prepare a draft PR for owner merge.
3. Inspect instructions, worktrees, owners, existing configuration/history and active PRs.
4. Prepare/verify the release cache and bootstrap dry run.
5. Preserve project-specific instructions, CODEOWNERS, configuration and history.
6. Bootstrap with backup semantics; do not apply server rules.
7. Install locked dependencies and run installed verification/configuration/status commands.
8. Show `operating show` and choose: keep defaults, review Epics and recommend, or custom.
9. Create/review the adoption draft PR and its checklist.
10. After owner merge, collect fresh default-branch/release-trust evidence before claiming `ACTIVE`.

### 19.3 Dispatching a ticket

1. Confirm it is in allowed scope with a valid contract, owner and satisfied dependencies.
2. Inspect file boundaries, other worktrees and active writers. Do not dispatch overlapping writers.
3. Check operating choice, effective ceiling and observed host capacity. Do not treat a ceiling as free slots.
4. Reserve the route/budget in the protected ledger.
5. Launch through a capable host and record observed agent identities/assignments.
6. Settle actual outcomes and retain evidence.
7. On worker completion, publish the scoped branch and draft PR before critic handoff.

### 19.4 Review, amendment and merge handoff

1. Verify current candidate-bound validation and requirements.
2. Clear the draft only after that evidence is current.
3. Dispatch an independent critic on the observed PR head.
4. Resolve findings through bounded amendments; register each new head and re-review.
5. Obtain required specialist reviews.
6. Run final-gate evaluation and reach `READY_FOR_OWNER_AUTHORIZATION`.
7. Obtain verified human authorisation for the exact candidate.
8. The owner/manual executor performs the merge. AWF records observed results; it does not silently merge.
9. If explicitly authorised and Jira is enabled, attempt one Done transition after confirmed merge, with read-before/read-after evidence.

### 19.5 Incident/recovery response

1. Pause new dispatch where the failure creates uncertainty.
2. Preserve ledger/state/journals/logs/PR and repository evidence.
3. Determine whether any external action occurred. Unknown is a valid result.
4. Reconcile only through the specified host/operator procedure.
5. Do not reset budgets, databases, amendment counts, merge permits or Jira writes to force progress.
6. Resume only after the relevant candidate, external state and resume guards are current.

---

## 20. Upgrading from 1.8.8 to 1.8.9

Upgrade through the same verified-skill installation and project-adoption flow. Retain the previous skill backup, project configuration, operating pins and audit evidence. The owner merges any governance change; installation alone does not migrate a project.

Version 1.8.9 changes operational handling in three ways:

| Change | Operational consequence |
| --- | --- |
| Computed effective ceiling | `operating show` and agent-supplied operating state expose the binding ceiling/path/sources. A disabled broker's stored worker limit does not constrain the stream count. |
| Enabled broker enforcement | If an enabled broker has a lower `max_workers`, operating validation and planning must reject a count above it. AWF does not silently clamp the count. |
| Compact recommendations | Unchanged pinned routes appear as a compact footnote rather than repeated no-change rows. Pins remain unchanged unless the user explicitly edits them. |

This patch does not raise accepted governance limits, enable a broker, qualify a host or regrade historical model results. Project-specific CI, workflow identity, live canary qualification and ownership remain separate work.

When migrating, review existing operating count and broker policy. If an accepted operating count exceeds a newly enabled broker cap, correct it through an audited operating change, allow surplus streams to drain and seek a governance PR only for protected-cap changes.

---

## 21. Troubleshooting

| Symptom | Meaning | Safe next action |
| --- | --- | --- |
| `CACHE_EXISTS_UNVERIFIED` | The requested cache already exists but is incomplete/unrelated. | Preserve it and use a new dedicated empty cache path. |
| Installer reports a conflicting/unknown/newer version | The target is not safely replaceable. | Do not force it. Inspect the existing receipt/version and choose an approved migration/recovery path. |
| `INSTALLED_UNCONFIGURED` or configuration rejection | Managed files exist but config is incomplete/invalid. | Use the reported field/path remedy, then re-run installed verification and config validation. |
| Status remains `CONFIGURED` | Local consistency is proven but release trust/adoption merge/default-branch evidence is incomplete. | Follow the single reported next action; do not claim `ACTIVE`. |
| `operating set` refuses a stream count | A governance ceiling, enabled broker cap, legacy reviewer constraint or schema limit binds. | Read the returned path/remedy. Change operating choices only within policy; submit a PR for protected caps. |
| Route is `unavailable` | The host does not support the required allowed route. | Supply observed capability data or revise policy through review; do not silently downgrade. |
| Routing ledger quarantined | Actual results mismatched/overran reservation or remain uncertain. | Preserve evidence and reconcile through authorised host process. |
| Critic report is stale | PR head/base/requirements/policy changed. | Revalidate and obtain fresh independent review. |
| CI check is green but gate blocks | Candidate/head/provenance/freshness/App/workflow binding is wrong or incomplete. | Gather current candidate-bound CI evidence. |
| Jira result is unknown/mismatched | Another automation or unavailable API may have affected the issue. | Stop Jira writes, read authoritative state and report the conflict; never retry the same transition. |
| Scheduled-loop push is uncertain | A remote effect may have occurred. | Preserve run state and reconcile remote/local history before resume. |

---

## 22. Glossary

| Term | Definition |
| --- | --- |
| Adoption | Project-specific integration of AWF-managed governance/configuration files, prepared through a draft PR. |
| Active (`ACTIVE`) | Adoption state with independent release trust, observed accepted adoption merge and fresh accepted default-branch bytes. It does not enable adapters. |
| Candidate | Exact PR/repository/head/base/target/requirements/policy tuple under review. |
| Ceiling | Governance upper bound, not evidence of current capacity or dispatch permission. |
| Critic | Independent reviewer of the observed PR head. |
| Effective ceiling | Minimum of structural six-stream maximum, project ticket ceiling and enabled broker worker cap. |
| Final gate | Derived current evidence decision required before owner authorisation. |
| Host | Environment responsible for actual agent launch, limits, identity/termination observations and external access controls. |
| Native stream | One bounded implementation workstream, A–F, with one independent reviewer per active stream. |
| Operating configuration | Mutable project-owned choices within protected governance, stored in `OPERATING_CONFIG.yaml`. |
| Pin | A route/user choice protected from optional escalation; it cannot override mandatory floors. |
| Protected ledger | Durable routing/retry/accounting state outside worktrees. |
| Qualification | Evidence that an optional live adapter/host boundary has been tested and accepted for a defined purpose. |
| Reconciliation | Controlled recording of operator/host observations after an uncertain run or external result. It does not authenticate arbitrary assertions. |
| Release preparation | Pinned archive verification and separate-cache extraction without installation or adoption. |
| Routine publication | A presentation classification for an eligible PR handoff; it grants no permissions. |
| Stream drain | Reducing active operating streams without cancelling already reserved/current tickets. |

---

## Source basis

This manual was derived from the verified AWF 1.8.9 portable distribution, including its release/skill manifests, `INSTALL.md`, `README.md`, `AGENTS.md`, `.agentic/SPECIFICATION.md`, project configuration, operating configuration, lifecycle/routing/adoption/review runbooks, CLI schemas, migration document and portable test suite. It intentionally distinguishes verified release facts from project-specific choices, host observations and human authorisations.
