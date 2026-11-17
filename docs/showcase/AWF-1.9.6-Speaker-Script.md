# AWF 1.9.6 showcase: speaker script

Exported from the speaker notes in `AWF-1.9.6-Showcase-Presentation.html` (press **N** in the deck to see them beside each slide).

**When sharing your screen:** press **P** (or the ⧉ button) to open the presenter window with these notes, a timer and the next slide's title. Share only the deck's browser window, not your whole screen, and keep the presenter window on your other monitor. Arrow keys work in either window. If nothing opens, allow pop-ups for the file and press P again. Core talk: 28 minutes plus 2 for questions. Lines in quotes are what to say; other lines are presenter notes.

## 1. AWF 1.9.6: a practical guide  (0.5 min · 0:00 → 0:30)
*On screen:* AI-assisted delivery, from idea to reviewed PR

"Good morning. This is a practical walkthrough of AWF, the Agentic Workflow Framework, as of release 1.9.6. The idea is simple. You set the objective. AWF helps you write the specification, plan the work, run AI writers and independent reviewers in parallel, and bring you a pull request with the evidence attached. You make the merge decision."

"I'll take about twenty-eight minutes, then questions. Everything I show is a prompt you can type yourself."

> This deck is self-contained: nobody needs to have seen an earlier AWF presentation.

> AWF 1.9.6 was published as a GitHub Release on 16 November 2026. The Quant Foundry deployment context comes from Jonathan; do not infer the exact installed version or a live controller from it.

> Sources: CHANGELOG 1.9.6; AWF User Guide; AWF Technical Reference.

## 2. What AWF gives you  (1 min · 0:30 → 1:30)
*On screen:* An unstructured coding chat compared with the same task under AWF

"Why bother? Compare two ways of doing the same task. On the left is how most of us use a coding assistant today: one long chat. Requirements, changes and decisions pile up in the conversation. You keep re-explaining the scope and checking what was actually tested. If you want a second opinion you open another chat, which nobody tracks. And if you run several agents, they can edit the same files."

"On the right is the same task under AWF. A reviewed specification becomes a set of bounded tickets. A controller hands safe work to the available lanes. Writers implement, and separate critics challenge what they produce. What reaches you is a pull request with the tests and the findings."

"Four roles, then: you as owner, a controller, writers and critics. It isn't a new IDE or a new model, and I'm not promising a throughput multiplier."

> Use AWF for research tooling and software delivery. Research conclusions still need scientific validation.

> Sources: AWF User Guide · What AWF does.

## 3. Starting inside Quant Foundry  (1 min · 1:30 → 2:30)
*On screen:* The start prompt, and three checks before any work begins

"Where do you start? In your own project. AWF is installed in the Quant Foundry repository, so it is available to the projects inside it. You don't install it again for each project."

"The prompt on the left is the one to copy. Name the project folder and the goal. Ask it to read the repository and project instructions, confirm which AWF skill and version it can see, check the project's status and settings, and tell you what is ready and what needs your decision."

"That last check matters. If a skill, a permission or a data resource is missing, you want to hear it now, before any work is delegated. And being available in the repository doesn't give your session access to Jira or to anyone else's data."

> Deployment statement supplied by Jonathan Kinlay, 29 October 2026. Availability in the repository does not prove that a skill is visible in every host session, which is why the start prompt asks for that check.

> Working outside Quant Foundry: Appendix B has the installation and adoption steps.

> Sources: AWF User Guide · getting started; deployment context from the team owner.

## 4. What you can do with AWF today  (1 min · 2:30 → 3:30)
*On screen:* Before coding: better preparation. During delivery: less manual coordination.

"Here is what you can do with it today, in two halves. Before any coding: AWF helps turn an idea into a specification with measurable requirements, and has that design independently challenged. It maps the accepted specification to Jira tickets with dependencies and a safe order. And it can run up to twelve worker lanes in parallel."

