# Scheduled review-loop runbook

[SPECIFICATION](../SPECIFICATION.md) defines authority. This adapter supports Codex CLI and same-repository GitHub.com PRs only. Installation neither enrolls a PR nor creates a scheduler.

These requirements apply to live enablement, not adoption. Missing rules warn; enrollment still needs observed rules, configured CI and trusted merge owners. See [adoption](20-NEW-PROJECT-SETUP.md).

## Prepare the host

Use an approved release and locked Python environment outside candidate checkouts. Prepare four physically separate directories: trusted runtime, protected state, clean worker clone and independent critic clone. Both clones need exact origin `https://github.com/OWNER/REPO.git`; the worker must already match the owned PR branch/head. Transfer existing writer ownership before enrollment.

Copy [host-config.example.json](../review-loop/host-config.example.json) into the state directory. Complete its repository/PR/branch, executable, runtime, contract, model, scope, finding, CI, limit and qualification fields. Changed requirements, binaries or configuration need reviewed replacement enrollment. Optional `reasoning_effort` uses `approved_model_effort_pairs`, or the candidate's immutable-base routing policy when that map is empty. `codex_config_overrides` only allows `windows.sandbox`.

The AWF source repository may opt into protected paths only when the candidate contains root `MANIFEST.json` and source markers, while reviewed config declares Tier 3 and an exact `governed_source_paths` allowlist. Runtime markers do not qualify a candidate; downstream repositories retain refusal.

Qualification uses a disposable same-repository PR and the configured separate checkouts. Set the named operator and evidence path, leave all four flags false, then run:

```text
python -B .agentic/scripts/review_loop.py --config <state-dir>/config.json qualify --confirm-disposable-pr
```

The command launches read-only critic and workspace-write worker probes with network disabled. It verifies denied critic writes/network, a worker write confined to its checkout, credential-like environment names, explicit `~/.codex/auth.json` and `$CODEX_HOME/auth.json` readability, the exclusive branch-owner constraint and the canonical database/lock. A readable or unevaluable agent auth file is a blocking finding, never credential-isolation evidence. The record names the operator, disposable candidate, host binding and observations, and pins retained inputs, effective config, log and result. The command writes only that record and reports its SHA-256; it never changes configuration or sets flags.

The evidence path must be a direct state-directory file distinct from the host configuration, pinned contract, canonical database/lock and other protected host state. Path and physical-file aliases are refused before any probe runs.

After inspecting a `PASS`, the operator pins `evidence_sha256` and sets all flags true. Normal commands require exact bytes, matching operator/host binding and evidence no older than `max_age_seconds`, capped at 604800 seconds (seven days). Reset flags and rerun after expiry or a bound runtime, executable, checkout, database, model or sandbox-policy change. Legacy `{operator,evidence,<booleans>}` configuration is refused: migrate to `evidence_path`, `evidence_sha256`, `max_age_seconds` and a live `qualify` record. Offline fixtures do not qualify a host. Keep state, config, contract and credentials inaccessible to workers.

[HostDriver](../lib/agentic/review_host.py) uses ephemeral contexts, role-specific sandboxes, no approval escalation/network, and strips API/GitHub-token variables. Saved credentials and descendants still need the probe; quotas need external enforcement.

## Create the first draft, then enroll

An explicit `first_draft` host may start with `pr: 0`. It requires empty
tracked/staged/untracked/ignored inventories, charges the worker, binds effort
to the observed base, and freezes local Git config, origins and hooks. The
publisher validates the tested tree and `ignored_untracked`, records receipt,
plan and base/head/body, makes one child commit, scans, pushes, creates a draft
and enrolls only a matching observation. Control drift blocks publication.

```text
python -B .agentic/scripts/review_loop.py --config ABSOLUTE_STATE_DIR/config.json first-draft --title "AWF: implement ticket"
```

