# Acceptance evidence

Candidate: AWF 1.8.0. [Release pins](RELEASE.json) bind the artifacts. The [archive acceptance report](archive.json) records fresh-source/project, preservation, CLI and Git checks on Windows/Python 3.12.14. Offline tests do not qualify live providers.

| Acceptance criterion | Evidence |
| --- | --- |
| Balanced routing and finite incident retries | 57 routing/CLI tests, including persistent ticket-wide retry counts, safe closure after exhaustion, legacy optional defaults and unchanged policy-hash cohorts; independent review. |
| External-review component retained in one core | Split model/publisher code, workflow templates and 40 offline tests. Explicit workflow opt-in; component qualification false. Root required-engine policy and gate unchanged. |
| Useful concise documentation | 9,462 source Markdown words, plus 1,029 generated manifest words; all per-file caps pass. Five native prompts contain 301–326 words each. Migration guides replace historical pointer stubs. Skill prose: 711 words. No global 6,000-word claim. |
| Native decision evidence | [Adjudication](NATIVE-ADJUDICATION.md): one independent six-case simulation; unchanged responses score 6/6 under a corrected rubric. Original 2/6 report retained. Nineteen mechanical benchmark tests include unsafe-extra and missing-required rejection. No comparative model-quality claim. |
| Recoverable user-skill upgrade | [Portable acceptance](skill-package.json): isolated installed 1.7 upgrade, complete byte-identical backup, preserved local settings, idempotence and verified discovery. Portable suite: 88 tests, one Windows symlink-privilege skip. |
| Complete source and installed acceptance | Fresh source: 429 tests; installed project: 371 tests. Each passes with two Windows symlink-privilege skips and one unavailable-OpenSSL skip. Reports preserve exact skipped cases. |
| Reproducible artifacts and safe Git bytes | All five source/skill/distribution digests reproduce independently. Archive report records full end-to-end installation, configuration preservation, regeneration and Git checkout/corruption-negative checks. |
| Public core and private reporting | Apache-2.0, Jonathan Kinlay, one maintained lineage with preserved Git history and versioned downloads. SECURITY.md contains the publisher-designated private contact. Publisher signatures remain unavailable. |

Three scoped independent passes reviewed routing, the external adapter and benchmark/hygiene behavior; a separate final package/privacy audit checked exact inventories and absence of private host settings. Fresh extraction caught an obsolete documentation link, corrected before the final complete acceptance. No unexplained byte mismatch was rehashed away.

`git diff --check` reports trailing-blank-line advisories in the two byte-pinned benchmark JSON fixtures and the preserved original rubric. These valid JSON bytes are retained exactly as evaluated; the advisories are not test failures or a claim of a clean whitespace check. Distribution digest sidecars use canonical LF in Git; archive bytes and digest values are unchanged.

Native independent reviews do not satisfy the required external-engine review. The accepted upstream policy and gate remain unchanged and unqualified. This candidate stays draft pending current-head external review, live qualification and human merge acceptance. No merge, bypass, signed tag, live model deployment, Jira write or scheduler enrollment is represented by these results.

Reproduce with `core/scripts/validate_archive.py --help` and the hash-locked dependencies. Keep work/reports outside the source. Portable tests run with `python -B -m unittest discover -s .agents/skills/awf/tests`. Consult the core benchmark guide for decision-smoke reproduction and its limits.