"During delivery: it orders the finished pull requests and checks that they integrate. Independent review is bounded, so the number of review rounds depends on the risk of the ticket and stops at the first pass. And it keeps a record of who wrote what, who reviewed it, and anything whose outcome is uncertain."

"The rest of this talk walks through each of those in order."

> What the source supports, what the accepted operating policy allows and what a qualified live host can do are three different things. Keep that caveat short in the room.

> Sources: AWF User Guide · Key features.

## 5. New in release 1.9.6  (1.5 min · 3:30 → 5:00)
*On screen:* Work continues around the clock; decisions arrive on schedule

"And this is what release 1.9.6 adds. On the left, the changes you'll notice day to day."

"First, twelve lanes is now the default for a new project, and it works end to end. If your project is upgraded, it keeps the lane count it already had."

"Second, and this is the big one: the agents work around the clock, but routine requests for your approval are held and delivered as a digest at seven in the morning on weekdays. You don't get pinged at midnight."

"Third, optional project reports to Teams or Slack, hourly and at end of day. They're off until an owner turns them on."

"On the right, control and upkeep. Token budgets start switched off and change only through a merged pull request. Upgrades are now preservation-based: your settings, routes, Jira binding and history are compared before and after. And if you clone an adopted project onto a new machine, one command rebuilds the local runtime."

"There's also a batch of fixes: gaps in Jira updates closed, fewer dead ends for the operator, and stricter release publishing. I'll come back to the main items."

> Tickets, if asked: AWF-271 (twelve-lane routing and model defaults), AWF-244 (calendar and review batching), AWF-241 (Teams and Slack reporting), AWF-242 (budgeting), AWF-238 (preservation-based upgrades), AWF-286 (provision-runtime), AWF-243 (Jira write gaps and operator dead ends).

> Sources: CHANGELOG 1.9.6; MIGRATION-v1.9.5-to-v1.9.6.md; first-parent log v1.9.5..v1.9.6.

## 6. Writing and reviewing the specification  (2 min · 5:00 → 7:00)
*On screen:* You describe the outcome; AWF returns an independently reviewed design; you accept it

"Let's start a piece of work. I'll use one example throughout: a market-data validation tool. Deliberately dull. We are validating a feed, not discovering a strategy."

"The prompt asks AWF to help write the specification. Notice what it asks for: rules for timestamps, calendars and missing data; requirement IDs that stay stable; acceptance tests; and a list of exclusions, assumptions and open decisions. It asks for an independent design critic, at most two review rounds, stopping at the first pass. And it ends with 'do not code yet'."

"What comes back has already been through AWF's own review process. A separate critic, in a separate context, challenges the draft exactly as it would challenge code. Its findings are recorded, the draft is amended, and the review stops at the first pass or at the limit you set. So you receive requirements with measurable acceptance criteria, interfaces and failure behaviour, a test plan, and the critic's findings with what changed in response to each."

"For our kind of work, look for the quant-specific items: data lineage, point-in-time availability, units, numerical tolerances and leakage controls. A good requirement reads like this: a missing timestamp produces a named diagnostic and does not trigger imputation."

"Then the decision is yours. Should a missing observation fail validation or only warn? You resolve that, and you accept the revision that will govern the build. A good specification says what it doesn't know. It shouldn't invent requirements or results."

> Independent review must run in a separate supported context. If one is not available, AWF must say so.

> The two document-review rounds are an example budget for this prompt. They are separate from the review caps on tickets.

> If the specification is committed to the repository, that change is a pull request and is reviewed like any other.

> Sources: AWF User Guide · Specification prompt.

## 7. Jira planning from the accepted specification  (1.5 min · 7:00 → 8:30)
*On screen:* You name the destination; AWF returns an independently reviewed backlog; you authorize creation

"Next, turn the accepted specification into a plan. The prompt names three things: the repository and project folder, the Jira site and project key, and which revision of the specification was accepted."

