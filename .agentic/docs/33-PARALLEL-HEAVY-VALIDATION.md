# Bounded parallel heavy validation

AWF 1.9.3 executes partitioned validation only from a reviewed, exact candidate tuple. This facility has no merge, deployment, installation, activation, upgrade, or provider-mutation authority.

## Frozen execution identity

A version 3 plan binds:

- numeric repository ID, base SHA, head SHA, and tree SHA;
- the exact absolute working directory;
- one absolute executable path and file SHA-256 per partition;
- literal argument vectors, timeouts, accepted exit codes, engine, framework adapter, resource class, and per-partition named-resource quantities;
- one deterministic seed, bounded retry limit, and required process-tree, host-network-policy, and worktree isolation declarations.

Before any child starts, a trusted Git checkout attestor proves that the execution root is clean and its HEAD/tree equal the reviewed tuple. AWF also rejects a different directory, lexical or resolved alias, changed executable, plan/review mismatch, or config/capacity not pinned to that tuple. `shell=False` is mandatory.

The version 4 review includes an exact workload-authorization record covering plan digest, cwd, argv, executables, resources, retries, and isolation. These fields remain claims until a trusted adapter finds the record digest in a formal provider review. `run_validation` requires both review and checkout authenticators. The production adapter uses read-only GitHub observations to bind repository, PR tuple, formal review, immutable reviewer, APPROVED state, reviewed commit, and `AWF-HEAVY-VALIDATION-AUTHORIZATION-SHA256: <digest>`. A copied local `APPROVE` file never authorizes work.

## Broker lease and serial fallback

Parallel execution requires all of these:

1. fresh capacity bound to the plan, config, and candidate tuple;
2. configured and observed worker, heavy/GPU, engine, and named resource capacity;
3. an actual broker lease bound to the complete request digest;
4. a positive fencing token and expiry covering every retry plus bounded overhead;
5. exact lease release after every partition reaches a terminal result.

Zero worker, class, engine, or named-resource capacity starts no child. Broker-enabled work always needs an exact lease, including serial work. A denied, stale, failed, or unavailable parallel acquisition may use serial only after a separate serial request is granted. Every stale acquired fence is released before another request; release failure blocks execution. Failure to release the execution fence keeps the aggregate result at `FAIL`. Capacity observations alone never grant shared resources.

The single-host broker strictly validates every state field, positive quantity, resource, unique monotonic fence, and engine identity/slot claim before and after mutation. Its OS-locked durable file is fsynced with its directory. Malformed state fails closed. Configured and observed worker, heavy, GPU, engine, and named-resource limits govern every active lease.

## Process and credential containment

Each child receives a scrubbed environment. The frozen provider set includes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `AZURE_OPENAI_API_KEY`, `CODEX_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, and `OPENAI_API_KEY`, plus validated `execution.child_env_strip_extra`. `GH_TOKEN` and `GITHUB_TOKEN` cannot be configured as extra removals because GitHub authentication is governed separately.

POSIX children run in a new session/process group. On Windows, a trusted launcher waits for one bounded argv record; AWF assigns that launcher to a kill-on-close Job Object before releasing it to start the workload. Containment setup failure terminates the waiting launcher, so workload code never starts outside the Job. Timeout/cancellation terminates the contained tree. Every partition records cleanup outcome; partial or failed cleanup prevents PASS.

## Evidence and gate consumption

The result records authorization, clean-checkout proof, pinned inputs, executable, admission, fence release, seed/retries, resources/isolation, bounded output, and cleanup. It emits one name-sorted terminal record per partition. `AWF_VALIDATION_SEED` and `AWF_VALIDATION_ATTEMPT` bind each child. A timestamp-free serial-equivalence digest covers the sorted result set.

The production command requires external `--result-log`, `--result-receipt`, and controller-held `--receipt-key-file` paths. It exclusively creates and fsyncs a canonical result plus HMAC-SHA256 receipt, fsyncs both directories, refuses overwrite, and verifies both on read before reporting completion. Changed logs, receipts, or keys fail verification. Rejected runs also attempt signed durable evidence.

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

The regression covers workload authorization, clean checkout, exhausted capacity, serial/fallback leases, release failure, engine fencing, malformed broker state, retry-aware expiry, tamper detection, GitHub authentication, Windows containment, seed/retry equivalence, adapters/resources, cancellation, descendant termination, tuple/cwd/executable movement, credentials, aliases, caps, and bounded output. It uses no proprietary engine, GPU, or license server.
