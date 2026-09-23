# Showcase rehearsal runbook: AWF 1.9.1 on signal-lab

**Demo repository:** github.com/jkinlay/signal-lab (private). Local clone at `C:\Users\jkinlay\Documents\GitHub\signal-lab`.
**Purpose:** produce every capture the deck needs from one real run, and serve as the acceptance test of the 1.9.1 install and adoption path.
**Rule:** capture real output only. If a step fails or surprises you, capture that too; it goes on the "what went wrong" slide.

## What is in the repo

A small quant research library (`signallab`): synthetic and CSV price loading with validation, two signals (momentum, MA crossover), a vectorised backtest with an explicit one-bar lag, metrics, a CLI, 35 tests (including perturbation tests that prove no look-ahead) and three years of synthetic sample data. There are no AWF files yet, so adoption starts from a clean repository.

Three open issues are written to be converted directly into ticket contracts:

| Issue | Ticket | Why it is a good demo |
| --- | --- | --- |
| #1 | SL-1: z-score mean-reversion signal | Low risk and quick. Easy traps for a critic to catch: centred windows (look-ahead), divide-by-zero on flat prices (±inf), and the population-vs-sample standard deviation. |
| #2 | SL-2: transaction costs | Changes backtest results and adds a public API field, so it should be tiered higher. Cost timing (which bar pays) is a classic off-by-one that a critic should check against AC2. |
| #3 | SL-3: gaps and late listings | Touches data integrity and two modules. The traps are back-filling and forward-filling across a late listing, both of which introduce look-ahead. |

Momentum loses on this data (Sharpe −1.71 at `lookback=120, skip=5`), which gives SL-1 a natural "before and after" number.

## Step 0: one-off fixes (about 5 minutes)

1. **Add CI.** The CI workflow could not be pushed from this session: the `jkinlay` GitHub token lacks the `workflow` scope, and workflow files are protected from remote writes. Copy `signal-lab-ci.yml` (delivered alongside this runbook) to `.github/workflows/ci.yml`, then:

   ```powershell
   gh auth refresh -h github.com -s workflow
   cd C:\Users\jkinlay\Documents\GitHub\signal-lab
   git add .github/workflows/ci.yml; git commit -m "Add CI"; git push
   ```

   Wait for the `ci` check to go green on `main`. Record the two job names (`test (py3.11)`, `test (py3.12)`); they become `validation.required_ci_checks` after adoption.

2. **Fix the local pytest temp folder.** On this machine, tests that use `tmp_path` fail with a `PermissionError` from pytest's default temp directory. With `--basetemp=.pytest_tmp` all 35 pass. Deleting the stale `%TEMP%\pytest-of-jkinlay` folder usually fixes it. Do this before adoption, or the worker's validation runs will fail for reasons unrelated to the ticket.

3. **Decide the pin source.** Publish the 1.9.1 distribution ZIP as a GitHub Release on `agentic-workflow-framework` with its SHA-256 (see the proof pack, section 5). The checksum slide then shows a pin obtained from somewhere other than the ZIP itself.

## Step 1: verify and install (slides "Verify + install")

```powershell
Get-FileHash .\AWF-v1.9.1-distribution.zip -Algorithm SHA256
python -B install_awf.py --dry-run
python -B install_awf.py
```

**Capture:** the hash next to the Release page's pin; dry-run output; install result (`status`, `version: 1.9.1`, `previous_version`, backup, duplicates).

## Step 2: adopt (slides "What gets installed", "Adopt")

In a new Codex task on signal-lab:

```text
$awf adopt AWF 1.9.1 in this project and prepare a draft PR for owner review.
Code owner @jkinlay. Test command: python -m pytest. Jira disabled.
```

**Capture:** the first message (it should say governance files change, hence a draft PR); the bootstrap dry-run file list; `post_install_checks`; `status` (expect `AWF 1.9.1: CONFIGURED`); rules observation (APPLIED, MISSING or UNOBSERVED); the draft adoption PR; and a `Get-ChildItem .agentic` listing for the "what gets installed" slide.