"It asks AWF to check for duplicates and read the live Jira fields, then propose Epics, tickets and dependency links. And, like the specification, the plan goes through AWF's own review. An independent critic checks it against the specification before anything is created. Does every requirement have a ticket? Are the dependencies right? Is anything out of scope? The prompt ends with a stop: 'do not create issues yet'."

"When the reviewed plan comes back and you're happy, you authorize it with one sentence: 'I authorize creation of the reviewed plan in the named project. Do not start implementation.' AWF creates the issues in dependency order and reads them back to confirm."

"Two points. Creating a backlog and letting AWF update tickets during delivery are separate decisions. And Jira is optional: without it, AWF keeps local records."

> Project and site identities, assignees, hierarchy and link directions must be observed, not assumed.

> If a Jira response is uncertain, reconcile it before retrying so the backlog is not duplicated.

> Sources: AWF User Guide · Jira backlog prompt.

## 8. Priorities, sizes, dates and the critical path  (1.5 min · 8:30 → 10:00)
*On screen:* What AWF proposes on every ticket, and how the critical path sets the finish date

"What does that plan actually contain? Every ticket AWF proposes carries six things. A priority, ranked from the specification and from what the ticket unblocks. A size: an effort estimate, with the assumptions behind it. A planned start date and a planned end date. Its dependencies. A risk tier, which sets how many review rounds it gets. And a lane."

"The dates aren't typed in by hand. They come from the critical path analysis on the right. AWF lays the tickets out by dependency and finds the longest chain. In our example that's data contract, parser, validation, integration tests. That chain sets the earliest date the Epic can finish, so those tickets go first. Reporting isn't on that chain. It has slack, so it runs on a feeder lane when there's capacity to spare."

"Two things follow. You can see on day one where the finish date comes from, and which tickets you can't afford to let slip. And the reports compare the estimates with what actually happened, so you watch the forecast move."

"All of it is a proposal. You can change any priority, size or date before you authorize creation."

> Field names follow the team's own Jira configuration, which is why the planning prompt reads the live fields first.

> A ticket with no estimate or no owner is reported as unestimated or unassigned. AWF does not fill the gap with a guess.

> Sources: Jira planning template; .agentic/lib/agentic/planning_graph.py; .agentic/templates/reporting/layouts.md.

## 9. Twelve lanes and selectable model routes  (1.5 min · 10:00 → 11:30)
*On screen:* 12 worker lanes A–L by default; you choose within governance

"How much runs at once? New projects in 1.9.6 default to twelve worker lanes, labelled A to L. The table shows the default model routes. Ordinary workers, the controller, the critic and specialists all use Sol at high effort. Simple work can go to Luna, the cheaper model, but only when it is low risk, low complexity and low uncertainty, with strong verification and no risk flags. A low tier alone isn't enough."

"Twelve lanes is a ceiling. Twelve workers plus the controller and the shared critic need fourteen model slots on the host. If the capacity isn't there, the work doesn't start. And AWF never quietly downgrades a critic to save capacity."

"So start small. The prompt on the right asks for the accepted settings, starts with three lanes, and asks what prevents safe parallel starts. You can pick any approved model and effort. Changes apply to future work; running tasks keep the route they started with."

> An upgraded project keeps its lane count and routes. Where an older project never set a lane cap, the cap is still six; a fresh project is given twelve explicitly.

> If the host cannot serve a configured route, AWF refuses. There is no silent fallback to another model.

> Sources: .agentic/docs/27-MODEL-ROUTING.md; 29-OPERATING-CONFIGURATION.md; 52-ADOPTION-REPAIRS.md.

## 10. 24/7 work, batched review requests  (1.5 min · 11:30 → 13:00)
*On screen:* Default calendar: continuous work, 07:00 weekday review digest, 17:00 cutoff

"This is the main change in how 1.9.6 feels to work with. The default calendar separates working from asking. Work mode is continuous: implementation, testing and independent review run twenty-four hours a day, seven days a week. But the routine request for your merge authorization waits for a review window. By default that is seven in the morning to five in the evening, London time, Monday to Friday."

