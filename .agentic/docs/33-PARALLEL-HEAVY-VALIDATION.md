# Bounded parallel heavy validation

AWF 1.9.3 executes partitioned validation only from an exact reviewed candidate. It cannot merge, deploy, install, activate, upgrade, or mutate providers.

## Frozen execution identity

A version 3 plan binds:

- numeric repository ID, base SHA, head SHA, and tree SHA;
- the exact absolute working directory;
- one absolute executable path and file SHA-256 per partition;
- argv, timeouts, accepted exits, engine, framework, resource class, and named-resource quantities;
- one deterministic seed, bounded retry limit, and required process-tree, host-network-policy, and worktree isolation declarations.

Before launch, a trusted attestor proves the source root clean at the reviewed HEAD/tree. Execution uses a private snapshot from that Git object, so later worktree changes cannot affect dispatched bytes. Each executable is digest-checked under a Windows deny-write/delete handle or copied to a sealed POSIX artifact held through launch. Identity movement fails closed; `shell=False` is mandatory.

The version 4 review binds plan digest, cwd, argv, executables, resources, retries, and isolation. These remain claims until a trusted adapter finds that record digest in a formal provider review. Production read-only GitHub observations bind repository, PR tuple, immutable reviewer, APPROVED state, reviewed commit, and `AWF-HEAVY-VALIDATION-AUTHORIZATION-SHA256: <digest>`. Local `APPROVE` never authorizes work.

## Broker lease and serial fallback

Parallel execution requires all of these:

1. fresh capacity bound to the plan, config, and candidate tuple;
2. configured and observed worker, heavy/GPU, engine, and named resource capacity;
3. an actual broker lease bound to the complete request digest;
4. a positive fence and expiry covering dispatch attestation, snapshot creation, every partition/retry timeout, startup, two cleanup/exit waits, both reader joins, and terminal-barrier overhead;
5. exact lease release after every partition reaches a terminal result.

Zero worker, class, engine, or named-resource capacity starts no child. Broker-enabled work always needs an exact lease, including serial work. A denied, stale, failed, or unavailable parallel acquisition may use serial only after a separate serial request is granted. Every stale acquired fence is released before another request; release failure blocks execution. Failure to release the execution fence keeps the aggregate result at `FAIL`. Capacity observations alone never grant shared resources.

The broker validates every field, positive quantity, resource, unique monotonic fence, and engine identity/slot before and after mutation. Its OS-locked file and directory are fsynced. Malformed state fails closed. Configured and observed limits govern every lease.

## Process and credential containment

Each child receives a scrubbed environment. The frozen provider set includes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `AZURE_OPENAI_API_KEY`, `CODEX_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, and `OPENAI_API_KEY`, plus validated `execution.child_env_strip_extra`. `GH_TOKEN` and `GITHUB_TOKEN` cannot be configured as extra removals because GitHub authentication is governed separately.

POSIX children run in a new session/process group. On Windows, a trusted launcher waits for one bounded argv record; AWF assigns that launcher to a kill-on-close Job Object before releasing it to start the workload. Containment setup failure terminates the waiting launcher, so workload code never starts outside the Job. Timeout/cancellation terminates the contained tree. Every partition records cleanup outcome; partial or failed cleanup prevents PASS.

## Evidence and gate consumption

The result records authorization, checkout and snapshot proofs, fenced executable artifact, pinned inputs, admission, fence release, seed/retries, resources/isolation, bounded output, and cleanup. It emits one name-sorted terminal record per partition. `AWF_VALIDATION_SEED` and `AWF_VALIDATION_ATTEMPT` bind each child. A timestamp-free serial-equivalence digest covers the sorted result set.

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

The regression covers authorization, checkout snapshot and executable races, exhausted capacity, serial/fallback leases, release failure, engine fencing, malformed state, worst-case expiry, tamper detection, GitHub authentication, Windows containment, determinism, adapters/resources, cancellation, descendant termination, tuple/cwd movement, credentials, aliases, caps, and bounded output. It uses no proprietary engine, GPU, or license server.
