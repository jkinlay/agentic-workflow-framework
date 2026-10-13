# Writer and critic provenance

AWF provenance makes the producer of every generated commit and critic verdict
explicit. It is evidence, not execution authority: a trailer, run log, or owner
record cannot grant scope, authenticate an actor, approve a review, or permit a
merge.

## Writer trailers

Every AWF-produced commit ends with exactly one canonical Git trailer:

```text
Writer: codex/11111111-1111-4111-8111-111111111111
```

or, for a reproducibly mechanical operation:

```text
Writer: script/manifest-regen
```

Codex run IDs are canonical lowercase UUIDs. Script names are bounded lowercase
portable identifiers. A Writer-like body line, duplicate trailer, unknown
namespace, non-canonical value, or Writer line outside the final Git trailer
block is invalid. `interaction.writer_commit_message` constructs a message with one
trailer and refuses non-mechanical work without a Codex run ID. It does not
create the commit. Publishers must supply the actual run UUID; a role name or
placeholder such as `writer-001` is invalid.

A `script/*` claim is accepted only when the commit is independently classified
as mechanical. The classifier recognizes two cases:

- A two-parent merge whose second parent is in the observed `origin/main`
  history and whose tree is byte-identical to Git's automatic merge result.
  Reproduction writes only to a disposable object database and reads the
  repository through an alternate-object binding. A conflicting merge passes
  only when every conflict is a declared generated output, the committed tree
  differs from the automatic tree only at those conflicts, and the generator
  reproduces every resolution.
- A commit changing only declared generator outputs. `MANIFEST.json` and
  `MANIFEST.md` are declared by default. A trusted caller must reproduce every
  changed output, and every committed tree entry must match its content, mode,
  and object type. A byte value means a regular non-executable `100644 blob`;
  an executable output must return an exact `mode`, `type`, and `content`
  mapping.

The script name alone never proves either case. Code, tests, configuration,
documentation, mixed changes, deleted outputs, non-generated conflicts,
unavailable reproduction, or any entry mismatch remain non-mechanical.

`provenance.check_commit_range` examines every commit in `base..head`. Its
result contains each SHA, changed-file inventory, parsed writer, mechanical and
run-log observations, status, and all refusal reasons. A malformed or absent
Writer trailer refuses that commit. A script tag on a non-mechanical commit also
refuses it.

## Run-log verification

For `codex/*`, a host may supply a verifier that checks the run exists and its
changed files match the commit. `MATCH` passes and `MISMATCH` refuses. `ABSENT`
or an unconfigured verifier is reported but does not fail unless
`require_run_logs` is true. Once a log is observed, a mismatch always fails.
This distinction lets installations without retained logs use trailers without
misrepresenting unobserved evidence.

## Critic binding

`validate_critic_run_binding` requires a critic run UUID and full reviewed-head
SHA, optionally checks the expected head, and checks any embedded critic
artifact/review binding. It renders `Critic run: <run id>` for a future verdict
record or provider comment. Missing runs, head drift, and contradictory nested
bindings fail. Verdict schema and merge-gate integration belong to the
subsequent AWF-49 split.

## Owner fallback override

A fallback is explicit and narrow. `record_owner_override` accepts only an
owner identity present in the caller's reviewed trusted-owner IDs and records:
who, when, why, PR number, and the exact covered commit SHAs. The sealed digest
detects mutation. The function writes to the supplied AWF state only after all
validation passes; `render_owner_override` produces the matching PR section.

Range checking accepts such a record only for its bound PR, only when the PR has
the exact `fallback: bot-written` label, and only when every covered SHA belongs
to the checked range. Uncovered failures still refuse. The result marks covered
failures `OVERRIDDEN` and names the override digest; there is no silent bypass.
The host remains responsible for authenticating the owner source and posting
the rendered record. Gate, schema, template, and generator wiring are part of
the follow-up split.

An existing record is never replaced in place. `supersede_owner_override`
validates the complete current chain, requires another trusted-owner identity,
and appends a version-2 record whose `supersedes` field names the current
record's sealed digest. It retains every previously covered commit. The
original record remains byte-for-byte unchanged; a stale, missing, cross-PR,
unauthorised, or tampered link refuses without changing state. Range checking
uses the latest verified record supplied by the controller.
