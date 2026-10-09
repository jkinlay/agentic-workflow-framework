# Bounded parallel heavy validation

AWF 1.9.4 executes partitioned validation only from an exact reviewed candidate. It cannot mutate providers.

## Frozen execution identity

A version 4 plan binds repository/base/head/tree IDs, exact working directory, executable paths and digests, argv, timeouts, engine/framework/resources, deterministic retries, process/network/worktree isolation, and normalized target-environment and credential-stripping policy.

Before launch, a trusted attestor proves the reviewed HEAD/tree clean. Git reads use a minimal environment and ignore ambient configuration, global attributes, and replacements. Execution archives the exact tree. Independent inventory must match extracted paths, modes, and computed blob IDs; content SHA-256 is rechecked after sealing. Windows handles and DACLs seal namespaces; unsupported hosts fail closed. Target, interpreter, launcher, sanitizer, and isolation flags are reviewed and fenced. The launcher loads the pinned sanitizer from the immutable snapshot. Identity movement fails; `shell=False` remains mandatory.

Version 6 binds plan and provider review. GitHub evidence binds repository, PR, reviewer, approved commit and authorization digest. AWF reauthenticates before dispatch and under the launch lock before each child/retry. Clock or authority failure records rejection, cancels work and blocks later launches. Local `APPROVE` authorizes nothing.

## Broker lease and serial fallback

Parallel execution requires all of these:

1. fresh capacity bound to the plan, config, and candidate tuple;
2. configured and observed worker, heavy/GPU, engine, and named resource capacity;
3. an actual broker lease bound to the complete request digest;
4. a positive fence and duration covering dispatch attestation, snapshot creation, every partition/retry timeout, startup, cleanup/exit waits, both reader joins, and terminal-barrier overhead;
5. exact lease release after every partition reaches a terminal result.

Zero capacity starts no child. All broker-enabled work needs an exact lease. Serial fallback needs a separate grant. Stale-fence release failure blocks work; execution-fence release failure keeps `FAIL`. Capacity observations never grant resources.

Any engine reporting `parallel_available: false` caps dispatch and the broker request to one slot. Partial/failed cleanup cancels queued launches under the gate and retains the lease until termination is established; the run fails.

The broker validates all fields, quantities, resources, monotonic fences, and engine identity/slots. Microsecond expiry starts at acquisition and covers the full request plus validation margin. A live guard terminates expired work. OS-locked state is fsynced; malformed state fails closed.

## Process and credential containment

Each target receives a new deterministic environment made only from reviewed normalized values, forced Python user-site and UTF-8 controls, and the reviewed seed/attempt additions. It never inherits the host environment. The bound strip set covers provider/GitHub credentials, Python module-search variables, and validated `execution.child_env_strip_extra`; that config list must exactly equal the plan list. Target values cannot redefine a stripped or forced name.

POSIX children run in a new session/process group. On Windows, a trusted launcher waits for one bounded ASCII JSON record containing exact argv and target environment; plan validation enforces the same 65,536-byte limit, including spaces and Unicode escaping. AWF assigns that launcher to a kill-on-close Job Object before releasing it to start the workload. Containment setup failure terminates the waiting launcher, so workload code never starts outside the Job. Timeout/cancellation terminates the contained tree. Every partition records cleanup outcome; partial or failed cleanup prevents PASS.

## Evidence and gate consumption

The result records authorization, checkout/snapshot proof, fenced artifacts, pinned inputs, admission/release, determinism, resources/isolation, bounded output, and cleanup. It emits one sorted terminal record per partition and a timestamp-free serial-equivalence digest.

The production command requires external result, receipt, and controller-held key paths. It exclusively creates and fsyncs a canonical result plus HMAC-SHA256 receipt, refuses overwrite, and verifies both before completion. Tampering fails verification; rejected runs attempt signed evidence.

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

Replace every placeholder and compute independent pins. Invoke `run_heavy_validation.py` with the GitHub identity flags, three result/receipt flags, and, when enabled, `--broker-state` plus pinned capacity. Adapter labels are `command`, `python-unittest`, `pytest`, `matlab`, and `wolfram`; each enforces structured argv. No receipt key or other secret belongs in the checkout or records.

Run the synthetic regression with:

```powershell
python -B -m unittest discover -s .agentic/tests -p test_heavy_validation.py -v
```

The regression covers every control above, including replacement/config injection, mutation races, lease expiry/exhaustion, authorization movement, containment, tampering, cancellation, adapters, credentials, caps, and output bounds. It uses no proprietary engine, GPU, or license server.

Durable lease quarantine, recovery, and launch artifact receipt semantics are
specified in [five-slot acceleration](36-FIVE-SLOT-ACCELERATION.md).
