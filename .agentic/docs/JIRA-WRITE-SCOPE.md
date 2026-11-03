# Jira mutation scope

`observe_issue_policy(issue)` returns `issue_key`, `issue_type` (`LEAF` or
`EPIC`), `observed_at` and `snapshot` with `issue_id`, `project_key`,
`epic_ids`, `labels`, `component_ids` and `summary`. Observations must bind
the canonical requested issue. The lifecycle accepts observations at most
30 minutes old and rechecks immediately before mutation.

Inventory filtering alone does not authorize writes. Missing, malformed or
unrelated scope refuses mutations. Assignment adapters return scope fields
with `read_issue`; scope is checked before labels and again before comments.
Lifecycle and batch writes preserve identity, durable-stop, read-back and
no-Epic-transition guards.

The reference writer requires pinned accepted project-configuration evidence
and rechecks scope before HTTP POST. Its Epic observer follows at most 16
canonical same-project parents. Legacy custom Epic-link mappings require
separate qualification. Nonempty `additional_jql` refuses writes pending
qualified predicate support.

These guards do not qualify credentials, required transition fields or status
name/ID normalization. Test both admitted and unrelated issues, absent evidence,
and a scope change between reads and writes before activation. Use fake
adapters for these regressions; successful tests grant no Jira write authority.
