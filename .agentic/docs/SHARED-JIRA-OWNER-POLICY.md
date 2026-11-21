# Shared Jira controller accounts

Configuration reference for the owner-authorized exception.

`jira.shared_owner_controller_actor_id` defaults to null. When set, it must be
the exact canonical `jira.controller_actor_id` string. Inspection names it in
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
