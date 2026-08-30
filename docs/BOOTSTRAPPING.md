# Bootstrapping a Project

## Purpose

Bootstrapping creates a project-local AWF instance without transferring product
ownership to the framework repository.

## Existing repository

Use this mode for established repositories.

1. Work from a clean branch or isolated worktree.
2. Inventory and read every applicable instruction and executable agent/runtime
   configuration, including nested `AGENTS.md`, `AGENTS.override.md`,
   `CLAUDE.md`, `.github/`, `.agents/`, `.codex/`, and `.claude/` content.
3. Complete the project-onboarding checklist.
4. Compare existing files with `scaffolds/project/` and the distribution
   manifest.
5. Add managed files only when their target paths do not conflict.
6. Treat existing `AGENTS.md` and `ARCHITECTURE.md` as project-owned. Propose
   amendments; never replace them wholesale.
7. Create `.agentic/project.yaml` from its seed-once scaffold; have a human set
   the autonomy ceiling, approvers, protected paths, and required reviewer.
8. Resolve every token listed in `scaffolds/project/placeholders.yaml` and fail
   validation if any `__AWF_*__` token remains.
9. Generate `.agentic/workflow.lock.yaml` from installed files. Record the AWF
   version, provider adapters, ownership policies, source and installed content
   hashes, and bootstrap base commit; never copy a static lock template.
10. Protect instruction, reviewer, CI, ownership, runtime, and project-policy
    paths from the developer identity before granting A2 push access.
11. Verify the external reviewer's identity, effective read-only permissions,
    current-head behavior, structured result, fail-closed gate, and seeded
    benchmark. For Claude, run the demonstration workflow against a
    same-repository pull request and attach `demonstration.json`. Availability
    must be explicitly approved, not inferred.
12. Record the dedicated developer/reviewer principals, audit location and
    retention, incident stop authority, token-revocation owners, and ruleset
    bypass actor. The expected source of `awf/review` is GitHub Actions for the
    supplied gate.
13. Run repository tests plus confidentiality, absolute-path, placeholder, and
    secret scans.
14. Submit the bootstrap as a dedicated, human-reviewed pull request.

Bootstrap does not authorize connector writes, unattended execution, merge,
release, or production/research promotion.

## Greenfield repository

A GitHub template may create a new repository from AWF-derived starter content.
The new project must still replace placeholders, define its architecture and
tests, select runtime adapters, and pass onboarding review.

GitHub template repositories create independent histories. Treat the generated
repository as a new project, not as a synchronized child of AWF.

## Bootstrap output

The bootstrap report records:

- target repository and immutable base reference;
- AWF version;
- selected runtime adapters;
- selected external reviewer and permission evidence;
- reviewer identity, expected status source, stale-SHA result, and outage
  bypass actor;
- files created, amended, skipped, or conflicted;
- installed content hashes and protected paths;
- validation results;
- unresolved decisions;
- recommended activation level.
