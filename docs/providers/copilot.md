# GitHub Copilot Review Adapter

## Capability

GitHub Copilot code review can be requested automatically through a repository
or organization ruleset, including re-review on every new push. It reads review
instructions from the pull-request head branch.

Copilot submits a `COMMENTED` review, not an approval or change request. It does
not satisfy required human approvals or block merge by itself.

Official documentation:

- [Configure automatic review](https://docs.github.com/en/copilot/how-tos/copilot-on-github/set-up-copilot/configure-automatic-review)
- [Use Copilot code review](https://docs.github.com/en/copilot/how-tos/copilot-on-github/use-copilot-agents/copilot-code-review)
- [Customize code review](https://docs.github.com/en/copilot/tutorials/customize-code-review)

## AWF mapping

| Review-policy requirement | Copilot control |
|---|---|
| Fresh execution | GitHub-hosted Copilot review request |
| Current head SHA | Enable `Review new pushes`; gate compares review `commit_id` with live head |
| Own identity | Adapter constant `copilot-pull-request-reviewer[bot]` |
| Review-only behavior | Native review comments; do not invoke cloud-agent fixes |
| Review instructions | Protected `.github/copilot-instructions.md`, path instructions, and `AGENTS.md` |
| Human gate | Non-author approval, conversation resolution, and required checks |

Copilot's model/vendor lineage is not guaranteed and is recorded as unknown.
Copilot does not expose a controllable blind pass followed by a claims pass;
that limitation must be recorded in qualification evidence.

## Activation checklist

1. Confirm availability and the approved credit/cost owner.
2. Apply the AWF instruction scaffolds through human review.
3. Protect `.github/**`, `AGENTS*.md`, skills, runtime configuration,
   `.agentic/**`, `.awf/**`, gate/test harnesses, and CODEOWNERS from the
   developer identity.
4. Enable automatic Copilot review and `Review new pushes` in an active ruleset.
5. Require conversation resolution and a non-author human approval.
6. Install the supplied API-only gate and require its `awf/review` job with
   GitHub Actions as the expected status source.
7. Confirm a stale-SHA review fails and a current review from
   `copilot-pull-request-reviewer[bot]` passes.
8. Disable review MCP access unless each server and data boundary is approved.
9. Run the seeded-defect benchmark.

The gate runs from the base-branch workflow definition using
`pull_request_target`, never checks out or executes pull-request content, holds
only `pull-requests: read`, paginates review records, and re-evaluates on every
push and review event.

Because Copilot consumes head-branch instructions, server-side path protection
is a hard prerequisite. A merge-only check is not push-time protection.
