# AWF 1.9.3 change request: run-time fit and hardening follow-ups

**Date:** 24 September 2026 · **Revision 3.6: B–K frozen; item L (publication safety, rule consistency, external-resource admission) added** (owner addendum, 25 Sep 2026; see "Revision notes")
**Raised by:** the owner, after SL-2 (signal-lab PR #9) and the 1.9.2 release work (AWF PRs #15, #16, #17)
**Base:** the published `v1.9.2` tag (`9221dd3`, `MANIFEST.json` `29738ffb…2e6706`)
**Type:** minor release. It hardens the existing architecture and does not change the review-evidence model. No review floor, critic independence rule, specialist trigger, final-gate evidence, merge authorisation (other than F), reconciliation or Jira rule is weakened.
**Status:** design review passed; amendments made. Frozen and ready to enter the AWF implementation and review cycle.
**Out of scope:** the full speculative merge queue (candidate generations, drift classifier, queue backends, signed-digest batching). It is retained as an optional later mode in Appendix A of `AWF-v1.10.0-integration-queue-design.md`. The simplified **reviewed epic integration** is in scope as item **J** (Revision 3.4).

## Why

1. **The run cap is too tight for a normal ticket that finds defects.** SL-2 (tier 2, `public_api`) had three real defects and used 10 of its 12 runs.
2. **The publisher equality check breaks on Windows line endings.** The worker hashes CRLF working files, while git stores LF blobs.
3. **A shipped default route named a model the host refuses** (`gpt-6-astra`). Every high-risk ticket was refused until signal-lab #8 and #10.
4. **The correct way to settle a review run is undocumented.**
5. **No owner approval record was bound to the head.** The owner merged in GitHub after the gate report. GitHub also won't let an author approve their own PR, and every AWF PR was opened with the owner's own token.
6. **Releases were not published.** There were no tags, releases or release workflow, and the pin was recorded by hand.
7. **PR #17 left credential-hardening follow-ups.** Other credential names are not stripped, the enforcement test can be bypassed, and a worker claimed there was "only one launch site" without evidence (there were thirteen).

## Changes

### B. Run cap that fits tickets which find defects

- **New default.** The shipped default `max_runs_per_ticket` rises from 12 to 16. Upgraded projects keep their existing value; the migration note explains how to opt in through a governance PR.
- **Thresholds, in integer runs.** Let *used* be the settled plus reserved runs for the ticket, counting the one being reserved:
  - `WARN` when `used >= ceil(0.75 × cap)`, which is run 12 of 16;
  - `NEEDS_DISPOSITION` when `used >= ceil(0.90 × cap)`, which is run 15 of 16.
- **What NEEDS_DISPOSITION does.** It is a signal, not a gate. The reservation that crosses the threshold succeeds. The controller must then record a disposition request in the ledger and send it to the owner, with runs used, open findings and a recommended action (continue, rescope or park). Later reservations are **not** refused because of the threshold. Only the run cap and the existing gates refuse work.
- **Run records.** The ledger records each run's role (worker, fix, critic, specialist). `route_model.py summary --ticket T` prints runs and tokens by role.
- **Invariant.** The run cap and the review-round cap are independent controls. Raising run capacity never raises or relaxes the round cap. A fourth review round still needs the owner's round-cap decision, whatever the run count.

### C. Publisher equality on git trees

- **Required field.** `worker-result` gains `tested_tree`. It is required when `commit_route: PUBLISHER` and optional otherwise. `worker-result` also gains `changes`: an explicit list of `{path, action: added|modified|deleted}`.
- **What the candidate tree contains.** The candidate is **the base commit's tree plus exactly the declared changes**, never "everything under the worktree":
  - modified and added paths take their current worktree content and mode;
  - deleted paths are removed;
  - every other path keeps its base tree entry unchanged.
- **Consistency checks.**
  - A tracked file that differs from base (per `git status --porcelain`, which reads without writing) but is not declared is a **scope violation**.
  - A declared path that is unchanged is an error.
  - Undeclared untracked files are excluded and listed in the worker result as `ignored_untracked`.
- **Blob hashing.** For each changed path, `git hash-object --path=<path> <file>` without `-w` computes the blob ID. That command applies `.gitattributes`, EOL conversion and clean filters, and writes nothing.
- **Entry types.**
  - Executable bits map to modes `100755` and `100644`.
  - Symbolic links map to mode `120000`; the blob is the link target text.
  - Gitlinks (submodules, `160000`) are copied from base; a declared change to a gitlink is rejected.
- **Unsupported paths.** Paths containing newline or NUL characters are rejected with an explicit error rather than risk a wrong hash.
- **Tree hashing.** `agentic.gittree` builds canonical tree bytes in memory, recursively and in git's sort order. It lets git compute each tree ID with `git hash-object -t tree --stdin` (no `-w`), so the repository's object format (`git rev-parse --show-object-format`, SHA-1 or SHA-256) is honoured and never assumed.
- **Publisher check.** After committing, the publisher compares `git rev-parse HEAD^{tree}` with `tested_tree`. Any difference returns the work to the worker as a scope violation; it is never a publisher repair.
- **Test temp folders.** Worker prompts put test temporary directories (`--basetemp` and similar) outside the repository.

### D. Routes limited to models the host actually serves

- **Shipped defaults.** Default risk and specialist routes name only models in the reference host's observed-capability record: on ChatGPT-backed Codex, `gpt-5.6-sol` / high.
- **Provenance.** Each observation record carries the host identifier, host software and version, the observation method (a successful probe or a recorded refusal) and the time. A record older than a configurable age (default 30 days) is reported as stale.
- **Preflight row `route_models_observed`.** It WARNs for each configured route whose model is missing or refused, and reports stale observations. It never blocks INSTALLED.
- **Documentation.** `27-MODEL-ROUTING.md` gains the rule: a listed capability is a claim until observed.

### E. Settling runs: documented outcome shapes for every role

- **Documentation.** `27-MODEL-ROUTING.md` documents the outcome shape for worker, fix, critic and specialist (and, from 1.10.0, delta-review). Review runs always settle with `independent_review_passed: false`. Verdicts are recorded as review records.
- **Templates.** `route_model.py outcome-template --role {worker,fix,critic,specialist}` prints a valid template for each role.
- **Error text.** The `settle` self-attestation error points to that document section.

### F. Owner authorisation bound to an immutable provider identity, with an agent PR identity

- **PR author identity.** AWF-managed PRs are opened by an **AWF GitHub App** installed on the governed repositories, so the author is `<app>[bot]`, never the owner.
  - The owner creates and installs the App once and grants contents, pull-request and comment permissions only.
  - The controller and publisher use short-lived installation tokens, not the owner's personal token.
  - Preflight row `pr_author_identity` reports PASS when AWF-managed PRs will be authored by the App, and WARN when they would be authored by the owner.
  - No other person is involved: the App is a machine identity, and the owner remains the only human.
- **Accepted authorisation records.** Either of these counts:
  - **(a)** the existing signed AWF approval record; or
  - **(b)** a GitHub approving review satisfying the effective-state rule below.
- **Effective owner review state, for rule (b).**
  - Take the trusted owner's reviews on the PR, identified by **immutable** provider ID (numeric user ID or node ID, never login or display name).
  - Keep only reviews whose `commit_id` equals the head SHA.
  - Discard reviews in COMMENTED or PENDING state; they neither grant nor revoke.
  - The latest remaining review, by submission time, is the effective decision. It qualifies only if its state is APPROVED. A DISMISSED approval does not qualify, and a later CHANGES_REQUESTED supersedes an earlier APPROVED.
- **Ledger record.** The ledger keeps the provider, review ID, the reviewer's immutable ID, their login at the time (for information only), the head SHA and the submission time.
- **Reconciliation.** A merge whose merged parent has no qualifying record reconciles as `MERGED_WITHOUT_AUTHORIZATION`.
- **Contingency.** If setting up the App is awkward, F may move to 1.9.4 without weakening governance, because record (a) stays valid.
- **Dependency.** Item J's *enforced* mode, and any later queue mode, depend on the capability "AWF GitHub App identity" (F), not on a version number. J works in advisory mode without F. If F moves to 1.9.4, J's enforced mode moves with it.

### G. Release publishing with reproducible assets

- **Script.** `scripts/publish_release.py --commit SHA` runs from a clean checkout:
  1. verify the manifest;
  2. run the self-test, release hygiene and the portable distribution build and its tests;
  3. create an annotated tag `vX.Y.Z` whose message records the commit, the `MANIFEST.json` SHA-256 and each release asset's SHA-256;
  4. create a **draft** GitHub Release with the CHANGELOG notes, the same hashes, the validation counts and the assets. The owner publishes it.
- **Dry run.** `--dry-run` changes nothing remotely.
- **Reproducibility.** Release assets are byte-for-byte reproducible from the tagged commit. Archive generation normalises:
  - entry order (sorted, `/` separators);
  - timestamps (fixed, for example the tagged commit's time);
  - permissions (`0644`, and `0755` for executables);
  - compression settings;
  - platform metadata (no extra fields).

  Generated files are written in deterministic order and encoding.
- **No compression.** Release ZIPs use deterministic `ZIP_STORED` (uncompressed) entries, so no compression library's output is part of the reproducibility boundary. The size cost is accepted; the current assets are about 0.8 MB each.
- **Verification.** `verify-release --tag vX.Y.Z` rebuilds the assets from the tag and checks that their SHA-256 values equal both the tag's record and the published assets.

### H. Credential hardening follow-ups

- **Wider credential set.** Extend `PROVIDER_API_KEY_ENV_VARS` with `ANTHROPIC_AUTH_TOKEN`, `AZURE_OPENAI_API_KEY`, `GEMINI_API_KEY` and `GOOGLE_API_KEY`.
  - Add a validated `execution.child_env_strip_extra` list; it cannot name `GH_TOKEN` or `GITHUB_TOKEN`.
  - The PowerShell list is tested against the same constant.
- **Python enforcement.** It stays AST-based and narrow, covering the child-process invariant only:
  - it resolves `import subprocess as sp` and `from subprocess import run, Popen`;
  - it flags `os.system`, `os.popen`, `os.exec*`, `os.spawn*`, `pty.spawn` and `shell=True`;
  - every exception needs an allowlist entry with a written reason.
- **Non-Python scripts: a governed launch-surface inventory, not syntax detection.** In a shell script, running any command launches a process, so enumerating "launch constructs" would give false confidence. Instead:
  - every production `.ps1`, `.sh`, `.cmd` or `.bat` under `.agentic/` or `scripts/` is treated as a launch surface and must appear in a reviewed inventory, `.agentic/launch-surfaces.json`;
  - each entry records how its child processes lose the stripped credentials: a sanitised process start, a scrubbed Python entry point, or delegation to the approved launcher;
  - the self-test fails when a production non-Python script is not in the inventory, or an inventory entry has no matching file.
- **Uniqueness claims.** In worker and critic prompts, any claim that a location, caller or path is the "only" one, or that there is "no other", must cite the search command and its result count. Critics treat uncited uniqueness claims as unverified.

### I. Direct installation and multi-version upgrade (addendum, owner requirement, 24 Sep 2026)

**Why.** The owner requires that anyone can install 1.9.3 directly, and that any existing project on **1.8.0 or later** can upgrade directly to 1.9.3 in one step. Today the installer accepts only the immediately preceding version (`UPGRADE_FROM_VERSION = "1.9.1"`, plus a same-version re-pin). Releases before 1.9.2 were never published, so a step-by-step path isn't available in practice.

- **Fresh installation.**
  - `bootstrap_project.py --mode install` of 1.9.3 into a repository with no AWF, and the portable distribution's `install_awf.py` on a machine with no prior `awf` skill, must both succeed without any earlier version present.
  - Both are proven by tests. The documentation states that a fresh install never requires an earlier version.
- **Known-versions table.**
  - The release ships a table of every *recoverable* version from 1.8.0 to 1.9.2 (Revision 3.3: 1.8.3, 1.8.9, 1.9.1, 1.9.2). For each version it records the source commit, the `MANIFEST.json` SHA-256, and the receipt and configuration schema it used, all taken from git history.
  - The installer identifies the installed version only by verifying the project's receipt and managed files against this table, never from a version string alone.
  - An installation that matches no entry, or has locally modified managed files, is refused with a precise report. It is never upgraded on a guess.
- **Migration chain.**
  - Each adjacent step between table versions (Revision 3.3: 1.8.3→1.8.9, 1.8.9→1.9.1, 1.9.1→1.9.2, 1.9.2→1.9.3) is one pure, individually tested migration of `PROJECT_CONFIG.yaml`, `OPERATING_CONFIG.yaml` (where present) and the receipt.
  - `--mode upgrade` applies the steps from the installed version to 1.9.3, in order and in one run.
- **Migration rules (all versions).**
  - Every owner-set value is preserved exactly: bytes, comments, ordering and line endings.
  - Where a later version introduced a new **required** setting, the step adds it with that version's documented *upgrade-safe* value, never a new-adoption default. It is listed in the upgrade report.
  - `template.expected_workflow_version` ends at 1.9.3.
  - A setting that cannot be migrated deterministically stops the upgrade with a named question for the owner. Nothing is guessed.
- **Saved state.** `.agentic-state` content (the routing ledger schema, operating-change records, review and lifecycle records) is migrated by the same chain. Where a record format cannot be migrated, the record is archived read-only with a manifest entry, never deleted.
- **Managed and project files.**
  - AWF-managed files are replaced with 1.9.3's.
  - Project-owned files are never modified. A project-owned file that collides with a new managed path is reported, and it blocks the upgrade until the owner resolves it.
- **Dry run and PR.**
  - `--dry-run` prints the full plan: the detected version, the migration steps, the configuration diff per step and in total, the managed files added, changed and removed, and the state migrations.
  - A real upgrade produces one change set for a single reviewed upgrade PR through the normal AWF lifecycle.
- **Refusal below the floor.** Versions before 1.8.0 are refused with an explicit message naming the floor.
- **Documentation.** `MIGRATION-to-v1.9.3.md` covers every supported starting version, with per-version notes. The per-step guides stay for reference.

| ID | Criterion | Validation |
| --- | --- | --- |
| AC14 | A fresh `--mode install` into a repository with no AWF, and a fresh portable install with no prior `awf` skill, both succeed and verify, with no earlier version present. | Integration tests in clean temporary environments |
| AC15 | For **every version in the known-versions table** (1.8.3, 1.8.9, 1.9.1, 1.9.2), a fixture repository installed from that version's historical commit upgrades directly to 1.9.3. Owner-set values are byte-identical. Only the documented new settings and the version line differ. The installation verifies as INSTALLED/CONFIGURED. | Fixture matrix, one fixture per version |
| AC16 | For every starting version, the direct upgrade gives the same configuration bytes, managed-file tree and migrated state as applying the single steps one by one (path independence). | Fixture matrix |
| AC17 | Each of these is refused with a precise report and makes no changes: an unrecognised installation (including an in-range version not in the table), locally modified managed files, a version before 1.8.0, a non-deterministic setting, and a project-owned file colliding with a new managed path. `--dry-run` writes nothing. | Unit and integration tests |

### J. Reviewed epic integration (owner decision, 25 Sep 2026; Revision 3.4)

**Why.** A reviewer proposed a shared feature branch per epic, so agents can see each other's work and tests run on the combined state. The owner added two requirements. The human reviews each task's own branch, never the combined branch. And merges happen in series, so the order is explicit. This replaces the full queue for our scale. The full specification is in `AWF-v1.10.0-integration-queue-design.md`, Revision 4, main body.

- **Integration branch.** The steward rebuilds `awf/int/<epic>` deterministically: `T` plus each admitted task head in merge order `O`, joined with merge commits. It is evidence only. Nothing merges from it into `T`.
- **Admission.** One head at a time, under a lock. The tests run after each admission. A failing or conflicting head is removed and marked `INTEGRATION_FAILED` or `INTEGRATION_CONFLICT`, and it goes back to its own worker. Blame follows order, never timing, and no agent edits another ticket's code.
- **Task PRs.** Each task PR targets `T`, or its declared dependency's branch (a stacked PR), and contains only its own commits. Review is unchanged and bound to `head_r`.
- **Merge in series.** Only the front of `O` can merge. It needs:
  - valid review evidence;
  - a green integration build on the current `T` that starts with exactly `(PR, head_r)`;
  - a path-overlap delta review where one is required;
  - owner authorisation.
- **Order.** The owner can reorder or skip PRs, and the steward rebuilds and retests. A stalled front PR moves behind the next independent PR. Dependencies are never reordered past what they depend on.
- **Workers.** Before designing a task, a worker reads the integration branch as untrusted context. It declares `depends_on` when it uses unmerged code, and otherwise branches from `T`.
- **The `awf-integration` check.** It is advisory before F. With F it is enforced: it is required from the App's integration ID, and activation refuses if that cannot be verified. Merge commits only.
- **Steward.** Deterministic, with no model route. It never writes task code, never merges to `T`, and opens a revert PR if the target's post-merge CI goes red.
- **Defaults.** `integration.mode: serial` for every project. Epic mode is enabled per epic by an owner instruction. Branch protection on `T` is part of the rollout.

| ID | Criterion |
| --- | --- |
| AC18–AC29 | As defined in the integration design, Revision 4, "Acceptance criteria (1.9.3 item J)": deterministic builds; one admission at a time with blame by order; conflict handling; the front-of-order gate; rebuild triggers and prefix search; reorder, skip and stall; stacked PRs and retargeting; path-overlap delta review; own-commits-only PR diffs; steward limits and revert PRs; defaults and App binding; worker guidance. |

### K. Project-conversion and activation hardening (owner addendum, 25 Sep 2026; Revision 3.5)

**Source.** Converting the HFT Strategies project from a ChatGPT project to a Codex desktop project exposed these defects. They were hit while installing, configuring and activating AWF 1.9.1 on Windows. The owner confirmed that K1, K2, K3 and K10 still exist at the 1.9.3 preparation head `b1cbf5e`:
- `providers/github.py:356`: the hard-coded API header.
- `providers/github_status.py:113`: accepted-checkout verification uses raw bytes.
- `providers/github_status.py:273`: merge verification uses REST only.
- `operating.py:328`: read-only inspection takes the writer lock.
- `scripts/self_test.py:174`: test output is buffered until the run ends.

**Objective.** No audit can prove a failure will never recur. The release therefore aims to:
- close every observed class of failure;
- make any remaining environmental failure diagnosable in one run;
- add end-to-end regression tests that reproduce the HFT host conditions.

**Release blockers (owner's priority list).** K1, K2, K3, K4, K5, K6 and K13 (Jira writes). AC42 is a **mandatory release gate**.

#### K1. Remove the hard-coded future GitHub API version and make merge identity robust (P0)

AWF 1.9.1 sends `X-GitHub-Api-Version: 2026-03-10` on every `gh api` request. For the already-merged adoption PR, the REST response returned `merge_commit_sha: null`. GitHub's GraphQL-backed `gh pr view` returned the valid merge commit. Status therefore reported `adoption: UNOBSERVED` and blocked ACTIVE.

- Use a documented, supported API version, set in one central place rather than in the request helper. The worker confirms the version against GitHub's REST documentation at implementation time.
- Treat a null REST merge identity as an incomplete provider observation, not as proof that the PR has no merge identity.
- Fall back to a second official GitHub surface: GraphQL `mergeCommit.oid`, or an equivalent query the provider supports.
- Before accepting the fallback identity, cross-check the repository ID, base ref, merged state and time, and ancestry.
- Reject non-null identities that disagree between provider surfaces.
- Record which provider surface and API version supplied the accepted identity.
- Activating an ordinary project must not require a custom `gh` wrapper.

#### K2. Compare accepted checkouts using Git canonical objects, not working-tree bytes (P0)

AWF reads `.github/CODEOWNERS` from the filesystem and computes a blob ID from those raw bytes. On Windows, `core.autocrlf=true` turned the LF blob into CRLF in the working tree. The repository was clean and `HEAD` held the correct accepted blob, yet AWF reported `Accepted default checkout differs at .github/CODEOWNERS`. This is separate from item C: C fixes candidate-tree equality between worker and publisher, while K2 fixes accepted-checkout verification during adoption and activation.

- Compare the remote accepted tree with the object IDs in local `HEAD` and the index.
- Where the current working content must be checked, compute the would-be Git blob using Git's attribute and filter rules, for example `git hash-object --path=<path> <file>` without `-w`.
- Separately require the relevant paths to be clean according to Git. Do not mistake an end-of-line conversion at checkout for a content change.
- Keep byte-exact filesystem verification for immutable AWF release files where the installed manifest requires it.
- Choose one boundary and keep it consistent: either add every raw-verified project-owned text path, including CODEOWNERS, to the installed `.gitattributes`, or stop raw-verifying those paths.
- Diagnostics distinguish four cases: a dirty file, a Git attribute conversion, a mismatch with the remote tree, and a missing path.

#### K3. Make status, validate-config and operating show genuinely read-only (P0)

`inspect_operating()` calls `load_operating()`, which takes the exclusive writer lock and may create `.agentic-state/operating/lock`. Under the Windows restricted-token sandbox this failed with `Errno 5`. A valid configuration was then reported as REJECTED, and the project fell back to INSTALLED.

- `status`, `validate-config`, `preflight` and `operating show` must not create directories, create or open a writable lock, or otherwise change the filesystem.
- Add a read-only snapshot path, using pinned reads with before-and-after identity checks or another optimistic consistency protocol.
- Keep the exclusive lock for commands that change operating state.
- A pending journal, or an observed concurrent change, must fail safely with a precise state and without needing write access.
- Classify an operating-system access failure as `ACCESS_UNAVAILABLE` or `UNOBSERVED`, not as invalid project configuration.
- Keep the Windows error code and the affected path in a safe diagnostic.

#### K4. Report every activation blocker in one run, and add a strict activation exit mode (P0)

The activation observer stops at the first exception. Fixing the merge-identity observation only revealed the CODEOWNERS problem, and fixing that revealed further host conditions. Discovering blockers one at a time is the main reason a simple conversion took hours.

- Evaluate independent activation stages separately and return a structured `checks` collection.
- Return every blocker that can safely be discovered in the same run, each with a stable code, stage, evidence and exact remedy.
- Distinguish `INVALID`, `MISMATCH`, `UNAVAILABLE`, `UNOBSERVED` and `NOT_APPLICABLE`.
- Keep the highest state actually established. An unavailable observation must not rewrite an accepted configuration as REJECTED.
- Add `status --require-active`, which exits non-zero unless the final state is ACTIVE.
- Add an activation or doctor summary that lists all blockers first, then prints the single next command.
- Keep the ordinary read-only status report, but make its exit codes explicit.

#### K5. Make release trust durable and activation self-contained (P0)

AWF 1.9.1 had no usable published v1.9.1 release or tag, and its portable host skill could not be built from the public repository. HFT therefore depended on an approved source extracted to a temporary directory and a custom, temporary GitHub compatibility executable. That is not acceptable as a steady-state way to activate.

- A 1.9.3 release cannot be published until the portable host skill builds, installs and verifies on a clean Windows host.
- The published release contains the release archive, the manifest digest and the portable skill assets needed for durable host trust.
- Once the host skill is installed, `workflow.py status` establishes release trust without a temporary source tree or temporary wrapper.
- Store host trust outside the candidate repository, with an atomic receipt and a complete verified inventory.
- Print the resolved Codex home and host-skill path. Never build paths by joining strings in a way that can drop a separator.
- Copying a second framework implementation into the governed project stays prohibited.

#### K6. Enforce clean, internally consistent release sources (P0)

During diagnosis, an in-progress source checkout showed inconsistent version claims across the Python source, the README and manifest material, and the released version on the remote. A development checkout is legitimate, but it must never be mistaken for a trusted release.

- Release publication runs only from the exact clean commit being tagged.
- These must all agree: VERSION, the workflow version, schema constants, README, changelog, the portable-skill version and the manifest header.
- The manifest is regenerated from that commit and verified before tagging.
- Untracked files, tracked modifications, stale generated files or any version disagreement block publication, with a complete report.
- The release workflow publishes the tag, the draft release and independently reproducible checksums, as item G describes.

#### K7. Support verified archive extractions as release sources (P1)

The approved 1.9.1 source was an archive extraction with no `.git` directory. An activation procedure wrongly required `git rev-parse HEAD` and stopped, even though AWF's manifest-and-pin trust model can verify an extracted tree.

- Document and test that `--release-source` accepts a closed, manifest-verified archive extraction with no `.git`.
- The archive trust path gains no Git-checkout precondition.
- If the caller asks for Git provenance, it is a separate, optional check. Explain that pinned archive verification does not need it.
- Test extracted-directory inputs and release ZIP inputs separately.

#### K8. Canonical Windows runtime discovery and copy-and-paste-safe commands (P1)

The migration repeatedly used wrong paths, such as `.agentic.venv\Scripts\python.exe` and `.agentic\scripts\workflow\.py`. The real runtime is `.agentic\.venv\Scripts\python.exe` and the script is `workflow.py`. Markdown link and escape transformations also produced invalid PowerShell commands.

- Add `workflow.py doctor`, or an equivalent bootstrap command, that finds and prints the exact absolute installed interpreter and entry point.
- Emit a single-line PowerShell command, both as structured output and as plain text, with no Markdown links, backslash escaping or line continuations.
- Never insert a separator before `.py`, and never drop the separator between `.agentic` and `.venv`.
- Add documentation lint and tests that run every published Windows command from a path containing spaces.
- Prefer response files or JSON output for long commands with hashes and paths.

#### K9. Fix false PASS results in Windows host preflight (P1)

The PowerShell execution-policy probe returned an error saying its security module could not be loaded. Host preflight still reported PASS: it treated any return code other than `None` as a successful observation, and only searched the output for policy words.

- A non-zero exit from a child process is WARN or SKIP, never PASS.
- Record the executable, the exit code and a bounded diagnostic category.
- Parse execution-policy output only after a zero exit and a check that the rows have the expected shape.
- Apply the same rule to every child command in preflight.
- Test a module-load failure, missing PowerShell, a timeout, a non-zero exit and valid output.

#### K10. Stream self-test progress and phase timings (P1)

The 697-test self-test looked hung. `TextTestRunner` wrote to an in-memory `StringIO`, and the full log was printed only when the suite finished. The first run was interrupted after about a minute, although the suite later passed. The same buffering left the PR #18 critic without a result after its 1,800-second limit.

- Emit a start record immediately, then periodic heartbeat and phase progress to stderr.
- Time discovery, syntax, documentation, release hygiene and the test suite separately.
- Keep the final machine-readable report deterministic and separate from progress output.
- On interruption or timeout, report the last completed phase and the test identity where available.
- Document a quick installation check separately from the full release self-test. Never label a quick check as a full pass.

#### K11. Encoding-safe status output on Windows (P1)

An em dash in the status line was rendered as a replacement character in one PowerShell path. Machine consumers must not depend on the active console code page.

- Use ASCII for the stable one-line status, or explicitly set UTF-8 output on supported Python and Windows hosts.
- Make JSON output ASCII-safe, or guarantee UTF-8 bytes regardless of the console.
- Test the command line under a legacy Windows code page and with output redirected.

#### K12. Bind GitHub observations to the intended account and repository access (P1)

The machine had two authenticated GitHub accounts. The wrong account and SSH protocol were active at first, and that produced `Repository not found` for a private repository the intended account could access.

- Preflight the authenticated GitHub actor by immutable provider ID plus login.
- Verify read access to the configured numeric repository ID before adoption or activation.
- Report the observed account and protocol without exposing tokens.
- Allow an expected actor or profile to be configured where a host legitimately uses several accounts.
- Refuse an identity mismatch before attempting any repository change. Never switch accounts automatically.

#### K13. Bind Jira routing to immutable site and actor identities (P0 for writes; P1 for reads)

The host exposed two Atlassian sites and identities. Generic Jira discovery kept selecting the employer site instead of the personal HFT site. AWF configuration records only a site URL and project key, which is not enough for deterministic connector routing.

- Extend the Jira configuration with an immutable cloud or resource ID and the expected account ID. Keep the site URL and project key as human-readable bindings.
- Resolve the connector by cloud or resource ID, never by taking the first accessible Atlassian connection.
- Preflight the observed actor, cloud ID, site URL, project key and browse permission.
- Before every write, require every immutable binding to match, and keep the existing read-before and read-after rule.
- A wrong site or account returns `IDENTITY_MISMATCH` before any issue lookup or change.
- Support several Atlassian connections without mixing credentials or project scopes.

#### K14. Host workspace and external-resource preflight (P1)

Adding both a repository and a mapped network drive as project source folders made the Windows restricted-token sandbox refuse to create processes, because it could not enforce two separate writable roots. In other runs the mapped drive was missing from the process token, and the UNC path mapped differently.

- Distinguish the single writable repository root from external read-only resources.
- Add project-owned declarations for external resources: a logical name, the local or mapped path, the canonical URI or UNC path, the required access (`read` or `write`), the sensitivity and a bounded validation probe.
- Host preflight reports three things separately: whether processes can be launched, whether the repository is writable, and the state of each external resource.
- Never advise adding a read-only data estate as another writable project source root.
- For mapped drives, report `mapping absent`, `access denied` and `path not found` separately, and show the configured canonical path.
- Never conclude that a resource is absent because one alias, or one provider-specific listing option, failed.

#### K15. Deterministic project handoff snapshot (P1)

The move between ChatGPT and Codex lost or blurred several facts: repository identity, AWF state, Jira routing, data-root mappings, completed tickets and current blockers. Agents repeatedly rediscovered stale facts and retried setup that had already been done.

- Add a read-only `workflow.py handoff` command that produces JSON and Markdown from verified observations.
- The snapshot includes:
  - the repository path, origin and numeric ID;
  - the branch and head;
  - the installed AWF version, current state and trust basis;
  - the adoption PR and its merge;
  - the operating hash and routes;
  - the Jira cloud, project and actor bindings;
  - declared external resources;
  - explicit blockers.
- It contains no credentials, tokens or raw sensitive data.
- Mark each field as verified, configured, user-asserted or unavailable, with the time it was observed.
- A receiving host runs `workflow.py doctor --handoff <file>` to compare the snapshot, rather than silently accepting stale state.
- The snapshot grants no execution, merge or Jira authority.

#### Relationship to existing 1.9.3 items

- **Item C** (publisher equality on Git trees) is still required. It complements K2 but does not replace it.
- **Portable-skill packaging** (1.9.2) and the **fresh-install and upgrade matrix** (item I) cover part of K5. An explicit activation test after installation is still required.
- **Item G** covers most of K6. K6 adds the version-consistency and clean-source assertions.
- **Route-capability work** (item D) does not cover provider accounts, Jira resources or external filesystem bindings.

#### Controller notes (for implementation)

1. **Confidentiality.** The prose above names private projects because it records where the defects came from. Nothing committed to the public repository may carry a private project's name, identifiers, paths, Jira keys, cloud IDs, account IDs or data. That covers code, tests, fixtures, generated docs and changelog alike. "HFT-shaped" means a *synthetic* fixture with the same host conditions.
2. **K13 and upgrades (item I).** The new immutable Jira bindings cannot be derived deterministically during an upgrade. The upgrade therefore adds them as unset, and never guesses. Until the owner binds them, Jira **writes** are refused with `IDENTITY_UNBOUND`, while reads and all other work continue. A new command, `workflow.py jira bind`, discovers the candidate cloud and account IDs, shows them, and records them only after owner confirmation in a governance record. This keeps upgrades non-blocking and writes safe.
3. **Windows proofs.** AC31, AC34, AC36, AC37, AC40 and AC42 need a real Windows host. The evidence comes from two sources:
   - the owner's Windows machine, using an isolated temporary `USERPROFILE`, `CODEX_HOME` and skills root;
   - a GitHub Actions `windows-latest` job in the public repository.

   Provider identities (AC30, AC39) and mapped drives (AC40, where possible via `subst`) use recorded fixtures, never live private accounts.
4. **Existing critic problems addressed.** K3 and K10 also fix the read-only-sandbox and silent-self-test problems that left the PR #18 critic's round 1 INCOMPLETE.

#### Acceptance criteria (K)

| ID | Criterion |
| --- | --- |
| AC30 | **GitHub merge-identity fallback.** A merged adoption PR fixture whose REST response has `merge_commit_sha: null`, and whose GraphQL response holds the correct merge OID, reaches `MERGED_VERIFIED` after the repository, base and ancestry checks. Conflicting identities fail closed. If both are absent, the state stays UNOBSERVED. |
| AC31 | **Windows checkout conversion.** With `core.autocrlf=true`, LF blobs in Git, `* text=auto` and a CRLF CODEOWNERS working file, a clean accepted checkout reaches ACTIVE. A real normalised content change fails, naming the exact path and the class of mismatch. |
| AC32 | **Read-only status.** In a valid configured project where the process can read `.agentic-state` but cannot create or write to it, `validate-config`, `operating show` and `status` complete without any filesystem change. A concurrent change or pending journal is detected without taking a writer lock. |
| AC33 | **Complete diagnostics and exit codes.** A fixture with at least three independent blockers reports all three in one run, with stable codes. `status --require-active` exits non-zero, and exits zero only for ACTIVE. |
| AC34 | **Durable clean-host activation.** On a clean Windows user profile: install the published 1.9.3 host skill, install or upgrade a fixture project, merge its adoption PR fixture, and reach ACTIVE. No temporary release tree, compatibility executable or second project implementation is used. |
| AC35 | **Archive source without Git metadata.** An independently pinned release extraction with no `.git` directory is accepted by `--release-source`. A changed member, or a wrong manifest pin, is refused. |
| AC36 | **Copy-and-paste-safe Windows commands.** Every generated adoption, verification, validation, status and operating command runs unchanged in PowerShell from a repository path containing spaces. Each generated command contains the resolved `.agentic\.venv\Scripts\python.exe` and `.agentic\scripts\workflow.py` paths. |
| AC37 | **Honest preflight and output.** A PowerShell module-load failure cannot report PASS. JSON and the one-line status stay parseable under a legacy Windows code page and when redirected. |
| AC38 | **Observable self-test.** The full self-test emits a start event and progress at a bounded interval, and keeps a deterministic final report. An interrupted run names the last phase and does not claim PASS. |
| AC39 | **Provider identity bindings.** With two GitHub accounts and two Atlassian connections, the expected identities succeed. The wrong GitHub actor, or the wrong Jira cloud or account, fails before any change, reporting both the observed and the expected immutable identities. An upgraded project with no Jira bindings refuses writes with `IDENTITY_UNBOUND` and still allows reads. |
| AC40 | **Split-root Windows host.** A fixture with one writable repository and one declared read-only mapped or UNC data root launches commands and validates both roots. A configuration that asks the restricted-token host for unsupported split writable roots gets one precise preflight remedy before normal work begins. |
| AC41 | **Handoff continuity.** A handoff exported on host A and checked on host B keeps the verified repository, AWF, Jira and resource identities, reports changed or stale facts, and grants no authority. |
| AC42 | **End-to-end HFT-shaped upgrade: mandatory release gate.** Start from a real 1.9.1 installation fixture on Windows with `core.autocrlf=true`, a project-owned CODEOWNERS file, operating history, several provider accounts and an external read-only resource. Then: (1) upgrade directly to 1.9.3; (2) run installation verification, configuration validation and the self-test; (3) merge the adoption or upgrade PR fixture; (4) reach ACTIVE in one status run with no compatibility shims; (5) emit a complete handoff snapshot; (6) leave project-owned values and external data unchanged. |

#### Evidence in the 1.9.1 implementation (still present at `b1cbf5e`)

- `.agentic/lib/agentic/providers/github.py`: `_gh_get()` hard-codes the future API header (line 356).
- `.agentic/lib/agentic/providers/github_status.py`:
  - `installed_bytes()` reads project-owned CODEOWNERS bytes from the filesystem, and `accepted_blobs()` hashes those raw bytes (line 113);
  - merge verification accepts only the REST `merge_commit_sha` field (line 273);
  - the activation observer stops at the first exception.
- `.agentic/tests/test_adoption_status.py`: the main fixture forces `core.autocrlf=false` and supplies a non-null merge identity, so neither production failure is represented.
- `.agentic/lib/agentic/operating.py`: `inspect_operating()` reaches `_snapshot()` through the exclusive writer lock (line 328).
- `.agentic/lib/agentic/host_preflight.py`: the execution-policy row does not reject a non-zero child exit before parsing output.
- `.agentic/scripts/self_test.py`: the test runner writes to `StringIO` and prints it only when the suite finishes (line 174).
- `.agentic/lib/agentic/cli.py`: JSON output is non-ASCII and no Windows output encoding is set.
- `.agentic/schemas/project-config.schema.json`: Jira has a site URL and project key, but no immutable cloud, resource or account binding.

### L. Publication safety, rule consistency and external-resource admission (owner addendum, 25 Sep 2026; Revision 3.6)

**Source.** Further HFT operations on AWF 1.9.1: publishing three completed streams, working against a mapped research-data estate, and applying the repository rules. **Most urgent regression:** a sensitive value added in commit 1 and deleted in commit 2 must still block publication (AC43).

**Release blockers:** L1–L5 (P0). L6–L12 are P1 operational hardening.

#### L1. History-aware publication safety (P0)

- Scan every commit and patch in `base..head`, including deleted lines, not just the final tree. A later redaction commit must not make a contaminated branch "publication safe".
- The scan also covers commit messages, and the PR body before it is posted (see L9).
- `git diff --check` checks whitespace only. It is never evidence of publication safety.

#### L2. Verified clean-history rewrite (P0)

For **unpublished** branches, provide a governed squash or rewrite operation that:
- preserves the final tree exactly (`HEAD^{tree}` is unchanged, checked with the item C machinery);
- produces the intended number of commits;
- runs the L1 scan over the rewritten history;
- confirms the old commits are no longer reachable from any branch or tag;
- confirms the branch does not exist on the remote.

A branch that has already been published is refused. Rewriting published history is the owner's decision and is out of scope.

#### L3. Publication readiness before dispatch (P0)

Before a stream is assigned, verify that its completion path works:
- repository authentication;
- the remote is reachable;
- the branch name is eligible;
- the rules that apply;
- permission to push;
- permission to create the draft PR.

AWF must never finish three streams and only then discover that none of them can publish. A failed check refuses dispatch and reports the precise blocker. It never silently degrades.

#### L4. Consistency between the ruleset and the configuration (P0)

The shipped ruleset template (`templates/awf-main-ruleset.json`, with the provider defaults in `providers/github.py`) hard-codes squash and rebase merges. HFT's `PROJECT_CONFIG.yaml` configures merge commits, and item J and item C's post-merge parent check require them. AWF must do one of two things:
- generate the allowed merge methods from the accepted project configuration; or
- reject configuration or activation with a precise statement of the incompatibility.

It must never recommend applying a ruleset that disables the project's configured merge method.

#### L5. Capability-aware ACTIVE status (P0)

`ACTIVE` does not mean fully ready to operate. Status reports a capability matrix covering:
- local work;
- reading external data;
- branch publication;
- PR creation;
- independent review;
- Jira read and Jira write;
- merge execution.

Each capability shows a state, evidence and time observed. The matrix builds on K4's structured `checks`. HFT was ACTIVE while branch publication was unavailable, and that limitation should have been explicit before dispatch. Dispatch of a stream is refused when a capability it needs is not available (L3).

#### L6. External-resource registry and admission per stream (P1; extends K14)

- **Logical resources.** Projects declare logical resources, never hard-coded paths:

  ```yaml
  resources:
    raw_estate:
      access: read_only
      required_by: [EX-6, EX-19]
      host_mapping: operator_local
  ```

  The private mapping from `{raw_estate}` to a drive letter or canonical UNC location stays **outside Git**, in operator-local, untracked configuration.
- **Workspace roles are distinct.** Repository workspace: writable. External research data: read-only. Approved output root: governed separately. An external root is never added as another writable project root; doing so contributed to the Windows "split writable-root sets" failure.
- **Admission per task.** A drive mapping belongs to one Windows user, session and token. Seeing `X:` in an interactive terminal does not prove that a Codex worker can see it. Before dispatch, every task that needs the resource runs a bounded admission check: `Get-PSDrive`, a root listing, sample-file metadata and a read-only file open. There is never a write probe.
- **No inherited access.** Controller access does not imply worker or critic access, and parent-task access does not carry over to child tasks or worktrees. Each stream that needs the resource gets its own admission result before launch.
- **Owner-supplied UNC fallback only.** If the drive letter is invisible, AWF may use a canonical UNC locator that the owner has explicitly provided. It never guesses one from a drive label, historical documentation or another machine. The locator never appears in tracked reports, prompts, commits or PRs; they use `{raw_estate}`.
- **Private mapping fingerprint.** AWF keeps a host-private identity of the mapping outside Git, so it can detect a later remap. Tracked evidence records only `{raw_estate}`, the time of verification and the result.
- **State model.**

  | State | Meaning |
  | --- | --- |
  | `UNOBSERVED` | No check has run |
  | `SANDBOX_BLOCKED` | Codex refused to create the process |
  | `MAPPING_MISSING` | PowerShell ran but could not see the drive |
  | `PATH_NOT_FOUND` | The mapping exists but the requested path does not |
  | `ACCESS_DENIED` | Windows or the file server refused the executing identity |
  | `READ_VERIFIED` | The listing and a bounded file read succeeded |
  | `STALE` | An earlier verification belongs to another task or session |
  | `MAPPING_CHANGED` | The drive letter now points to a different resource |

  AWF must never translate `SANDBOX_BLOCKED` into `PATH_NOT_FOUND`.

#### L7. Classify the error at the right layer (P1; extends K9)

AWF distinguishes six outcomes:
- the Codex sandbox refused the command before it ran;
- PowerShell failed to start the process;
- the command exited non-zero;
- Windows denied access;
- the path was not found;
- a bounded read succeeded.

A command that was refused before PowerShell started is never reported as "the path does not exist".

#### L8. Scope and lifetime of permissions, and exact-command retry (P1)

- Record whether an external-directory permission covers one command, the current task, the current application session or the project. A new task never relies on an access result that has expired; it runs admission again (`STALE` until then).
- If the sandbox refuses a read, AWF requests a narrow read-only approval once, then retries **the exact same command**, so the evidence before and after is comparable.
- AWF cannot change Codex sandbox policy. It can discover the restriction once, classify it correctly, and resolve it before launching streams. A persistent project-level read grant is partly a Codex platform concern.

#### L9. Logical path aliases and comprehensive redaction (P1; the scanning half is P0 with L1)

- Tracked reports, prompts, commit messages and evidence use aliases such as `{raw_estate}` and `{output_root}`. Machine-specific paths and internal network mappings stay in operator-local, untracked configuration.
- Publication scanning covers:
  - current files;
  - commit messages;
  - every reachable patch (L1);
  - generated reports;
  - captured command output that is committed or posted;
  - PR bodies and PR comments.
- The deny-list comes from the operator-local mapping and project declarations, so it catches the project's real private values without committing them.

#### L10. Evidence claims proportionate to the observation (P1)

AWF rejects conclusions that go beyond what was observed, both in worker results and in critic checks. Examples:
- searching for `*normal*` directories does not prove that normalised artefacts are absent;
- reading one byte proves the file is readable, not that its schema or data are valid;
- a bounded listing does not prove the estate is complete;
- a clean tip does not prove a clean history.

Concretely:
- **Bounded operations.** Network-share operations need an explicit depth, file limit, date range and timeout. An unbounded recursive scan of a mapped estate is refused.
- **Access is not validity.** Being able to read the data does not make it valid. Schema, provenance, manifests, hashes and quality evidence are recorded separately before data is declared usable.

#### L11. Owner-publication fallback (P1)

When policy requires the owner to publish, AWF:
- produces one exact, bounded command;
- keeps all stream state;
- observes the resulting remote heads;
- resumes draft-PR creation automatically.

It never reruns completed work or loses lifecycle state.

#### L12. Repository-rules decision at activation (P1)

During activation, AWF explicitly presents five things:
- the observed rules state;
- what that means for publication;
- a proposed ruleset compatible with the configuration (L4);
- the owner-approval action needed to apply it;
- a fresh observation after it is applied.

A `MISSING` rules state is never left as a hidden operational trap.

#### Windows-safe paths (with K8)

Commands use literal paths and fully support:
- drive letters;
- UNC paths;
- spaces;
- backslashes;
- long paths.

AWF generates single-line PowerShell where required, and never accidental forms such as `workflow\.py`.

#### Controller notes

- **Confidentiality, as for K.** The committed tests and fixtures use synthetic aliases, drive letters, UNC paths and ticket IDs (`EX-6`, not a real key). The private examples in the owner's text stay out of the public repository.
- **Rule already applied to this release's own PRs.** From PR 2/12 on, the publisher's confidentiality scan covers every commit patch in `base..head`, the commit message and the PR body, not just the final diff.
- **L4 and J.** Item J requires merge commits. L4 fixes the template conflict before J lands.

#### Acceptance criteria (L)

| ID | Criterion |
| --- | --- |
| AC43 | **History-aware scan.** Commit A adds a sensitive locator and commit B removes it. Publication fails, naming the commit and path, until the history is rewritten. A sensitive value that appears only in a commit message or the PR body also fails. |
| AC44 | **Verified rewrite.** On an unpublished branch, the rewrite keeps `HEAD^{tree}` identical, gives the intended commit count, passes the L1 scan, leaves no branch or tag reaching the old commits, and confirms the branch is absent on the remote. A published branch is refused. |
| AC45 | **Readiness before dispatch.** Separate fixtures for missing authentication, an unreachable remote, an ineligible branch name, a push-blocking rule, no push permission and no permission to create PRs each refuse dispatch before any work starts. Each names the precise blocker. |
| AC46 | **Ruleset consistency.** With merge commits configured, the generated ruleset allows merge commits. A ruleset or configuration that would disable the configured merge method is rejected, with a precise incompatibility, and is never recommended. |
| AC47 | **Capability matrix.** An ACTIVE project whose branch publication is unavailable reports ACTIVE with `branch_publication: UNAVAILABLE` and evidence. Dispatch of a stream that needs publication is refused. |
| AC48 | **Sandbox versus path.** A drive visible interactively but blocked by the sandbox reports `SANDBOX_BLOCKED`, not "does not exist". A process-start failure, a non-zero exit, access denied and path not found each classify distinctly. |
| AC49 | **Exact retry.** After a narrow approval, retrying the exact same command succeeds and reports `READ_VERIFIED`, with comparable evidence before and after. |
| AC50 | **No inherited access.** When the controller has access and the worker does not, the worker's dispatch is refused before any work starts. |
| AC51 | **Staleness.** A new task holding an old successful receipt reports `STALE` until it is verified again. The permission scope and lifetime are recorded. |
| AC52 | **Remap.** When the drive letter points to another share, the result is `MAPPING_CHANGED`. |
| AC53 | **UNC leak.** A UNC fallback or private mapping that appears in generated Git content (file, patch, commit message or PR body) fails publication. |
| AC54 | **Read-only external root.** An external root declared read-only while the repository stays writable produces no split-writable-root failure (extends AC40). |
| AC55 | **Bounded scans.** An unbounded recursive scan of the estate is refused, and a bounded scan records its bounds. |
| AC56 | **Proportionate claims.** A single-byte read records accessibility as confirmed and data validity as unconfirmed. A claim of completeness or absence that goes beyond the bounds of the evidence is rejected. |
| AC57 | **Owner-publication fallback.** When policy requires the owner to publish, AWF emits one bounded command, keeps stream state, observes the new remote heads and resumes draft-PR creation, without rerunning completed work. |
| AC58 | **Rules decision at activation.** Activation with rules `MISSING` presents the observed state, the consequences, a ruleset compatible with the configuration, the approval action and a fresh observation after it is applied. |

**AC42 (release gate) is extended.** The end-to-end fixture must also:
- block a branch whose history contained a sensitive value that was later removed (AC43);
- admit a declared read-only external resource per stream, with a synthetic `{raw_estate}` (AC48–AC51);
- report the capability matrix (AC47).

The 1.9.5 AC42 harness treats every listed check as required: `NOT_COVERED`
is an explicit gate failure and cannot produce `PASS` or `gate_eligible`.
ACTIVE evidence is collected by the installed project's
`.agentic/scripts/workflow.py status` command. Until AWF-8 supplies the
AC48–AC51 admission exercise, AC42 therefore fails closed by design.

## Acceptance criteria

| ID | Criterion | Validation |
| --- | --- | --- |
| AC1 | Default `max_runs_per_ticket` is 16 for new adoptions; upgraded projects keep their value. With cap 16, WARN first appears on run 12 and NEEDS_DISPOSITION on run 15. Both reservations succeed; a disposition request is recorded. Run 17 is refused by the cap. The summary prints runs and tokens by role. | Routing unit and CLI tests |
| AC2 | The run cap and round cap are independent. With runs available, a fourth review round still requires the owner's round-cap decision. With rounds available, the run cap still refuses at 16. | Lifecycle tests |
| AC3 | `tested_tree`, computed without writing to `.git`, equals `git write-tree` for fixtures with CRLF/LF files, `.gitattributes` EOL rules, a clean filter, executable bits, a symbolic link, nested directories, and added, modified and deleted paths. The publisher check fails for any content, mode or path difference. | Unit tests on all platforms |
| AC4 | A PUBLISHER `worker-result` without `tested_tree` or `changes` fails schema validation; WORKER results remain valid without them. | Schema tests |
| AC5 | The candidate tree includes tracked modifications and deletions and declared new files. It excludes, and reports, undeclared untracked files. An undeclared tracked change is a scope violation. Newline or NUL paths are rejected. Gitlinks are copied from base. The object format is detected rather than assumed (SHA-1 and SHA-256 fixture repositories). | Unit tests |
| AC6 | `route_models_observed` WARNs for a missing or refused model and for a stale observation. Shipped defaults pass on the reference host record. Records include host, version, method and time. | Preflight unit tests |
| AC7 | Outcome templates exist for worker, fix, critic and specialist, and each settles without error against a reserved run of that role. | CLI tests |
| AC8 | Effective owner review state, by recorded API fixture: APPROVE then COMMENT is authorised. APPROVE then CHANGES_REQUESTED is not. CHANGES_REQUESTED then APPROVE is authorised. APPROVE then dismissal is not. APPROVE on head A, when the head is B, is not. The same login with a different immutable ID is not. The ledger stores review ID, immutable ID, head SHA and time. A merge without a qualifying record reconciles as `MERGED_WITHOUT_AUTHORIZATION`. | Provider and reconciliation tests |
| AC9 | `pr_author_identity` reports PASS when PRs are opened with the App installation token, and WARN when they would be authored by the owner. The controller never uses the owner's personal token to open PRs once the App is configured. | Preflight and provider tests |
| AC10 | Every release ZIP entry is `ZIP_STORED` with normalised metadata. Two independent clean builds from the same tagged commit, on Windows and Linux, produce byte-identical assets with identical SHA-256 values. `publish_release.py --dry-run` records them in the tag message and release body. `verify-release --tag` rebuilds and matches both the tag's record and the published assets. | Script tests (Windows and Linux) |
| AC11 | The wider credential set and configured extras are stripped from every production child launch. Python enforcement fails a fixture using an aliased import, `from subprocess import run`, `os.system` and `shell=True`. | Unit tests |
| AC12 | Every production non-Python script is in `.agentic/launch-surfaces.json` with a credential-removal mechanism. An unregistered script, or an inventory entry with no file, fails the self-test. | Self-test |
| AC13 | Full `python -B scripts/self_test.py` passes; manifest regenerated; release hygiene and word budgets met; portable distribution builds and its tests pass. | Self-test report |

**Boundaries.** No change to review floors, critic independence, specialist triggers, reconciliation or Jira rules. F adds an accepted authorisation record and an agent PR-author identity without removing the signed record. No existing test is weakened. `docs/` showcase files are untouched.

## Suggested order

Twelve PRs, with the owner's P0 release blockers first:

1. **C, E, D.** Merged as PR #18.
2. **I.** In progress.
3. **L1, L2, L9 (scanning).** Publication safety: history-aware scan, verified rewrite, redaction of commit messages and PR bodies (AC43, AC44, AC53).
4. **K1–K4 + L5.** Activation core and capability matrix (AC30–AC33, AC47).
5. **H + K12 + K13 + L3 + L11.** Credentials, provider identity, publication readiness before dispatch, owner-publication fallback (AC11, AC12, AC39, AC45, AC57).
6. **L4 + L12.** Consistency between ruleset and configuration, and the rules decision at activation (AC46, AC58).
7. **B + G + K5 + K6 + K7.** Run cap and release trust (AC1, AC2, AC10, AC34, AC35).
8. **K8–K11.** Windows host diagnostics (AC36–AC38).
9. **K14 + L6–L8 + L10.** External-resource registry and admission, error layers, permission lifetime, proportionate claims (AC40, AC48–AC52, AC54–AC56).
10. **F.** GitHub App identity.
11. **J.** Reviewed epic integration (needs L4 merged first).
12. **K15 + AC42.** Handoff snapshot, then the extended end-to-end **release gate**. 1.9.3 is tagged only after AC42 passes.

F depends on the owner creating the GitHub App and may move to 1.9.4 without weakening governance. J ships in advisory mode if F has moved. AC42 does not depend on F.

## Revision notes

**Revision 3.6** (owner addendum, 25 Sep 2026):

- Added item **L**, L1–L12 with AC43–AC58.
  - L1–L5 are P0 release blockers: history-aware publication scan, verified rewrite, readiness before dispatch, ruleset/configuration consistency and the capability matrix.
  - L6–L12 cover external-resource admission and operational hardening.
- The most urgent regression, a sensitive value added then removed, is AC43. Publication safety moves to PR 3.
- AC42 is extended.
- The order is now twelve PRs.
- The history-aware scanning rule applies immediately to this release's own publisher steps.

**Revision 3.5** (owner addendum, 25 Sep 2026):

- Added item **K**, K1–K15 with AC30–AC42, from the HFT project conversion.
- The owner's release blockers are K1–K6 and K13 (Jira writes).
- AC42, the end-to-end Windows upgrade and activation, is a mandatory release gate.
- The owner confirmed that K1, K2, K3 and K10 still exist at `b1cbf5e`.
- Controller notes added:
  - no private identifiers in committed artefacts;
  - K13 upgrade behaviour: bindings are left unset, writes are refused with `IDENTITY_UNBOUND`, and owner-confirmed binding goes through `workflow.py jira bind`;
  - Windows evidence comes from the owner's host plus a GitHub Actions Windows job.
  - The owner approved the K13 upgrade behaviour and the GitHub Actions Windows job (25 Sep 2026).
- New order: nine PRs.
- B–J are otherwise unchanged.

**Revision 3.4** (owner decisions, 25 Sep 2026):

- Added item **J**, reviewed epic integration (AC18–AC29). It comes from a reviewer's shared-feature-branch proposal plus two owner requirements: humans review each task's own branch, and merges happen in series in an explicit order.
- The full speculative queue stays out of scope. It is kept as an optional later mode (design doc Appendix A).
- The order is now C, E, D → I → H → B, G → F → J (six PRs).
- B–I are unchanged.

**Revision 3.3** (owner decision, 25 Sep 2026, before implementing I):

- **Recoverable versions only.** A source search found verifiable artefacts only for **1.8.3, 1.8.9, 1.9.1 and 1.9.2**. Versions 1.8.0–1.8.2, 1.8.4–1.8.8 and 1.9.0 are not present in any repository history, release or distribution. The known-versions table therefore covers those four versions. The floor stays 1.8.0. A 1.8.x or 1.9.0 installation not in the table is refused as an **unrecognised version**, and the report asks for that version's distribution. It is never matched on a guess.
- **The table is data.** A version is added later by adding its entry and fixture, without code changes.
- **Provenance.** 1.8.9, 1.9.1 and 1.9.2 come from this repository's history. 1.8.3 comes from an installed-project snapshot, from which only AWF-managed files and schema shapes are taken; no project content enters this repository.
- AC15 and AC16 apply to every version in the table. AC17 adds an in-range but unrecorded version to the refusal cases.

**Revision 3.2** (owner requirement):

- Added addendum **I**: direct fresh installation, and a direct upgrade to 1.9.3 from any version since 1.8.0, via a known-versions table, a tested per-step migration chain, state migration, a dry run and one reviewed upgrade PR (AC14–AC17).
- B–H unchanged and still frozen.

**Revision 3.1** (third review: "ready with one small amendment"):

- G and AC10: release ZIPs use `ZIP_STORED` entries, so cross-platform reproducibility does not depend on a compression library.
- F: 1.10.0 depends on the App identity capability, not a version number.
- **Specification frozen for implementation.**

**Revision 3** (second design review, "passes with amendments"):

- B: the thresholds are now defined in integer runs (12 and 15 of 16). NEEDS_DISPOSITION is explicitly a signal, not a gate.
- C: the candidate tree is defined as base tree plus declared `changes`. Undeclared tracked changes are a scope violation, and untracked files are excluded and reported. Tree IDs come from `git hash-object -t tree` in the repository's object format. Symlinks, gitlinks and the rejection of newline/NUL paths are now specified. AC3 and AC5 are extended.
- F: exact effective-review-state algorithm and six API fixtures (AC8). The AWF GitHub App is the PR author identity, so the owner can approve without involving another person (AC9). F may move to 1.9.4.
- G: byte-for-byte reproducible assets with normalised archive metadata. `verify-release` rebuilds and matches (AC10).
- H: shell "construct detection" replaced by a governed launch-surface inventory (AC12).

**Revision 2:** the merge queue moved to 1.10.0. Added the 90% threshold and the run-cap/round-cap invariant, the required `tested_tree` computed without writing `.git`, observation provenance, templates for all roles, immutable identity for F, and asset hashes for G. Python enforcement made AST-only and narrow.

## Evidence

- SL-2 run record (project `reviews/sl2-run-record-2026-09-24.md`): 10 runs, 834,135 tokens, 3 real defects.
- AWF PR #17: 6 runs, 867,863 tokens. The critic refuted the single-launch-site claim twice, and the PowerShell script was the launch point the Python-only check missed.
- AWF PRs #15 and #16: round-cap disposition and follow-up, 1,835,877 tokens.
- signal-lab PRs #7, #8 and #10: the budget and route governance changes.
