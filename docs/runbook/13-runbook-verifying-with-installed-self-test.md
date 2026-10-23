## 4. Verifying with the installed self-test

Run this at **step 8 of each OS sequence**, after installation/configuration verification and candidate preparation. For an existing repository, record the reviewed adoption or upgrade candidate before self-test so committed governed bytes match the checkout. For the supported commitless fresh-install case, retain the reasons for committed-HEAD-dependent skips.

The installed entry point is the managed `.agentic/scripts/self_test.py`, using the target repository’s canonical runtime. The source-only wrapper is not required in an installed project. Sources: `7fcaeece:scripts/tests/test_installed_self_test.py:104`; `7fcaeece:scripts/tests/test_installed_self_test.py:133`.

Acceptance requires:

- Exit **0** and final `status: PASS`.
- `tests.failures: 0`, `tests.errors: 0`, and successful test result.
- Review and retain every skipped test and its reason.
- Preserve a new report outside the installed root; existing reports are not overwritten.

Sources: `7fcaeece:.agentic/scripts/self_test.py:243`; `7fcaeece:.agentic/scripts/self_test.py:407`; `7fcaeece:.agentic/scripts/self_test.py:440`; `7fcaeece:scripts/tests/test_installed_self_test.py:138`.

The runner performs discovery, syntax, documentation, release-hygiene applicability, and test-suite phases. It emits start/phase/test events and 15-second heartbeats to stderr. Sources: `7fcaeece:.agentic/scripts/self_test.py:26`; `7fcaeece:.agentic/scripts/self_test.py:61`.

For an installed component check, source release review is `NOT_APPLICABLE`; `release_qualified` remains false. A component PASS does not qualify a release or establish ACTIVE. Sources: `7fcaeece:.agentic/scripts/self_test.py:295`; `7fcaeece:.agentic/scripts/self_test.py:421`.

Caught interruption/timeout produces `INTERRUPTED`, retains phase/test information, and exits 130. Other failures exit nonzero. An externally terminated run without a completed report is **UNVERIFIED**, not PASS. Sources: `7fcaeece:.agentic/scripts/self_test.py:422`; `7fcaeece:.agentic/scripts/self_test.py:451`.

**[POSTSUITE-001 branch]** Any additional interruption-recovery behavior is **UNVERIFIED** because no successor revision was supplied.

