# Codex Runtime Adapter

## Role

Codex is AWF's initial reference runtime. This adapter maps provider-neutral AWF
concepts onto Codex capabilities.

| AWF concept | Codex surface |
|---|---|
| Durable repository instructions | Root and nested `AGENTS.md` |
| Reusable workflow | Repository or user skill under `.agents/skills/` |
| Repository runtime defaults | `.codex/config.toml` in a trusted project |
| Bounded parallel analysis | Parent task with subagents |
| Parallel code changes | Separate tasks/worktrees with one writer each |
| Live private integration | Approved plugin, app connector, MCP server, or CLI |
| Recurring workflow | Scheduled task after a successful manual pilot |

## Adapter rules

- Project `AGENTS.md` remains authoritative over generic AWF guidance.
- Do not duplicate full project instructions inside a skill.
- Use subagents primarily for independent read-heavy work and review.
- Use explicit worktrees for parallel writers.
- Keep durable state in the issue tracker, repository, PR, CI, and evidence
  artifacts rather than relying on task history.
- Apply the project's sandbox, approval, and connector policies.
- When Codex develops the change, require the configured external reviewer for
  the binding review. A second Codex task may add useful analysis but does not
  satisfy separate-provider execution by itself. Record vendor/model diversity
  only when it is known; procedural independence is always required.
- Keep separate confidentiality domains in distinct approved environments and
  configuration roots; do not rely on task prose to prevent cross-domain state
  reuse.

## Official documentation

- [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
- [Skills](https://learn.chatgpt.com/docs/build-skills)
- [Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- [Git worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees)
- [Scheduled tasks](https://learn.chatgpt.com/docs/automations)
