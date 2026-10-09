# Independent Codex review

Version 1.9.4. AWF scales independent Codex critics; it does not ship a Claude
review engine, a publisher App, or a hosted model-review workflow. This guide
defines the qualified, operator-owned Codex review host. It is separate from
adoption: installing AWF does not enroll a pull request, start a scheduler, or
grant any merge authority.

## Capacity and independence

Native work starts with three active streams within the reviewed ceiling of
six. Each active stream has one separately scoped Codex reviewer context.
Workers and reviewers must not share a context, write lease, or owned path.
The host's observed capacity is authoritative; the configuration ceiling is
not evidence that those reviewers launched. Increase or reduce active streams
through reviewed operating choices; cap increases still require human
authorization. See [native coordination](24-STREAM-STARTUP.md) and
[model routing](27-MODEL-ROUTING.md).

For an enrolled pull-request loop, use the pinned `codex` executable and
separate worker and critic checkouts in
[host-config.example.json](../review-loop/host-config.example.json). One
enrolled loop tick owns one branch at a time, so it cannot race a native
writer. The host supplies the selected models, isolates credentials, enforces
quotas and records observed outcomes; the release never claims those host
properties from a configuration file alone. Each run records its effective model, optional policy-approved reasoning effort, CLI sandbox mode separately from any Windows sandbox override, and approved Codex overrides in `runs/RUN_ID/effective-config.json`. `--ignore-user-config` remains enabled; approved overrides are passed explicitly as `-c` flags and unknown override keys fail closed. Protected governance paths remain refused except for an AWF source checkout detected from its root manifest/source markers and explicitly enrolled with a reviewed Tier 3 `governed_source_paths` allowlist.

## Live qualification

Before a Codex review host may amend a PR or run on a schedule, retain current
evidence for sandbox isolation, credential separation, exclusive branch
ownership and one canonical host database. Relevant live execution also needs
configured required CI, trusted merge owners and observed default-branch
rules. Missing evidence fails closed for the live host but does not block
local adoption or preparation of a draft PR.

Use `review_loop.py qualify --confirm-disposable-pr` on a disposable
same-repository PR as specified in the [review-loop
runbook](22-AUTOMATED-REVIEW-LOOP.md). It launches concrete critic/worker
write, network, environment, agent-auth-file, branch-lease and database probes,
then writes an operator-local record without setting qualification. A readable
or unevaluable `~/.codex/auth.json` (or `$CODEX_HOME/auth.json`) is an explicit
blocking finding, not isolation. After inspecting `PASS`, the named operator
pins the record SHA-256 and sets the four flags. Loading requires that exact
record, matching operator/host controls, and age no greater than the configured
limit (maximum seven days). Bare legacy booleans/free-text evidence are refused;
migrate to the pinned record fields and rerun live qualification. Offline fakes
exercise the procedure but never qualify a host.

Run the [scheduled review-loop](22-AUTOMATED-REVIEW-LOOP.md) commands only
from a trusted runtime and protected state directory. Critics inspect the
current observed PR head, retain stable finding IDs and re-review after every
amendment. Findings, review completion and CI status never authorize a merge;
the final gate and human authorization remain mandatory.

For a new ticket, the first-draft bridge in
`agentic.review_first_draft` runs before this enrolled loop. The trusted
`review_loop.py first-draft` entry point wires the pinned worker, publisher and
GitHub host adapter together; it does not rely on a guessed PR number. It preserves the
same pinned worker route and exact allowed paths, requires a tested-tree
receipt, performs the history-aware scan on the prospective PR body before the
first push, durably freezes the publication base/head/body before that push,
creates one draft PR,
persists its number before snapshot, and enrolls only when the observed head
and base match. The host durably retains the original repository Git
configuration, remotes and hooks baseline throughout recovery and every
commit/push operation; changes or repository-local credential configuration
block publication. It rejects any tracked, staged, untracked or ignored
pre-worker residue, and requires the configured head branch to remain at the
provider base until the publisher makes its single child commit. Each
first-draft worker execution is counted in `max_agent_runs` before launch but
does not consume an amendment cycle; prepared-publication recovery does not
replay the worker. A host
publisher may commit the worker's tested tree when Git metadata is unavailable
inside the worker sandbox; any `HEAD^{tree}` mismatch is a scope violation and
must return to the worker. Recovery retains the reservation UUID and charges
each worker replay. A retained validated receipt and publisher plan let scan
or durable-record failures resume the exact unpublished local child. Recovery
looks up an uncertain PR in all states by the exact head, then reconciles the
remote ref before any exact-head push retry. It never calls PR snapshot with
`pr: 0`, reruns the worker for a prepared publication, or blindly creates a
second PR.
