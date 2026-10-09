# Bounded parallel heavy validation

AWF 1.9.4 executes partitioned validation only from an exact reviewed candidate. It cannot mutate providers.

## Frozen execution identity

A version 4 plan binds the candidate, cwd, executable identities, commands, bounds, resources, determinism, isolation, environment, and credential stripping.

Before launch, a trusted attestor proves the reviewed HEAD/tree clean. Isolated Git reads ignore ambient configuration, attributes, and replacements. Execution archives the exact tree; independent path, mode, blob-ID, and content inventories must match. Windows retains its handle/DACL seal. POSIX uses an owner-only frozen snapshot rehashed inside the serialized gate immediately before every child; unavailable guards fail closed. Targets and isolation flags are reviewed and fenced; `shell=False` remains mandatory.

Only a POSIX venv's final interpreter symlink may resolve: its regular target must be pinned inside that venv or a controller base-interpreter prefix. Parent aliases, escaping chains, arbitrary links, and Windows reparse aliases remain refused.

Version 6 binds plan and provider review to repository, PR, reviewer, approved commit, and authorization digest. AWF reauthenticates before dispatch and under each launch lock. Clock or authority failure rejects and cancels later launches. Local `APPROVE` authorizes nothing.

## Broker lease and serial fallback

Parallel execution requires all of these:

1. fresh capacity bound to the plan, config, and candidate;
2. configured and observed worker, heavy/GPU, engine, and named-resource capacity;
3. a broker lease bound to the complete request digest;
4. a positive fence covering dispatch, snapshotting, retries, timeouts, startup, cleanup, readers, and terminal barrier;
5. exact release after every partition terminates.

Zero capacity starts no child. All broker-enabled work needs an exact lease. Serial fallback needs a separate grant. Stale-fence release failure blocks work; execution-fence release failure keeps `FAIL`. Capacity observations never grant resources.

Any engine reporting `parallel_available: false` caps dispatch and the broker request to one slot. Partial/failed cleanup cancels queued launches under the gate and retains the lease until termination is established; the run fails.

The broker validates all fields, quantities, resources, monotonic fences, and engine identity/slots. Microsecond expiry starts at acquisition and covers the full request plus validation margin. A live guard terminates expired work. OS-locked state is fsynced; malformed state fails closed.

## Process and credential containment

Each target receives a new deterministic environment made only from reviewed normalized values, forced Python user-site and UTF-8 controls, and the reviewed seed/attempt additions. It never inherits the host environment. The bound strip set covers provider/GitHub credentials, Python module-search variables, and validated `execution.child_env_strip_extra`; that config list must exactly equal the plan list. Target values cannot redefine a stripped or forced name.

POSIX children use a new process group. Linux launches the pin from sealed `memfd`; without `memfd_create` (including macOS), AWF rehashes an owner-only, unlinked read-only descriptor before `/dev/fd` execution. On Windows, a trusted launcher accepts one bounded ASCII argv/environment record, enters a kill-on-close Job Object, then starts work. Setup failure prevents workload launch. Timeout/cancellation terminates the tree; incomplete cleanup prevents PASS.

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

Regression includes real POSIX venv links, escape refusal, simulated macOS descriptor and snapshot paths, races, leases, authority, containment, tampering, cancellation, adapters, credentials, caps, and bounds. `self_test.py` records named platform skips and reasons; none silently pass. No proprietary engine, GPU, or license server is required.

Durable lease quarantine, recovery, and launch artifact receipt semantics are
specified in [five-slot acceleration](36-FIVE-SLOT-ACCELERATION.md).
