# Reference Jira adapter configuration

Version 1.9.6. Review and SHA-pin the adapter configuration separately from the
accepted project configuration. The inert generated example is
[`reference-controller-adapter.json`](../examples/reference-controller-adapter.json).
Configuration stores environment-variable names, never credential values.

`jira.credential_type` is mandatory and selects exactly one protocol:

| Value | Authorization | REST base |
| --- | --- | --- |
| `oauth` | Bearer token from `token_env` | `https://api.atlassian.com/ex/jira/<cloud_id>` |
| `scoped_api_token` | Basic email:token from `email_env` and `token_env` | Same gateway |
| `unscoped_api_token` | Basic email:token from `email_env` and `token_env` | Configured `site` |

OAuth must omit `email_env`. Both API-token modes require a distinct named
email variable. Missing credentials and unsupported combinations refuse without
fallback or token inspection. The adapter strips both variables from child
environments, including arbitrary email-variable names. Never capture headers,
Basic encodings, HTTP debugging or raw exceptions as evidence.

`site` must be a credential-free HTTPS origin without even a trailing slash,
path, query or fragment. Pin `cloud_id`, `provider_project_id`, `project_key`
and `controller_actor_id`. The adapter reads the site's `/_edge/tenant_info`
anonymously and matches `cloudId`; `siteUrl` is unnecessary. OAuth additionally
matches the cloud ID and site URL in accessible resources. REST account and
project checks remain mandatory. No redirect is followed.

The authenticated account's `/myself.timeZone` must be an available IANA zone
for search. Enhanced GET `/rest/api/3/search/jql` explicitly requests
`issuetype,status,updated`, uses opaque provider tokens and requires `isLast`.
Each new traversal takes a fresh UTC observation. Its fixed JQL cutoff is the
start of that UTC minute, rendered as `yyyy-MM-dd HH:mm` in the account's zone,
with **exclusive** `updated < cutoff`. Ambiguous daylight-saving minutes refuse. The current partial minute is excluded;
counts describe that bounded query, not every recently updated issue.

Enhanced search is eventually consistent. A COUNTED result requires two
consecutive complete traversals with identical issue membership, status and
updated values (the adapter also compares issue type). This observes agreement,
not a transactionally consistent server snapshot. The first traversal emits no
countable items. Adapter continuation handles retain each opaque provider token
and traversal phase. A fresh cursor-less call replaces the observation.
Both traversals together must fit 20 search requests, 10,000 returned items,
16 MiB and 60 seconds; controller limits can be stricter. Duplicate IDs,
repeated tokens, malformed pages, missing fields, drift or exhausted limits
leave counts UNOBSERVED. No provider total is synthesized.

`status_map` values are **status IDs**, never transition IDs or display names.
The shipped PROJECT_CONFIG names (`Ready`, `In Review`, etc.) are placeholders,
not usable live IDs. In a three-status project, an explicitly reviewed mapping
can assign both logical `in_progress` and `in_review` to the same observed
In Progress status ID; PR_READY is then a no-op when already there. Alternatively,
retain the required mapping keys and set `lifecycle_writes.in_review: false`.
Do not silently invent an In Review status. Owner-closure holds still apply.

`merged_status_id` and `merged_transition_id` are separate identities. Before
any transition POST, the adapter GETs the issue's available transitions and
requires the selected transition's `to.id` to equal the planned status ID.
Absence or mismatch refuses before recording an attempt or writing. Existing
pre-read, durable attempt, readback, unknown-outcome and no-replay protections
remain; AWF-175 changelog pagination is deferred, including the 100-entry cap.

Local fake-HTTP tests do not qualify a tenant. Use the separately supervised
read-only probe; writes require separate owner approval.
