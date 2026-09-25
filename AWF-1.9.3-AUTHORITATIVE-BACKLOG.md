# AWF 1.9.3 authoritative implementation backlog — HFT live-use findings

**Status:** authoritative framework backlog for AWF 1.9.3 implementation follow-up  
**Source:** owner-observed defects during live HFT Strategies use  
**Repository scope:** `jkinlay/agentic-workflow-framework` only  
**HFT trust boundary:** the project owner's fixed AWF 1.9.1 trust decision for HFT Strategies is unchanged. These are AWF 1.9.3 framework defects and do not upgrade, mutate, or reinterpret the HFT project's trusted version.  
**External systems:** this backlog does not authorize changes to the HFT Strategies repository or Jira.

This file is the durable, version-controlled backlog for the twelve HFT-derived AWF 1.9.3 implementation findings below. The `HFT193-IC-*` findings are implementation-critic findings from publication/activation behavior. The `HFT193-MD-*` findings are mapped-drive and external-resource admission findings. They are distinct series and all are binding acceptance backlog items for AWF 1.9.3 follow-up implementation.

## Global invariants

1. Fail closed when the framework cannot establish publication safety, publication capability, worker resource capability, rule compatibility, or read-only external-resource bounds from observed evidence.
2. Controller-side visibility, owner approval, or a successful probe in a different execution context never implies worker capability.
3. Human authority is preserved. Repository/ruleset installation remains an explicit owner action; AWF may prepare and present compatible rules but must not install them on the owner's behalf.
4. Sensitive internal locators and values must not be written to tracked Git content, PR text, commit messages, or Jira.
5. Capability/status claims must name the observation context, evidence, and limitation. Unknown is not converted into PASS or FAIL.
6. HFT Strategies remains on the owner's fixed AWF 1.9.1 trust decision. No finding in this backlog changes that project-level trust decision.

---

## HFT193-IC series — implementation critic findings

### HFT193-IC-001 — Publication safety covers reachable history and the complete base..head patch

**Defect.** Publication-safety checks that inspect only the final working tree can miss sensitive material in earlier commits or in intermediate patch content.

**Implementation requirement.**
- Publication-safety evaluation must inspect:
  - every commit reachable from the candidate head that would become newly reachable by publication, relative to the approved publication base;
  - the complete patch for `base..head`, including additions and deletions across every commit;
  - candidate commit messages and other publication metadata covered by the publication path.
- The check must bind its evidence to exact `base` and `head` SHAs and record the inspected commit set.
- A final-tree scan may be retained as defense in depth but is not sufficient evidence by itself.

**Fail-closed behavior.**
- If the reachable commit set or complete patch cannot be enumerated and inspected, publication is refused with an explicit `PUBLICATION_SAFETY_UNPROVEN` (or equivalent) outcome.
- Any sensitive value found anywhere in the publication history or patch blocks branch publication even when absent from the final tree.

**Regression / acceptance tests.**
- **IC-AT-001:** secret introduced in commit 1 and deleted in commit 2; final tree is clean; publication is refused.
- **IC-AT-002:** sensitive value appears only in an intermediate commit message or patch hunk; publication is refused.
- **IC-AT-003:** clean multi-commit branch with complete reachable-history and `base..head` evidence passes and records the exact inspected commit SHAs.
- **IC-AT-004:** inability to enumerate a reachable commit or patch produces UNKNOWN/refusal, never PASS.

### HFT193-IC-002 — A redaction commit cannot make contaminated history publication-safe

**Defect.** A later redaction can make the tip appear clean while the sensitive value remains retrievable from an earlier reachable commit.

**Implementation requirement.**
- Publication safety must be monotone over the candidate publication history: if a sensitive value exists in any newly reachable commit, a later deletion/redaction does not clear the finding.
- The only acceptable remediation is a history change that removes the sensitive value from every commit that would become reachable, followed by a fresh scan bound to the rewritten `base..head`.
- Evidence must distinguish "tip clean" from "history clean".

**Fail-closed behavior.**
- A branch with a sensitive value in any earlier reachable commit remains blocked even when the tip no longer contains it.
- A redaction-only follow-up commit must not convert the branch's publication capability to PASS.

