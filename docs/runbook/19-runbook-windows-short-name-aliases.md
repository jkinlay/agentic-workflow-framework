### Windows temporary short-name aliases versus resolved paths

**[PR #205]** Git isolation scopes `safe.directory` to the resolved repository path. Its fixture resolves the temporary root before constructing the repository, and the Windows alias regression demonstrates refusal of the lexical alias while the resolved checkout is accepted. Sources: `c407bcf7:.agentic/lib/agentic/git_isolation.py:63`; `c407bcf7:.agentic/tests/test_git_isolation_safe_directory.py:28`; `c407bcf7:.agentic/tests/test_git_isolation_safe_directory.py:404`.

Use the reported canonical/resolved checkout consistently. Do not replace exact-path trust with wildcard trust; the implementation rejects wildcard-shaped trust paths. Sources: `c407bcf7:.agentic/lib/agentic/git_isolation.py:64`; `c407bcf7:.agentic/tests/test_git_isolation_safe_directory.py:361`.

**8.3-specific creation, detection, or repair commands are UNVERIFIED.** The inspected Windows regression uses a directory junction, which does not itself prove every 8.3 alias case. Native published 1.9.5 alias acceptance remains UNVERIFIED.