"So a PR that becomes ready at eight in the evening is held and appears in the next morning's seven o'clock digest. Anything finished on Friday evening or over the weekend arrives on Monday morning."

"Three things to know. First, only routine requests wait. Safety escalations are never held. Second, before a digest is sent, each PR is checked again: still open, not merged, still at the commit that was reviewed. If it has changed, it is withdrawn and has to qualify again. Third, the digest authorizes nothing. You still review and approve each exact candidate."

"The times are yours to change, through a settings pull request. One caveat: there is no holiday list, so a digest still turns up on a bank holiday."

> Batching can be switched off, in which case ready PRs are presented immediately. Calendar changes take effect only once the settings PR is merged and fetched.

> A digest whose delivery outcome is unknown is never re-sent blindly; it is reconciled against the provider first.

> Sources: .agentic/docs/operating-calendar.md; OPERATING_CONFIG.yaml defaults; AWF-244.

## 11. Your first ticket  (1.5 min · 13:00 → 14:30)
*On screen:* Worked example (illustrative): validate missing timestamps

"Now one ticket from that plan. The example is 'validate missing timestamps'. It's illustrative; I'm not showing you a real run. It touches only the validation module and its tests. Nothing in pricing, nothing in ingestion."

"The prompt says: work on this ticket under AWF, check the scope, the risk tier, the review cap and the dependencies, implement and test only what's allowed, keep the PR as a draft, and don't merge."

"The five boxes are what makes a ticket safe to hand over. Acceptance: what must be true, and the test cases. Scope: the allowed paths and the exclusions, named before work starts. Validation: the actual commands and results, including anything skipped. External data: approved aliases only, and access granted to the worker session itself. The fact that your session can read a dataset doesn't mean the worker's can. And risk: a small change can still be high risk."

> The example is a teaching aid, not an executed ticket. In a live walkthrough, ask for real evidence from the team's own project.

> Sources: AWF User Guide · Run your first ticket.

## 12. Parallel work without overlapping writers  (1 min · 14:30 → 15:30)
*On screen:* Illustrative plan: three active lanes, one of them correctly waiting

"With several tickets under way it looks like this. Lane A owns the validation module. Lane B is writing the reporting output in a separate module, so the two can't collide. Lane C is waiting, because the integration tests depend on A."

"That idle lane is correct. AWF only uses spare capacity for work that is truly independent, and it checks dependencies, overlapping files and shared resources before it starts anything."

"The prompt on the right is the one you'll use most: show each lane, its ticket, its model, the blockers, the review state and the next action. Ask it to keep what it has observed separate from what it is forecasting."

> The optional file-based status report takes supplied records and may show UNKNOWN; it does not query Jira live. The Teams and Slack reports new in 1.9.6 have their own slide later.

> Sources: Running guide · planning and reporting.

## 13. Draft PR and independent critic  (2 min · 15:30 → 17:30)
*On screen:* Writer returns changes, tests and a draft PR; a separate critic reviews the exact candidate

"The writer finishes. What comes back is a scoped change, the real test results, and a draft pull request. Ask to see the PR, the exact commit, the changed files, the test commands and results, and anything skipped. And ask what the validation doesn't cover."

"Then the independent critic. This is a separate reviewer, in a separate context, checking that exact commit against the specification, the ticket and the risk policy. The writer can never approve its own work."

"Review is bounded by risk tier. Tier one gets one round. Tier two gets up to two. Tier three gets up to three, plus owner review. It stops at the first valid pass. An open blocker or major finding stops the ticket moving forward."

"To make that concrete, a critic on our ticket might say: the timezone conversion needs a test at a daylight-saving boundary. That's a possible example, not a real finding. The writer fixes it within scope, and the review runs again on the new commit."

"And if the cap is reached with findings still open, it doesn't quietly start another round. It comes to you, and you decide: merge with notes, park it, rescope it, or allow one bounded extension."

