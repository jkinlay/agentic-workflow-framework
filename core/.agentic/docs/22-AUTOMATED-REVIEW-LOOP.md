# Scheduled review-loop runbook

[SPECIFICATION](../SPECIFICATION.md) defines authority and acceptance. This reference adapter supports Codex CLI and GitHub.com same-repository PRs; `github_host` rejects other hosts. Installation does not enroll a PR or create a scheduler. Native-agent guidance is separate from this loop.

## Prepare the host

Use an approved release and locked Python environment outside candidate checkouts. Prepare four physically separate directories: trusted runtime, protected state, clean worker clone and independent critic clone. Both clones need exact origin `https://github.com/OWNER/REPO.git`; the worker must already match the owned PR branch/head. Transfer existing writer ownership before enrollment.

Copy [host-config.example.json](../review-loop/host-config.example.json) into the state directory. Complete its placeholders: repository/PR/branches, executable SHA-256 pins for native Git/gh/Codex, runtime manifest pin, frozen contract path/hash, worker/critic models, exact allowed files, initial finding ledger, CI App/workflow pins, limits and qualification record. Changed requirements, binaries or configuration require reviewed replacement enrollment; never silently update pins.

Qualify sandbox restrictions, credential separation, exclusive branch ownership, quotas and one canonical host database using a disposable PR. Record observed evidence before setting qualification booleans true; those flags prove nothing themselves. Keep state, configuration, contract and credentials inaccessible to workers.

[HostDriver](../lib/agentic/review_host.py) requests ephemeral contexts, read-only critic/workspace-write worker, no approval escalation and no sandbox network. It strips API-key variables and agent GitHub-token variables. Saved credentials, project tools and process descendants still need demonstrated host isolation. Token/dollar/resource quotas require external enforcement. Protected paths, Git configuration checks and separate clones supplement that boundary.

## Check, enroll and run

From the trusted runtime, substitute actual paths:

```text
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json check
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json enroll
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json tick
python -B .agentic/scripts/review_loop.py --config C:/awf-state/config.json status
```

`check` reads GitHub and prepares the critic clone; it does not qualify models/isolation. `enroll` records ownership. `tick` may launch a model and publish a normal, non-force amendment push. Each invocation performs one review, amendment or CI observation. One shared database serializes all enrolled ticks; concurrent native writers must not own enrolled branches.

Defaults allow three amendment attempts, ten agent runs and 24 CI waits. Attempts remain consumed after failure/resume. Critics retain finding identities and review every changed file. CI requires current head, pinned App identity, Actions workflow path/content and successful conclusion; null App identity or unsupported provenance pauses.

`READY_FOR_FINAL_GATE` requires complete evidence/specialist reconciliation and a fresh human merge decision. The loop performs no merge or Jira write.

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

Resume starts full review; re-enable scheduling afterward. Retire/migrate manually with stopped processes and preserved state. [Validation](19-RELEASE-VALIDATION.md) distinguishes synthetic process tests from live host qualification.