**Regression / acceptance tests.**
- **IC-AT-005:** add secret → commit → redact secret → commit; tip scan clean, history scan dirty; publication remains refused.
- **IC-AT-006:** rewrite the contaminated commit so no reachable commit contains the secret; fresh scan passes only after new SHAs are observed.
- **IC-AT-007:** cached evidence from pre-rewrite history is invalidated by head/base change.

### HFT193-IC-003 — Stream admission must prove actual publication capability before dispatch

**Defect.** Streams can be admitted and complete work locally even though the agent cannot publish the resulting branch, stranding completed work.

**Implementation requirement.**
- Before dispatching a stream whose lifecycle requires agent branch publication, preflight/admission must perform a bounded publication-capability probe in the same provider/account/repository context that the stream will use.
- The probe must establish all required capabilities for the intended publication route, including authentication, branch/ref creation/update permission, and any applicable repository-rule/ruleset compatibility.
- Admission evidence must be current and bound to repository, target/base context, publication identity, and publication route.
- Operating stream count may not exceed the number of streams for which required publication capability has been established when publication is mandatory.

**Fail-closed behavior.**
- If publication capability is unavailable, denied, stale, or unproven, the affected stream is not dispatched.
- The controller reports the limiting capability and may offer a non-publishing/local-only mode only if that mode is explicitly supported and accepted; it must not silently dispatch work that requires later publication.

**Regression / acceptance tests.**
- **IC-AT-008:** controller configured for three streams but agent branch publication is unavailable; zero publication-dependent streams are dispatched and no work is stranded.
- **IC-AT-009:** capability exists for only one publication identity/slot; at most one dependent stream is admitted.
- **IC-AT-010:** capability probe passes, then credentials/rules change before dispatch; stale/failed re-observation prevents dispatch.
- **IC-AT-011:** successful publication-capability evidence records repository, identity, route, time, and observed limitations.

### HFT193-IC-004 — ACTIVE status must expose an evidence-backed capability matrix

**Defect.** A generic ACTIVE status can conceal missing runtime capabilities such as agent branch publication.

**Implementation requirement.**
- ACTIVE status must include a capability matrix, at minimum covering:
  - local repository read/write;
  - worker execution;
  - critic/specialist routing as applicable;
  - required CI observation/trigger capability;
  - agent branch publication;
  - repository-rule/ruleset compatibility;
  - owner-authorisation path;
  - configured external-resource capabilities when required for the admitted work.
- Each row records `PASS | WARN | FAIL | UNKNOWN | N_A`, observation context, evidence reference/time, and limitation/reason.
- ACTIVE may coexist with nonessential WARN/N_A rows, but any capability mandatory for the configured/admitted operating mode must be PASS.

**Fail-closed behavior.**
- AWF must not report operational ACTIVE for a publication-dependent mode when branch publication is FAIL/UNKNOWN/unobserved.
- Missing mandatory evidence yields a capability-specific degraded/not-active status, not an unqualified ACTIVE claim.

**Regression / acceptance tests.**
- **IC-AT-012:** publication unavailable; status output explicitly shows publication FAIL/UNKNOWN with limitation and does not claim fully operational ACTIVE for publication-dependent work.
- **IC-AT-013:** all mandatory capabilities pass; ACTIVE includes the matrix and evidence timestamps.
- **IC-AT-014:** optional capability unavailable; ACTIVE may remain valid only when that capability is not required by configured/admitted work.

### HFT193-IC-005 — Repository rules derive merge methods from adopter configuration

**Defect.** Hard-coded squash/rebase assumptions can conflict with an adopter project's configured merge method.

**Implementation requirement.**
- Rule/ruleset generation and validation must derive allowed/required merge methods from the adopter project's reviewed configuration and observed repository settings.
- No generated rule may hard-code squash, rebase, or merge-commit behavior independently of that configuration.
- Preflight must compare configured merge method, generated compatible ruleset, and observed repository rule/settings behavior and report discrepancies before activation/publication.

**Fail-closed behavior.**
- A merge-method mismatch blocks the affected activation/publication path.
- AWF must not install, recommend as compatible, or rely on a ruleset whose merge-method constraints contradict the adopter configuration.

