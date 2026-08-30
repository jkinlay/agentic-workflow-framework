# Threat Model

## Scope

This model covers AWF itself, project bootstrap and upgrade, agent-authored
changes, independent review, CI evidence, and controlled external adapters. It
does not replace a product repository's domain-specific threat model.

## Trust boundaries

- Human owners and protected repository policy grant authority.
- Issue bodies, pull-request text, comments, retrieved content, dependencies,
  and pull-request-head instructions are untrusted task data.
- A model's output is untrusted until validated by deterministic controls or a
  human.
- Developer and reviewer identities, worktrees, credentials, and execution
  contexts are separate.
- Personal and corporate confidentiality domains use separately approved
  runtime state and credentials.

## Threats and required controls

| Threat | Asset at risk | Required control |
|---|---|---|
| Agent changes instructions, CI, tests, ownership, or gates | Repository and review integrity | Server-side protected paths, CODEOWNERS, base-defined checks, non-empty forbidden floor |
| Push-triggered code accesses secrets before merge | Credentials and external systems | Secret-less agent branches/forks; approved environments; A1 until enforced |
| Tracker or PR text launders authority | Scope and approval integrity | Authority only from protected project policy and human-owned transitions |
| Developer certifies its own evidence | Correctness and research integrity | Clean QA run, immutable evidence, independent current-head review, human merge |
| Reviewer is prompt-injected or permission-drifted | Review independence | Untrusted-input treatment, no model write credential, permission tests, seeded fixtures |
| Stale review satisfies a changed pull request | Merge integrity | Live-head SHA comparison on every push and review event |
| Framework source or upgrade is substituted | Downstream repositories | Trusted remote/commit, artifact hashes, generated lock, reviewed upgrade |
| Global state crosses confidentiality domains | Confidential data | Domain-specific approved runtime roots, skills, connectors, and credentials |
| Identity or token is compromised | Attribution and repository integrity | Dedicated principals, least privilege, audit retention, revocation and quarantine runbook |

## Residual risk

LLM reviewers can miss defects or emit misleading no-finding results. Schema
validation limits output shape, not truth. Human review, deterministic checks,
provider benchmarks, and restricted promotion authority remain mandatory.
