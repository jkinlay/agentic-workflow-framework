### `ORIG_HEAD.lock` or sandbox refusal to write Git metadata

**An exact `ORIG_HEAD.lock` repair procedure is UNVERIFIED.** The publication guide discusses stale pseudoref claimants, not this lock error. Do not equate those conditions. Source: `7fcaeece:.agentic/docs/31-PUBLICATION-SAFETY.md:104`.

For a worker whose sandbox blocks commit metadata writes, the documented route is to leave the validated tree uncommitted and hand off `commit_route: PUBLISHER`, `tested_tree`, declared changes, and excluded ignored/untracked content. The publisher makes no content edits and verifies the resulting tree. Sources: `7fcaeece:AGENTS.md:20`; `7fcaeece:.agentic/docs/23-TICKET-LIFECYCLE.md:8`.

Retain the refusal. Do not retry around the sandbox, delete locks blindly, or claim a commit/PR exists without observation.

