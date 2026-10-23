### Post-upgrade verification and reactivation

1. Repeat the OS-specific installed doctor, verification, configuration, and operating commands in §3 step 7. Review the upgrade diff and scoped LF attributes, record the upgrade candidate in a local commit, and confirm that its governed bytes match the checkout before proceeding to self-test.
2. Run the installed self-test with a **new** report path, then execute the project’s actual tests separately.
3. Review the upgrade diff and obtain acceptance through the receipt-changing upgrade/adoption PR.
4. On the observed default branch at its current tip, run the OS-specific `status --require-active` command with the **new receipt-changing PR** and matching release pin.

The observer discovers the latest receipt-changing commit, requires the selected PR to introduce it, verifies its merged receipt, and checks accepted current governance bytes. An older adoption PR cannot automatically accept the new receipt. Sources: `7fcaeece:.agentic/lib/agentic/providers/github_status.py:617`; `7fcaeece:.agentic/lib/agentic/providers/github_status.py:629`; `7fcaeece:.agentic/tests/test_adoption_status.py:1006`.

The canonical runtime is ignored and absent from fresh clones. A source regression provisions only that clone’s runtime through `ensure_installed_runtime`; a documented standalone end-user runtime-rehydration CLI is **UNVERIFIED**. Do not rerun bootstrap merely to recreate a runtime without considering that it also creates a new receipt requiring acceptance. Sources: `7fcaeece:.agentic/templates/operating.gitignore:2`; `7fcaeece:scripts/validate_git_checkout.py:117`; `7fcaeece:scripts/validate_git_checkout.py:402`; `7fcaeece:.agentic/lib/agentic/installer.py:1832`.

