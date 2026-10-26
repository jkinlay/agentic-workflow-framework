### Migration behavior

- The chain follows registered adjacent transitions and reports each step. Source: `7fcaeece:.agentic/lib/agentic/upgrade.py:852`.
- The 1.9.4→1.9.5 migration invokes additive review-tier defaults: critic and specialist roles, blocking findings, maximum three rounds when the setting is absent. Existing `tier3_review` values are retained. Source: `7fcaeece:.agentic/lib/agentic/upgrade.py:639`.
- YAML migration preserves retained bytes around the additive mapping and verifies the resulting parsed mapping. JSON input is reserialized. Source: `7fcaeece:.agentic/lib/agentic/upgrade.py:750`.
- Operating configuration is carried through the migration bundle; ordinary upgrade does not itself authorize a capacity increase. Sources: `7fcaeece:.agentic/lib/agentic/upgrade.py:608`; `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:36`.
- Recognized durable state is classified/migrated. Unknown or incompatible state receives a read-only archive copy while retaining the source. Source: `7fcaeece:.agentic/lib/agentic/upgrade.py:883`.
- **[Unmerged d2-006d branch]** Archive target naming follows runtime VERSION rather than a hard-coded 1.9.4 target. Source: `9f44c2a1:.agentic/lib/agentic/upgrade.py:21`. Integration must also derive the archive manifest’s `target_version` from runtime VERSION. At repository revision `dc5b3a9de095a643bc2f7a27d3cf54b9f4366c84`, both the archive root and manifest target remain hard-coded to 1.9.4; changing the archive path alone is insufficient. Base sources: `.agentic/lib/agentic/upgrade.py:21` and `.agentic/lib/agentic/upgrade.py:898`. These constants are a base observation, not successful integration; the integrated candidate must derive both targets from runtime VERSION.
- Upgrade preserves installation identity and effective project ownership; conflicting new managed paths reject rather than overwrite project-owned files. Source: `7fcaeece:.agentic/lib/agentic/installer.py:1776`.

[Runbook index](INDEX.md)
