# Scheduled review-loop runbook

[SPECIFICATION](../SPECIFICATION.md) defines authority and acceptance. This reference adapter supports Codex CLI and GitHub.com same-repository PRs; `github_host` rejects other hosts. Installation does not enroll a PR or create a scheduler. Native-agent guidance is separate from this loop.

These host/rules/qualification requirements apply to live loop enablement, not installation or adoption. Record missing rules as warnings; live enrollment still needs observed rules, configured CI and trusted merge owners. Follow [adoption](20-NEW-PROJECT-SETUP.md) for owner application and secret-repository restrictions.

## Prepare the host

Use an approved release and locked Python environment outside candidate checkouts. Prepare four physically separate directories: trusted runtime, protected state, clean worker clone and independent critic clone. Both clones need exact origin `https://github.com/OWNER/REPO.git`; the worker must already match the owned PR branch/head. Transfer existing writer ownership before enrollment.

Copy [host-config.example.json](../review-loop/host-config.example.json) into the state directory. Complete its placeholders: repository/PR/branches, executable SHA-256 pins for native Git/gh/Codex, runtime manifest pin, frozen contract path/hash, worker/critic models, exact allowed files, initial finding ledger, CI App/workflow pins, limits and qualification record. Changed requirements, binaries or configuration require reviewed replacement enrollment; never silently update pins. `reasoning_effort` is optional, but each selected role must be present in `approved_model_effort_pairs`; `codex_config_overrides` is an explicit key allowlist (currently `windows.sandbox`).

The AWF source repository may opt into protected source paths only when the enrolled candidate checkout contains the root `MANIFEST.json` plus the named AWF source markers, and the reviewed config declares both `risk_tier: "Tier 3"` and an exact `governed_source_paths` allowlist (for example `[".agentic/**"]`). The trusted runtime is not evidence that a candidate is the AWF source repository; downstream repositories retain protected-path refusal.

Qualify sandbox restrictions, credential separation, exclusive branch ownership, quotas and one canonical host database using a disposable PR. Record observed evidence before setting qualification booleans true; those flags prove nothing themselves. Keep state, configuration, contract and credentials inaccessible to workers.

[HostDriver](../lib/agentic/review_host.py) requests ephemeral contexts, read-only critic/workspace-write worker, no approval escalation and no sandbox network. It strips API-key variables and agent GitHub-token variables. Saved credentials, project tools and process descendants still need demonstrated host isolation. Token/dollar/resource quotas require external enforcement. Protected paths, Git configuration checks and separate clones supplement that boundary.

## Create the first draft, then enroll

An explicitly configured `first_draft` host may start with `pr: 0`. The trusted
runtime charges the run before invoking the pinned worker, validates its
tested-tree receipt, scans before push, creates one draft PR, reads back its
body/tier, persists the assigned number, and enrolls it. A failed or uncertain
step retains the reservation for operator reconciliation; it is never silently
retried.

```text
python -B .agentic/scripts/review_loop.py --config ABSOLUTE_STATE_DIR/config.json first-draft --title "AWF: implement ticket"
```

The body records the target ticket's reviewed Tier 1, Tier 2 or Tier 3 risk
classification; the run consumes one `max_agent_runs` unit and zero amendment
cycles. The publisher retains `ignored_untracked`, checks `HEAD^{tree}`, scans,
and pushes the exact worker tree when Git metadata is unavailable to it.

## Check, enroll and run

From the trusted runtime, substitute actual paths:

```text
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json check
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json enroll
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json tick
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json status
```

For the operator live check against a disposable AWF PR on the 4090, run these exact commands from the trusted runtime after preparing separate pinned runtime/state/worker/critic directories (the writer does not perform this live check):

```text
ABSOLUTE_RUNTIME/python.exe -B ABSOLUTE_RUNTIME/.agentic/scripts/review_loop.py --config ABSOLUTE_STATE_DIR/config.json check
ABSOLUTE_RUNTIME/python.exe -B ABSOLUTE_RUNTIME/.agentic/scripts/review_loop.py --config ABSOLUTE_STATE_DIR/config.json enroll
ABSOLUTE_RUNTIME/python.exe -B ABSOLUTE_RUNTIME/.agentic/scripts/review_loop.py --config ABSOLUTE_STATE_DIR/config.json tick
```

Example config fragment:

