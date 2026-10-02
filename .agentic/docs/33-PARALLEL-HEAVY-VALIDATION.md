# Bounded parallel heavy validation

AWF 1.9.3 can execute a reviewed validation workload as deterministic partitions. The
runner supports Python, Wolfram, MATLAB, and project-specific engines through literal
argument vectors. It does not interpret shell command strings.

This facility only runs a plan that has an exact SHA-256 pin and a separate, current
approval record bound to that plan digest. The trusted host or controller must
authenticate the source of the review and capacity records. A copied JSON review file
does not authenticate its `reviewer_identity` by itself.

## Inputs

Start from these templates:

- `.agentic/templates/heavy-validation-plan.json`
- `.agentic/templates/heavy-validation-review.json`
- `.agentic/templates/heavy-validation-capacity.json`

The plan selects one engine and resource class for a workload. Each partition supplies
a unique name, literal `argv`, timeout, and accepted exit codes. AWF rejects shell
strings, empty arguments, control characters, duplicate names, unknown fields, and
documents larger than 1 MiB. A plan change invalidates its review.

Engine is a normalized label. `python`, `wolfram`, and `matlab` are conventional built-in
labels; a project can use another label such as `custom_solver_v2`. The label never
selects or constructs a command. The reviewed partition `argv` always names the exact
executable and arguments. Examples include:

```json
{"engine":"python","argv":["python","-m","pytest","tests/shard_01"]}
{"engine":"wolfram","argv":["wolframscript","-file","validation/shard_01.wls"]}
{"engine":"matlab","argv":["matlab","-batch","run('validation/shard_01.m')"]}
```

Each example is one structured partition fragment. Do not join its fields into a shell
command. Engine-specific quoting remains an argument understood by that executable.

## Capacity and licenses

Parallel mode requires all of the following:

1. `execution.host_broker.enabled` is true and its `broker_id` matches the capacity record.
2. A fresh, exact SHA-256-pinned capacity record supplies available worker, heavy job,
   GPU job, engine, and named-resource slots.
3. The engine reports `parallel_available: true` and at least two parallel slots.
4. Every named resource in `required_resources` has at least one configured and observed
   slot. Use names such as `wolfram_kernel` or `matlab_license` for license-bound work.

The effective parallelism is the smallest applicable value among:

- partition count and requested parallelism;
- broker `max_workers`, `max_heavy_jobs`, and, for GPU work, `max_gpu_jobs`;
- observed worker, heavy job, and GPU capacity;
- observed engine parallel slots;
- configured and observed slots for every required resource.

If the broker is disabled, capacity is missing or stale, engine parallelism is unavailable,
or a license/resource cannot be established, AWF executes the workload serially and records
the reason. Serial fallback does not bypass command review, hashes, timeouts, or result
requirements. An unavailable executable or license failure from the command is a failed
partition.

This feature reads the current host broker settings. It does not change daily limits,
per-ticket limits, native three-stream coordination, or dispatch policy.

## Run

Compute the SHA-256 of each finalized JSON input independently, authenticate the reviewer
and capacity source, then run:

```powershell
python .agentic/scripts/run_heavy_validation.py `
  --plan <controller-evidence-root>/heavy-plan.json `
  --expected-plan-sha256 <plan-sha256> `
  --review <controller-evidence-root>/heavy-review.json `
  --expected-review-sha256 <review-sha256> `
  --capacity <controller-evidence-root>/host-capacity.json `
  --expected-capacity-sha256 <capacity-sha256>
```

Omit both capacity arguments to request safe serial fallback. `Ctrl+C` records cancelled
terminal results for all scheduled partitions and returns failure after they finish
stopping. Save standard output as the aggregate evidence record in the controller-owned
evidence location.

## Gate rule and evidence

The result contains the plan, review, and capacity digests; effective concurrency and
fallback reasons; preserved cap and stream settings; and one result per scheduled
partition. Each partition records:

- deterministic partition and command digests;
- engine and literal command argument vector;
- start and end timestamps;
- exit code, timeout, cancellation, and terminal state;
- bounded stdout/stderr text, complete byte counts, truncation flags, and full-stream
  SHA-256 digests.

Result order and the aggregate digest are deterministic. Runtime timestamps are evidence
but are excluded from the deterministic aggregate digest.

A gate may consume this result only when `status` is `PASS`,
`all_partitions_terminal` is true, completed terminal count equals scheduled count, and
every partition state is `PASS`. Missing, failed, timed-out, cancelled, or internally
failed partitions keep the workload at `FAIL`. The runner waits for every scheduled
partition before it emits a result, including after an early failure.

The result states `execution_authority: false`. It is validation evidence and does not
authorize a merge, deployment, activation, or other lifecycle transition.

## Synthetic regression

The focused regression uses short Python child processes as engine-independent stand-ins:

```powershell
python -m unittest discover -s .agentic/tests -p test_heavy_validation.py -v
```

It validates parallel ceilings, GPU and named-license constraints, serial fallbacks,
engine labels, reviewed-command binding, deterministic aggregation, complete failure
evidence, timeouts, cancellation, missing executables, and unchanged caps/stream settings.
It does not require MATLAB, Wolfram Mathematica, a GPU, or a license server.