"One rule underlies all of this: evidence belongs to a specific commit. If the code, the base, the requirements or the policy changes, the affected checks are run again."

> A blocker or major finding has to name an acceptance criterion or a boundary, or it is refused. That is what stops endless nitpicking.

> A worker whose sandbox cannot commit hands its tested tree to the publisher; nobody weakens Git permissions to get round it.

> Sources: AWF User Guide · Evidence and review; .agentic/docs/30-REVIEW-TIERS-AND-CLOSEOUT.md.

## 14. PR queueing, batching and merge order  (1.5 min · 17:30 → 19:00)
*On screen:* Ask for a usable integration plan; queue versus merge train

"When several lanes finish close together, you need an order. This prompt asks for one: every PR ready for owner review, the best merge order, the current commit, CI and critic evidence for each, and any conflicts. And it ends 'do not merge or weaken any gate'."

"Even with no automation, that ordered list is worth having. It tells you what to look at first."

"There are two mechanisms behind it, and they differ in what you approve. With the integration queue, your individual PRs remain the things you merge; a throwaway integration branch only checks that they work together. With a merge train, AWF builds a new combined batch, and that batch gets its own reviews, its own CI and its own authorization. Approving a source PR admits it to a batch. It doesn't approve the batch."

"Live batching needs an adapter that supports it, and the reference adapter shipped with AWF does not. So today, treat this as ordered handoff. You keep the merge either way."

> Do not claim auto-merge or working batch publication from an enabled setting. Conflicts in hand-written files are refused, not resolved by an unreviewed steward.

> Sources: .agentic/docs/42-PR-INTEGRATION-QUEUE.md; 43-MERGE-TRAIN.md.

## 15. Owner review, authorization and merge  (1.5 min · 19:00 → 20:30)
*On screen:* AWF returns a current handoff packet; you inspect the exact candidate and authorize

"Then it comes to you. The handoff packet has the PR link and the exact commit and base; how each acceptance criterion was met; CI and validation results; the independent reviews and what happened to each finding; and anything still open."

"Your job is to inspect that exact candidate. Does it still solve the problem you accepted? Is the evidence current? What risk remains?"

"The question in the middle is a good habit: for this exact PR and commit, what passed, what is still unknown, and has anything changed since the review ran?"

"Then you authorize, and the merge goes through the supported route. Being ready for review is not permission to merge. Afterwards AWF observes the result and ties its closing record to the actual merge commit. If the outcome of a merge is uncertain, it checks with GitHub before anyone retries."

"In short: you get a package you can inspect, and the merge decision stays with you."

> Do not teach an abbreviated approval syntax. The accepted project defines the exact form of authority and how fresh the evidence must be. If a merge train is used, the batch is its own candidate.

> Sources: Ticket lifecycle · final gates.

## 16. Jira updates and recorded closeout  (1 min · 20:30 → 21:30)
*On screen:* Confirm the project binding; observe, update and read back; check the result

"Finishing the ticket. AWF starts out not connected to Jira. Before it writes anything, you name the site, the project, which Epics or filter it may touch, and the status mappings. Projects in the same repository don't inherit each other's tickets."

"Once bound, the controller moves the ticket through its lifecycle, and it is the only writer. 'Done' follows a confirmed merge and a verified closeout, not the writer saying it has finished. It reads Jira before and after every write, and if the result isn't what it expected, it stops and tells you."

"1.9.6 closes several gaps here, around updates after merge, owner holds and assignment. If Jira is disabled, AWF keeps local records and tells you plainly that nothing was written."

> AWF never transitions Epics. Creating a backlog (the Jira planning slide) and lifecycle updates during delivery are authorized separately.

> Sources: AWF User Guide · Jira integration; AWF-243.

## 17. Project reports in Teams and Slack  (1.5 min · 21:30 → 23:00)
*On screen:* Optional, off by default: hourly and end-of-day reports, owner alerts first