**Regression / acceptance tests.**
- **IC-AT-015:** project configured for merge commits; generated ruleset permits/requires the compatible merge path and does not force squash/rebase.
- **IC-AT-016:** project configured for squash; generated/validated rules reflect squash.
- **IC-AT-017:** observed repository rule conflicts with configured method; preflight fails closed with the exact mismatch.
- **IC-AT-018:** changing configured merge method invalidates prior compatibility evidence and requires fresh observation.

### HFT193-IC-006 — Activation offers owner-installable compatible rules and immediately re-observes them

**Defect.** Missing/incompatible repository rules may be discovered only at publication time.

**Implementation requirement.**
- During activation, when required repository rules are absent or incompatible, AWF must prepare and present the exact compatible ruleset (or precise owner action) for explicit owner installation.
- AWF must not install or mutate repository rules itself.
- After the owner reports/executes installation, activation immediately re-observes the live repository rules and validates them against project configuration before proceeding.
- Activation records the owner action boundary and the post-install observed evidence.

**Fail-closed behavior.**
- Until the owner installs the required rules and AWF re-observes a compatible live state, activation/publication-dependent admission remains blocked or degraded.
- A claimed owner installation without successful re-observation does not satisfy the gate.

**Regression / acceptance tests.**
- **IC-AT-019:** required rules missing; activation presents the compatible owner-installable ruleset and refuses publication-dependent activation.
- **IC-AT-020:** owner installs correct rules; immediate re-observation passes and capability state updates.
- **IC-AT-021:** owner installs incompatible rules; re-observation fails and activation remains blocked.
- **IC-AT-022:** no API/tool path may install or weaken repository rules without an explicit owner action.

---

## HFT193-MD series — mapped-drive and external-resource admission findings

### HFT193-MD-001 — Model resource-probe outcomes precisely

**Defect.** A Codex pre-execution sandbox rejection can be incorrectly collapsed into "path missing" or "path inaccessible", creating a false statement about the underlying resource.

**Implementation requirement.**
- Resource probes must classify outcomes distinctly:
  1. `PRE_EXECUTION_REJECTED` — host/model sandbox rejected the command before process start;
  2. `PROCESS_START_FAILED` — execution was attempted but the process could not start;
  3. `COMMAND_NONZERO` — process started and returned nonzero;
  4. `ACCESS_DENIED` — the execution context observed an authorization/permission denial;
  5. `PATH_MISSING` — the execution context successfully evaluated the path and established that it does not exist;
  6. `VERIFIED_READ` — the bounded read completed successfully.
- Probe records must preserve raw provider/host outcome category and the observation context.
- Error translation must not infer filesystem state from an outcome that did not reach filesystem evaluation.

**Fail-closed behavior.**
- `PRE_EXECUTION_REJECTED` is reported as execution-policy/capability UNKNOWN for the resource, not PATH_MISSING or ACCESS_DENIED.
- Any ambiguous/unclassified probe result blocks resource-dependent dispatch until resolved or explicitly handled by a supported owner-authorised path.

**Regression / acceptance tests.**
- **MD-AT-001:** simulated Codex pre-execution sandbox rejection returns `PRE_EXECUTION_REJECTED`; UI/logs do not claim missing/inaccessible path.
- **MD-AT-002:** process start failure, command nonzero, access denied, missing path, and verified read each map to their distinct canonical outcome.
- **MD-AT-003:** unrecognized provider response maps to UNKNOWN/refusal, never to PATH_MISSING.
- **MD-AT-004:** records identify execution context/provider plus outcome evidence.

### HFT193-MD-002 — Probe mapped-drive/resource capability in the actual worker context before dispatch

**Defect.** Controller visibility or owner approval can be mistaken for capability inherited by sandboxed workers.

**Implementation requirement.**
- Before dispatch of any ticket requiring a mapped drive or external resource, AWF must probe that resource in the **actual worker execution context** that will receive the work.
- Admission requires both:
  - bounded visibility/listing as appropriate; and
  - a bounded byte read of an owner-authorised probe target or file.
- Controller/preflight probes may provide diagnostic evidence but never substitute for worker-context evidence.
- Worker capability evidence is bound to worker execution mode/provider, resource token, private binding fingerprint, task/session, and observation time.

