# AWF Showcase Presentation Plan

Revision 2 — 19 September 2026. Supersedes the plan of 18 September 2026, which produced a governance-first deck. This revision builds the deck around one real ticket taken end to end, with the setup shown as it is actually performed.

## Purpose

A quant or engineer leaves the room knowing what they type, what comes back, what they still have to do by hand, and how to get AWF onto their own repository this week. Governance, evidence states and authority boundaries are still covered accurately, but they are shown as consequences of a demonstrated workflow rather than as the subject of the talk.

The controlling source remains the verified supplied AWF 1.8.9 release (16 September 2026, publisher Jonathan Kinlay, Apache-2.0, `jkinlay/agentic-workflow-framework`). Section references below are to the AWF 1.8.9 Complete Documentation.

## Audience and duration

- Audience: quant, engineering, delivery, risk and technology stakeholders. Assume they write code or manage people who do, and that they have used an AI coding assistant at least once.
- Core presentation: 27 minutes. Discussion margin: 3 minutes. Total 30 minutes.

## Narrative

Three acts around a single ticket.

**Act 1 — Set it up (slides 3–7, 10.5 min).** Verify the download, install the portable skill, adopt AWF into a real repository, look at the default operating configuration, change it in plain language, merge the adoption PR.

**Act 2 — Run a ticket (slides 8–12, 9.5 min).** Take a small real ticket from dispatch to merge: route reserved, worker in a stream, draft PR, independent critic, final gate, owner authorisation, human merge, the single Jira transition.

**Act 3 — What you control and what it will not do (slides 13–14, 4 min).** The two configuration layers, the non-goals, and the pilot ask.

Every slide in acts 1 and 2 carries at least one real artefact: a command, its output, a screenshot, or a PR link. Everything that only a governance auditor would care about goes into speaker notes and the appendix, where it is kept exact.

## Demo environment and rehearsal

The demo is **recorded during a rehearsal and replayed as stills** (terminal captures and screenshots) on the slides. A live run is optional if the rehearsal is clean; the stills are the fallback and the slides must work without a live terminal.

Prepare before rehearsal:

| Item | Requirement |
| --- | --- |
| Scratch repository | A small real Python or C# repository on GitHub.com under `jkinlay`, with a default branch, a passing test command and no AWF files. A trimmed copy of an internal repo is fine; a toy repo is not, because the adoption output must look like a real project. |
| Demo ticket | One bounded, low-risk Jira Story in a QA Epic (for example a small refactor with an existing test). Record its key. Jira may be left disabled for the ticket run; slide 12 handles both outcomes. |
| Distribution | `AWF-v1.8.9-distribution.zip` plus its SHA-256 pin obtained through a channel other than the download (§1). |
| Host | Codex with the `awf` skill installed as the delegation host (§5.2, §6.4). |
| Python | 3.11+ (§5.1). A project-local `.venv` outside the worker worktrees with `.agentic/requirements.lock` installed `--require-hashes --only-binary=:all:` (§5.3). |
| GitHub CLI | Authenticated `gh` for optional read-only rules observation (§7.3). |
| Capture | Terminal at 120×35 with a light-on-dark theme matching the deck; PowerShell or bash, one or the other throughout. Screenshots of GitHub PRs at 1600 px wide with browser chrome cropped. Save each capture under `docs/showcase-captures/NN-<slug>.png` and the raw text under `docs/showcase-captures/NN-<slug>.txt`. |

Rehearsal is also an acceptance test of 1.8.9. If bootstrap returns `INSTALLED_UNCONFIGURED`, `validate-config` is not `ACCEPTED`, or a ruleset blocks the adoption PR, stop and fix the release or the runbook before building slides. Do not present a step that did not work in rehearsal.

Redact nothing that is visible in a public repository; do redact tokens, App IDs and internal hostnames.

## Slide plan

