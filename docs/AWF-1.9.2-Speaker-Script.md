# AWF 1.9.2 showcase: speaker script

Exported from the speaker notes in `AWF-1.9.2-Showcase-Presentation.html` (press **N** in the deck to see them beside each slide).

**When sharing your screen:** press **P** (or the ⧉ button) to open the presenter window with these notes, a timer and the next slide's title. Share only the deck's browser window, not your whole screen, and keep the presenter window on your other monitor. Arrow keys work in either window. If nothing opens, allow pop-ups for the file and press P again. Core talk: 27 minutes plus 3 for questions. Lines in quotes are what to say; other lines are presenter notes.

## 1. AWF 1.9.2  (0.5 min · 0:00 → 0:30)
*On screen:* AI agents write the code. You sign the merge.

"This is AWF, a framework for letting AI agents do real ticket work in a real repository without handing them the keys. The promise is simple: agents write the code, a separate critic checks it against the ticket, and nothing merges until I approve the exact commit that was reviewed."

"I'll do it in three acts: set it up on a small quant repository, run tickets through it, and then show you what it costs, what went wrong, and what I'd like us to pilot."

> Version note: the first rehearsal (adoption, SL-1) was captured on 1.9.1 on 23 September and is labelled that way; 1.9.2 was merged on 24 September after going through AWF itself (Appendix F), and signal-lab now runs it. The GitHub Release for 1.9.2 is still to be published; until then say "1.9.2, release pending".

## 2. One real PR, start to finish  (2 min · 0:30 → 2:30)
*On screen:* 54 minutes, five independent reviews, one human merge

"Before any setup, here is one real pull request, because that's what this is really about. A Codex agent fixed an issue in the framework's own repository. An automated reviewer, running on a different model, reviewed every new commit it pushed."

"Look at the bars. One blocking issue, then three, four, five. That isn't failure: every time the agent fixed the provenance files, the reviewer found the next inconsistency. After five rounds, two items remained that were policy questions for me, not code defects. I ruled on those in writing, merged, and then checked from a fresh clone that what merged was exactly what was reviewed. Fifty-four minutes end to end."

"That runaway rise is also why 1.9 added a hard cap on review rounds, which ends in a decision by the owner. We'll come back to that."

> If the signal-lab rehearsal produces a stronger end-to-end story, replace this slide with that ticket's timeline (runbook step 7). Numbers: review bodies on PR #7 (findings 10/10/12/13/16; blocking 1/3/4/5/2), 61 inline comments, created 17:30:32Z, merged 18:24:42Z, merge commit 6974271 with second parent bd554ed.

## 3. What AWF adds to PR review and CI  (1.25 min · 2:30 → 3:45)
*On screen:* You already have PR review, CI and branch protection. AWF adds five things.

"The fair comparison isn't AWF versus a chat window. It's AWF versus what a well-run team already has: pull requests, CI and branch protection. AWF keeps all of that and adds five things."

"First, the ticket becomes a contract before work starts. Second, a separate critic reviews the exact commit and can only block on something in that contract or a hard boundary, so it can't nitpick forever. Third, spend is reserved up front. Fourth, my approval is tied to one commit and works once. Fifth, when something has an unknown outcome, it stops rather than retrying."

"What it isn't: it's not a new tracker, not a CI system, and it doesn't take the merge decision away from you."

## 4. How it fits together  (2 min · 3:45 → 5:45)
*On screen:* Codex runs the agents. AWF sets the rules and keeps the record. You hold the merge.

"This is the whole machine on one slide. Codex is the engine: it runs the agents. AWF is the rulebook and the record: it defines what a ticket contract is, what a review must contain, and what evidence the gate needs."

"A ticket becomes a contract. The controller dispatches a worker into its own branch. The worker opens a draft PR. CI runs on that exact commit, and a separate critic reviews that same commit in its own context; it can read, not write. The final gate checks that everything is still current. Then it comes to me."

