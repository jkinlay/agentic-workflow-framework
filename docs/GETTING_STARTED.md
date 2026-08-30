# Getting Started with the Agentic Workflow Framework

## What this guide does

This is the practical path for adopting the Agentic Workflow Framework (AWF)
in either a new repository or an established repository. It takes a project
from initial assessment through a local A1 pilot and, when the required
server-side controls and reviewer evidence exist, to A2 pull-request work.

Use this guide for the sequence of actions. Use the
[integrated system architecture](SYSTEM_ARCHITECTURE.md) to understand how the
components fit together, and the root [ARCHITECTURE.md](../ARCHITECTURE.md) for
authoritative architectural decisions and invariants.

AWF v0.1.1 uses a manual, reviewable bootstrap. There is no supported command
that safely installs or upgrades every project automatically. The distribution
manifest, project scaffolds, provider assets, and checks are the installation
inputs; a human-reviewed pull request is the installation mechanism.

## Understand the activation states

Do not treat copied files or available credentials as proof that AWF is ready
for agent-authored pull requests.

| State | Meaning | What it permits |
|---|---|---|
| **Assessed** | Ownership, repository, confidentiality, tests, identities, and external systems are known. | A0 read-only assessment only. |
| **Installed** | Selected AWF files are present in a reviewed project change and the generated lock matches them. | A1 local work after human plan approval. |
| **Configured** | Protected policy, identities, repository variables, secrets, rulesets, and provider settings exist. | Configuration tests; still A1. |
| **Demonstrated** | Permission, identity, current-head, failure, and seeded-review evidence has been retained. | Human qualification decision; still A1 until approved. |
| **Qualified** | Every qualification flag is true because reviewed evidence supports it. | The engine may be selected as required. |
| **Enabled** | The qualified engine is required, `awf/review` is a required check, branch protections are active, and the project owner authorizes A2. | Bounded branch, push, and draft-PR writes within project policy. |

If any required evidence is missing, stay at A1. A failing gate is a stop
condition, not a reason to weaken policy or bypass a check.

## Choose your path

