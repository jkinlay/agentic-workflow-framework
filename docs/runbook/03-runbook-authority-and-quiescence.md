### Authority and quiescence

Prepare adoption as a governance change for owner review and merge. Preserve project instructions, configuration, and effective CODEOWNERS policy. Missing repository rules do not prevent local adoption or draft-PR preparation. Sources: `7fcaeece:AGENTS.md:10`; `7fcaeece:AGENTS.md:12`; `7fcaeece:.agentic/docs/20-NEW-PROJECT-SETUP.md:7`.

Before installation or upgrade, arrange exclusive ownership and pause these existing controller switches:

```text
dispatch_enabled
auto_dispatch
auto_request_critic
auto_resume_amendments
auto_transition_jira
```

Every listed switch must be explicitly false when existing configuration is present. The installer rejects otherwise. Sources: `7fcaeece:.agentic/lib/agentic/installer.py:814`; `7fcaeece:AGENTS.md:14`.

## 2. Getting v1.9.5