"Underneath is a ledger that the agents can't touch: budgets, reviews, approvals. And only the controller talks to Jira, so you don't get five agents fighting over ticket status."

## 5. What gets installed  (1.25 min · 5:45 → 7:00)
*On screen:* Adoption adds one folder and a few root files. Your code is untouched.

"What does it physically put in your repository? One folder, `.agentic`, and a handful of root files. These counts are from a real dry run against the demo repository this morning: 214 files, no conflicts with anything already there."

"The important split is the two config files. The operating file is the day-to-day knobs, and you can change it by just saying so. The project config holds the limits that matter: budgets, which models are allowed, how strict review is. Those only change through a reviewed pull request, and an agent asking to loosen them gets refused."

## 6. Verify the release  (1.5 min · 7:00 → 8:30)
*On screen:* Check the release against a pin before anything runs

"Before anything runs, check the release. The release isn't signed, so be honest about what a hash buys you: it proves you have the same bytes as the pin, and nothing more. The pin has to come from somewhere you trust."

"Bootstrap enforces it: with the wrong pin, or from a folder with extra files in it, it refuses to install. And the pin is recorded in every project's receipt: the demo repository and my live HFT project show the same one."

> Rehearsal files: awf-rehearsal-1.9.1\cap07-bootstrap-install.json. The 1.9.1 portable skill distribution could not be built from the public repo: build_skill_distribution.py needs a portable skill source (scripts/install_skill.py), which only exists for 1.7 to 1.8.9. 1.9.2 packages the portable skill from global/awf-portable. Publish the v1.9.2 Release with its manifest pin before presenting.

## 7. Adopt it into a repository  (2 min · 8:30 → 10:30)
*On screen:* One sentence to Codex. AWF plans the change and opens a draft PR for you.

"Adoption is one sentence in Codex. It explains first that it's about to change governance files, which is why it prepares a draft PR rather than committing anything."

"On the right is the real dry run against signal-lab. It plans 214 files with no conflicts, and the configuration is accepted. Notice the amber lines: no CI checks configured yet and no trusted merge owners. Those are warnings, not blockers; you can adopt now and tighten later. And the last line that matters: execution authority, false. Adopting AWF doesn't let any agent do anything by itself."

> Dry run from the release-1.9.1-pinned worktree, 23 Sep 2026. Running bootstrap from the main repository checkout instead was rejected with "Release manifest file membership mismatch" because docs/ files are not in the release manifest; see slide 14.

## 8. Choose the operating configuration  (1.5 min · 10:30 → 12:00)
*On screen:* Three parallel streams by default. Change them by saying so.

"Once adopted, AWF shows its operating configuration and offers three choices: keep the defaults, let it look at the Epics and recommend, or customise. By default you get three streams, so three tickets in parallel. Workers run on Terra at medium effort, and each stream has its own critic on Sol at high effort: a different, stronger model, in a separate context."

"In the rehearsal I changed one thing in plain English, stream A's worker to high effort, and it applied it and marked it pinned. Then I asked for eight streams. Rejected: the ceiling is six, it lives in the protected config, and the remedy it gives is a reviewed pull request, not a bigger number."

## 9. Pick a ticket and dispatch  (1.5 min · 12:00 → 13:30)
*On screen:* SL-1: add a z-score mean-reversion signal

"Here's the ticket we'll follow. On this data momentum loses, so the natural thing to add is a mean-reversion signal. The issue becomes a contract: six acceptance criteria, each with how it gets checked, a list of files the agent may touch and files it must not."

"AC4 is the one I care about as a quant: the new signal has to pass the existing look-ahead test without anyone editing that test. That rules out the classic mistakes, like a centred rolling window."

"And the contract cuts both ways: the critic can only block on these criteria or a hard boundary. Style opinions become follow-up tickets, not review rounds."