## Step 3: configure (slide "Configure")

`workflow.py operating show`, then one plain-language change that **keeps the critic on a different model from the worker**, for example:

```text
Stream A worker Terra/high; keep reviewers on Sol/high.
```

**Capture:** the before and after tables, and the refusal message if you also try something that exceeds a protected ceiling (for example "8 streams"). The refusal is worth a line on the slide.

## Step 4: merge adoption, reach ACTIVE

Review and merge the adoption PR yourself. Then:

```powershell
python -B -I .agentic\scripts\workflow.py status --adoption-pr N --release-source ABS_SOURCE --expected-manifest-sha256 PIN
```

**Capture:** the merged PR and the `ACTIVE` line. If it stays CONFIGURED, capture the reported next action; that is a legitimate "what went wrong" item.

## Step 5: run the tickets (Act 2 slides)

Dispatch all three issues so the stream board shows real parallel work:

```text
$awf dispatch SL-1 (issue #1)
$awf dispatch SL-2 (issue #2)
$awf dispatch SL-3 (issue #3)
```

For **each** ticket capture:

1. The ticket contract (risk tier, closure standard, acceptance criteria, expected paths).
2. Dispatch readiness, the stream board, and `route_model.py suggest` / `reserve` output.
3. The draft PR: branch, files changed, and CI on the exact head SHA.
4. **The diff and the tests the worker added.**
5. The critic review on that head: verdict, every finding with severity and basis (AC or boundary).
6. Any amendment commit and the re-review.
7. The final gate (`READY_FOR_OWNER_AUTHORIZATION`), the authorisation request, your authorisation, and the merged PR.
8. `route_model.py settle` for the worker and each critic run: tokens and cost.

Keep a stopwatch running for **your own** minutes on each ticket (reading, deciding, merging). The cost slide needs it.

## Step 6: write the log

Create `REHEARSAL-LOG.md` in the AWF repo's `docs/showcase/` folder with one row per event: time, ticket, step, command, result, capture file name. Close it with a summary table per ticket: elapsed time, human minutes, review rounds, findings by severity, worker cost, critic cost.

## Step 7: choose the spine ticket

Pick the ticket with the best **real** critic finding as the one the deck follows end to end. Use the other two on the stream board and in the totals. If every ticket passes first time, say so on the slide, and use PR #7 and the 1.9.0 build ledger from the proof pack as the critic exhibit instead.

## Capture checklist (matches `docs/AWF-1.9.1-Showcase-Presentation.html`)

The deck marks each outstanding capture with an amber **CAP-nn** box; the slide overview (press O) counts how many remain. Replace each box with the real output.

| Slide | Box | Capture |
| --- | --- | --- |
| 6 Verify and install | CAP-06 | Release pin beside `Get-FileHash`; installer result |
| 7 Adopt | CAP-07 | Draft adoption PR (number, files); `status` line |
| 8 Configure | CAP-08 | Echoed change and updated table; the refusal for "8 streams" |
| 9 Dispatch | CAP-09 | `$awf dispatch` readiness for SL-1 |
| 10 Route, reserve, run | CAP-10 | Stream board; `suggest` / `reserve` for SL-1 |
| 11 The code | CAP-11 | SL-1 diff and new tests; PR, head SHA, CI on head |
| 15 The bill | CAP-15 | Per-ticket settle rows and stopwatch minutes, with totals |

Optional upgrades, no box on the slide: replace slide 2's PR #7 timeline with the spine ticket's timeline if it is stronger; put a real signal-lab critic finding first on slide 12; show the SL-1 gate, authorisation and merge on slide 13; add rehearsal problems to the top of slide 14.

Already filled from real output: slide 5 and the right-hand panel of slide 7 come from a bootstrap dry run against signal-lab on 23 September 2026 (`docs/showcase/capture-bootstrap-dry-run.txt`, from the `release-1.9.1-pinned` worktree). Running bootstrap from the main repository checkout is rejected with "Release manifest file membership mismatch" because `docs/` is not in the release manifest, so run it from the extracted release.
