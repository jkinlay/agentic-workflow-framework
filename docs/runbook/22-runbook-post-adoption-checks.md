## 6. Post-adoption checks

Offer `.agentic/templates/awf-main-ruleset.json` for separate owner application. Inspect existing rules first and update them rather than creating duplicates. Bootstrap never applies server rules; missing or unobserved rules remain adoption warnings.

Retain:

- Actual installation/configuration subprocess commands, exits, outputs, and source/policy/operating digests.
- Installed self-test report, stderr progress, skips, and project-test results.
- Actual state, blockers, capabilities, next action, adoption PR, accepted merge, and current default-head identities.
- Preserved configuration/instructions/ownership, reviewed LF attribute changes, and rollback locations.

Source: `7fcaeece:.agentic/templates/adoption-pr.md:5`.

Confirm that scoped LF rules preserve immutable installed bytes without changing unrelated product files. Do not renormalize unrelated files or bless corrupted managed content by rehashing it. Sources: `7fcaeece:.agentic/templates/installed.gitattributes:1`; `7fcaeece:scripts/validate_git_checkout.py:375`; `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:54`.

ACTIVE does not imply that all capabilities are AVAILABLE. Inspect `local_work`, external data, publication, PR creation, independent review, Jira read/write, and merge execution individually. Dispatch refuses required capabilities that are not explicitly AVAILABLE. Sources: `7fcaeece:.agentic/lib/agentic/activation.py:17`; `7fcaeece:.agentic/lib/agentic/activation.py:101`; `7fcaeece:.agentic/tests/test_adoption_status.py:218`.

Before relevant live enablement, run the installed configuration command with `--require-enablement`:

```text
<installed-canonical-python> -B -I <repo-path>/.agentic/scripts/workflow.py --root <repo-path> validate-config --require-enablement
```

Use the OS-specific canonical interpreter from §3. Flag and validation behavior: `7fcaeece:.agentic/lib/agentic/cli.py:139`; `7fcaeece:.agentic/lib/agentic/cli.py:443`.

Require configured CI, actual trusted owners, bound provider identity, observed rules, and the relevant adapter qualification. In addition to installed `validate-config --require-enablement`, re-observe repository rules with the actual review App ID using `--review-app-id APP_ID --require-enablement`. Retain APPLIED/MISSING/UNOBSERVED evidence and applicable host qualification; MISSING or UNOBSERVED does not establish live readiness. Empty CI/owner lists warn during adoption but do not establish live readiness. Sources: `7fcaeece:.agentic/lib/agentic/configuration.py:320`; `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:66`.

**[PR #219]** Durable planning admission changes are unmerged; do not infer deployed controller behavior from the revised guide. Source: `47c5a0b4:.agentic/docs/29-OPERATING-CONFIGURATION.md:39`.

**[AWF-54 successor branch]** Jira reconciliation now distinguishes numeric issue IDs from canonical issue keys during readback. This is downstream Jira behavior, not evidence of install/adoption readiness. Full adapter/caller verification is **UNVERIFIED**. Source: `7ddb195a:.agentic/adapters/reference_controller_adapter.py:646`.

**Separate rules observation (R-575):** Use the OS-specific installed canonical Python. This documentation template has not been executed:

```text
<installed-canonical-python> -B -I <repo-path>/.agentic/scripts/repository_rules.py --repository <owner>/<repo> --default-branch <observed-default-branch> --observe --review-app-id <actual-positive-review-app-id> --require-enablement --save-observation <new-absolute-observation-path> --report <new-absolute-report-path>
```

Resolve and quote paths for the selected OS. Observation and report paths must be distinct, new, absolute and outside the runtime tree. Preserve their digests and actual APPLIED/MISSING/UNOBSERVED results. The default-branch argument binds the observed branch; it does not override the live default. Sources at `dc5b3a9de095a643bc2f7a27d3cf54b9f4366c84`: `.agentic/scripts/repository_rules.py:2–6,27–34,51–93`; `.agentic/lib/agentic/cli.py:137–140` defines the separate configuration flag. Do not append `--review-app-id` to `workflow.py validate-config`.

Observation checks only the rules prerequisite; it neither enables nor qualifies live automation. Actual App ID, repository identity, report, observation and live results remain operator-specific and **UNVERIFIED**. Synthetic fixtures are never live authority.

**Publication and qualification checklist:** Documentation does not repair runtime defects or establish acceptance. Retain evidence for every item:

- Repair the all-controller-values-false assertion defect while preserving cadence **600**.
- Resolve both undefined report inputs, `source_external_unit` and `installed_external_unit`, and demonstrate complete report serialization.
- Accept the exact intended migration delta under AC42 and cover the required external-resource admission check.
- Align root/runtime/manifest/upgrade-target identities and verify byte-exact predecessor registration; advancing VERSION alone is insufficient.
- Reproduce generated outputs, regenerate both final manifests, retain independent pins and obtain candidate-bound review.
- Obtain required platform evidence, including clean Windows, and publication acceptance.
- Bind the final tag, integrated commit and complete distribution to that acceptance. Release and upgrade status remain **UNVERIFIED** until those gates pass.

**Acceptance sources:** [Complete publication checkpoint](06-runbook-publication-checkpoint.md); [installed self-test requirements](13-runbook-verifying-with-installed-self-test.md); [final acceptance and remaining UNVERIFIED work](40-runbook-unverified-until-publish.md).

[Runbook index](INDEX.md)