The body records Tier 1, 2 or 3. A worker launch consumes one agent run but no
amendment; prepared-publication recovery consumes neither. The provider number
is persisted before snapshot. Failure retains the UUID/charge. Inspect effects,
then `resume --reconciled-run UUID`; replay adds a charge only without a valid
receipt. Scan/record recovery resumes the exact local child. For `pr: 0`, the
host searches all PR states by exact head, then reconciles the remote ref; it
never snapshots zero, replaces a closed match, blindly creates a second PR or
replays a worker when the frozen publication survives. Retry `first-draft` with
the original title.

## Check, enroll and run

From the trusted runtime, substitute paths:

```text
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json check
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json enroll
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json tick
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json status
```

For Jonathan's later live check against a disposable AWF PR on the 4090, prepare separate pinned runtime/state/worker/critic directories and run from the trusted runtime (the writer does not run this):

```text
ABSOLUTE_RUNTIME/.agentic/.venv/Scripts/python.exe -B ABSOLUTE_RUNTIME/.agentic/scripts/review_loop.py --config ABSOLUTE_STATE_DIR/config.json qualify --confirm-disposable-pr
```

After Jonathan inspects `PASS`, pins the reported digest and sets the four flags:

```text
ABSOLUTE_RUNTIME/.agentic/.venv/Scripts/python.exe -B ABSOLUTE_RUNTIME/.agentic/scripts/review_loop.py --config ABSOLUTE_STATE_DIR/config.json check
ABSOLUTE_RUNTIME/.agentic/.venv/Scripts/python.exe -B ABSOLUTE_RUNTIME/.agentic/scripts/review_loop.py --config ABSOLUTE_STATE_DIR/config.json enroll
ABSOLUTE_RUNTIME/.agentic/.venv/Scripts/python.exe -B ABSOLUTE_RUNTIME/.agentic/scripts/review_loop.py --config ABSOLUTE_STATE_DIR/config.json tick
```

Example config fragment:

```json
{"models":{"worker":"<approved-worker>","critic":"<approved-critic>"},"reasoning_effort":{"worker":"medium","critic":"high"},"approved_model_effort_pairs":{"<approved-worker>":["medium"],"<approved-critic>":["high"]},"codex_config_overrides":{"windows.sandbox":"elevated"}}
```

`check` reads GitHub and prepares the critic clone; it does not qualify models/isolation. `enroll` records ownership. `tick` may launch a model and publish a normal, non-force amendment push. Each invocation performs one review, amendment or CI observation. One shared database serializes all enrolled ticks; concurrent native writers must not own enrolled branches.

Before amendment push, the host scans every patch/message in the full range and the PR body. Git disables replacement objects; a finding or unscanned binary/oversize content refuses push. Keep `publication-deny.json` only in external state.

Defaults allow three amendment attempts (plus at most `max_cap_extensions` owner extensions, default two), ten agent runs and 24 CI waits. Attempts remain consumed after failure/resume; amendments touching only `evidence_paths` consume none. At the cap the loop pauses with `REVIEW_CAP_REACHED` and resumes only with `--disposition` (an owner's MERGE_WITH_NOTES, PARK, RESCOPE or EXTEND_ONE_CYCLE). Critics retain finding identities and bases and review every changed file. CI requires current head, pinned App identity, Actions workflow path/content and successful conclusion; null App identity or unsupported provenance pauses.

`READY_FOR_FINAL_GATE` hands complete evidence/specialist reconciliation to the [ticket lifecycle](23-TICKET-LIFECYCLE.md); it is not READY_FOR_OWNER_AUTHORIZATION until the final gate passes. Ordinary enrolled ticks operate an existing PR; they never create the initial draft, merge or write Jira. The supported `first-draft` entry point creates the initial draft from a native ticket, then enrollment, current validation/requirements and mark-ready precede critic dispatch on the observed PR head. Jira lifecycle writes belong to the controller (one mapped write per event with readback, no Epic writes, no mismatch/unknown retry); the loop never writes Jira.

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