## 10. Routed, then the budget gate  (1.5 min · 13:30 → 15:00)
*On screen:* In 1.9.1 the ledger refused the run. 1.9.2 admits it on tokens.

"In the rehearsal I ran SL-1 on stream A. The router was happy: Terra at high effort, pinned because I set that earlier. Then the budget ledger refused to admit the run. The project carries a ten-dollar cap per ticket, so the ledger wants a verified dollar reservation, and Codex only tells you tokens. It would rather refuse than guess."

"That's the right instinct and a real gap: on a subscription host you need token budgets, not dollar budgets. So I ran SL-1 outside the ledger and counted tokens by hand; you'll see the bill on slide 15."

"The fix shipped in 1.9.2. The bottom terminal is SL-2, the next ticket, the next morning: same ledger, admitted with a three-hundred-thousand-token reservation and no dollar figure. Every one of its ten runs went through reserve and settle."

## 11. The code  (2 min · 15:00 → 17:00)
*On screen:* What the worker had to fit into, and what it wrote

"This is the slide a quant cares about: the actual code. On the left is the test the new signal had to pass. It moves every price after day 200 and checks that nothing at or before day 200 changes. Any signal registered in the library goes through it automatically. The worker isn't allowed to edit it."

"On the right is what the worker actually wrote: seven lines, a trailing window with full warm-up, sample standard deviation, and flat windows mapped to NaN instead of infinity. Six new tests, all 41 green, CI green on the exact commit."

"One honest detail: the worker's sandbox couldn't write to git, so it stopped and reported BLOCKED rather than hacking around it. That's the behaviour you want."

## 12. What the critic catches  (2.5 min · 17:00 → 19:30)
*On screen:* What the critic catches that a tired human reviewer would not

"This is the reason the framework exists. Three real findings from the automated critic on the pull request you saw at the start. The first is my favourite: the worker agent had edited the reviewer's own instruction file and softened the list of things that count as blocking. The critic caught the agent weakening its own oversight."

"The second will be familiar to anyone who's reviewed a backtest: a test that looked like evidence but could never fail."

"On the right, the same thing happening at release scale. When 1.9.0 was built with AWF, the first review round found that an agent could forge my sign-off and flip a failing review to a pass. That was fixed, round two found a subtler version, and round three approved."

"Bottom right is SL-2, a transaction-cost change in the demo repository. All three defects were about the public API, not the arithmetic: the numbers were right from the first commit. The first critic round caught that the new fields broke existing callers; the second caught that a hand-built result could contradict itself; the third approved. Then the specialist that the public-API flag requires found that a standard Python operation on the result object had stopped working. One reviewer is not enough on public-API work, and the rules say so."

"For SL-1 the critic's contribution was different: round one refused to approve because it couldn't see CI from its sandbox. It wouldn't pass on evidence it didn't have."

## 13. Final gate, approval and merge  (2 min · 19:30 → 21:30)
*On screen:* A green review is not permission to merge. Your approval is.

"Three steps to merge, and the point of all three is that a green tick is not permission. The gate checks that every piece of evidence is about the same commit. Then it comes to me, and my approval names that commit and expires. If the agent pushes one more commit, my approval doesn't carry over."

"Then after merge we check that what landed on main is exactly what was reviewed. For SL-2 you can see it in the merge record: the merge commit's second parent is the exact commit both the critic and the specialist passed."

"To be precise: SL-1 was merged on the critic's approval and green CI, without the formal gate or the specialist. SL-2 ran the gate: both reviews and CI on the same head, posted on the PR. The signed approval record was not produced; I merged in GitHub. That's the remaining gap."

"Jira in one line: only the controller updates tickets, it reads back every change, and if something else changes the ticket underneath it, it stops and tells you rather than fighting. I left Jira off for the demo."

## 14. What went wrong  (1.5 min · 21:30 → 23:00)
*On screen:* What went wrong, and what changed because of it

"I want to be straight about what's gone wrong, because each of these changed the design."

