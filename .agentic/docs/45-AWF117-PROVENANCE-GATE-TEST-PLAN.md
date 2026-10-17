# AWF-117 provenance merge-gate test plan

Design-only plan, based on the AWF-117 Jira description and repository commit
`de76c49`. Existing `provenance.check_commit_range` unit coverage is not merge-gate
coverage: on this baseline `gates.evaluate` never calls it. Implementation starts
only after AWF-110/#149 and AWF-97 merge and the resulting default-branch head is
re-read.

## Acceptance tests

All Git fixtures use temporary directories, POSIX repository paths and immutable
full OIDs. The main gate target is
`.agentic/tests/test_awf117_provenance_gate.py` unless stated otherwise.

| Criterion (verbatim) | Named case, fixture and action | Expected fail on main; pass condition |
| --- | --- | --- |
| 1. "Tests first (fail on main, pass on head)." | `test_ac1_red_suite_precedes_gate_implementation`: record the first execution of the AC2-AC5 cases against `de76c49`, then execute the unchanged cases on the implementation head. | Main does not wire commit provenance into `evaluate`; the run is red. The retained red/green commands identify their exact commits, and the same tests pass on head. This is delivery evidence, not a history-dependent shipped test. |
| 2. "The merge gate refuses a PR containing an untagged code commit (no Writer trailer)." | `test_ac2_merge_gate_refuses_untagged_code_commit`: a valid generated gate bundle plus a temporary `merge_base..head` containing `src/feature.py` without a trailer; call `evaluate` with the repository and registered provenance inputs. | Main returns a passing provenance gate because it never calls `check_commit_range`. Head returns `NOT_READY`, provenance `FAIL`, and evidence bound to the offending commit/refusal. |
| 3. "Refuses a script-tagged (Writer: script/...) commit that is not mechanical." | `test_ac3_merge_gate_refuses_nonmechanical_script_commit`: commit `src/feature.py` with `Writer: script/manifest-regen`; register the reviewed manifest reproducer and evaluate. | Main ignores the range. Head records the helper's non-reproducibly-mechanical refusal and cannot become ready. |
| 4. "Refuses a verdict without a critic run id." | `test_ac4_merge_gate_refuses_verdict_without_critic_run_carrier`: otherwise valid review-round/provider bytes omit the sole explicit `Critic run:` carrier while retaining caller-supplied IDs; validate and evaluate. | Main can rely on copied `run_id` fields. Head schema or semantic validation refuses before a ready verdict; the carrier's UUID must equal the registered critic run and reviewed head binding. |
| 5. "A labelled fallback PR passes only with a recorded owner override." | `test_ac5_fallback_label_alone_refuses_and_sealed_owner_override_passes`: use one untagged commit and exact `fallback: bot-written` label; evaluate subcases with no override, malformed/untrusted/stale override, and a fresh digest-linked owner record covering exactly that commit. | Main does not gate the range. Head refuses every label-only/invalid case and passes provenance only for the provider-observed, trusted, PR-bound override; unrelated gates remain independently required. |
| 6. "Schemas, templates and docs 30/41 updated and generator --check clean; no test or scan weakened." | `test_ac6_generated_provenance_contracts_are_current` in `scripts/tests/test_awf117_generated_provenance.py`: run contract/example generation in a temporary tree, compare bytes, and assert docs 30/41 describe the enforced gate. Then run existing provenance, review-policy, publication-scan and release-hygiene focused suites. | Main lacks the new gate contract/docs. Head has byte parity, both generator `--check` commands clean, and all existing refusal/scan tests unchanged and green. |

## Changed-file map and generation order

- `.agentic/lib/agentic/gates.py`: call `check_commit_range` for the candidate
  range, fail closed, and bind its result into the provenance gate evidence.
- `scripts/generate_contracts.py`: define the closed provenance policy/result
  fields used by project-config, evidence-bundle and final-gate schemas.
  `scripts/generate_examples.py` supplies matching configured/unconfigured
  examples and draft forms. Change `scripts/generate_review_loop.py` only if the
  carrier or host-config surface changes.
- Regenerated outputs: the affected files under `.agentic/schemas/`,
  `.agentic/templates/`, `.agentic/examples/`, and generated
  `.agentic/PROJECT_CONFIG.yaml`; never hand-edit them.
- `.agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md` states the final-gate rule;
  `.agentic/docs/41-WRITER-CRITIC-PROVENANCE.md` states range, carrier and fallback
  semantics. Tests are the two files named above.
- Generator slot follows the release order: contract schemas, examples/forms,
  then review-loop assets if changed; run `generate_contracts.py --check` and
  `generate_examples.py --check`, focused tests/scans, and manifest-only release
  generation last.

## Open owner questions

1. Is the canonical PR range `merge_base_sha..head_sha`, or must a freshly
   observed default-branch OID be an additional bound input?
2. Should the gate retain a new closed commit-provenance record, or recompute it
   from Git/provider bytes and retain only content-addressed evidence?
3. Does AWF-117 keep the existing exact `fallback: bot-written` label and v2
   override chain, and which actor/config field is authoritative at final gate?
4. Must the critic carrier live in every retained round receipt, the terminal
   verdict only, or both?
