### Supported starting installations

Support is based on an exact registered receipt identity and verified managed bytes—not just the version string. Below-floor, unknown, unregistered, inconsistent, or locally modified managed installations reject. Source: `7fcaeece:.agentic/lib/agentic/upgrade.py:155`.

| Starting version | What the inspected code supports |
|---|---|
| 1.8.3 | Registered historical starting point; adjacent migration proceeds to 1.8.9. Sources: `9f44c2a1:.agentic/upgrade/known-versions.json:7`; `7fcaeece:.agentic/lib/agentic/upgrade.py:618`. |
| 1.8.9 | Registered starting point; next step is 1.9.1. Sources: `9f44c2a1:.agentic/upgrade/known-versions.json:31`; `7fcaeece:.agentic/lib/agentic/upgrade.py:622`. |
| 1.9.1 | Registered starting point, including only registered receipt variants; next step is 1.9.2. Sources: `9f44c2a1:.agentic/upgrade/known-versions.json:57`; `7fcaeece:.agentic/lib/agentic/upgrade.py:626`. |
| 1.9.2 | Registered starting point; next step is 1.9.3. Sources: `9f44c2a1:.agentic/upgrade/known-versions.json:96`; `7fcaeece:.agentic/lib/agentic/upgrade.py:630`. |
| 1.9.3 | Registered starting point; the 1.9.3→1.9.4 step is explicitly version-only. Sources: `9f44c2a1:.agentic/upgrade/known-versions.json:122`; `7fcaeece:.agentic/lib/agentic/upgrade.py:634`. |
| 1.9.4 | **[Unmerged d2-006d branch]** Describes pending registration of a specific byte-exact 1.9.4 predecessor. Enable it only in an integrated candidate whose root/runtime identities and known-versions target consistently identify 1.9.5; advancing VERSION alone is insufficient. Published 1.9.4→1.9.5 acceptance is **UNVERIFIED**. Sources: `9f44c2a1:.agentic/upgrade/known-versions.json:148`; `9f44c2a1:.agentic/tests/test_upgrade_195_path.py:72`. |

The table’s floor is 1.8.0, but that does **not** establish support for every release at or above 1.8.0. Identification still requires a registered entry and matching receipt identity. Sources: `9f44c2a1:.agentic/upgrade/known-versions.json:3`; `7fcaeece:.agentic/lib/agentic/upgrade.py:167`.

The d2-006d test constructs a temporary source with VERSION advanced to 1.9.5. That is test-fixture evidence, not a published 1.9.5 distribution. Source: `9f44c2a1:.agentic/tests/test_upgrade_195_path.py:141`.

