# Upgrade from 1.9.5 to 1.9.6

Use the separately verified release and its manifest pin. Run a non-writing upgrade plan first. Stop active dispatch and establish exclusive ownership before applying the transactional upgrade; retain backups and recovery records until verification succeeds.

The upgrade recognizes the published 1.9.5 receipt and changes the expected workflow version. Existing project routing, operating choices, reviewer allocation, pins, budgets and accounting remain. Existing projects do not automatically adopt the new twelve-stream or model defaults: request those operating changes separately within reviewed governance ceilings.

Fresh projects default to twelve logical workers. Controller, critic, specialist and ordinary worker defaults are gpt-6.1-sol/high; qualifying simple work uses gpt-6-luna/high. Host capacity still limits actual concurrent dispatch.

Unknown state remains present and receives a read-only archive copy for inspection under the selected target release. All existing archive subtrees are retained without recursively archiving them. A conflicting archive destination refuses the upgrade before writes. Never reset accounting or edit managed receipts to force acceptance.

Verify installed integrity and accepted configuration through the installed isolated interpreter. Human-reviewed adoption acceptance and independent release trust are required for ACTIVE. ACTIVE alone does not enable Jira or start a controller. Automatic activation reporting remains deferred.

Follow the [release qualification checklist](.agentic/docs/38-RELEASE-196-QUALIFICATION.md)
for exact candidate bindings, external preservation reports, recovery and the
Windows Python 3.12 installed cohort. The new preservation runner only snapshots,
compares and makes a non-writing plan; it never applies an upgrade. Its synthetic
owner customization evidence is not CMC evidence. An actual adopter baseline and
disposable-copy proof require separate authorized access.

If installation is interrupted, keep the transaction marker, journal and recovery
objects intact. Invoke the verified release's `scripts/bootstrap_project.py
--dest DISPOSABLE_PROJECT --recover` in a fresh process, retain its actual result,
then repeat recovery to prove a stable endpoint. A missing/tampered recovery
object or unknown outcome requires investigation; do not remove diagnostics,
rehash corruption, edit receipts or reinstall over the installation. Re-run both
canonical installed checks after recovery. Never infer CONFIGURED/ACTIVE from a
rollback result or a successful library-only check.

The accounting/budget compatibility migration remains dependent on the approved
AWF-242 implementation and final integrated qualification. Preserve historical
rows/reservations and explicit settings, keep new opt-in controls OFF, and report
unsupported settings to the owner rather than silently replacing them. This
document does not assert that all seven deliverables have passed release review
or authorize any live upgrade, activation or adapter enablement.