"New in 1.9.6: project reports in Teams or Slack. This is optional and off by default."

"When it's on you get two regular reports. An hourly report, around the clock; if the system was down for a while, you get one labelled catch-up, not a flood. And an end-of-day report, at five in the evening London time by default. Owner alerts, the things that need a decision from you, are always delivered first."

"The reports are built from recorded evidence. Where data is missing, the report says 'unavailable'. It doesn't estimate."

"Turning it on is an owner decision made through a settings pull request, like the calendar. You choose Teams, Slack or both, and each destination is tied to one project and repository. Credentials are given as a reference to a secret, never pasted in. Editing the file on a branch does nothing until it is merged."

"And a report has no authority. It can't dispatch work, write to Jira or merge. It tells you what happened."

> Known limit: under sustained load, review notifications can be delayed for many cycles while alerts stay first. Hourly and end-of-day reports are unaffected. A bounded fix is planned for 1.9.8.

> A send is confirmed by reading it back from the platform, with at most two attempts. Live delivery needs a qualified host and transport.

> Sources: .agentic/docs/REPORTING-196.md; REPORTING-SETTINGS-196.md; AWF-241.

## 18. Operating choices and protected limits  (1 min · 23:00 → 24:00)
*On screen:* Your operating choices versus reviewed governance changes

"Who can change what? Two files. The operating file holds your day-to-day choices: how many lanes, which approved models. You can change those by asking, within the accepted limits, and they apply to future work."

"The project configuration holds the limits that matter: ceilings, allowed models, review floors, merge policy. Those change only through a reviewed pull request, and AWF refuses an invalid change outright."

"The boundary to remember is at the bottom right. A setting on its own starts nothing. AWF does not merge its own work, invent a review, work around missing data access, or retry something whose outcome it doesn't know."

> The five controller switches are the proposed policy. An installed project still needs accepted governance, qualified adapters, credentials and real capacity before any of them does anything.

> Sources: Operating policy; .agentic/docs/29-OPERATING-CONFIGURATION.md.

## 19. Token budgets: off by default  (1 min · 24:00 → 25:00)
*On screen:* Dormant limits, both switches off; ask in chat, approve as a PR

"Budgets changed in 1.9.6. A new project starts with token accounting off and enforcement off. The limits are there but dormant: four million tokens per ticket and a billion over the life of the project."

"There are two switches. Accounting records usage and refuses nothing. Enforcement, which needs accounting, refuses work at the limit."

"To change any of it, ask in chat. AWF validates the proposal and prepares a pull request showing before and after. The owner merges it, and it takes effect once fetched. Editing the file locally changes nothing, even to switch enforcement off."

"If your project is upgraded from 1.9.5, it keeps its existing budget policy until an owner adopts the new controls."

> There are no per-Epic budget overrides. Before enabling accounting on an upgraded project, follow the budget-history prerequisites in the migration guide.

> Sources: .agentic/docs/39-BUDGETING.md; MIGRATION-v1.9.5-to-v1.9.6.md; AWF-242.

## 20. Upgrades and new machines  (0.5 min · 25:00 → 25:30)
*On screen:* Upgrades preserve and compare your project; one command rebuilds the runtime on a fresh clone

"Two housekeeping items, briefly. Upgrading a project to 1.9.6 is preservation-based: AWF snapshots the project before and after and compares them, and it refuses if anything it doesn't recognise has changed. The project must be quiet while that runs. And on a new laptop, Git doesn't copy AWF's local runtime, so after cloning you run the provision-runtime command on the right. It makes no commit and changes no project record."

> Most of the team will never run an upgrade; the repository owner does. The fresh-clone command is the part everyone needs.

> The host Python must match the wheelhouse's Python and platform tags; 3.12 is an example. Afterwards run verify-installation, validate-config and status with the installed runtime.

> Sources: MIGRATION-v1.9.5-to-v1.9.6.md; .agentic/docs/38-RELEASE-196-QUALIFICATION.md; docs/runbook/41-runbook-fresh-adopted-clone.md.