| # | Slide | The audience's question | Artefact on the slide | Min |
| --- | --- | --- | --- | ---: |
| 1 | AWF 1.8.9 | What is this? | One sentence. Release line. | 1.0 |
| 2 | A ticket, with and without AWF | Why would I bother? | Two-column before/after of the same ticket | 2.0 |
| 3 | What you need | Can I run it? | Prerequisite strip; `sha256sum` output beside the pin | 1.5 |
| 4 | Install the skill | What do I install? | `install_awf.py --dry-run` then install; installer summary | 2.0 |
| 5 | Adopt it into a repository | How does it get into my repo? | Adoption prompt; bootstrap dry-run report; `status` output | 3.0 |
| 6 | Choose the operating configuration | How many agents, which models? | `operating show`; `operating set --instruction "4 streams, each Sol/high"`; result | 2.5 |
| 7 | Merge the adoption PR | What did it change? | Draft adoption PR screenshot; post-merge `status` showing `ACTIVE` | 1.5 |
| 8 | Pick a ticket and dispatch | How do I start work? | Ticket card; dispatch prompt; contract check | 2.0 |
| 9 | Routed, reserved, running | What is actually running? | `route_model.py suggest` and `reserve` output; stream board | 2.0 |
| 10 | Draft PR and independent critic | Who reviews it? | Draft PR screenshot; critic review comment | 2.5 |
| 11 | Final gate, authorisation, merge | Who presses the button? | `evaluate --request` output; authorisation record; merge screenshot | 2.0 |
| 12 | Jira: one transition, once | What happens in Jira? | Read-before / attempt / read-after log, or the "Jira disabled: stays MERGED" line | 1.0 |
| 13 | What you control, what it will not do | What can I change; what is off-limits? | Two-file diagram; six-line non-goals list | 2.0 |
| 14 | Pilot | What do we do next? | One repo, ten tickets, the measures | 2.0 |
| | **Total** | | | **27.0** |

## Slide-by-slide demo script

For each demo slide: the commands to run during rehearsal, what to capture, what appears on the slide, and the speaker-note gist with its manual reference. Substitute absolute paths; the manual requires reviewed absolute paths for every project command (§18).

### Slide 1 — AWF 1.8.9

No demo. Slide text: "A governed workflow for AI-assisted delivery: agents write, an independent critic reviews, you merge." Release line: 1.8.9 · 16 Sep 2026 · Apache-2.0 · `jkinlay/agentic-workflow-framework`.

Notes: verified supplied release, not "latest"; 92 tests, 0 failures in the portable-distribution suite (§1).

### Slide 2 — A ticket, with and without AWF

No demo. Left column: the ticket done with an unstructured assistant (one chat, one person, no record of what was reviewed, review readiness mistaken for approval). Right column: the same ticket under AWF (bounded contract, reserved route, draft PR, independent critic, final gate, owner merge, one Jira write). Use the demo ticket's real key in both columns so the audience follows it for the rest of the talk.

Notes: §2, §4. Do not claim throughput uplift; the pilot on slide 14 is where that gets measured.

### Slide 3 — What you need

Run:

```text
sha256sum AWF-v1.8.9-distribution.zip        # or Get-FileHash on Windows
```

Capture the output beside the independently obtained pin (`e8a5b4b2…c34719b`). Slide: prerequisite strip (Python 3.11+, Git, a GitHub repo you own, Codex with the `awf` skill, a Jira project if you want the Done transition) and the hash comparison.

Notes: §1, §5.1. The release is unsigned; a matching hash proves the bytes match the pin, not who published them. Say it once, here, and not again on the slides.

### Slide 4 — Install the skill

Run, from the extracted `AWF-v1.8.9-distribution` folder:

```text
python -B install_awf.py --dry-run
python -B install_awf.py
```

Capture both outputs. Trim the dry run to the lines showing destination, previous version, backup location and preserved settings; trim the install to its final summary line. Slide: the two commands and the two trimmed outputs, plus one line: "Then start a new Codex task so the skill is discovered."