**Fail-closed behavior.**
- If worker-context visibility/read is not VERIFIED, resource-dependent stream dispatch is refused.
- A PASS from controller context with UNKNOWN/FAIL in worker context is reported explicitly as context divergence, not as resource availability.

**Regression / acceptance tests.**
- **MD-AT-005:** controller can read mapped drive but worker sandbox cannot; dispatch is refused.
- **MD-AT-006:** owner can see resource but worker probe is pre-execution rejected; dispatch is refused with the precise MD-001 outcome.
- **MD-AT-007:** worker bounded listing and byte read pass; admission records exact worker-context evidence.
- **MD-AT-008:** capability from a different worker provider/session is not silently reused where freshness/context binding requires re-observation.

### HFT193-MD-003 — Narrow owner approval and retry for pre-execution rejection

**Defect.** A sandbox pre-execution rejection may be recoverable by explicit narrow command approval, but broad or stale approvals must not be reused.

**Implementation requirement.**
- For `PRE_EXECUTION_REJECTED`, AWF may offer an **exact-command narrow approval** workflow when the host supports it.
- The approval record must bind:
  - exact normalized command/arguments;
  - resource token/binding fingerprint;
  - execution context;
  - approver/authority;
  - issued time and lifetime/expiry;
  - intended probe purpose.
- After approval, capability must be re-observed in a **new task/session** rather than treating approval itself as evidence of access.
- Expired, mismatched, or context-stale approvals are not reusable.

**Fail-closed behavior.**
- No broad wildcard/escalated approval is synthesized from a narrow request.
- An approval that is stale, differently scoped, or not followed by a new-context VERIFIED probe does not satisfy admission.

**Regression / acceptance tests.**
- **MD-AT-009:** exact approved probe command succeeds in a new session and establishes capability only from the fresh observed result.
- **MD-AT-010:** changed command/path/token fails approval matching and requires new owner approval.
- **MD-AT-011:** expired approval is rejected as stale.
- **MD-AT-012:** approval exists but retry still receives pre-execution rejection/nonzero/access denied; capability remains unavailable with the new precise outcome.

### HFT193-MD-004 — Canonical network fallback is owner supplied and private-bound

**Defect.** Guessing a UNC/network fallback or leaking an internal locator into tracked/project systems can disclose infrastructure details and create incorrect remaps.

**Implementation requirement.**
- Any canonical network-path fallback must be explicitly supplied by the owner; AWF must never infer, guess, enumerate, or silently select one.
- Tracked configuration, PR text, commit messages, logs intended for publication, and Jira must use a provider-neutral token such as `{raw_estate}`, never the exact internal locator.
- The exact locator is kept only in an approved private binding mechanism outside tracked Git.
- Durable evidence may store a non-reversible binding fingerprint plus token, provider/context, owner provenance, and freshness.
- Re-observation must detect remap drift: a token resolving to a different binding fingerprint invalidates prior capability evidence until owner review/rebinding.

**Fail-closed behavior.**
- No owner-supplied binding means no fallback and no resource-dependent dispatch.
- A guessed/discovered candidate path is never automatically adopted.
- Binding fingerprint drift invalidates admission and requires explicit owner confirmation/rebinding.

**Regression / acceptance tests.**
- **MD-AT-013:** no owner fallback supplied; framework does not guess a UNC/network path and refuses fallback use.
- **MD-AT-014:** repository diff/PR/commit/Jira-render fixtures contain only `{raw_estate}` and no exact private locator.
- **MD-AT-015:** same token and same private binding fingerprint permits fresh probe; changed fingerprint is reported as remap drift and blocks admission.
- **MD-AT-016:** owner explicitly replaces binding; only fresh post-rebind worker evidence can restore capability.

### HFT193-MD-005 — Read-only external-resource admission forbids mutation beneath the resource

**Defect.** A resource admitted only for reading must not become a source of write/rename/copy/delete/materialization or permission-changing side effects.

