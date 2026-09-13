---
name: awf
description: Verify and adopt AWF releases; guide project work with bounded model routing, independent review and evidence accounting. Includes AWF 1.7.
metadata:
  publisher: Jonathan Kinlay
  license: Apache-2.0
---

# AWF

Installing this skill does not migrate projects. Preserve accepted baselines.

For skill installation, read [installation](references/skill-installation.md). For adoption, read [workflow](references/workflow.md); check owner releases through [update channels](references/update-channel.md). An unavailable channel is not proof this bundle is latest.

Verify and prepare [bundled source](assets/agentic-workflow-template-v1.7.zip) against [pins](assets/release.json):

```text
python -B PATH_TO_SKILL/scripts/prepare_release.py --cache-dir ABSOLUTE_EXTERNAL_CACHE --version 1.7
```

Use Python 3.11+ and a separate cache with an existing parent. Preserve differing/incomplete caches. Use the returned catalog path for locate/inspect. Read verified AGENTS, specification and project configuration, then only the relevant runbook.

For execution, read [routing](references/model-routing.md). Native operation is host-dependent guidance, not a dispatcher. Defaults are three streams and three independent reviewers total, one per stream, sharing host capacity. Keep one writer per path, independent reviewer contexts and observed identities.

Escalation within approved limits needs no repeated prompt. Cap increases, expanded model allowlists, weaker reviews and reconciliation enablement require explicit human direction. Candidate policy cannot approve itself. Reconciliation needs authenticated operator evidence; preserve charges and history.

Report observed outcomes and next steps. Continue independent authorized work; do not invent background execution, merge authority or completion. Publisher is Jonathan Kinlay; [Apache-2.0](LICENSE) applies as stated in [NOTICE](NOTICE).
