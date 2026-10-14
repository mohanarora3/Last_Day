# Last Day

**Know what your company forgets before someone leaves.**

Every company has lost the one person who knew why some system works the way it does, and found out only after they left. Last Day builds a FalkorDB graph of who knows what, using GitHub, Slack answers, docs, tickets, incidents, decisions and customer ownership. Remove a person and it recomputes what the company would no longer know. Then its agent goes and saves that knowledge: a targeted exit interview where every question cites its source, successor recommendations, handover tickets, and a Ghost Mode that answers questions from the trail of people who already left.

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB), Track 03: Company Brain.

---

## Run it (5 minutes)

You need Docker and Python 3.10+.

```bash
docker compose up -d                 # FalkorDB on :6379, FalkorDB Browser on :3000
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # optional: GITHUB_TOKEN, an LLM key
python -m lastday.server             # http://localhost:8000
```

The first start builds the **Acme Pay** demo graph: a fictional fintech with 17 people, 21 systems, customers and processes, and about 2,100 pieces of evidence. No API keys are needed for the demo. With `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (any OpenAI-compatible endpoint), answers and handover summaries are written by an LLM. Without one they are extractive and still cited.

`make db`, `make setup` and `make run` do the same steps.

## The 3-minute demo

1. **Click Priya Raman.** Her node goes dark, and Ledger Reconciliation, Zenith Retail (the biggest customer) and Settlement Batch turn red. Red spreads one hop at a time along dependency edges to everything built on top of them. The headline says Payments and Risk lose 100% of their connection: she was the only link.
2. **Impact tab.** Areas are ranked by criticality (FalkorDB `algo.pageRank` on dependencies plus business weight) times the coverage lost. Knowledge health goes 87 → 76.
3. **Write their exit interview.** For example: *"You've answered 29 questions about Ledger Reconciliation in #payments; everyone else combined answered 3. What are the three questions people keep asking?"* Each question lists the Slack threads, ADRs and incidents it came from.
4. **Answer two questions.** The answers are written back into the graph as persistent knowledge, and Ledger Reconciliation goes from red to amber in front of you (24% → 47%).
5. **Handover tab.** Rahul Verma is the top successor because he works on neighbouring systems, Priya answered his recon questions 7 times, and he's on the owning team. Draft the ticket, mark it done, and the node goes green.
6. **Ask:** *"If Arjun and Meera are on leave Diwali week, who covers payments?"* The answer is Priya, at 32% familiarity. **Ask:** *"How do I rotate the Unity Bank SFTP key?"* Ghost Mode answers from Vikram's trail. He left 8 months ago, and that integration has been a silent gap ever since.
7. **Open "Cypher behind this answer"** at the bottom to show the exact queries FalkorDB ran, with timings.
8. **Backtest tab.** Run a real repository (see below) and show that areas predicted at risk really did slow down after the person left.

## How the graph does the work

```
(:Person)-[:MEMBER_OF]->(:Team)-[:OWNS]->(:Asset)            Asset = system | module | customer | process
(:Person)-[:AUTHORED]->(:Evidence)-[:ABOUT {share}]->(:Asset)  Evidence = commit, pr, review, slack_answer,
(:Person)-[:HELPED {n}]->(:Person)                            issue_answer, ticket, incident, decision, doc,
(:Asset)-[:DEPENDS_ON]->(:Asset)                              customer_owner, oncall, interview, handover
derived:  (:Person)-[:KNOWS {score, raw, n, last}]->(:Asset)
          (:Person)-[:COLLAB {w}]->(:Person)
          (:Asset)-[:HANDED_TO]->(:Person)
          (:Question)-[:FOR]->(:Person), (:Question)-[:ABOUT]->(:Asset)
```

Each company or repo gets **its own graph** in one FalkorDB instance (`lastday__demo`, `lastday__gh__owner__repo`, …), using FalkorDB's multi-graph tenancy.

**1. Knowledge, computed in Cypher.** Every piece of evidence has a weight by kind (a decision counts more than a typo fix) and fades with age:

```cypher
MATCH (p:Person)-[:AUTHORED]->(e:Evidence)-[r:ABOUT]->(a:Asset)
WITH p, a, sum(e.weight * r.share * exp(-1.0 * ($now - e.ts) / $tau)) AS raw, count(e) AS n
CREATE (p)-[:KNOWS {raw: raw, n: n}]->(a)
```

`score = 1 - exp(-raw / saturation)`. Saturation adapts to each graph, so a person at the 90th percentile of evidence scores about 0.9. That keeps a 20-person startup and a 600-contributor repo on the same scale.

**2. Coverage, and what happens when someone leaves.** Coverage is the probability that at least one remaining person, or something written down, can answer a question about an area (noisy-OR):

```cypher
MATCH (a:Asset)
OPTIONAL MATCH (p:Person)-[k:KNOWS]->(a) WHERE p.active = true
WITH a, sum(log(1.0 - k.score)) AS before,
        sum(CASE WHEN p.id IN $removed THEN 0.0 ELSE log(1.0 - k.score) END) AS after,
        sum(CASE WHEN p.id IN $removed THEN k.raw ELSE 0.0 END) / sum(k.raw) AS share_lost