**Implementation requirement.**
- Resource admission declares access mode. For `read_only`, the worker policy must prohibit beneath that resource:
  - file/directory write or create;
  - rename/move;
  - delete;
  - copy **into or out of** the resource when that copy materializes resource content elsewhere;
  - materialization/caching of resource content into the repository or other persistent local stores;
  - ACL/permission/ownership changes.
- Bounded in-memory reads needed for the task are allowed.
- Separately authorized writes inside the governed repository remain allowed and must not be conflated with external-resource write authority.
- Child-process/tool policy must propagate the read-only resource restriction.

**Fail-closed behavior.**
- Any requested/attempted mutation beneath a read-only resource is refused and recorded as a policy violation.
- If a tool cannot guarantee read-only behavior for the external resource, it is not admitted for that resource.

**Regression / acceptance tests.**
- **MD-AT-017:** bounded read succeeds while create/write/rename/delete operations under the resource are refused.
- **MD-AT-018:** copy/materialize from resource into repo or persistent temp location is refused under read-only admission.
- **MD-AT-019:** permission/ACL mutation is refused.
- **MD-AT-020:** separately authorized repository write outside the external-resource boundary remains permitted.
- **MD-AT-021:** subprocess/tool that cannot enforce the read-only boundary is denied access.

### HFT193-MD-006 — Bounded probes and proportional conclusions

**Defect.** Unbounded traversal risks excessive disclosure/cost, and weak negative observations can be overstated (for example, "directory name not found" becoming "normalized artifacts absent").

**Implementation requirement.**
- Resource admission probes use configured hard bounds for:
  - directory entries/depth;
  - bytes read per file/probe;
  - number of files/probe operations;
  - elapsed time where supported.
- Evidence records the applied bounds.
- Conclusions must be no stronger than the probe:
  - a successful byte read establishes readability of those bytes in that context;
  - a bounded listing establishes only what was observed within its bounds;
  - a negative search for a directory/file name establishes only "not observed within the searched scope", not absence of semantic/normalized artifacts.
- Any artifact-presence claim requiring content/semantic validation needs its own explicit bounded check.

**Fail-closed behavior.**
- A probe that would exceed bounds stops and returns `INCOMPLETE_BOUNDED_OBSERVATION` (or equivalent), never a broad negative conclusion.
- Resource-dependent admission requiring stronger evidence remains blocked until the required bounded evidence exists.

**Regression / acceptance tests.**
- **MD-AT-022:** listing hits entry/depth bound; result is incomplete, not "resource absent".
- **MD-AT-023:** bounded byte read succeeds; output claims readability only and does not claim dataset completeness/validity.
- **MD-AT-024:** negative search for directory name records "not observed in bounded scope" and does not assert normalized artifacts are absent.
- **MD-AT-025:** semantic/artifact-presence requirement cannot be satisfied by a filename-only negative search.
- **MD-AT-026:** probe logs record exact bounds without exposing private locator values.

---

## Acceptance-test backlog summary

| Finding | Mandatory acceptance tests |
| --- | --- |
| HFT193-IC-001 | IC-AT-001..004 |
| HFT193-IC-002 | IC-AT-005..007 |
| HFT193-IC-003 | IC-AT-008..011 |
| HFT193-IC-004 | IC-AT-012..014 |
| HFT193-IC-005 | IC-AT-015..018 |
| HFT193-IC-006 | IC-AT-019..022 |
| HFT193-MD-001 | MD-AT-001..004 |
| HFT193-MD-002 | MD-AT-005..008 |
| HFT193-MD-003 | MD-AT-009..012 |
| HFT193-MD-004 | MD-AT-013..016 |
| HFT193-MD-005 | MD-AT-017..021 |
| HFT193-MD-006 | MD-AT-022..026 |

## Completion rule

A finding may be marked resolved only when:
1. its implementation requirement is present in framework code/configuration/documented policy as applicable;
2. its fail-closed path is covered by automated tests;
3. every listed regression/acceptance test passes against the candidate 1.9.3 implementation head;
4. an independent critic reviews the exact candidate head and finds no unresolved issue against that finding; and
5. release evidence records the finding IDs and test evidence.

No resolution in this framework backlog changes the HFT Strategies project's fixed AWF 1.9.1 trust decision. Adoption of a later AWF version by HFT remains a separate explicit owner decision.
