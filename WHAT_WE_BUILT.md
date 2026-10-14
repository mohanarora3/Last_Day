# Last Day: what we built and why

Last Day is our entry for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB, Oct 15–18, 2026), **Track 03: Company Brain**.

> **Know what your company forgets before someone leaves.**

---

## 1. The problem

Every company has lost the one person who knew why some system works the way it does, and found out only after they left. Today you find out too late, when an incident hits a system nobody understands anymore.

The closest existing tool is CodeScene's off-boarding simulator. It only looks at who committed code, it's only in their on-premise version, and it only shows a heatmap. Last Day goes further in two ways:

- **More sources:** code, reviews, Slack answers, docs, tickets, incidents, decisions and customer ownership.
- **It acts:** it writes the exit interview, finds successors, drafts handovers and answers questions from the trail of people who left.

## 2. How we got to this idea

1. We read the hackathon brief. There are three tracks: Agents That Act on Connected Data, Agent Memory and Coordination, and Company Brain.
2. We picked one idea per track after checking what already exists:
   - **Track 1: Domino.** Find prescribing cascades: medicines given only to treat another medicine's side effect.
   - **Track 2: Pheromone.** Agent memory that lives on the work itself and fades unless other agents confirm it.
   - **Track 3: Last Day.** Knowledge-loss simulation plus an agent that saves the knowledge.
3. We dropped ideas that were already taken: memory-poisoning defence (MemLineage, Memward) and blast-radius guardrails for coding agents (BUER).
4. We chose **Last Day for Track 3**. It covers every point in the track brief, and the backtest gives us real-world evidence.

## 3. What Last Day does

| Feature | What happens |
|---|---|
| **Remove a person** | Click someone. Last Day recomputes what the company still knows. Areas turn red (orphaned), amber (fragile) or purple (already a gap). |
| **Cascade** | Red spreads along dependencies. Anything built on an orphaned system inherits the risk. |
| **Criticality ranking** | Areas are ranked by FalkorDB PageRank on the dependency graph, plus business importance. |
| **Team bridges** | Betweenness centrality finds people who are the only link between two teams. |
| **Exit interview** | Questions are generated only about what will be lost, and each one cites its source. For example: "You answered 29 questions about Ledger Reconciliation; everyone else combined answered 3." |
| **Answers go back into the graph** | Saved answers become written knowledge. Coverage goes up and red turns amber. |
| **Successors** | The best person to take over is ranked by graph closeness: direct knowledge, neighbouring systems, how often the leaver helped them, team ownership and current workload. |
| **Handover tickets** | A ready ticket with a reading list, interview answers, who to ask, and a checklist. Marking it done turns the area green. It can also be opened as a GitHub issue. |
| **Leave planning** | Switch to "On leave": "If Arjun and Meera are away Diwali week, who covers payments?" |
| **Ask / Ghost Mode** | Ask anything. Answers quote their sources, including people who already left ("according to Vikram, who has left…"). |
| **Cypher drawer** | Every answer shows the exact FalkorDB queries that produced it, with timings. |
| **GitHub repos** | Load any repo. PRs, reviews, discussions and issue answers become a graph of who knows which folder. |
| **Backtest** | Rebuild a repo's graph from data before someone left, predict what breaks, then check what actually happened. |
| **MCP server** | Claude, Cursor or any agent can call Last Day's tools directly. |

## 4. The demo company: Acme Pay (fictional)

Acme Pay is a made-up fintech built so the story works without any API keys:

- 17 people in 5 teams, with 21 systems, customers and processes.
- About 2,100 pieces of evidence across 18 months: commits, PRs, reviews, Slack answers, tickets, docs, incidents, decisions and customer ownership.

It has deliberate storylines:

- **Priya Raman** is the hidden single point of failure. She's the only one who really knows ledger reconciliation, she made the key idempotency decision, she owns the biggest customer's escalations, and she's the only link between Payments and Risk.
- **Arjun and Meera** both know the payments gateway. Take both out for Diwali week and only Priya is left, with 32% familiarity.
- **Vikram Nair** left 8 months ago. The bank SFTP integration has been silently orphaned ever since.

When you remove Priya, 3 areas are orphaned, 1 becomes fragile, 7 more inherit risk through dependencies, Payments and Risk lose 100% of their connection, and knowledge health drops from 87 to 76.

## 5. How the graph does the work

### The data model in FalkorDB
```
(Person)-[:MEMBER_OF]->(Team)-[:OWNS]->(Asset)
(Person)-[:AUTHORED]->(Evidence)-[:ABOUT]->(Asset)
(Person)-[:HELPED]->(Person)
(Asset)-[:DEPENDS_ON]->(Asset)

computed:  (Person)-[:KNOWS {score}]->(Asset)
           (Person)-[:COLLAB]->(Person)
           (Asset)-[:HANDED_TO]->(Person)
           (Question)-[:FOR]->(Person)
```
Each company or repo gets its own graph inside one FalkorDB instance.

