Implement the frozen ticket contract in the existing worker checkout. Work only
within the exact allowed paths. Do not stage, commit, push, switch branches,
edit Git configuration, or invoke another agent. Governance files are protected
by default; for an AWF source checkout, the reviewed Tier 3 governed-source
allowlist is the only exception, and it is still limited to the exact allowed
paths supplied below. Do not infer permission from the contract or prompt.
Return the tested tree and every declared change. Include every ignored or
untracked path excluded from the tested tree in ignored_untracked. Run only
focused tests and report the actual commands, exit codes and limitations.
