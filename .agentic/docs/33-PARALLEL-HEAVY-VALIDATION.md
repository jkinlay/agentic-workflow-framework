# Bounded parallel heavy validation

AWF 1.9.3 executes partitioned validation only from a reviewed, exact candidate tuple. This facility has no merge, deployment, installation, activation, upgrade, or provider-mutation authority.

## Frozen execution identity

A version 2 plan binds:

- numeric repository ID, base SHA, head SHA, and tree SHA;
- the exact absolute working directory;
- one absolute executable path and file SHA-256 per partition;
- literal argument vectors, timeouts, accepted exit codes, engine, resource class, and named resources.

Before any child starts, AWF rejects candidate movement, a different working directory, lexical or resolved symlink/reparse aliases, a changed executable digest, a plan/review mismatch, or mutable config/capacity that is not pinned to the same tuple. `shell=False` is mandatory.

The review record contains provider, immutable reviewer ID, informational login, the plan digest, and candidate tuple. Those JSON fields are claims until a trusted host adapter authenticates them against provider evidence. `run_validation` therefore requires `review_authenticator`; absence or mismatch returns `Authenticated review authority is unavailable` before execution. The standalone command deliberately supplies no authority and remains unavailable until invoked through an authenticated controller adapter. A copied local `APPROVE` file never authorizes work.

## Broker lease and serial fallback

Parallel execution requires all of these:

1. fresh capacity bound to the plan, config, and candidate tuple;
2. configured and observed worker, heavy/GPU, engine, and named resource capacity;
3. an actual broker lease bound to the complete request digest;
4. a positive fencing token and expiry covering the bounded workload;
5. exact lease release after every partition reaches a terminal result.

Denied, stale, failed, or unavailable acquisition falls back safely to serial execution and records the reason. A malformed or tuple-mismatched grant is rejected. Failure to release an acquired fence keeps the aggregate result at `FAIL`. Capacity observations alone never grant shared resources.

## Process and credential containment

Each child receives a scrubbed environment. The frozen provider set includes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `AZURE_OPENAI_API_KEY`, `CODEX_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, and `OPENAI_API_KEY`, plus validated `execution.child_env_strip_extra`. `GH_TOKEN` and `GITHUB_TOKEN` cannot be configured as extra removals because GitHub authentication is governed separately.

POSIX children run in a new session/process group. Windows children are assigned to a Job Object with kill-on-close, with bounded tree termination on timeout or cancellation. Every partition records the process-tree cleanup outcome. Partial or failed cleanup prevents PASS.

## Evidence and gate consumption

The result records plan, review, config, capacity, candidate, cwd, executable, lease, fence-release, output, and cleanup bindings. It emits one terminal record per scheduled partition, sorted by partition name. Runtime timestamps are evidence but excluded from deterministic aggregation.

A consumer may use a result only when:

- `status` is `PASS`;
- `all_partitions_terminal` is true;
- completed count equals scheduled count;
- every partition is `PASS`;
- process-tree cleanup is complete;
- an acquired lease was released with the same fence;
- the result remains bound to the current candidate tuple.

The result always states `execution_authority: false`.

## Templates and focused regression

Start from:

- `.agentic/templates/heavy-validation-plan.json`
- `.agentic/templates/heavy-validation-review.json`
- `.agentic/templates/heavy-validation-capacity.json`

Replace every placeholder and compute independent SHA-256 pins. Host/controller integration supplies authenticated reviewer evidence and the lease broker adapter. No secret value belongs in these records.

Run the synthetic regression with:

```powershell
python -B -m unittest discover -s .agentic/tests -p test_heavy_validation.py -v
```

The regression covers acquire/release fencing, denied/stale/failed leases, candidate/cwd/executable movement, review authentication, mutable input binding, widened credential stripping, descendant-tree termination, alias rejection, deterministic all-partition aggregation, shell refusal, unchanged caps/stream settings, and bounded output. It uses no proprietary engine, GPU, or license server.
