# Agentic Workflow Framework architecture

## Decision

AWF is a provider-neutral framework-and-instance system. The upstream
distribution supplies versioned contracts, schemas, templates, validation,
portable skills, and narrowly scoped adapters. Each adopted repository retains
its source code, architecture, instructions, issue-tracker configuration,
tests, CI, ownership, and release controls.

## System boundaries

- The issue tracker is the programme-management record; its content is
  untrusted until represented in a reviewed contract.
- Git and CI are the source-change and build-evidence records.
- AWF records workflow evidence and validates policy; it does not become a
  replacement tracker, source-control system, release system, or secret store.
- Provider-specific implementations—including GitHub, Jira, and external
  review engines—remain behind explicit adapters. GitHub repository discovery
  and rules observation live in `.agentic/lib/agentic/providers/github.py`;
  the legacy module is an import-only compatibility facade. A disabled adapter
  performs no external mutation.
- A human retains authority for merge, release, publication, production or
  research promotion, changes that weaken governance, and cap increases.

## Governing invariants

1. One writer owns a ticket/worktree; reviewer contexts are separate and
   effectively read-only.
2. Every contract declares scope, risk tier, acceptance criteria, closure
   standard, and evidence requirements before review.
3. Mandatory boundaries always block. Tier 1 may make eligible non-boundary
   findings advisory only through validated owner dispositions.
4. A review cap reaches a resumable decision state with a finite, owner-signed
   disposition; it is never reset by an agent.
5. Closeout is bound to Git objects and validated records, never a working tree
   or free-form Markdown.
6. Lifecycle comments are generated digests that identify authority not
   claimed; a comment is not a lifecycle transition.

The normative data contracts and state transitions are in
[.agentic/SPECIFICATION.md](.agentic/SPECIFICATION.md). Operational details
live in its linked runbooks; this document records the upstream boundary and
architecture decisions.
