# Bounded parallel heavy validation

AWF 1.9.3 executes partitioned validation only from a reviewed, exact candidate tuple. This facility has no merge, deployment, installation, activation, upgrade, or provider-mutation authority.

## Frozen execution identity

A version 3 plan binds:

- numeric repository ID, base SHA, head SHA, and tree SHA;
- the exact absolute working directory;
- one absolute executable path and file SHA-256 per partition;
- literal argument vectors, timeouts, accepted exit codes, engine, framework adapter, resource class, and per-partition named-resource quantities;
- one deterministic seed, bounded retry limit, and required process-tree, host-network-policy, and worktree isolation declarations.

Before any child starts, AWF rejects candidate movement, a different working directory, lexical or resolved symlink/reparse aliases, a changed executable digest, a plan/review mismatch, or mutable config/capacity that is not pinned to the same tuple. `shell=False` is mandatory.

The review record contains provider, immutable reviewer ID, informational login, the plan digest, and candidate tuple. Those JSON fields are claims until a trusted host adapter authenticates them against provider evidence. `run_validation` therefore requires `review_authenticator`; absence or mismatch returns `Authenticated review authority is unavailable` before execution. The production command uses read-only GitHub API observations to bind the numeric repository, PR base/head, commit tree, formal review ID, immutable reviewer ID, login, APPROVED state, and reviewed commit. A copied local `APPROVE` file never authorizes work.

## Broker lease and serial fallback

Parallel execution requires all of these:

1. fresh capacity bound to the plan, config, and candidate tuple;
2. configured and observed worker, heavy/GPU, engine, and named resource capacity;
3. an actual broker lease bound to the complete request digest;
4. a positive fencing token and expiry covering the bounded workload;
5. exact lease release after every partition reaches a terminal result.

Zero worker, class, engine, or named-resource capacity starts no child. Broker-enabled work always needs an exact lease, including serial work. A denied, stale, failed, or unavailable parallel acquisition may use serial only after a separate serial request is granted. Every stale acquired fence is released before another request; release failure blocks execution. Failure to release the execution fence keeps the aggregate result at `FAIL`. Capacity observations alone never grant shared resources.

The shipped single-host broker uses an OS-locked durable state file, monotonic fences, expiry pruning, and configured/observed worker, heavy, GPU, and named-resource limits. It grants no provider or merge authority.

## Process and credential containment

Each child receives a scrubbed environment. The frozen provider set includes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `AZURE_OPENAI_API_KEY`, `CODEX_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, and `OPENAI_API_KEY`, plus validated `execution.child_env_strip_extra`. `GH_TOKEN` and `GITHUB_TOKEN` cannot be configured as extra removals because GitHub authentication is governed separately.

POSIX children run in a new session/process group. On Windows, a trusted launcher waits for one bounded argv record; AWF assigns that launcher to a kill-on-close Job Object before releasing it to start the workload. Containment setup failure terminates the waiting launcher, so workload code never starts outside the Job. Timeout/cancellation terminates the contained tree. Every partition records cleanup outcome; partial or failed cleanup prevents PASS.

## Evidence and gate consumption

The result records plan, review, config, capacity, candidate, cwd, executable, admission attempts, lease/fence release, seed, attempts/retries, resource/isolation declarations, bounded output, and cleanup. It emits one terminal record per scheduled partition, sorted by name. `AWF_VALIDATION_SEED` and `AWF_VALIDATION_ATTEMPT` bind each child. Its serial-equivalence digest covers the exact sorted partition result set and is invariant to scheduling; controller evidence can compare serial and parallel executions. Runtime timestamps are excluded.

The production command requires a new `--result-log` path. It creates one canonical envelope with result SHA-256, mode 0600 where supported, flushes and fsyncs it, and refuses overwrite. Console output is only the receipt. Rejected controller runs also attempt a durable rejection record.

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

Replace every placeholder and compute independent SHA-256 pins. Invoke `run_heavy_validation.py` through the controller with `--github-repository`, `--github-pr`, `--github-review-id`, `--result-log`, and, when the broker is enabled, `--broker-state` plus pinned capacity. Supported adapter labels are `command`, `python-unittest`, `pytest`, `matlab`, and `wolfram`; framework labels enforce their structured argv shape. No secret value belongs in these records.

Run the synthetic regression with:

```powershell
python -B -m unittest discover -s .agentic/tests -p test_heavy_validation.py -v
```

The regression covers zero/exhausted capacity, governed serial admission, separate fallback leases, stale/granted release failure, durable broker fencing/logs, GitHub authentication, Windows pre-execution containment failure, seed/retry binding, serial equivalence, framework/resource declarations, cancellation, descendant termination, candidate/cwd/executable movement, credentials, alias rejection, shell refusal, unchanged caps/streams, and bounded output. It uses no proprietary engine, GPU, or license server.
