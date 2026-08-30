# Framework Repository Activation Draft - 30 August 2026

## Status

**Activation candidate - human review required.** This change installs the
project-local targets needed to dogfood the Claude reviewer. Every qualification
flag remains false. It creates no credential, repository variable, secret,
ruleset, required check, or claim of reviewer availability.

The immutable bootstrap base is `2c7fe134f50393c7727bcf1de6e5bb694dfa24fa`.

## Installed targets

- `.agentic/project.yaml`, seeded for this public repository with A1 as the
  autonomy ceiling, Copilot selected but unqualified, and Claude optional;
- `.agentic/schemas/review-report.schema.json`;
- `.agentic/review/claude/`, Git index blob-identical to the managed scaffold;
- `.github/workflows/awf-review-claude.yml`;
- `.github/workflows/awf-review-claude-demonstrate.yml`.

The existing `.github/workflows/awf-review-gate.yml` remains the active common
gate. Until qualification evidence exists, all three qualification flags stay
false and `awf/review` must not be configured as a required check.

## Owner-provided configuration

The owner supplied the following non-secret values. They remain subject to the
live permission demonstration rather than being treated as qualification
evidence:

- reviewer login `awf-reviewer[bot]`, App ID `4770484`; the owner reports that
  the App is installed only on this repository with Pull requests read/write,
  Metadata read, and no active webhook;
- Anthropic API model `claude-opus-5`. Anthropic's
  [model-ID and versioning documentation](https://platform.claude.com/docs/en/about-claude/models/model-ids-and-versions)
  identifies this dateless Claude 5 ID as a canonical pinned snapshot. The
  adapter omits non-default sampling parameters because the documented
  [Opus 5 compatibility rules](https://platform.claude.com/docs/en/about-claude/models/extended-thinking-models)
  reject them, while default adaptive thinking supports forced tool choice.

## Owner actions still required outside Git

1. Add repository variable `AWF_REVIEWER_APP_ID` and repository secrets
   `AWF_REVIEWER_APP_PRIVATE_KEY` and `ANTHROPIC_API_KEY`. Never put their
   values in Git, task text, logs, comments, or this report.
2. Configure the protected-path and pull-request ruleset, but do not require
   `awf/review` until the qualification PR is ready.

`.agentic/workflow.lock.yaml` is generated from the final installed files and
records their source and installed hashes. AWF-021 still tracks automation of
that deterministic generation; a hand-written or stale lock is not acceptable.

## Evidence sequence after the activation change is on `main`

1. Open a same-repository canary pull request from the developer identity.
2. Confirm the Claude workflow produces one `COMMENT` review by the configured
   App identity with the `awf-review-status` marker for the live head.
3. Push another commit and retain evidence that `awf/review` fails until the new
   head is reviewed.
4. Run `AWF Claude adapter demonstration` against the canary and retain its
   `demonstration.json` artifact.
5. Run the seeded-defect benchmark and record catch rate by required class.
6. Submit a separate human-reviewed qualification PR that sets flags only from
   recorded evidence, changes the required engine to Claude, and only then makes
   `awf/review` a required check.

## Acceptance evidence for this draft

| Criterion | Required evidence before review |
|---|---|
| Installed files match their scaffold sources | Staged Git blob-ID comparison |
| No credential or local path is tracked | Secret and absolute-path scans |
| Project remains fail-closed | All qualification flags false |
| Adapter remains executable after installation | Project-local 34-test offline suite |
| Common gate is unchanged and synchronized | Root/scaffold gate hash comparison |
| GitHub activation is not implied | No App/settings/secret evidence and this draft status |

This draft is not permission to merge, enable external writes, spend API funds,
or mark any reviewer qualified.
