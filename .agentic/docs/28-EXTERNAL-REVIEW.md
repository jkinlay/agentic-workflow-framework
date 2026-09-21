# Independent Codex review

Version 1.9.1. AWF scales independent Codex critics; it does not ship a Claude
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
properties from a configuration file alone.

## Live qualification

Before a Codex review host may amend a PR or run on a schedule, retain current
evidence for sandbox isolation, credential separation, exclusive branch
ownership and one canonical host database. Relevant live execution also needs
configured required CI, trusted merge owners and observed default-branch
rules. Missing evidence fails closed for the live host but does not block
local adoption or preparation of a draft PR.

Run the [scheduled review-loop](22-AUTOMATED-REVIEW-LOOP.md) commands only
from a trusted runtime and protected state directory. Critics inspect the
current observed PR head, retain stable finding IDs and re-review after every
amendment. Findings, review completion and CI status never authorize a merge;
the final gate and human authorization remain mandatory.
