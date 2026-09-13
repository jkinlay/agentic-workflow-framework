# Acceptance evidence

Candidate: AWF 1.7.0. [Release pins and documentation measurements](RELEASE.json) bind the artifacts. [Final archive acceptance](archive.json) passed 41 end-to-end checks. Windows/Python 3.12.14 was exercised; no live-provider qualification is claimed.

| Acceptance criterion | Evidence |
| --- | --- |
| Balanced routing, bounded escalation, honest accounting/reconciliation | Routing and CLI regressions; independent review preserved negative evidence and late defects across reconciled retries. |
| Three independent reviewers total, one per workstream | Configuration/capacity regressions, including a separate coordinator consuming the last host slot. |
| Documentation reduced tenfold | 60,021 to 5,866 source Markdown words: 90.23% reduction. Generated manifest inventories excluded consistently; skill prose totals 664 words. |
| Easy, recoverable skill upgrade | [Portable acceptance](skill-package.json): isolated upgrade of an installed 1.6 payload, exact backup/settings preservation, idempotence and verified discovery. Portable suite: 88 tests, one Windows symlink-privilege skip. |
| Exact reproducible source/skill/distribution | All five artifact digests reproduce on a separate build. Fresh-source suite: 338 tests; installed-project suite: 305 tests. Both passed with one Windows symlink-privilege skip each. Git round trips preserve bytes and negative controls reject corruption. |
| Apache-2.0, Jonathan Kinlay, one maintained lineage | LICENSE, NOTICE, PUBLISHER metadata and consolidation ADR; prior Git history retained. |

Three scoped independent native review passes covered routing/capacity, host/process behavior, and condensed adoption/documentation. Publication inventory was separately checked. A source-only generator dependency in one installed test was found during acceptance and corrected before delivery; no runtime behavior was changed by that correction.

Native review is not external-engine qualification. The accepted upstream review policy and gate remain unchanged and unqualified. The consolidation stays draft: no merge, required-check bypass, signed tag, live scheduler, Jira write or provider deployment is represented by these results.

Reproduce the source/project checks with `core/scripts/validate_archive.py --help`; install `core/.agentic/requirements.lock` using pip's `--require-hashes --only-binary=:all:`. Keep outputs outside `core/`. Run portable tests with `python -B -m unittest discover -s .agents/skills/awf/tests`.