Notes: §6.1–6.4, §19.1. Installer is fail-closed and rollback-capable; it stages a replacement and keeps the previous folder in a same-filesystem backup. Installing the skill changes no project.

### Slide 5 — Adopt it into a repository

In a new Codex task on the scratch repository:

```text
$awf adopt AWF 1.8.9 in this project and prepare a draft PR for owner review and merge
```

Capture the host's transcript of the adoption run. The manual's sequence (§7.2, §19.2) is what the skill should perform; capture and show these three moments from it:

1. The bootstrap dry run and its proposed derivation/residue report:

   ```text
   python -B scripts/bootstrap_project.py --dest ABS_TARGET --expected-manifest-sha256 f18aaac8…a50b56dbe --codeowner '@jkinlay' --dry-run
   ```

2. The backed-up install:

   ```text
   python -B scripts/bootstrap_project.py --dest ABS_TARGET --expected-manifest-sha256 f18aaac8…a50b56dbe --codeowner '@jkinlay' --mode install --on-conflict backup
   ```

3. The installed checks:

   ```text
   python -B -I ABS_TARGET/.agentic/scripts/workflow.py verify-installation
   python -B -I ABS_TARGET/.agentic/scripts/workflow.py validate-config
   python -B -I ABS_TARGET/.agentic/scripts/workflow.py status
   ```

Slide: the one-line adoption prompt, the dry-run report trimmed to the file list it proposes to add, and the `status` output showing `integrity_valid: true`, `status: ACCEPTED` and the adoption state `CONFIGURED` with its next action. Add a `tree -L 2 .agentic` capture if space allows, so the audience sees what landed in the repo.

Notes: §7.1–7.5, §19.2. Both bootstrap calls use the locked runtime created outside the worktree. If bootstrap returns `INSTALLED_UNCONFIGURED`, stop, remedy and rerun; later checks do not rewrite it. Project-owned instructions go to `PROJECT_INSTRUCTIONS.md`; `AGENTS.md` is AWF-managed and byte-exact. Existing repositories keep their history, CODEOWNERS, configuration and active work (§7.1, §20); say this in one sentence and move on.

### Slide 6 — Choose the operating configuration

Run:

```text
python -B ABS_TARGET/.agentic/scripts/workflow.py operating show
```

Capture the full output. It should show three streams A–C, Terra/medium workers, Sol/high reviewers, Sol/medium controller, Astra/high specialist, Luna/low simple-worker route enabled, and `effective_ceiling` with its governance path (§10, §10.1).

Then change it in plain language, exactly as a user would:

```text
python -B ABS_TARGET/.agentic/scripts/workflow.py operating set --instruction "4 streams, each Sol/high" --set streams.count=4 --set streams.A.worker=gpt-5.6-sol/high --set streams.B.worker=gpt-5.6-sol/high --set streams.C.worker=gpt-5.6-sol/high --set streams.D.worker=gpt-5.6-sol/high
python -B ABS_TARGET/.agentic/scripts/workflow.py operating show
```

Capture the echo of the applied change and the second `show`. Optionally also capture the Epic-scoped form for the notes:

```text
python -B ABS_TARGET/.agentic/scripts/workflow.py operating set --instruction "Stream B's worker on Astra/xhigh for this Epic" --epic QA-NNNN --set streams.B.worker=gpt-6-astra/xhigh
```

Slide: the default `show` on the left, the instruction and the changed `show` on the right. Headline: "Default is 3 streams. Say what you want; it applies within governance."

Notes: §10–10.4. Operating choices live in root `OPERATING_CONFIG.yaml` and change by direct instruction; ceilings, budgets, allowlists and risk floors live in `.agentic/PROJECT_CONFIG.yaml` and change by reviewed PR. Effective ceiling is the minimum of six, `max_parallel_tickets` and the broker cap when the broker is enabled; a ceiling is not free host capacity. Invalid or mixed valid/invalid changes are refused whole. `operating recommend --epics` produces a recommendation table that waits for acceptance; mention it, do not demo it.

