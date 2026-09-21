# Adopt the 1.8.4 patch

Verify independently supplied archive/manifest pins, then prepare a reviewed adoption PR using the [adoption guide](.agentic/docs/20-NEW-PROJECT-SETUP.md). Preserve and back up accepted configuration, project instructions, Jira mappings, ownership, current work and canonical external state. Cross-version adoption uses a reviewed backed-up install; same-version `--mode upgrade` does not migrate between releases. A portable skill update changes discovery, not every project's installed baseline.

No routing database reset, fresh routine authorization or external-review activation is required by this patch. Preserve consumed approval identities, reservations, usage, reduction history and existing policy ceilings. Do not reopen old approvals to recover capacity. External-review settings stay disabled/unqualified until the actual environment satisfies its existing [qualification procedure](.agentic/docs/28-EXTERNAL-REVIEW.md).

## Maintainer release acceptance

Freeze the intended source and obtain current independent review of its final bytes. Keep the review JSON outside the release tree and pin it independently. Each record requires `review_id`, `status: PASS`, `scope_kind: current_release`, `release: 1.8.4` and a nonempty `reviewed_file_sha256` mapping of release-relative paths to lowercase SHA-256 values. Supply one record or a list. Retain superseded records as history, never as stale current claims with overrides.

```text
python -B scripts/release_review.py --source ABS_SOURCE --expected-manifest-sha256 MANIFEST_PIN --reviews ABS_REVIEWS.json --expected-reviews-sha256 REVIEWS_PIN
```

By default every manifest content file needs coverage. For an approved narrower scope, pass both `--review-required-paths ABS_SCOPE.json` and `--expected-review-required-paths-sha256 SCOPE_PIN`. That external document is exactly `{"format":"awf-review-coverage-1","release":"1.8.4","paths":["README.md"]}`, replacing the example list with the complete intended scope. It must contain unique existing content paths. Its completeness against project history remains an operator assertion, not something hashes prove. Include all required changes and explain omissions in the actual review plan.

```text
python -B .agentic/scripts/self_test.py --expected-manifest-sha256 MANIFEST_PIN --reviews ABS_REVIEWS.json --expected-reviews-sha256 REVIEWS_PIN --report ABS_SOURCE_TESTS.json
python -B scripts/validate_archive.py --archive ABS_RELEASE.zip --expected-zip-sha256 ZIP_PIN --reviews ABS_REVIEWS.json --expected-reviews-sha256 REVIEWS_PIN --report ABS_NEW_ACCEPTANCE.json --workdir ABS_EXTERNAL_WORK
```

Catalog publication also requires the review arguments and independently rechecks their agreement with the final archive and source-test report:

```text
python -B scripts/publish_catalog.py --source ABS_SOURCE --archive ABS_RELEASE.zip --validation-report ABS_NEW_ACCEPTANCE.json --catalog ABS_EXTERNAL_CATALOG.json --reviews ABS_REVIEWS.json --expected-reviews-sha256 REVIEWS_PIN
```

Add the same optional scope flags to acceptance, source tests and catalog publication when applicable. Existing automation must pass these inputs; a component-only report cannot publish a qualified catalog entry. Archive acceptance invokes the shipped source review gate, checks fresh source/installed components and rechecks current evidence before its final completed report. Existing reports remain evidence; choose a new output path. Self-test timeout configuration remains bounded by the existing CLI. File changes after review require a new review of changed bytes; do not merely overwrite digests to conceal drift.

For source development without release evidence, use `self_test.py --checks-only`; this reports component results with `release_qualified: false` and review `NOT_PROVIDED`. It cannot substitute for release acceptance. Installed self-tests report source review `NOT_APPLICABLE` because acceptance establishes it separately. Passing byte consistency does not authenticate reviewer identity or qualify a live deployment.

## Decision contract v2

New [native smoke/evaluation](.agentic/benchmarks/native/README.md) packets include pinned operational definitions and a full response schema. Each case has one affected target and a distinct unaffected scope; split cases that need multiple targets of one category. Update observation writers to use the packet's v2 schema and definitions. `continue_unaffected_work`, `reject_stale_review` and `preserve_escalation_ceiling` distinguish previously overlapping choices. Local validation rejects contradictory same-target decisions independently of the rubric; retain invalid raw answers without editing or automatic model retry.

Use `prepare --legacy-v1` or `grade --legacy-v1` only for exact historical reproduction. Do not relabel v1 packets/responses as v2 or rewrite their decisions. Preserve **5/12 FAIL**, the zero-scoring third batch and the earlier **1/3 FAIL**. Qualitative analysis remains retrospectively assessed; a prohibited-code count is not an executed unsafe-action rate. Any follow-up measurement needs fresh cases and definitions/rubric frozen before new responses; unit tests and a conservative CLI schema projection are not new model observations.