- Follow [Path A](#path-a-new-repository) for a new or nearly empty repository.
- Follow [Path B](#path-b-existing-repository) when the repository already has
  code, instructions, CI, architecture, ownership, or release controls.

Both paths converge on [the common installation and activation
sequence](#common-installation-and-activation-sequence).

## Prerequisites

Identify these before creating a bootstrap branch:

- canonical repository, default branch, project owner, and issue tracker;
- confidentiality domain and an approved agent/runtime environment;
- root architecture and agent instructions, or the human owners who will write
  them;
- reproducible setup, test, lint, and domain-gate commands;
- protected default branch and an owner able to configure rulesets, CODEOWNERS,
  required checks, variables, secrets, and GitHub Apps;
- dedicated developer, reviewer, publisher, and human identities as applicable;
- required external-review engine and its licensing, data-egress, credential,
  and budget approval;
- durable audit/evidence location, retention period, incident stop authority,
  credential-revocation owners, and ruleset bypass actor;
- release, publication, deployment, or research-promotion boundary.

Complete [PROJECT_ONBOARDING.md](PROJECT_ONBOARDING.md) before enabling writes.
For quantitative projects, also identify point-in-time, leakage, determinism,
lineage, execution-cost, capacity, and research-promotion gates.

## Path A: new repository

1. Create the repository inside the correct personal or corporate boundary.
   If a reviewed AWF-derived GitHub template is available, it may seed the
   repository. Otherwise start with a normal empty repository and use the AWF
   project scaffolds manually.
2. Add a human-written `README.md` explaining the product or research project.
3. Create the project's `ARCHITECTURE.md` from
   `scaffolds/project/ARCHITECTURE.md.template`. Replace every placeholder and
   describe the actual system, data, deployment, security, and approval
   boundaries.
4. Create `AGENTS.md` from `scaffolds/project/AGENTS.md.template`. Preserve the
   managed AWF guidance block, then add concise project-specific rules and
   verification commands.
5. Establish the project's initial tests and CI before asking an agent to make
   implementation changes.
6. Create a dedicated bootstrap branch and continue with the common sequence.

A repository produced from a GitHub template has an independent history. It is
not automatically synchronized with AWF; later changes use the normal upgrade
procedure.

## Path B: existing repository

1. Start from a clean branch or isolated worktree based on an immutable current
   default-branch commit.
2. Inventory every applicable instruction and executable workflow, including:

   - root and nested `AGENTS.md` and `AGENTS.override.md` files;
   - `CLAUDE.md`, `.agents/`, `.codex/`, and `.claude/` content;
   - `.github/` workflows, instructions, CODEOWNERS, and dependency automation;
   - project architecture, setup, test, release, and incident documentation;
   - hooks, generators, registries, controlled CLIs, and research pipelines.

3. Record existing server-side branch rules, environments, Apps, secrets, and
   identities. Files in a checkout are not protected merely because they are
   listed as forbidden to an agent.
4. Compare the repository to
   [`.agentic-workflow/distribution-manifest.yaml`](../.agentic-workflow/distribution-manifest.yaml).
5. Preserve project-owned `AGENTS.md`, `ARCHITECTURE.md`, tests, and CI. Apply
   merge-assisted amendments; never replace them wholesale with generic
   scaffolds.
6. Stop on a target conflict or unexplained local drift. Decide ownership with
   the project owner rather than silently overwriting a file.
7. Continue with the common sequence, recording every created, amended,
   skipped, or conflicted artifact in the bootstrap evidence.

## Common installation and activation sequence

### 1. Create protected project policy

Create `.agentic/project.yaml` from
`scaffolds/project/.agentic/project.yaml.template` and resolve every token in
`scaffolds/project/placeholders.yaml`.

At minimum, have the human owner set:

- stable project ID and confidentiality domain;
- authoritative repository, tracker, CI, and artifact systems;
- `autonomy.maximum_level: A1` for initial adoption;
- human approvers, audit retention, and incident stop authority;
- the complete protected-path floor plus project-owned gate and test-harness
  paths;
- one proposed required review engine and any optional engines;
- adapter-owned reviewer identities and provider settings;
- all `review.qualification` flags to `false` initially;
- `review.fail_closed: true` and human approval requirements.

Commit the project policy. Put machine-specific mappings in ignored local
configuration, not in the protected shared file.

### 2. Select and install distribution artifacts

Use the distribution manifest as the canonical source-to-target list. Apply
only artifacts whose `requires` condition matches the selected runtime and
review engines.

The normal core includes:

- project policy and a generated workflow lock;
- provider-neutral review, threat, identity, audit, and incident documents;
- the review-report schema;
- the engine-aware `awf/review` gate;
- the selected runtime adapter documentation;
- the selected reviewer instructions, workflow, scripts, and tests;
- merge-assisted CODEOWNERS and project-instruction amendments.

For Claude, install the split model/publisher scripts and both Claude workflows
from `scaffolds/providers/claude/`. For Copilot, install the Copilot instruction
and gate assets selected by the manifest. Keep every `managed` installed Git
blob identical to its scaffold source; line-ending differences in a working
tree do not matter when the staged Git blob IDs are equal.

### 3. Generate the workflow lock

Generate `.agentic/workflow.lock.yaml` only after the installation diff is
final. Record:

- framework repository, version, immutable current source commit, original
  bootstrap source commit, and Git object format;
- manifest Git blob ID and content hash;
- selected runtime and review adapters;
- every installed target, source, ownership policy, source and installed Git
  blob ID, and source and installed SHA-256;
- the exact staged tree represented by the installation, excluding the lock's
  self-reference.

Never copy a static lock template. Recompute the lock whenever a managed source
or installed target changes.

`framework.source_base_commit` identifies the immutable framework source for
the current lock contents. `bootstrap.base_commit` remains the immutable source
used for the project's original bootstrap, so later upgrades retain their
three-way comparison point; it is not advanced on each upgrade.

### 4. Configure the reviewer outside Git

Credentials and live repository settings are owner actions. Never put their
values in policy, documentation, commits, task text, comments, or logs.

For the split Claude adapter, the owner normally:

1. Creates a dedicated reviewer GitHub App installed only in the approved
   repository or confidentiality domain.
2. Grants Metadata read and Pull requests read/write, with no webhook unless a
   separately reviewed design requires one.
3. Records the App's bot login in protected project policy.
4. Adds the repository variable and secrets named by the workflow for the App
   identity/private key and approved Anthropic API credential.
5. Selects a pinned model ID and approved API base URL in protected policy.
6. Sets an Anthropic workspace spend limit and task budget large enough for the
   demonstration while retaining human control of expenditure.

Pull requests read/write is not a literal comment-only token permission. GitHub
may allow other pull-request metadata operations, such as labels. The security
property is that the model job cannot access this token and the deterministic
publisher implements only validated `COMMENT` review behavior. The live
demonstration must record the App's effective permissions rather than relying
on the intended configuration.

Provider requirements and activation details are maintained in:

- [providers/claude-code-action.md](providers/claude-code-action.md)
- [providers/copilot.md](providers/copilot.md)

### 5. Protect the control plane

Before granting A2 push access, configure server-side controls for:

- every agent instruction and override path;
- `.agentic/`, `.agents/`, `.codex/`, `.claude/`, and provider instructions;
- CI/workflow definitions, CODEOWNERS, hooks, dependency execution, and project
  gate/test-harness paths;
- stale-approval dismissal, approval of the latest reviewable push,
  conversation resolution, and a non-author human approval;
- secret-less agent-authored branches or human-approved secret-bearing
  environments;
- a named non-agent bypass actor whose use creates an incident record.

Do not make `awf/review` a required check while its selected required engine is
unqualified unless the owner deliberately accepts using the bypass actor for
the activation sequence. The safer order is: install with flags false, collect
evidence, qualify in a separate PR, and then enable the required check.

### 6. Validate the installation

Run the project tests and provider tests from the final staged content. For the
Claude adapter, the offline suite can be run with:

```text
python -m unittest discover -s .agentic/review/claude/tests -v
```

Framework maintainers also run the distribution regression suite manually
until AWF-022 adds upstream CI:

```text
python -m unittest discover -s tests -p 'test_*.py' -v
```

Run it from a full Git clone: provenance checks deliberately fail when Git or
the lock's recorded source history is unavailable.

Also verify:

- policy resolves the intended engine, identity, model, base URL, and evidence
  paths;
- every installed managed target has the same staged Git blob ID as its source;
- the workflow lock's blob IDs, hashes, and pre-lock tree reproduce exactly;
- all active third-party Actions are pinned to reviewed commit SHAs;
- no unresolved `__AWF_*__` placeholder remains in a runtime target;
- local links and paths resolve;
- no credential, private key, environment dump, confidential content, raw tool
  log, or machine-specific absolute path is tracked;
- `git diff --check` and the project's authoritative checks pass.

Record commands, results, limitations, and skipped environment-dependent tests
in the bootstrap evidence.

### 7. Merge the installation before demonstrating it

Submit a dedicated bootstrap or activation pull request. A human reviews and
merges it under the repository's existing controls. The reviewer workflows run
from the base-branch definitions, so a workflow added only on a pull-request
branch cannot provide the trusted live demonstration for that same change.

Merging the adapter means **installed**, not **qualified**.

### 8. Run a canary and reviewer demonstration

Create a same-repository canary pull request that will never be merged. Include
reviewable code or documentation, a task contract, an execution report, and
human-approved seeded defects covering the required benchmark classes.

For Claude:

1. Confirm the model job reads the protected policy and treats the head as data.
2. Confirm the publisher posts one review under the dedicated bot identity with
   state `COMMENTED`, the exact head commit ID, and the AWF status/head markers.
3. Confirm findings and claim assessments are schema-valid and phase-labelled.
4. Push a second commit and confirm the old review remains bound to the old head
   and a new review is required.
5. Exercise a provider failure or inspect recorded failure evidence: an error
   report must exit non-zero and the publisher must not run.
6. Run the Claude demonstration workflow and retain `demonstration.json` plus
   the relevant immutable run, installation, review, and artifact references.
7. Record all effective permissions. A capability that the deterministic
   publisher does not use is still part of the token's permission evidence.

The demonstration's `suggested_qualification` values are advisory evidence,
not authorization to edit flags automatically. Close the canary without
merging after its durable evidence has been copied into an evidence or
qualification pull request. Retain reviewed refs for the project's approved
audit period.

### 9. Decide qualification explicitly

Create a documentation-only evidence pull request based on the current default
branch. It should contain or durably link:

- App installation scope and effective permissions;
- model-job and publisher-job negative permission results;
- reviewer identity and COMMENT-only publisher behavior;
- protected source/target hashes and offline test results;
- first-head review and second-head/stale-head evidence;
- seeded benchmark results by mandatory defect class;
- limitations, unexpected capabilities, provider constraints, costs, and
  unresolved design decisions;
- a criterion-by-criterion statement of what supports each qualification flag.

Do not set a flag merely because adjacent evidence exists. For example, model
write denial supports effective permissions, while server-side path rules
support protected instructions, and current/stale-head behavior supports head
binding. If the gate's ordering prevents a desired live observation while
another qualification flag is false, the human owner must decide and document
whether publisher-side binding plus deterministic gate tests are sufficient or
whether a post-qualification canary is required. Do not silently redefine the
criterion.

### 10. Enable the qualified engine and A2 separately

Only after the evidence pull request is accepted:

1. Submit a protected-policy pull request setting supported qualification flags
   to true.
2. Select the qualified engine as `review.required_external_engine`.
3. Confirm the gate succeeds for a fresh review of the live head.
4. Make `awf/review` and project CI required checks with the expected GitHub
   Actions source.
5. Verify branch rules, non-author human approval, CODEOWNERS, stale dismissal,
   secret isolation, audit retention, and incident recovery.
6. Have the human owner explicitly authorize A2 for the project or bounded
   workflow.

Optional engines provide additional evidence but never satisfy the required
engine's gate.

## Run the first AWF task

Start with a documentation-only or test-only pilot, then one small
implementation task without external side effects.

1. A human marks an issue ready with objective, non-goals, acceptance criteria,
   dependencies, affected repository/component, required checks, budget,
   deliverables, and approvals.
2. A human approves a task contract with an immutable base, explicit allowed
   and forbidden paths, and the project's protected-path floor.
3. The coordinator assigns one writer to one worktree, branch, issue, and PR.
4. The developer implements only the bounded task, runs authoritative checks,
   and produces an execution report and acceptance-evidence map.
5. The external engine reviews the current head independently. The developer
   does not provide the binding review or resolve its own findings unilaterally.
6. Failed checks or blocking findings return the task to implementation. A new
   commit invalidates the old head review.
7. QA and CI run from clean state; the coordinator links rather than rewrites
   their evidence.
8. A non-author human inspects the diff, findings, and evidence and decides on
   merge. Release, publication, deployment, and research promotion are separate
   human decisions.

The authoritative lifecycle and state transitions are in
[OPERATING_MODEL.md](OPERATING_MODEL.md).

## Common problems

| Symptom | Meaning and safe response |
|---|---|
| `awf/review` says policy is missing | The protected base branch has no valid `.agentic/project.yaml`; install or repair policy through human review. |
| Qualification flags are false | Expected during installation and evidence collection; remain at A1. |
| Claude workflow does not run | It must exist on the base branch, the PR must be non-draft and same-repository, and required secrets/settings must exist. |
| Model job emits `status: error` | Provider, credential, schema, timeout, or token-budget failure; publisher remains skipped and the gate stays closed. |
| Publisher is skipped | The model job did not succeed; never post or manufacture a review manually as a substitute. |
| Reviewer App key is rejected | Re-enter the complete private key in the approved secret store; never commit or paste it into evidence. |
| Provider reports insufficient credit or spend limit | A human owner decides whether to add approved budget, then reruns the failed job. |
| Review exists only for the old head | Push or rerun for the new head; stale review is evidence of prior work, not current approval. |
| App token can perform an unexpected PR operation | Record the effective permission, confirm the model cannot access the token, assess the deterministic publisher, and withhold qualification if the residual risk is unacceptable. |
| Fork PR receives no Claude review | Expected: the supplied secret-bearing `pull_request_target` workflow is restricted to same-repository branches. |
| Required check blocks an activation PR | Use the named human bypass actor with an incident record only if already approved, or remove the premature requirement through normal owner review and restore it after qualification. |

## Upgrade and recovery

For a new AWF version, follow [UPGRADING.md](UPGRADING.md): compare the new
manifest to the installed lock, stop on drift, update managed files, propose
merge-assisted changes, rerun validation and qualification as required, and
regenerate the lock in a normal reviewed pull request.

For compromise, unexpected writes, confidentiality crossing, or reviewer
bypass, follow [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md). Stop execution,
revoke credentials, quarantine affected refs, preserve evidence, recover from
a trusted base, and require human reactivation.

## Completion checklist

A project is ready for its first A2 task only when all of these are true:

- [ ] onboarding and confidentiality review completed;
- [ ] project architecture, instructions, setup, tests, and CI are authoritative;
- [ ] selected AWF artifacts installed through a human-reviewed change;
- [ ] generated workflow lock exactly reproduces installed content;
- [ ] project policy is protected and contains no unresolved placeholders;
- [ ] developer, reviewer, publisher, and human identities are distinct and audited;
- [ ] protected paths, CODEOWNERS, branch rules, and secret isolation are enforced server-side;
- [ ] selected reviewer demonstrated on the project with retained permission and head-binding evidence;
- [ ] seeded benchmark accepted by required defect class;
- [ ] every qualification flag is supported by reviewed evidence and true;
- [ ] `awf/review` and project CI pass for the live head and are required checks;
- [ ] non-author human approval and bypass/incident procedures are configured;
- [ ] the human owner explicitly approved A2 and its budget/scope ceiling.

If any box is unchecked, continue with A0 assessment or A1 local pilots.