### Slide 7 — Merge the adoption PR

Capture the draft adoption PR the skill opened (title, file list, the adoption checklist in the description). Merge it as owner. Then, on a fresh checkout of the default branch:

```text
python -B -I ABS_TARGET/.agentic/scripts/workflow.py status --adoption-pr N --gh ABS_GH --release-source ABS_RELEASE_CACHE --expected-manifest-sha256 f18aaac8…a50b56dbe
```

Capture the output showing `ACTIVE`. Slide: PR screenshot left, `ACTIVE` status right. Headline: "You merge it. Then it is active."

Notes: §7.2 step 9, §8, §18.2. `ACTIVE` needs independent release trust, the observed receipt-changing adoption merge and fresh default-branch evidence. It is an adoption state; it does not by itself enable a model launch, adapter, merge or Jira write. Keep the `INSTALLED` / `CONFIGURED` / `ACTIVE` table in the appendix.

### Slide 8 — Pick a ticket and dispatch

Show the demo ticket as a card (key, title, Epic, one-line scope). In Codex:

```text
$awf dispatch QA-NNNN
```

Capture the host's readiness checks: contract valid, owner, dependencies satisfied, file boundaries, no overlapping writers, effective ceiling versus observed capacity (§19.3 steps 1–3). Slide: the ticket card, the dispatch line, and the readiness check trimmed to its pass/fail lines.

Notes: §12.1 (`BACKLOG → READY → DISPATCHED`), §19.3. One writer owns a ticket or path at a time (§3.4).

### Slide 9 — Routed, reserved, running

Capture the routing calls the host performs, or run them by hand against the same request file:

```text
python .agentic/scripts/route_model.py suggest --config ABS_TARGET/.agentic/PROJECT_CONFIG.yaml --request request.json --capabilities observed-host.json
python .agentic/scripts/route_model.py reserve --config ABS_TARGET/.agentic/PROJECT_CONFIG.yaml --project-root ABS_TARGET --request request.json --capabilities observed-host.json --ledger ABS_STATE/routing.sqlite
```

Capture the suggested route (model, effort, why: role default → stream override → floors) and the reservation record (run id, budget bound). After the worker finishes, capture `settle`:

```text
python .agentic/scripts/route_model.py settle --config ABS_TARGET/.agentic/PROJECT_CONFIG.yaml --project-root ABS_TARGET --ledger ABS_STATE/routing.sqlite --run-id RUN_ID --outcome outcome.json
```

Slide: a small stream board (A: QA-NNNN running on Sol/high; B, C idle) drawn as HTML, with the `reserve` record beside it. Headline: "Reserve before launch. Settle what was observed."

Notes: §11.1–11.3, §11.6. Route precedence; `unavailable` rather than silent downgrade; unknown usage is not zero; exit 0 is not evidence that a model ran. Reconciliation and the two-incident ceiling (§11.4) belong in the appendix, not here.

### Slide 10 — Draft PR and independent critic

Capture the draft PR the publisher opened for the ticket branch (§12.2): title, branch, files changed, CI status. Capture the moment the draft is cleared after validation. Capture the critic's review on the observed head: the review comment, its verdict and at least one finding. If the critic requests changes, capture the amendment commit and the re-review; that is a better demo than a first-pass approval.

Slide: PR screenshot with the critic comment inset. Headline: "The critic reviews the exact head. It cannot merge."

Notes: §12.1 (`PR_DRAFT → READY_FOR_CRITIC → CHANGES_REQUESTED → AMENDING`), §13.2. Critic runs in a separate context; specialists are triggered by risk (§9.4). Any change of head, base, requirements or policy invalidates prior evidence (§12.4). The Claude external-review adapter and the scheduled loop are optional, separately qualified components (§15–16); one sentence, pointing to the appendix.

