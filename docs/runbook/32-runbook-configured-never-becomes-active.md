### CONFIGURED never becomes ACTIVE

Check the returned blockers in this order:

1. Independent release trust and matching receipt.
2. Configuration and operating acceptance.
3. Correct repository/default-branch identity.
4. Actual receipt-changing PR and verified merge.
5. Current default checkout at the observed tip.
6. Matching remote, local HEAD, index, and governed working bytes.
7. Final unchanged-state rechecks.

Source: `7fcaeece:.agentic/lib/agentic/providers/github_status.py:551`.

Missing provider access, an unmerged PR, an unrelated PR, a different default tip, or changed governance bytes keeps ACTIVE unestablished. Sources: `7fcaeece:.agentic/tests/test_adoption_status.py:963`; `7fcaeece:.agentic/tests/test_adoption_status.py:992`; `7fcaeece:.agentic/tests/test_adoption_status.py:1006`; `7fcaeece:.agentic/tests/test_adoption_status.py:1025`.

