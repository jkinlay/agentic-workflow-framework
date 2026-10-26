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
