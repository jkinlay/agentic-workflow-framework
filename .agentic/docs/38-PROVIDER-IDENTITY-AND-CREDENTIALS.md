# Provider identity and child credentials

Version 1.9.4. Provider identity checks are read-only observations. They never
switch accounts, select the first available connection, disclose tokens, or
grant publication/Jira authority. Use them alongside [publication readiness](37-PUBLICATION-READINESS.md).

## Child environment

Every production subprocess uses `agentic.child_process.child_env`. It removes
the built-in model-provider API-key variables case-insensitively, including keys
introduced through caller additions. `execution.child_env_strip_extra` adds
reviewed environment-variable names to that exclusion set. Empty names,
whitespace, invalid environment-name characters and case-insensitive duplicates
are rejected. The extra list cannot remove `GH_TOKEN` or `GITHUB_TOKEN`, which
the trusted GitHub host adapter needs. Ordinary process settings remain intact.

The scheduled-tick and review-loop entry points scrub their process environment before loading other
application modules. The PowerShell registration wrapper constructs the same
sanitized child environment rather than changing the parent shell. Its
configuration is read as data; no credential value is written to diagnostics.

### Isolated controller Codex home

The reference continuous-controller adapter requires an absolute, dedicated
`codex.isolated_home` disjoint from worktrees, run records and the outbox. For
every writer, critic and read-only probe it replaces any ambient `CODEX_HOME`
with that path and also uses `--ignore-user-config`. The user's desktop-app
`config.toml`, plugins, MCP servers and notify hooks therefore cannot enter the
effective launch configuration. Per-name disable overrides are forbidden: when
a user entry disappears, such an override can create an incomplete MCP server
and an `invalid transport` failure.

Provision auth in place by temporarily setting `CODEX_HOME` to the dedicated
directory and running `codex login`; do not copy the user's auth file. The
adapter requires a nonempty regular `auth.json`, but never reads its value into
argv, prompts, run records, logs or evidence. The adapter creates a private,
exact `config.toml` containing only AWF approval, network and Windows-sandbox
settings; model, reasoning effort and CLI sandbox mode remain explicit per
launch. A different pre-existing config fails closed. Use mode `0700` for the
home and `0600` for `auth.json` and `config.toml` on POSIX. On Windows, disable
inherited ACLs and grant full control only to the controller operator and Local
System. Keep the complete home outside evidence and backup collection roots.

`.agentic/launch-surfaces.json` inventories production non-Python launchers and
any explicitly reviewed Python exceptions. The static launch validator scans
production ASTs, follows supported import aliases, requires the shared
environment helper, and rejects shell execution and unsupported `os`/`pty`
launches. It also rejects missing, unregistered or stale launcher inventory
entries. This static gate does not execute the discovered launchers.

## GitHub identity

Accepted `github.repository_id` pins the numeric repository. Configure
`github.expected_actor_id`, `github.auth_profile`, or both; an optional
`github.expected_actor_login` adds a login check. A trusted observation contains
the actor ID/login, authentication profile, numeric repository ID/name,
readability, Git protocol and observation time. Wrong actor/profile/repository
fails with `IDENTITY_MISMATCH`; an unbound expectation reports
`IDENTITY_UNBOUND`. Read denial reports `ACCESS_DENIED`. The result names
expected and observed identities without including token values.

## Jira identity and binding

Accepted Jira configuration pins `cloud_id`, HTTPS `site`, `provider_project_id`,
`project_key` and `controller_actor_id`. Enumerate accessible connections first,
then match this complete identity. A matching project key in another cloud or
account is not interchangeable. The controller verifies provider identity
before issue lookup or mutation; disabled Jira performs neither.

Upgrades insert explicit null values for previously absent immutable Jira
bindings. Discovery remains possible, while writes fail with `IDENTITY_UNBOUND`
until the binding is reviewed. `workflow.py jira bind --connections OBSERVATION`
reports candidates and their digest without modifying configuration.

To prepare a configuration proposal, add `--confirmation CONFIRMATION --output
PROPOSAL`. The `awf-jira-binding-confirmation-1` record must select one exact
candidate, bind the candidate digest, name a configured trusted owner, and carry
fresh host-authentication evidence. Confirmation cannot predate discovery or
exceed fifteen minutes. The command creates a new proposal file and refuses an
existing output; applying that governance proposal remains a reviewed action.

`workflow.py preflight --github-identity OBSERVATION --jira-connections
CONNECTIONS` reports these host observations. Supplied files must come from the
trusted host adapter; candidate-authored identity assertions are not authority.

## Troubleshooting Codex sandbox setup

If a launch reports `helper_unknown_error`, `setup refresh had errors`, Windows
error 32, a file-in-use error or `invalid transport`, use the diagnostic's
named config source. The reference adapter reports `AWF isolated
CODEX_HOME/config.toml`; it does not echo raw stderr because provider output can
contain credentials. Acceptance waits for nonce-bound terminal proof. On
Windows the handoff, run and proof share the watcher PID even when a venv
launcher differs. Stderr is drained while only a 1 MiB tail is retained.

Stop that lane. Inspect the isolated home's path, ACLs and managed config. A
desktop, plugin or user-MCP path means the host bypassed the adapter. Do not
stop or signal a process the controller did not start, or add per-name
overrides. Re-provision the dedicated home, confirm auth and permissions, then
run an owner-approved disposable probe.
