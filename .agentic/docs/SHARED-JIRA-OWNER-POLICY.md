# Shared Jira controller accounts

Configuration reference for the owner-authorized exception.

`jira.shared_owner_controller_actor_id` defaults to null. When set, it must be
the exact canonical `jira.controller_actor_id` string. Only whole-repository
projects qualify, using GitHub's same root rule and independent accepted-policy
reader: subfolder, bare and nested repository roots refuse at validation,
observation and every consumer. Linked worktrees with matching backlinks qualify.
Null/absent retains existing subfolder and whole-repository behavior; unusable or
unobservable accepted policy cannot grant authority. A working proposal cannot
hide an observable accepted setting at a qualifying root. Links from a project's
`.agentic`, scripts or library cannot hide that project's accepted setting.
The root's own `.git` must remain outside worker authority: rewriting or removing
it can defeat root identification. A host invoking external resolved code must
also protect its root association. Inspection names it in
`SHARED_OWNER_CONTROLLER_JIRA_ACCOUNT`. Supplied or feature configuration grants
no exception: shared closure requires accepted default-branch policy, an
owner-closure contract and keyword, complete latest actor-bound Jira history,
and complete protected controller history under exclusive issue ownership.
An identity-bound HELD intent with retained proof of no attempt is permitted;
attempted Done, unknown, abandoned or in-flight operations remain held. Missing
history refuses. Retained closures recheck policy and live history. Evidence
names the shared account and binds its policy digest. This proves only an
account and rules out only instrumented controller operations. Prefer separate
accounts. Controllers never write Done for owner-closure tickets or transition Epics.

Agents never propose either shared-account setting unasked or sign owner decisions.
