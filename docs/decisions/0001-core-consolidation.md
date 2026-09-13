# Core consolidation

Decision: Jonathan Kinlay authorized one Apache-2.0 core, reusing this public repository and preserving history. The 0.1.1 scaffold at commit `6974271869257f8c33d796d3c85c529277c5691d` is retired from the candidate tree; its commits remain reachable. AWF 1.7 is the maintained distribution. Private workspace material is excluded.

The former scaffold's provider-neutral architecture aspirations and Copilot/Claude App-based external-review adapter are not represented as implemented replacement features. Core currently provides an offline reference evaluator and an optional Codex/GitHub.com host adapter. Existing installed projects are not migrated or stripped of their controls.

Core documentation is reduced by over 90% against the verified 1.6 source, excluding generated manifest inventories on both sides. Compatibility pointers replace repeated manuals. Three independent reviewers total are configured, one per workstream; this is not three PR approvals or proof of concurrent launches.

Publication is proposed through a draft PR. The upstream review policy and current-head `awf/review` gate remain byte-identical to the accepted base because these controls are still required. Other scaffold/adapter files are retired. Policy qualification remains false; residual optional-engine settings do not provide the removed Claude adapter. A human must resolve upstream review qualification before merge; there is no automatic bypass or direct-to-main publication.
