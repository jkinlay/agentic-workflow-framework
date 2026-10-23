### Publication-scan failure

Use the installed OS-specific canonical runtime:

```text
<installed-canonical-python> -B -I <repo-path>/.agentic/scripts/workflow.py --root <repo-path> publication-scan --base <base-sha> --head <head-sha> --pr-body <pr-body-file> --json
```

Flags and exit handling: `7fcaeece:.agentic/lib/agentic/cli.py:245`; `7fcaeece:.agentic/lib/agentic/cli.py:283`.

**Success:** exit 0, PASS, matching exact candidate/body, and no blocking/unscanned content. **Failure:** inspect reported history/path/channel findings; whitespace checks are insufficient. Rescan after candidate/body/comment changes. Source: `7fcaeece:.agentic/docs/31-PUBLICATION-SAFETY.md:49`.

Large-file allowances require exact path/hash/reason/approver; they are not wildcard exemptions. Source: `7fcaeece:.agentic/docs/31-PUBLICATION-SAFETY.md:38`.

The publication library and its complete transitive callees were not read in full; exhaustive implementation verification is **UNVERIFIED**.

