# Adopt the 1.8.5 patch

This patch separates ordinary checks and project adoption from release qualification and live enablement. Preserve accepted project configuration, instructions, Jira mappings, partial work, owners, model choices and canonical external state. No routing reset, new routine approval or live activation follows from this update.

## Project adoption

Follow the [combined new/existing-project guide](.agentic/docs/20-NEW-PROJECT-SETUP.md). Verify independent archive/manifest pins and prepare an isolated adoption branch/worktree. Explain in the first message that governance-file changes are being prepared as a draft PR for the owner to merge. Missing default-branch rules do not block local installation, mapping or PR preparation. Record `repository_rules: MISSING` after a successful observation of insufficient rules; absent access or unverifiable evidence is `UNOBSERVED`, never guessed absence.

```text
python -B scripts/bootstrap_project.py --dest TARGET --expected-manifest-sha256 MANIFEST_PIN --codeowner '@handle' --dry-run
python -B scripts/bootstrap_project.py --dest TARGET --expected-manifest-sha256 MANIFEST_PIN --codeowner '@handle' --on-conflict backup
```

Cross-version adoption uses reviewed backed-up `install`; `--mode upgrade` is same-version maintenance only. Set the target's expected version to `1.8.5` while preserving real settings and limits. Move product-specific AGENTS instructions into reviewed project-owned `PROJECT_INSTRUCTIONS.md`; keep managed AGENTS byte-exact. Preserve recovery journals and backups.

New `.github/CODEOWNERS` defaults to `@maintainer`; `--codeowner` selects another handle. It remains project-owned and editable, and an existing file is preserved. Reconcile the old example through the reviewed project change; do not delete project ownership history. CODEOWNERS ownership does not require human approval by default. A solo maintainer must not require their own code-owner approval; a multi-maintainer policy needs eligible non-author reviewers.

The shipped ruleset follows `~DEFAULT_BRANCH`, not a branch hard-coded as `main`. Bootstrap never applies server rules. Offer separate owner application, respecting existing rules, permissions and plan support. Observe the real repository/default branch with the new rules helper; pass a pinned observation into bootstrap if available. APPLIED denotes an adequate baseline with visible no-bypass policy, not a qualified engine or a required `awf/review` check. Missing/stale/incomplete observations remain explicit warnings.

Live external review, scheduled amendments and agent pushes to repositories holding secrets still need observed rules plus existing mode qualification. Where that push restriction applies, prepare the local branch/body for owner publication or observe rules first. Otherwise continue through authorized draft-PR tools. Retain actual publication prerequisites and human merge acceptance; verify the accepted checkout before calling adoption active. A skill replacement does not migrate every project.

## Checks and release automation

The developer command now runs component checks without independent-review arguments:

```text
python -B scripts/self_test.py --report ABS_NEW_COMPONENT_REPORT.json
```

Its report has `release_qualified: false` and source review `NOT_PROVIDED`; `--checks-only` remains a compatibility spelling. Component success never substitutes for current independent review. Installed default checks report source review `NOT_APPLICABLE`; `--release` refuses to qualify an installed tree as release source. Existing source release pipelines should select `--release` explicitly; supplying `--reviews` also activates the review requirement:

```text
python -B scripts/self_test.py --release --expected-manifest-sha256 MANIFEST_PIN --reviews ABS_REVIEWS.json --expected-reviews-sha256 REVIEWS_PIN --report ABS_NEW_SOURCE_REPORT.json
python -B scripts/validate_archive.py --archive ABS_RELEASE.zip --expected-zip-sha256 ZIP_PIN --reviews ABS_REVIEWS.json --expected-reviews-sha256 REVIEWS_PIN --report ABS_NEW_ACCEPTANCE.json --workdir ABS_EXTERNAL_WORK
python -B scripts/publish_catalog.py --source ABS_SOURCE --archive ABS_RELEASE.zip --validation-report ABS_NEW_ACCEPTANCE.json --catalog ABS_EXTERNAL_CATALOG.json --reviews ABS_REVIEWS.json --expected-reviews-sha256 REVIEWS_PIN
```

Use final current-release review records for `1.8.5`; do not relabel old approvals. The optional paired scope flags remain `--review-required-paths ABS_SCOPE.json --expected-review-required-paths-sha256 SCOPE_PIN`, using scope release `1.8.5`. Apply the same scope to source acceptance, archive acceptance and catalog publication. Keep evidence/reports outside source, choose new output paths, and preserve failures. Missing pins, stale reviews or component-only results cannot publish a qualified release. See the [previous migration](MIGRATION-v1.8.3-to-v1.8.4.md) for the unchanged review-record contract.

Historical **10/12 strict FAIL**, **5/12 FAIL** and **1/3 FAIL** remain unchanged. Their differing cases/contracts do not establish causal improvement. No new model evaluation or live repository qualification is performed by this migration.