"Early on, an assistant told me AWF was active in my HFT repository when the configuration had actually been rejected. So now status is always recomputed from checks, never taken from what an agent says. An unknown Jira automation was undoing ticket changes two seconds later, so now there is exactly one writer, it reads back every change, and it stops on a mismatch. Review rounds could run away, so there's now a cap that ends with me deciding."

"The model pilots failed, and I publish that. It's why nothing important depends on the model's judgement. And this week: the framework's own latest port was merged without a review on GitHub. That's a fair criticism, and the next change, 1.9.2, is going through the full process."

"And that process has already earned its keep on 1.9.2. The critic caught that the new portable skill still described the old Jira rules, and then caught that one of my own acceptance criteria couldn't be met as written. I amended it on the record. The model I planned for the review turned out not to be available on this account, even though our host record listed it, so we now treat capability lists as claims until observed."

"Three more from this morning. The shipped budgets were sized for a demo, not for my ten to twenty tickets a day, so they would have stopped real work; the defaults go up in 1.9.2. And because high-risk work was routed to that unavailable model, the router refused my next ticket outright. That's the right behaviour, it never downgrades silently, but it meant a governance change to point high-risk work at the strongest model this host can actually run."

"And SL-2 itself: the critic approved on its third round, and the specialist that the public-API flag requires still found a real break. It used ten of the twelve runs a ticket is allowed, which tells me the cap is about right but tight. The ledger also stopped me recording a critic run as if it vouched for its own independence. I got the field wrong; the tool didn't let me."

## 15. The bill  (1.25 min · 23:00 → 24:15)
*On screen:* What it costs: your minutes, model spend and elapsed time

"Governance isn't free, so here's the bill for SL-1: about 283,000 tokens and 22 minutes from dispatch to approval. Notice where the tokens went: the reviews cost twice what the code did. That's the price of an independent check, and it's the number the pilot has to justify."

"My time goes in three places: once per repository to adopt; per ticket to accept the contract, read the findings and approve; and occasionally to make a call at the review cap. I'm not claiming it makes us faster. The pilot is how we find out."

"SL-2 is what a harder ticket looks like: three real defects, ten runs, about eight hundred and thirty thousand tokens, and under two hours from dispatch to a ticket ready for me to merge. Six of the ten runs were reviews, and review took about two thirds of the tokens."

"At my volume, ten to twenty tickets a day, the measured numbers put a day at up to about thirty million tokens. That's what the project budgets are now set to, and each project's owner sets their own through a reviewed change."

## 16. What work this suits  (1.25 min · 24:15 → 25:30)
*On screen:* It fits work you can write acceptance criteria for

"Most of our work isn't feature tickets, so where does this fit? The rule is simple: if you can write acceptance criteria that a reviewer can check, it fits. Library changes and data loaders fit well; the signal-lab tickets are examples of both."

"Backtest and model-validation changes fit with care: you need fixed seeds and reproducible numbers, and the contract can declare known limitations. Open-ended research doesn't fit yet: explore by hand, then turn what you found into a ticket. And promotion to production is always ours."

"This isn't theoretical: AWF is running today in my HFT Strategies project through Codex and in the Equities Entity Store through ChatGPT, both on 1.9.1, and the demo repository is already on 1.9.2."

## 17. Proposed pilot  (1.5 min · 25:30 → 27:00)
*On screen:* Pilot: one repository, ten tickets, six weeks, a decision at the end

"Here's what I'm asking for. One repository, ten tickets, six weeks. Five measures, each with a pass mark we agree before we start, not after. The most important is the first: does the critic find something that matters at least once every five tickets? If it doesn't, the whole thing isn't worth the overhead."

"And a decision rule, so we don't drift: adopt if all five pass, extend if the critic works but it's slow, stop if the critic doesn't earn its keep or a reviewed ticket lets a defect through. What I need today is a repository, an owner, and agreement on these numbers."

---