### Slide 11 — Final gate, authorisation, merge

Run:

```text
python -B -I ABS_TARGET/.agentic/scripts/workflow.py evaluate ABS_BUNDLE --request
```

Capture the gate result reaching `READY_FOR_OWNER_AUTHORIZATION` and the rendered authorisation request (candidate fields: repository, PR number, head SHA, base SHA, target branch, merge method, expiry). Capture the owner's authorisation record and:

```text
python -B -I ABS_TARGET/.agentic/scripts/workflow.py verify-authorization --record ABS_R --request ABS_Q --gate ABS_G --config ABS_C
```

Then merge the PR by hand as owner and capture the merged PR and the `MERGED` state in `status`.

Slide: three panels — gate output, authorisation request with the candidate fields highlighted, merged PR. Headline: "Green review is not merge authority. You authorise one exact candidate, once."

Notes: §12.3, §13.3, §19.4. Authorisation binds ordered fields, nonce, gate hash and expiry, and is consumed once. Offline helpers check consistency; they do not authenticate the person. `MERGE_UNKNOWN` reconciliation: appendix.

### Slide 12 — Jira: one transition, once

If Jira is enabled and scoped for the demo repo (`--jira-site` and `--jira-key` at bootstrap, Done mapping configured), capture the read-before / attempt / read-after log for the single Done transition after confirmed merge. If Jira is disabled, capture the line stating no Jira write was performed and the ticket remains `MERGED`.

Slide: the three-line log, or the one-line "Jira disabled" outcome, and the sentence "AWF never moves Epics and never mirrors its states into Jira."

Notes: §14. Mismatched, unavailable or unknown results stop all further Jira writes without retry. The shipped reference adapter performs no Jira writes. This slide replaces the earlier decision-tree treatment.

### Slide 13 — What you control, what it will not do

No demo. Left: a two-file diagram — `OPERATING_CONFIG.yaml` (streams, routes; change by chat) and `.agentic/PROJECT_CONFIG.yaml` (ceilings, budgets, allowlists, floors, merge gates; change by reviewed PR). Right: six lines from §4, chosen for this audience:

- does not launch agents on its own or attest that a model ran;
- does not merge code;
- does not create tickets, update Epics or mirror states into Jira;
- does not treat a review approval as merge authority;
- does not retry an uncertain merge, Jira write or provider outcome;
- does not enable the external reviewer or scheduled loop because a project is `ACTIVE`.

Notes: §4, §9, §10, §17.1 (secrets and state outside worker worktrees; worktrees are not an isolation boundary; candidate content grants no authority).

### Slide 14 — Pilot

No demo. One owner-approved repository, ten bounded tickets, a matched baseline of ten comparable tickets done without AWF. Predeclared measures: merged PRs, defects found in review, rework loops, lead time ticket-to-merge, human minutes per ticket, model cost per ticket, recovery incidents. Owner decisions: confirm scope and baseline; merge and observe adoption; decide from evidence; qualify Jira writer and optional adapters separately.

Notes: §19.2, §11.5 (shadow threshold of 20 reviewed low-risk tickets, 95% acceptance, no escaped defects is a routing-qualification threshold, not a productivity benchmark). The QA Jira `Fixed`-work-item throughput figures from the previous deck (3.41 items per business day, Jan–Jun 2026) may appear in the notes as baseline context; the 21.1× ratio is withdrawn.

## Appendix (two pages, in the deck after slide 14, not presented)

- A1: Adoption states table (`UNVERIFIED`, `INSTALLED`, `CONFIGURED`, `ACTIVE`) — what each proves and does not prove (§8).
- A2: Candidate-bound evidence and invalidation events (§3.2–3.3, §12.4, §13.3).
- A3: Reconciliation, retry ceiling and the trusted-host raise (§11.4); `MERGE_UNKNOWN` (§12.4).
- A4: Optional components — scheduled review loop and external review adapter: prerequisites, credential scopes (`pull_requests:write`, `actions:write`), qualification (§15–16).
- A5: Command reference for everything shown, with absolute-path placeholders (§18).