```

An area is **orphaned** when coverage falls below 40%. It is **fragile** when coverage falls below 65%, when only one person who really knows it remains, or when the leavers take at least half of everything known about it. It is **already a gap** when it was uncovered before anyone left.

**3. Cascade.** Anything that depends on an orphaned area inherits the risk, found with a variable-length path query:

```cypher
MATCH p = (a:Asset)-[:DEPENDS_ON*1..3]->(o:Asset) WHERE o.id IN $orphaned
RETURN a.id, o.id, length(p), [n IN nodes(p) | n.id]
```

**4. Graph algorithms as tools.** `algo.pageRank` ranks how critical each area is. `algo.betweenness` on the collaboration graph finds people who are the only link between teams. Full-text search (`db.idx.fulltext.queryNodes`) powers Ghost Mode.

**5. The agent acts and writes back.** Interview answers become persistent `Evidence` nodes, so coverage rises. A completed handover adds evidence for the successor plus a `HANDED_TO` edge. Everything is recomputed in the graph, so the next answer is better.

## Real data: GitHub and the backtest

```bash
export GITHUB_TOKEN=...               # a read-only token is enough
python scripts/cli.py ingest FalkorDB/FalkorDB
python scripts/cli.py departures owner/repo          # top contributors, flags who went quiet
python scripts/cli.py backtest owner/repo their-login [YYYY-MM-DD]
```

You can also click **Load a GitHub repo** or open the **Backtest** tab in the UI.

The GitHub connector reads PRs, files, reviews, PR discussion and issue answers through the GraphQL API, and caches them in `data/cache` so later runs work offline. Modules are directories. Module dependencies come from co-change coupling. Teams are inferred with community detection.

**Backtest:** build the graph using only data from before the person left, simulate their departure, then measure the year after:
- median time until a PR gets its first human response, for modules predicted at risk vs. predicted safe
- the share of PRs nobody responded to within 14 days
- the **successor hit rate**: how often the person who actually took over a module was in Last Day's top-3 recommendation

To find a good case, run `departures` on a repo you know and pick a top contributor marked *went quiet* while the project kept going.

On the bundled offline fixture (`tests/make_fixture.py`, a fake repo where the core maintainer leaves), modules predicted at risk slowed down **11.6×** while modules predicted safe didn't slow at all (**0.9×**).

## Real data: Slack and Notion

Click **Connections** in the top bar (or use the CLI below).

- **Slack.** Upload a workspace export ZIP (an admin exports it from Settings & administration → Workspace settings → Import/Export Data; no app needed), or paste the `xoxb-` token of a Slack app made for your workspace with `channels:read`, `channels:history`, `users:read` and `users:read.email`. Every reply in a thread by someone other than the asker becomes a `slack_answer`, with the asker as the person helped. A deactivated Slack account marks that person as already left.
- **Notion.** Create an integration with "Read content" and "Read user information including email addresses", share your pages or teamspaces with it, and paste its `ntn_` token. Each page becomes a `doc` for its author, or a `decision` for ADRs, RFCs and pages in a decisions database. Both count as written knowledge that outlives the author.
- **One person, three accounts.** GitHub, Slack and Notion accounts are joined by email, by a Slack handle equal to a GitHub login, or by the same full name. The panel shows how each match was made, suggests likely ones, and lets you fix them.
- **Areas.** With a GitHub repo, messages and pages are linked to its modules by name (or you map a whole channel to a module). Without one, each Slack channel becomes an area.

**Build company graph** writes it all into its own graph (`co__<name>`). Tokens and synced messages stay in `data/connections/`, which is gitignored; tokens are never sent to the browser.

```bash
python scripts/cli.py connect-slack path/to/slack-export.zip      # or: connect-slack --api  (SLACK_BOT_TOKEN)
python scripts/cli.py connect-notion                              # NOTION_TOKEN
python scripts/cli.py people --name Acme --repo owner/repo        # check how accounts were matched
python scripts/cli.py company --name Acme                         # build the graph
```

See `HOW_IT_WORKS.md`, section 8b, for the details.

## Use it from Claude, Cursor or any MCP client

```json
{"mcpServers": {"last-day": {"command": "/path/to/company_brain/.venv/bin/python",
  "args": ["-m", "lastday.mcp_server"], "cwd": "/path/to/company_brain",
  "env": {"LASTDAY_GRAPH": "demo"}}}}
```

Tools: `who_knows`, `simulate_departure`, `who_covers`, `exit_interview`, `ask`, `list_people`.

## Project layout

```
lastday/
  db.py                FalkorDB client wrapper (official client, or a built-in RESP fallback) + Cypher log
  config.py            every scoring knob, documented
  ingest/              loader (normalized format → graph), demo_company.py, github.py,
                       slack.py, notion.py, merge.py (one person across tools)
  connections.py       Slack / Notion settings, tokens and synced data (data/connections/, gitignored)
  engine/              knowledge.py (KNOWS), simulate.py (coverage, cascade, team links),
                       algorithms.py (PageRank, betweenness), successors.py
  agent/               interview.py, handover.py, ask.py (router + Ghost Mode), llm.py (optional)
  backtest.py          predict → measure what actually happened
  server.py            Starlette API + UI      mcp_server.py   MCP over stdio
web/                   index.html, app.js, styles.css, vendor/cytoscape (works offline)
scripts/cli.py         command line
tests/                 test_e2e.py (28 checks against a live FalkorDB), make_fixture.py,
                       test_connectors.py + make_connector_fixtures.py (Slack / Notion, fake APIs)
```

## Tests

```bash
python tests/test_e2e.py         # needs FalkorDB running; seeds the demo, runs every feature, the MCP server and a backtest
python tests/test_connectors.py  # Slack / Notion: 54 checks run anywhere, the rest need FalkorDB
```

## Limitations

- "Knowledge" is inferred from activity. It can't see what someone knows but never wrote, reviewed or answered. That's the gap the exit interview exists to close.
- The Slack, docs, tickets and CRM data in the demo is synthetic. Real Slack and Notion can be connected (above); tickets and CRM have no connector yet. Real connectors emit the same normalized format (see `ingest/loader.py`).
- Slack: only answers inside threads in public channels are read. Notion: only a page's creator and last editor are known.
- Code dependencies are approximated by co-change, not by parsing imports.