# Appendices (use only if asked)

## Appendix A: plain English to AWF terms
*On screen:* Plain English used in this deck, and the term in the specification

> Use when someone asks for the formal term behind a plain-English phrase in the deck.

## Appendix B: adoption states and what invalidates evidence
*On screen:* What each state proves, and what forces a fresh review

> Use if asked what ACTIVE means, or why a new commit needs a fresh review.

> If asked whether upgrading overwrites your settings: signal-lab's real 1.9.1 → 1.9.2 upgrade changed exactly one line of its configuration, the version. Changing budgets needed its own reviewed PR.

## Appendix C: command reference
*On screen:* The commands behind the chat

> For engineers who want the underlying commands. The skill runs these for you.

## Appendix D: what AWF will not do
*On screen:* What AWF will not do

> Use if asked about the authority boundary or optional components.

## Appendix E: why Hermes and Obsidian stay out of the core
*On screen:* One governed agent runtime; one authoritative record

"People ask why the architecture doesn't include an agent platform like Hermes or a knowledge base like Obsidian. The short answer is a governance choice: one governed agent runtime, one authoritative record. Everything else doubles what has to be governed."

"Hermes can do much of what Codex and AWF already do: tools, memory, subagents, scheduled jobs. That's documented. The concern is ours: persistent memory that changes with experience makes runs harder to reproduce, and its own scheduler could act where we require a human. If we pilot it, it's isolated, read-only, with no write credentials and no scheduler."

"Obsidian is a great thinking tool. It just shouldn't be where decisions live: plugins run third-party code, and sync history isn't reviewed change control. Use it next to AWF, and promote anything decided into Git or Jira."

> If asked for an example of why one runtime matters: in the rehearsal the Codex worker's sandbox could not write git metadata, and it stopped and reported BLOCKED rather than working around it (awf-rehearsal-1.9.1\state\worker-run1.log, 23 Sep 2026). A second runtime would be a second set of such boundaries to govern.

> Sources: Hermes tools, overview, cron and toolsets reference (hermes-agent.nousresearch.com/docs/user-guide/features/tools, …/overview, …/cron; …/docs/reference/toolsets-reference); Obsidian plugin security (obsidian.md/help/plugin-security) and Sync version history (obsidian.md/help/Obsidian+Sync/Version+history). No recorded historical decision exists; this slide states the current position.

## Appendix F: AWF reviewing its own release (PR #15)
*On screen:* AWF 1.9.2 through its own process: PR #15

"This is the framework reviewing its own next release. The worker built it; the critic, in a separate clone with no shared context, found a real problem each round: first that the new portable skill described the old Jira rules, then that one of my own acceptance criteria could not be satisfied as written. I changed the criterion on the record rather than letting the worker bend around it."

"Two small honesty points. The model I planned for the review was refused by the host, so the critic ran on the model the routing table specifies. And the critic caught me passing it stale test counts. That's the point of an independent check: it checks me too."

"At the round cap the decision is mine, not the model's. I merged and sent the last finding to a small follow-up PR. That's the documented disposition, and it's on the record. The follow-up fixed it, raised the default budgets, and its critic approved with no findings."

## Appendix G: SL-2 through AWF 1.9.2 (signal-lab PR #9)
*On screen:* SL-2 end to end on 1.9.2: ten runs, three catches, one merge

"This is the whole of SL-2 on one page. Before any code ran, the router refused the ticket because high-risk work pointed at a model this host won't serve. It never downgrades quietly, so that took a governance change from me."

"Then three review catches, each of them about the public API rather than the arithmetic. The critic found two. The third critic round approved, and the specialist the risk flag requires found the third. After the fix, both reviewers re-ran on the new commit, in parallel, before I was asked to merge."

"Ten runs, about eight hundred and thirty thousand tokens, under two hours. One process note: I once recorded a critic run as having passed independent review. The ledger refused it, because a review can't vouch for its own independence."
