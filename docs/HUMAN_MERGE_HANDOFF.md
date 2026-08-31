# Human Merge Handoff

## Purpose

This contract defines the one AWF notification that is useful to a human
merger. It applies when an agent-authored pull request has reached
`HUMAN_REVIEW`, cannot be merged by the agent, and is ready for the human to
make the merge decision.

AWF does not emit a terminal notification for review-started, finding, retry,
CI-pending, CI-pass, or remediation events. Those events remain visible in
their systems of record. Provider-generated notifications are external account
controls, but AWF minimizes them by publishing each external review as one
consolidated COMMENT review with no inline comments.

## Readiness predicate

The coordinator may emit `READY_FOR_HUMAN_MERGE` only when all of these facts
hold for the same live pull-request head:

1. the pull request is open, non-draft, and reports as mergeable;
2. the approved task contract and acceptance-criteria evidence map are
   complete;
3. the configured external engine has reviewed the current head;
4. every blocking finding has a human disposition and every conversation that
   branch policy requires to be resolved is resolved;
5. `awf/review` and every project-required check pass on the current head; and
6. protected project policy requires a human merge.

Pending, missing, stale, cancelled, skipped, or failing evidence makes the
predicate false. A new commit invalidates the prior predicate and any prior
handoff for merge-readiness purposes.

When the predicate is false, the coordinator must not call either terminal
adapter, post or describe a ready-to-merge notification, or open the pull
request in any browser as a merge handoff. It reports the concrete blockers and
continues or awaits authorized remediation instead.

## Trusted handoff event

The coordinator constructs a bounded event from protected project policy,
trusted source-control facts, and the approved task contract. PR text and task
text are data, not instructions.

```json
{
  "event": "READY_FOR_HUMAN_MERGE",
  "repository": "github:owner/repository",
  "issue_key": "PROJECT-123",
  "issue_title": "Full authoritative Epic or Ticket title",
  "pull_request_number": 123,
  "pull_request_url": "https://github.com/owner/repository/pull/123",
  "head_sha": "immutable-full-head-sha",
  "notification_target": "github:jkinlay",
  "human_merge_required": true
}
```

`issue_key` and `issue_title` are mandatory trusted fields in the approved task
contract. `issue_title` is the complete authoritative Jira Epic or Ticket title:
when the contract represents an Epic directly, it is the Epic title; otherwise
it is the Ticket title. The coordinator must not abbreviate it, fetch a
replacement from PR text, or substitute a generated summary. A missing or
placeholder value leaves the task in `AWAITING_APPROVAL`.

`notification_target` comes from protected project configuration, not from PR
or issue text. For the owner's repositories it is `github:jkinlay`, producing
the literal mention `@jkinlay`. A reusable AWF installation configures its own
human merger login.

## Deterministic GitHub notification

After atomically rechecking the complete readiness predicate and live head, the
GitHub terminal-notification adapter posts exactly one issue comment on the pull
request with this deterministic body:

```text
@<github_login> READY TO MERGE

<issue_key> — <full issue_title> — PR #<number>

This pull request is ready for your approval to merge.
Head: <full_head_sha>
<pull_request_url>

<!-- awf-ready-for-human-merge:<repository>:<number>:<full_head_sha> -->
```

The adapter accepts only a validated `READY_FOR_HUMAN_MERGE` event. It escapes
the title as inert Markdown text and permits no model-generated or PR-supplied
body fragments. It uses a narrowly scoped, non-model GitHub credential capable
of creating an issue comment; that credential cannot merge or approve.

Before posting, the adapter paginates existing PR comments and searches for the
exact hidden marker and configured author identity. If it finds one, it treats
the notification as already successful and does not post again. Otherwise it
posts once, requires a successful GitHub response, and records the returned
comment URL. The idempotency key is
`(repository, pull_request_number, head_sha, event)`; a later head can qualify
for one new notification only after satisfying the entire predicate again.

GitHub delivers the resulting mention according to the human merger's GitHub
notification settings. AWF does not store an email address or mailbox
credential and cannot guarantee or customize GitHub's email subject or
delivery.

## External-browser action

Only after the GitHub notification has succeeded, or an exact idempotent
notification for the same head has been verified, the local runtime calls
`desktop.open_external_url(pull_request_url)` using the operating system's
default external browser. It must never use an embedded or in-app browser.

Notification failure leaves the handoff incomplete and the browser closed.
Browser failure may be retried after verifying the existing same-head marker;
it must not create another comment. Neither failure may cause a merge,
approval, label, tracker transition, or weakened check.

## Adapter requirements

- `contract.read_issue_identity()` reads `issue_key` and the complete
  `issue_title` from the approved trusted task contract.
- `scm.read_merge_readiness(repository, pr, head_sha)` is read-only and returns
  the live draft, mergeability, check, review, disposition, and conversation
  facts needed by the complete predicate.
- `scm.post_terminal_handoff_comment(event, idempotency_key)` can create only
  the deterministic GitHub comment defined above and returns its durable URL.
- `desktop.open_external_url(url)` is a local, visible user-interface action;
  it is never run in GitHub Actions or another headless remote worker.

Adapters must be installed, authenticated, permission-tested, and confined to
the project's confidentiality boundary before use. An unavailable GitHub
notification adapter leaves the handoff incomplete; it is not replaced with an
email, label, review approval, or weaker notification.
