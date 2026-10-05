# Repository rules at activation

AWF 1.9.3 binds the proposed default-branch ruleset to the accepted project
configuration. The configured merge method is one of `merge`, `squash`, or `rebase`.
The generated proposal permits that method, so AWF never recommends a ruleset that
would disable the project's merge policy.

The shipped `.agentic/templates/awf-main-ruleset.json` permits all three supported
methods and is therefore safe as a general baseline. The activation decision generates
a narrower proposal containing exactly the accepted `github.merge_method`. Missing,
invalid, or unaccepted configuration fails with the exact configuration path. Any applicable
pull-request rule that omits the configured method is `MISSING`, even in a partial ruleset.

## Decision flow

Before activation, collect a read-only rules observation using
`.agentic/scripts/repository_rules.py`. Pin the observation bytes independently. Then run
the installed decision helper:

```powershell
python -B .agentic/scripts/rules_activation.py `
  --observation <controller-evidence-root>/rules-before.json `
  --expected-observation-sha256 <before-sha256> `
  --now <trusted-rfc3339-time>
```

The output presents all five required parts:

1. the observed rules state: `APPLIED`, `MISSING`, or `UNOBSERVED`;
2. the publication consequence of that state;
3. a deterministic ruleset compatible with the accepted configuration and its SHA-256;
4. the exact owner/admin approval action, provider endpoint, method, and bound request body;
5. the post-action observation state.

The helper performs no provider request and grants no execution authority. The owner or
repository administrator decides whether to apply the exact digest-bound request through
an authenticated provider session. A pending or declined decision remains blocked.

If the owner reports the rules applied, collect a new read-only observation. Both observations
must bind the configured numeric repository ID, repository name, and default branch. The
post-action observation must be later than the pre-action observation, be fresh at the trusted
controller time, and show a baseline that permits the configured method:

```powershell
python -B .agentic/scripts/rules_activation.py `
  --observation <controller-evidence-root>/rules-before.json `
  --expected-observation-sha256 <before-sha256> `
  --owner-outcome APPLIED `
  --post-observation <controller-evidence-root>/rules-after.json `
  --expected-post-observation-sha256 <after-sha256> `
  --now <trusted-rfc3339-time>
```

Stale, malformed, wrong-repository, wrong-branch, unchanged-time, or still-incompatible
post-action evidence fails closed. `MISSING` remains an explicit operational blocker
until a fresh compatible `APPLIED` observation is recorded. `RULES_OBSERVED` establishes
this prerequisite only from a live read-only GitHub API observation; synthetic fixtures
cannot clear activation. Application credentials, publication capability, independent
review, CI, owner authorization, and other live qualification remain separate gates.

The deterministic output contract is
`.agentic/schemas/rules-activation-decision.schema.json`; its generated fixture is
`.agentic/templates/rules-activation-decision.yaml`. Regenerate them with:

```powershell
python -B scripts/generate_contracts.py
python -B scripts/generate_rules_activation.py
```