## Accuracy rules for notes and appendix

The previous plan's content guardrails still apply to every claim on slides, in notes and in the appendix. In summary: describe 1.8.9 as supplied and verified, not latest; the release is unsigned and a digest proves bytes, not publisher; installation, adoption states and live authority are distinct; human merge authorisation binds one candidate and is consumed once; streams default to three with a structural maximum of six and one reviewer per stream; reconciliation is disabled by default with a two-incident ceiling; GitHub rules are observed, never mutated by bootstrap; Jira gets at most one explicitly authorised post-merge Done transition and Epics are never targets; optional components are separately qualified; no causal productivity claim. The change in this revision is where those statements live, not whether they are true.

## HTML implementation

Unchanged from the previous plan: standalone 16:9 HTML, inline assets, keyboard and touch navigation, table of contents, slide counter, presenter-notes toggle, one-slide-per-page print, dark navy with blue/teal accents, warm highlight reserved for human-authority moments, reduced-motion support, semantic landmarks, text equivalents for diagrams, and no Space-to-advance while a control has focus.

Additions:

- A terminal block component: monospace, dark background, prompt in muted colour, output in high contrast, with a small caption giving the command's manual section. Render the captured `.txt` files verbatim inside it; do not retype output.
- A screenshot frame component for PR captures, with a caption and the PR URL as text.
- Captures embedded as data URIs from `docs/showcase-captures/`; keep each PNG under 300 KB.
- The appendix slides excluded from the slide counter and from Space/arrow navigation unless the presenter enters them via the table of contents.

## Acceptance criteria

- Fourteen presented slides plus a two-page appendix; 27-minute core with 3-minute margin.
- Every slide from 3 to 12 shows at least one artefact captured during the rehearsal: a command with its real output, a screenshot, or a PR link.
- A reader could reproduce the setup in slides 3–7 from the slides plus §18 of the manual, without the speaker.
- The demo ticket's key appears on slides 2, 8, 9, 10, 11 and 12, so one story runs through the deck.
- No slide is composed only of definitions or non-claims; every governance statement on a slide is attached to an artefact that shows it operating.
- All accuracy rules above hold in slides, notes and appendix.
- The rehearsal ran clean end to end on 1.8.9; any step that failed was fixed in the release or runbook before the slide was built, and the fix is recorded in the rehearsal log.

## Review

Two independent reviews before implementation, run in this order:

1. **Practitioner review.** Persona: a senior quant on the CMC team who has used Codex but not AWF. Question for every slide: "Do I know what to do next?" Authority to strike any slide copy that only a governance auditor would need, and to require a missing artefact. Verdict is on the slides only.
2. **Manual-fidelity review.** The existing Sol/Max reviewer, comparing notes and appendix against the 1.8.9 manual. Authority over accuracy in notes and appendix, and over the wording of any governance sentence that stays on a slide, but not to add slides or reinstate slide copy struck by the practitioner reviewer.

Findings from both are incorporated before the deck is built; the completed deck gets a second practitioner pass against the acceptance criteria.

## Changes from the previous plan

- Purpose reframed from "understand purpose, limits, adoption and authority" to "know what to type, what comes back, and how to adopt it this week".
- Fifteen governance slides replaced by fourteen slides in three acts around one ticket; the earlier slides 3, 5, 6, 9, 10 and 13 collapse into notes and appendix A1–A4.
- A recorded rehearsal on a real scratch repository is now a prerequisite, and doubles as an acceptance test of the 1.8.9 install and adoption path.
- The Quant Team throughput comparison and the 21.1× ratio are removed from the slides; the observed Jira baseline may be cited in the pilot notes.
- Review split into a practitioner pass on slides and a manual-fidelity pass on notes and appendix, with the practitioner reviewer holding authority over slide copy.