```json
{"models":{"worker":"<approved-worker>","critic":"<approved-critic>"},"reasoning_effort":{"worker":"medium","critic":"high"},"approved_model_effort_pairs":{"<approved-worker>":["medium"],"<approved-critic>":["high"]},"codex_config_overrides":{"windows.sandbox":"elevated"}}
```

`check` reads GitHub and prepares the critic clone; it does not qualify models/isolation. `enroll` records ownership. `tick` may launch a model and publish a normal, non-force amendment push. Each invocation performs one review, amendment or CI observation. One shared database serializes all enrolled ticks; concurrent native writers must not own enrolled branches.

Immediately before its amendment push, the host scans every patch and message in the complete base-to-new-head range plus the current PR body. Host Git commands disable replacement objects through command flags, configuration and environment so local replacement refs cannot alter amendment identities or pinned workflow bytes. A finding or binary/oversize changed content refuses the push. The operator-local deny mapping belongs in the external review state directory as `publication-deny.json`; it is never copied into the checkout.

Defaults allow three amendment attempts (plus at most `max_cap_extensions` owner extensions, default two), ten agent runs and 24 CI waits. Attempts remain consumed after failure/resume; amendments touching only `evidence_paths` consume none. At the cap the loop pauses with `REVIEW_CAP_REACHED` and resumes only with `--disposition` (an owner's MERGE_WITH_NOTES, PARK, RESCOPE or EXTEND_ONE_CYCLE). Critics retain finding identities and bases and review every changed file. CI requires current head, pinned App identity, Actions workflow path/content and successful conclusion; null App identity or unsupported provenance pauses.

`READY_FOR_FINAL_GATE` hands complete evidence/specialist reconciliation to the [ticket lifecycle](23-TICKET-LIFECYCLE.md); it is not READY_FOR_OWNER_AUTHORIZATION until the final gate passes. The loop operates an existing PR, never creates the initial draft, merges or writes Jira. Native COMPLETE first creates the draft; current validation/requirements and mark-ready precede critic dispatch on the observed PR head. Jira lifecycle writes belong to the controller (one mapped write per event with readback, no Epic writes, no mismatch/unknown retry); the loop never writes Jira.

### First draft from a native ticket

The native worker is the only initial writer. It receives the pinned ticket
contract, model/effort, sandbox and allowlisted Codex overrides from the host
configuration and returns a `review_first_draft` receipt containing only its
declared paths, actions and `tested_tree`. A worker that cannot write Git
metadata leaves that receipt and worktree to the publisher. The publisher
computes the same tree, verifies `HEAD^{tree}` equals `tested_tree`, renders a
body containing the reviewed Tier 3 classification, and runs the history-aware
publication scan over the exact base/head/body before a normal push. A failed
scan blocks the push. After the draft PR is created, the host observes its
number/head/body, calls `enroll_created_pr`, and records one run against
`max_agent_runs`; amendment cycles remain zero. No merge or Jira action occurs.

## Schedule and recover

Preview Windows registration, then repeat with `-Create` within existing host authority:

```powershell
& .agentic/scripts/register_review_loop.ps1 -RuntimeRoot C:/awf-runtime -Python C:/awf-venv/Scripts/python.exe -Config C:/awf-state/config.json -TaskName AWF-Example-7
```

The default five-minute schedule requires the user logged in. Verify an actual scheduled execution and its retained report. Other scheduler integrations are unqualified. `status` is read-only; [scheduled_tick.py](../scripts/scheduled_tick.py) runs work and records outcomes, without delivering chat notifications.

Before maintenance, disable wakeups and pause:

```powershell
Disable-ScheduledTask -TaskName AWF-Example-7
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json pause --reason "Maintenance"
```

Disabling scheduling does not stop an active process. After timeout/crash/uncertain push, inspect retained `runs/RUN_ID` inputs/results/logs, remote state, local commits and surviving descendants. Preserve changes; never replay an uncertain push or create another database to reset limits. Once processes and Git are reconciled:

```text
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json resume --reconciled-run EXACT_RETAINED_UUID_OR_none
```

Resume starts full review; re-enable scheduling afterward. Retire/migrate manually with stopped processes and preserved state. Run the [adoption checks](20-NEW-PROJECT-SETUP.md) and relevant project tests. Offline process fixtures are not live host qualification; retain separate observed scheduler, credential and permission evidence.
