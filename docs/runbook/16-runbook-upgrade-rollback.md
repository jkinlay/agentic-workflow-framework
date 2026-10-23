### Upgrade rollback

Before durable commit, authenticated recovery can restore the exact old managed/runtime state. After durable commit, recovery completes the committed endpoint; it is not a downgrade command. Sources: `7fcaeece:.agentic/lib/agentic/installer.py:1544`; `7fcaeece:.agentic/lib/agentic/installer.py:1494`.

For rollback after a completed upgrade or accepted merge, use an owner-reviewed restoration/revert plan based on preserved backups and accepted history. **Exact commands, complete restore procedure, and subsequent ACTIVE acceptance are UNVERIFIED.** Do not use `--recover` as an assumed undo operation.

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

## 7. Rollback — install/adoption