## 21. Daily prompts and quantitative validation  (1 min · 25:30 → 26:30)
*On screen:* Two prompts for every session, and three rules for research evidence

"Day to day you need two prompts. At the start of a session: resume the project, inspect status and open PRs, reconcile anything unfinished, and show what needs a decision. Before you stop: pause new dispatch, account for running workers, and save a handoff. Pausing doesn't stop workers that are already running."

"And three rules for research work. Put the research acceptance in the specification: data snapshot, point-in-time availability, tolerances. Keep coding evidence and research evidence apart: a green build and a critic's approval don't show that a signal works. You still need leakage controls, out-of-sample tests and realistic costs. And use approved data and compute, granted to the task that needs them."

"A backtest that hasn't been run is unverified, whatever the code review said."

> A correct implementation can still be an invalid study if data timing, assumptions or selection are wrong. With work now running overnight, the start-of-session prompt matters more than before.

> Sources: AWF User Guide · Quant work; Running guide.

## 22. Your first AWF project and reference links  (0.5 min · 26:30 → 27:00)
*On screen:* One Quant Foundry project, one accepted specification, one bounded first ticket

"So, the first step. One Quant Foundry project, one accepted specification, one bounded first ticket, run supervised. Use the prompts from this deck; the User Guide and Technical Reference have the full versions. Then look hard at the first complete handoff before you authorize anything."

> Do not suggest reinstalling AWF in Quant Foundry. In any wider pilot, record decisions and measure outcomes; do not claim a productivity gain that has not been measured.

> Sources: AWF User Guide; AWF Technical Reference.

## 23. What’s next: AWF 1.9.7  (1 min · 27:00 → 28:00)
*On screen:* Two themes: working the Agile way, and making AWF easier for people to read

"Finally, where this is going. The next release, 1.9.7, has two themes."

"The first is fitting in with how teams already work. AWF plans by dependency and critical path; most teams plan in sprints. 1.9.7 is intended to bring the two together: AWF work organised into sprints, with milestones that have target dates, so it sits on the same boards and in the same meetings as everything else."

"The second is what I call humanizing AWF. A lot of what it writes today, the documentation, the Jira tickets, the reports, is precise but hard going. The aim is that a person can pick up any AWF ticket or document and understand it at first reading: plain language, the purpose up front, and the framework's own vocabulary kept out of the way."

"Those are plans, not promises. I'll confirm the scope when the release is defined. Questions?"

> Nothing on this slide is in 1.9.6. Do not give dates. If asked for detail, say the 1.9.7 scope is still being set and invite the team's suggestions, especially on which Jira fields and documents they find hardest to read.

> Sources: Owner's stated direction for 1.9.7, 17 November 2026.

## Appendix A: Adoption states and candidate evidence
*Optional reference, outside the timed core.*

> Use this only if asked what ACTIVE proves. Keep deployment availability distinct from a specific installed version. Do not infer live automation from a status.

> Sources: AWF User Guide · activation.

## Appendix B: Installation for repositories outside Quant Foundry
*Optional reference, outside the timed core.*

> Use the published 1.9.6 distribution and its pins; do not reuse an older distribution or checksum.

> Managed-runtime commands use the installed interpreter, not an arbitrary system Python. Missing CI or Jira is a reported limitation, not a reason to stop preparing a local adoption.

> Sources: AWF User Guide · installation runbook; .agentic/docs/20-NEW-PROJECT-SETUP.md.

## Appendix C: Live-operation conditions, release status and known limits
*Optional reference, outside the timed core.*

> The main deck is an operator tutorial. This appendix lets the presenter answer questions about availability and qualification accurately. Release publishing itself runs on Linux and Windows; macOS fails closed.

> Sources: .agentic/docs/38-RELEASE-196-QUALIFICATION.md; REPORTING-196.md; 32-ACTIVATION-STATUS.md.