### Scoring
1. **Knowledge:** each piece of evidence has a weight by type (a decision counts more than a commit) and fades with age. Summing it in Cypher gives a KNOWS score from 0 to 1. The scale adapts to each graph, so a small startup and a huge repo are comparable.
2. **Coverage:** the probability that at least one remaining person, or something written down, can answer a question about an area.
3. **Statuses:**
   - **Orphaned:** coverage below 40%.
   - **Fragile:** coverage below 65%, or only one real expert left, or the leavers take at least half of what's known about it.
   - **Already a gap:** nobody knew it before anyone left.
4. **Cascade:** dependency paths up to 3 hops, found with a variable-length Cypher query.
5. **Algorithms:** FalkorDB's built-in `algo.pageRank`, `algo.betweenness` and full-text search.

Every number in the scoring lives in `lastday/config.py`, with a comment explaining it.

## 6. Code structure

```
lastday/
  ingest/      demo_company.py, github.py, loader.py   → data in
  engine/      knowledge.py, simulate.py, algorithms.py, successors.py   → the thinking (Cypher)
  agent/       interview.py, handover.py, ask.py, llm.py   → acting on results
  backtest.py  → real-data proof
  server.py    → web API      mcp_server.py → MCP tools      service.py → shared glue
  db.py        → FalkorDB connection + logs every Cypher query
  config.py    → every scoring knob
web/           → the interface (graph, tabs, Cypher drawer); Cytoscape bundled so it works offline
scripts/cli.py → command line
tests/         → test_e2e.py (28 checks), make_fixture.py (offline fake repo)
```

## 7. How we built and tested it

- **Real FalkorDB, not a mock.** Docker Hub was blocked in the build environment, so we compiled FalkorDB and Redis 8 from source and tested against them.
- **Both client paths tested.** The official `falkordb` Python client and a built-in fallback client both pass all 28 checks.
- **Browser testing.** We drove the full UI flow in a real browser with screenshots. That caught and fixed the headline overlapping the graph, a broken Handover tab, the health box showing when it shouldn't, and the mobile layout.
- **Tuning the model.** We adjusted scoring until the stories came out right, and added two extra signals along the way:
  - **"Only one expert left"**, which catches the Diwali case.
  - **"Share of knowledge lost"**, because coverage alone missed a maintainer who held 60% of what was known about an area.
- **Offline backtest.** A fake repo where the core maintainer leaves. Modules predicted at risk slowed down 11.6×, while predicted-safe ones didn't slow (0.9×).

## 8. Fixes after testing on real data

Testing on the real **FalkorDB/FalkorDB** repo showed that AI review bots were counted as people: `copilot-pull-request-reviewer`, `cubic-dev-ai`, `github-code-quality`, `copilot-swe-agent` and `github-advanced-security`. GitHub doesn't give them the usual `[bot]` suffix. Because they comment on almost every PR, they made coverage look better than it really is.

Fixes:
- Bots are now recognised by name patterns (`copilot`, `github-…`, `…-ai`, `…-agent`, `…-reviewer`).
- Last Day also asks GitHub whether each account is a bot.
- The **Load a GitHub repo** dialog has a **Download fresh data** option, and its default history is now 365 days.

## 9. How to run it

```bash
cd ~/Desktop/company_brain
docker compose up -d                       # FalkorDB
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m lastday.server                   # open http://localhost:8000
```

- **No API keys needed** for the demo.
- **Optional `.env` settings:** `GITHUB_TOKEN` for real repos and backtests, and `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` for nicer written answers.
- **If port 8000 is busy:** `lsof -ti :8000 | xargs kill -9`, or `PORT=8001 python -m lastday.server`.
- **Tests:** `python tests/test_e2e.py`, which should end with `ALL 28 CHECKS PASSED`.

## 10. Honest limitations

- Knowledge is inferred from activity. Last Day can't see what someone knows but never wrote, reviewed or answered. That gap is what the exit interview is for.
- The Slack, docs, tickets and CRM data in the demo is synthetic. Real connectors would only need to output the same normalized format.
- Code dependencies in GitHub repos are estimated from files that change together, not from parsing imports.
- The project files are dated 12 Oct 2026, before the hackathon's Oct 15 start. The rules ask for the main FalkorDB work to be done during the event.

## 11. What's left to do

- [ ] Reload FalkorDB/FalkorDB with **Download fresh data** to get the bot-free graph.
- [ ] Find a real repo where a maintainer left (use **Find who went quiet**) and run the backtest. This is the strongest proof for judges.
- [ ] Record the 3-minute demo video (script in `DEMO.md`).
- [ ] Optional: add a free LLM key (for example Groq) for nicer Ask answers in the video.
- [ ] Push to GitHub. Make sure `.env` stays out; it's already in `.gitignore`.
- [ ] Write the blog post for the AirPods side prize.
