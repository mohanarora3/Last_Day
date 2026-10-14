# Last Day: Feature List

**Know what your company forgets before someone leaves.**

Entry for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB, Oct 15–18, 2026), Track 03: Company Brain.

This document has three parts: what Last Day does today, the features we propose to add, and the decisions we need from you.

---

## 1. Summary

Last Day builds a FalkorDB graph of who knows what in a company. Its sources are code, reviews, Slack answers, docs, tickets, incidents, decisions and customer ownership. When you remove a person, it recalculates what the company would no longer know. Then an agent works to keep that knowledge: it writes a targeted exit interview, recommends successors, drafts handover tickets, and answers questions from the records of people who have already left.

The closest existing tool is CodeScene's off-boarding simulator. It only looks at who committed code, it is only in their on-premise version, and it only shows a heatmap. Last Day uses many more sources, and it acts on what it finds.

---

## 2. Features built so far

### Simulation and analysis

| Feature | What it does |
|---|---|
| **Remove a person** | Click someone, and Last Day recalculates the company's knowledge. Each area turns red (orphaned), amber (fragile) or purple (already a gap). |
| **Cascade** | Risk spreads along dependencies. Anything built on an orphaned system inherits the risk, up to 3 hops away. |
| **Criticality ranking** | Areas are ranked using FalkorDB's PageRank on the dependency graph, combined with business importance. |
| **Team bridges** | Betweenness centrality finds people who are the only link between two teams. |
| **Knowledge health score** | A single company-wide number, for example 87 → 76 when Priya is removed. |
| **Leave planning** | "If Arjun and Meera are on leave Diwali week, who covers payments?" |

### Agent actions

| Feature | What it does |
|---|---|
| **Exit interview** | Questions are generated only about knowledge that will be lost, and each one cites its source. For example: "You answered 29 questions about Ledger Reconciliation; everyone else combined answered 3." |
| **Answers go back into the graph** | Saved answers become written knowledge, coverage goes up, and red areas turn amber. |
| **Successor ranking** | Ranks who should take over, using direct knowledge, neighbouring systems, how often the leaver helped them, team ownership and current workload. |
| **Handover tickets** | Each ticket includes a reading list, interview answers, who to ask, and a checklist. Marking it done turns the area green. It can also be opened as a GitHub issue. |
| **Ask / Ghost Mode** | Answers questions with cited sources, including people who have left ("according to Vikram, who has left…"). |

### Data and integrations

| Feature | What it does |
|---|---|
| **GitHub ingestion** | Pulls PRs, reviews, discussions and issue answers from any repo through the GraphQL API, with an offline cache. Bots are filtered out. |
| **Backtest** | Rebuilds a repo's graph from data before someone left, predicts what will break, then measures what actually happened. |
| **MCP server** | Claude, Cursor or any MCP client can call `who_knows`, `simulate_departure`, `who_covers`, `exit_interview`, `ask` and `list_people`. |
| **Cypher drawer** | Every answer shows the exact FalkorDB queries that produced it, with timings. |
| **Demo company** | Acme Pay, a fictional fintech: 17 people, 21 systems, about 2,100 pieces of evidence. No API keys are needed. |

### Quality

- 28 end-to-end checks run against a live FalkorDB, and pass with both the official Python client and the built-in fallback client.
- The whole UI flow was tested in a real browser.
- In the offline backtest, modules predicted at risk slowed down **11.6×**, while modules predicted safe did not slow down (**0.9×**).

---

## 3. Proposed features

Effort is a rough estimate for one developer.

### Priority A: highest value for the hackathon

| # | Feature | Why it matters | Effort |
|---|---|---|---|
| A1 | **Pull-request guardian (GitHub Action)** | When a PR touches a module with low coverage, the bot comments with the expert, a suggested reviewer and the coverage figure. This turns Last Day from a dashboard into an agent working inside the team's normal workflow, which fits the hackathon theme directly. | ~1 day |
| A2 | **Vector + graph search for Ghost Mode** | Uses FalkorDB vector indexes to find evidence by meaning, then walks the graph for who said it, about which system, and when. This is a strong FalkorDB showcase. | ~1 day |
| A3 | **True bus factor per area** | Finds the smallest group of people whose departure orphans each area, for example "Payments has a bus factor of 2." This is easier to explain than a percentage. | ~0.5 day |
| A4 | **Knowledge-sharing planner** | "You have 5 pairing sessions. Which pairs raise coverage the most?" Because coverage has diminishing returns, a greedy choice is provably close to the best possible. This moves Last Day from describing the problem to prescribing the fix. | ~1 day |

### Priority B: fixing current limitations

| # | Feature | Why it matters | Effort |
|---|---|---|---|
| B1 | **Real code dependencies** | Parse imports with tree-sitter instead of relying only on files that change together, so the cascade is more accurate on real repos. | ~1 day |
| B2 | **Real Slack connector** | Read a Slack workspace export, which is plain JSON and needs no API approval. This answers the criticism that the demo data is synthetic. | ~0.5 day |
| B3 | **Stale documentation detection** | A doc written before major code changes counts for less, so old docs no longer make an area look safe. | ~0.5 day |
| B4 | **Multi-repo backtest** | Run 3–5 real departures and report the successor hit rate and slowdown across all of them. This is the strongest evidence we can show the judges. | ~1 day |

### Priority C: larger features if time allows

| # | Feature | Why it matters |
|---|---|---|
| C1 | **Knowledge health over time** | Monthly coverage trends per area, with alerts when an area is slowly declining. |
| C2 | **Reorganization simulation** | Moving a person to another team, not just removing them. |
| C3 | **Onboarding mode** | A learning path for a new hire, ordered by dependencies, with who to ask about each system. |
| C4 | **Incremental updates** | Use GitHub webhooks to update only the affected parts of the graph, so it scales to large organizations. |
| C5 | **More connectors** | Jira or Linear, Notion or Confluence, and PagerDuty. |

### Privacy and responsible use

Features that watch individuals can look like they predict who will quit. We propose:

- Presenting all signals as **where knowledge is concentrated**, never as monitoring of people.
- Team-level views for managers, and letting people see their own data.
- An opt-out, plus a short privacy section in the README and the demo.

---

## 4. Current limitations

- Knowledge is inferred from activity. Last Day cannot see what someone knows but never wrote, reviewed or answered. The exit interview exists to close that gap.
- The demo's Slack, docs, tickets and CRM data is synthetic (B2 addresses this).
- Code dependencies in GitHub repos are estimated from files that change together (B1 addresses this).
- **Hackathon timing:** the project files are dated 12 Oct 2026, before the event starts on Oct 15. The rules ask for the main FalkorDB work to be done during the event. Building Priority A features during the event, with commits, gives us clearly in-window work to point to.

---

## 5. Remaining tasks before submission

- [ ] Reload FalkorDB/FalkorDB with **Download fresh data** to get a graph without bots.
- [ ] Find a real repo where a maintainer left, and run the backtest.
- [ ] Record the 3-minute demo video (script in `DEMO.md`).
- [ ] Optional: add a free LLM key for better-written Ask answers.
- [ ] Push to GitHub, keeping `.env` out of the repo.
- [ ] Write the blog post for the side prize.

---

## 6. Decisions needed

1. Which **Priority A** features should we build during the event? We recommend A1 and A2, plus A3 if there is time.
2. Should we spend time on a **real data connector (B2)**, or focus on the **multi-repo backtest (B4)** as our main evidence?
3. Should we take any steps on the **hackathon timing** point?
4. Any other features or changes you'd like to see?
