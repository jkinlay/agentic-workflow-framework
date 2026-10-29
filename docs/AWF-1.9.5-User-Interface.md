# AWF 1.9.5 - User Interface (summary)

The full User Interface Q&A preserves historical source wording, including supplied-snapshot `main/...` observations, rather than current-base verification. Its evidence is pinned to `7fcaeece364531da65ca1b8293ebd7643bf264ed`, whose tree identifies itself as `1.9.4`. Released 1.9.5 bytes, desktop execution and end-to-end operation remain **UNVERIFIED**.

## Interface and workflow

## Entry and command surfaces

Users request AWF work in a Codex host conversation using the installed `awf` skill and accepted project instructions. The sources establish no dedicated AWF desktop application, required desktop button, slash command or automatic specification runner.

The skill guides adoption and governed project work. Its `scripts/awf.py` exposes `locate` and `inspect` for release discovery. Installed `workflow.py` exposes governance and operating/controller interfaces. `review_loop.py` runs an explicitly configured PR review host with operations including `first-draft`, `enroll`, `tick` and `status`.

## Separate writer and critic runs

Implemented adapters construct fresh ephemeral `codex exec` runs with role-specific models, checkouts and result schemas. The PR host uses a read-only critic sandbox and a workspace-write worker sandbox, retaining inputs, configuration, logs and results. Changing a role label within the writer's conversation does not provide independent review.

Native coordination depends on actual host delegation facilities. ACTIVE does not start adapters or prove readiness; dispatch switches ship disabled and installation/upgrade requires quiescence.

## Adapting AWF to specification work

AWF's design-first trial separates a writer from an adversarial reviewer, but its critique is not a PASS/FAIL review round. Planning tools schedule verified ticket inventory; they do not write specifications. A document PR can reuse ordinary review machinery under an appropriate contract, but that is an inference rather than an observed end-to-end specification test.

The proposed three-pair specification protocol needs an accepted document-task procedure, independent runs, explicit counting and document-specific validation. Ordinary PR amendment counts do not mean three writer/critic pairs, and existing result schemas do not supply a simplicity score. The PR host protects `.agentic` paths by default; a project-owned output folder needs accepted scope. Standalone specification/critique files do not establish Git-bound PR closeout.

## Activation and resource admission

Use absolute installed canonical runtime/script paths and an explicit root:

```text
<installed-canonical-runtime> -B -I <installed-workflow.py> --root <repo-path> status --require-active --release-source <verified-release-source> --expected-manifest-sha256 <independently-approved-pin>
```

The verified release source must be outside the project; the manifest pin must be independently approved. Add `--adoption-pr <receipt-changing-pr-number>` when needed. Omitting both explicit source/pin arguments is supported only with a valid, complete, matching trusted host-skill installation receipt and verified inventory at `$CODEX_HOME/skills/awf` (default `~/.codex/skills/awf`). An arbitrary project receipt does not authenticate the publisher. Exit 4 means below ACTIVE; exit 2 means invalid input or no established state. Inspect capabilities separately: ACTIVE requires independent release trust and observed adoption acceptance, but does not establish adapter readiness or enable live capabilities.

Before dispatch, admit every required external resource for the exact task, principal and child application session using that task's own admission evidence. Parent-session access is not inherited. Missing, stale, changed or wrong-principal evidence must prevent launch.

## Proposed supervised procedure

1. Identify the installed skill, accepted instructions, permitted output folder and approved independent-run facility. Stop if the facility is unavailable.
2. Check installation status and capabilities separately; freeze readable snapshots, validation and completion criteria, and obtain child-specific resource admission.
3. Run Writer 1, Critic 1, Writer 2, Critic 2, Writer 3 and Critic 3 sequentially. Retain each round's specification, CSV, critique, findings and execution identity. The final critic supplies the custom verdict and simplicity score.
4. Apply explicit stop conditions and the three-pair limit. Return artifacts for owner approval. A document PR follows ordinary publication, review, merge and closeout.

## Known limitations

Release publishing is not supported on macOS in 1.9.5. The release step fails closed because the host lacks atomic no-follow release materialization primitives. Publish from Windows or Linux.

Post-#249, the reference adapter supplies the cycle CLI's planning operations and passes its adapter check on fresh installs and upgrades from 1.9.4 (#249 tests: `.agentic/tests/test_reference_controller_adapter.py:1652`, `.agentic/tests/test_reference_controller_adapter_install.py:133`, `:143`; Task 55 dry run, 25 October 2026 16:33 GMT). Successful current-controller operation, desktop permissions, independent-run facilities, live qualification and integrated release/upgrade acceptance remain UNVERIFIED. The supervised specification procedure is proposed and unexecuted; no automatic specification command is established.

## Further reading

[Install and adoption runbook](runbook/INDEX.md)

[Full User Interface text (repository-only; not included in the release ZIP)](https://github.com/jkinlay/agentic-workflow-framework/blob/8ad399943c8f5e4f17db72f3e321b9365ee27782/docs/showcase/AWF-1.9.5-User-Interface.md)
