# How Last Day works

This file explains the whole `company_brain` project in simple words, from the moment data comes in to the moment you see a red node on the screen. It follows the code in this folder, file by file.

If you only have two minutes, read sections 1 and 2.

---

## Contents

1. [The idea in one minute](#1-the-idea-in-one-minute)
2. [The whole pipeline in one picture](#2-the-whole-pipeline-in-one-picture)
3. [Words used in this file](#3-words-used-in-this-file)
4. [How to run it](#4-how-to-run-it)
5. [Step 1: Setup files](#5-step-1-setup-files)
6. [Step 2: Pulling data from GitHub](#6-step-2-pulling-data-from-github)
7. [Step 3: Turning GitHub data into a clean dataset](#7-step-3-turning-github-data-into-a-clean-dataset)
8. [Step 3b: The demo company instead of GitHub](#8-step-3b-the-demo-company-instead-of-github)
   - [8b. Step 3c: Slack and Notion (a connected company)](#8b-step-3c-slack-and-notion-a-connected-company)
9. [Step 4: Writing the graph into FalkorDB](#9-step-4-writing-the-graph-into-falkordb)
10. [Step 5: Working out who knows what](#10-step-5-working-out-who-knows-what)
11. [Step 6: Simulating someone leaving](#11-step-6-simulating-someone-leaving)
12. [Step 7: Finding a successor](#12-step-7-finding-a-successor)
13. [Step 8: The agent (Ask, exit interview, handover)](#13-step-8-the-agent-ask-exit-interview-handover)
14. [Step 9: The backtest (proof on real data)](#14-step-9-the-backtest-proof-on-real-data)
15. [Step 10: How you use it (web, MCP, command line)](#15-step-10-how-you-use-it-web-mcp-command-line)
16. [Step 11: How the graph is drawn in the browser](#16-step-11-how-the-graph-is-drawn-in-the-browser)
17. [Tests](#17-tests)
18. [Every file at a glance](#18-every-file-at-a-glance)
19. [Every setting you can change](#19-every-setting-you-can-change)
20. [Limitations and known issues](#20-limitations-and-known-issues)

---

## 1. The idea in one minute

In every company, some people are the only ones who really understand a system, a customer or a process. When they leave, that knowledge leaves with them, and nobody notices until something breaks.

Last Day tries to see this **before** it happens:

1. It collects **evidence** of who worked on what: pull requests, code reviews, answers to questions, documents, incidents, decisions and so on.
2. It stores all of this as a **graph** in FalkorDB (a graph database): people, systems, and the evidence that connects them.
3. From that evidence it calculates a **knowledge score**: how well each person knows each area.
4. When you click a person, it **removes them** and recalculates. Areas that nobody else knows turn **red**. Areas that depend on those areas turn red too.
5. Then an **agent** helps save the knowledge: it writes a targeted exit interview, suggests who should take over, writes a handover ticket, and answers questions using the trail of people who already left.

The project was built for the Graph Hacks hackathon (WeMakeDevs × FalkorDB), Track 03: Company Brain.

---

## 2. The whole pipeline in one picture

```
   GitHub GraphQL API         Slack export / API      Notion API          demo_company.py
   (PRs, reviews, issues)     (thread replies)        (pages, ADRs)       (fictional "Acme Pay")
            |                         |                     |                     |
            v                         v                     v                     |
   ingest/github.py           ingest/slack.py       ingest/notion.py              |
   - fetch, cache, clean      - answers             - docs, decisions             |
            |                         |                     |                     |
            |       (optional)        v                     v                     |
            +-------------> ingest/merge.py: one id per person,                   |
            |               link messages and pages to areas                      |
            |                         |                                           |
            +-------------------------+-------------------------------------------+
                               v
                      NORMALIZED DATASET
              (teams, people, assets, depends, evidence)
                               |
                               v
                      ingest/loader.py
              writes nodes and edges into FalkorDB
                               |
                               v
                      engine/knowledge.py
          calculates KNOWS scores (person -> area, 0 to 1)
                               |
           +-------------------+--------------------+
           v                   v                    v
  engine/simulate.py   engine/successors.py      agent/
  coverage, red/amber, who should take over      ask.py, interview.py,
  cascade, PageRank,                             handover.py, llm.py
  betweenness
           |                   |                    |
           +-------------------+--------------------+
                               v
                          service.py
                (shared functions for everything below)
                               |
           +-------------------+--------------------+
           v                   v                    v
       server.py          mcp_server.py        scripts/cli.py
   web API + web UI       tools for Claude,    terminal commands
   (web/ folder)          Cursor, etc.
```

The most important idea: **every data source ends up in the same "normalized dataset" format**. After that point, the code does not care whether the data came from GitHub, Slack, Notion or the demo company.

---

## 3. Words used in this file

| Word | Meaning in this project |
|---|---|
| **Graph** | Data stored as dots (nodes) and lines (edges). Example: `(Priya) -[KNOWS]-> (Ledger Reconciliation)`. |
| **FalkorDB** | The graph database. It runs in Docker and speaks the Redis protocol on port 6379. |
| **Cypher** | The query language for graphs. Looks like `MATCH (p:Person)-[:KNOWS]->(a:Asset) RETURN p, a`. |
| **Node label** | The type of a node: `Person`, `Team`, `Asset`, `Evidence`, `Question`, `Meta`. |
| **Asset** | Anything someone can "know": a system, a code module (folder), a customer, or a process. |
| **Evidence** | One thing a person did that shows they know an asset: a PR, a review, an answer, a doc, an incident, a decision... |
| **KNOWS score** | A number from 0 to 1. How well a person knows an asset. Calculated, not typed in. |
| **Coverage** | The chance that at least one remaining person (or a document) can answer a question about an asset. 0 to 1. |
| **Orphaned / fragile / gap / safe** | The colour labels. See section 11. |
| **Cascade** | Risk spreading: if A depends on B and B is orphaned, A is at risk too. |
| **Bus factor** | How many people really know an area (KNOWS score at least 0.30). |
| **Saturation** | A scaling number so scores don't all become 1.0 in big graphs. See section 10. |
| **Normalized dataset** | The common JSON-like format every data source is converted into. |
| **Ghost Mode** | Answering a question from evidence written by people who already left. |
| **MCP** | Model Context Protocol. A standard way for AI tools (Claude, Cursor) to call outside tools. |

---

## 4. How to run it

```bash
docker compose up -d                 # starts FalkorDB on :6379 and its browser on :3000
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # falkordb, starlette, uvicorn, networkx
cp .env.example .env                 # optional: GITHUB_TOKEN and an LLM key
python -m lastday.server             # open http://localhost:8000
```

On the first start, the server sees there is no `demo` graph yet and builds the Acme Pay demo automatically. No API keys are needed for the demo.

---

## 5. Step 1: Setup files

### `lastday/__init__.py` — reads your `.env`

When any part of the `lastday` package is imported, this file reads the `.env` file in the project root and puts each `KEY=value` line into the environment. It uses `setdefault`, so if the variable is already set in your terminal, your terminal value wins. Empty values are skipped.

### `lastday/config.py` — all the numbers

Every number the scoring uses lives here, with a comment. The most important ones:

**Evidence weights** (how much one piece of evidence counts):

| Kind | Weight | Why |
|---|---|---|
| `commit` | 1.0 | Wrote code here |
| `pr` | 1.5 | Authored a merged PR here |
| `review` | 1.4 | Reviewed changes here, so knows what "good" looks like |
| `issue_answer` | 1.2 | Answered someone's question/issue about this |
| `slack_answer` | 1.3 | Answered a question in chat |
| `ticket` | 1.0 | Resolved a ticket |
| `oncall` | 0.8 | Did an on-call shift for this |
| `doc` | 1.6 | Wrote documentation |
| `incident` | 2.2 | Handled an incident |
| `decision` | 2.5 | Made or wrote down a decision |
| `customer_owner` | 3.0 | Owns the customer relationship |
| `interview` | 3.0 | An exit-interview answer captured by Last Day |
| `handover` | 2.5 | Default only; a real handover uses 1.2 × saturation (section 13) |

**Persistent kinds**: `doc`, `decision`, `interview`. These are "written down", so they still help after the author leaves.

**Fading over time**: `TAU_PERSON` = 360 days, `TAU_DOC` = 720 days. Evidence is multiplied by `exp(-age / tau)`. For people this means evidence loses half its value in about 250 days.

**Thresholds**: orphaned below 0.40 coverage, fragile below 0.65, and a few more (all in section 19).

### `lastday/db.py` — talking to FalkorDB

This is the only file that talks to the database.

- **Two ways to connect.** If the official `falkordb` Python package is installed, it uses that. If not, it uses a tiny client written in this file (`_RespConn`) that speaks the Redis protocol directly over a socket and sends `GRAPH.QUERY ... --compact`. So the app only needs a running FalkorDB, nothing else.
- **One graph per dataset.** `DB.graph("demo")` gives you the graph named `lastday__demo`. Every graph name gets the prefix `lastday__`. GitHub repos become `lastday__gh__owner__repo`, backtests become `lastday__bt__owner__repo__login`. All of them live inside one FalkorDB server.
- **`Graph.q(query, params)`** runs a Cypher query and returns a list of Python dicts, one per row. `Graph.q1()` returns just the first row.
- **The Cypher log.** Every query run through `q()` is also written to a per-request log (using Python `contextvars`, so two browser requests at the same time don't mix their logs). It stores the query text, short versions of the parameters, the time in milliseconds and the row count, up to 60 queries. This is what the **"Cypher behind this answer"** drawer at the bottom of the UI shows.
- **`get_db()`** creates one shared `DB` object the first time it is called and reuses it afterwards. Host, port, username and password come from `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USERNAME`, `FALKORDB_PASSWORD` (defaults: `localhost`, `6379`, none, none).

---

## 6. Step 2: Pulling data from GitHub

File: `lastday/ingest/github.py`, top half.

### Why GraphQL

GitHub has two APIs. With the normal REST API, you need about four requests per pull request (the PR, its files, its reviews, its comments). With GraphQL, **one request** returns 40 PRs together with all their files, reviews and comments, and only the fields you ask for.

### The PR query (`PR_QUERY`)

```graphql
query($owner: String!, $name: String!, $cursor: String) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $name) {
    pullRequests(first: 40, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url createdAt mergedAt closedAt state
        author { login ... on User { name } }
        files(first: 100) { nodes { path } }
        reviews(first: 30) { nodes { author { login } submittedAt state } }
        comments(first: 30) { nodes { author { login } createdAt } }
      }
    }
  }
}
```

Reading it line by line:

- `$owner`, `$name`: the repo, for example `FalkorDB` and `FalkorDB`.
- `$cursor`: a bookmark. Empty on the first page, then the `endCursor` from the page before.
- `rateLimit`: how much of your hourly GitHub quota is left. Only shown in the progress messages.
- `pullRequests(first: 40, ... DESC)`: 40 PRs per page, **newest first**.
- `pageInfo`: tells the code whether there is another page, and the bookmark for it.
- `author { login ... on User { name } }`: the author can be a User, a Bot or an Organization. Only Users have a real `name`, so `... on User` means "also give me the name if this is a User".
- `files(first: 100)`: the paths of up to 100 changed files. This is how the code knows **which folders** a PR touched.
- `reviews(first: 30)`: who reviewed and when.
- `comments(first: 30)`: who commented and when (not the text).

### The issue query (`ISSUE_QUERY`)

Same idea, 50 issues per page, but it also asks for:

- `labels(first: 10)`: used to guess which folder the issue is about.
- `comments(first: 25) { ... body }`: the **text** of each reply. This is the only place text is fetched, and it is what Ghost Mode later searches.

### Sending a request: `_gql()`

- POSTs the query and variables to `https://api.github.com/graphql` with your token.
- If GitHub returns a 502, 503 or 504 error, it waits (2s, 5s, 8s...) and tries again, up to 6 attempts.
- If GitHub says `RATE_LIMITED`, it waits 60 seconds and tries again.
- Other errors (bad token, 403...) stop immediately with a clear message.
- If GitHub returns some data and some errors at the same time, it keeps the data.

### Going through pages: `_fetch()`

```
page 1  (cursor = none)        -> 40 newest PRs
page 2  (cursor = "Y3Vy...")   -> next 40
...
stop when:  there is no next page
       or:  the oldest PR on this page is older than `since`
       or:  4,000 items have been collected
```

`since` is "730 days ago" by default (you can change it in the UI dialog). Because the list is newest first, as soon as a page reaches older items than `since`, there is no point going further.

### Caching: `fetch_repo()`

1. Looks for `data/cache/<owner>__<repo>.json`.
2. If the file exists, `refresh` is not set, and the cached data goes back at least as far as you asked, it **uses the file** and does not call GitHub at all.
3. Otherwise it needs a token (`GITHUB_TOKEN` or `GH_TOKEN`), fetches PRs, then issues, and saves:
   ```json
   {"owner": "...", "repo": "...", "since": ..., "fetched": ..., "prs": [...], "issues": [...]}
   ```

This is why the demo and the backtests can run offline after the first fetch, and why `data/cache/` has files like `FalkorDB__FalkorDB.json` (about 4 MB).

---

## 7. Step 3: Turning GitHub data into a clean dataset

File: `lastday/ingest/github.py`, function `build_dataset(raw)`.

Raw GitHub data is messy. This function turns it into the **normalized dataset** (the shared format described in section 9).

### 7.1 Choosing "now"

The code needs a "today" to measure how old evidence is. For GitHub it uses **the repo's own last activity + 1 day**, not the real today. So a repo that went quiet a year ago is judged as of when it was last busy, and not everyone looks "gone".

When `until` is given (the backtest uses this), everything after that date is thrown away.

### 7.2 Folders become "modules" (the assets)

`module_of(path)` decides which module a file belongs to:

| File path | Module |
|---|---|
| `README.md` | `(root)` |
| `docs/api.md` | `docs` |
| `src/parser/lexer.py` | `src/parser` (because `src` is a "container" folder and the path is deep enough) |
| `src/main.py` | `src` (only two parts, so no deeper) |
| `.github/workflows/ci.yml` | `.github` |

The "container" folders that get one level deeper are: `src, lib, libs, packages, pkg, internal, app, apps, crates, modules, plugins, components, services, cmd, source, core, python, js`.

The code counts how many PRs touched each module and keeps the **top 40**. Each one becomes an asset with:

- `business` = its PR count divided by the busiest module's PR count (so the busiest module is 1.0)
- `description` = "N PRs touched this module"
- `aliases` = the last part of the folder name (so you can type "parser" instead of "src/parser")

### 7.3 Actions become evidence

For each PR:

- **Shares.** If a PR changed 3 files in `src/core` and 1 file in `docs`, the shares are 0.75 and 0.25. A share is never smaller than 0.2.
- **The author** gets one `pr` evidence, **only if the PR was merged**, dated at the merge time.
- **Each reviewer** (not the author, counted once per PR) gets one `review` evidence, dated when they reviewed. Its `counterpart` is the PR author ("I reviewed their work").
- **Each commenter** (not the author, not already a reviewer, counted once) gets one `issue_answer` evidence titled "Discussed PR #...", with the PR author as counterpart.

For each issue:

- The code looks at the issue **title and labels**. If a module's short name (at least 4 letters) appears as a whole word, the issue is linked to that module. Example: an issue labelled `parser` links to `src/parser`.
- Issues that match no module are skipped.
- **Each person who replied** (not the issue author, counted once) gets an `issue_answer` evidence, including the **first 600 characters of their reply**.

Bots are skipped everywhere using `is_bot()`: logins ending in `[bot]` or `-bot`, the login `ghost`, and a fixed list (`dependabot`, `renovate`, `github-actions`, `codecov`, `copilot`, `claude`, `coderabbitai` and others). See section 20 for a known gap here.

**A real example** from the cached `accomplish-ai/node-pty` data. PR #2 changed:

```
.github/workflows/ci.yml           -> module ".github"
scripts/accomplish-test-package.mjs -> module "scripts"
src/windowsTerminal.test.ts        -> module "src"
```

It was merged and had no reviews. Result: one `pr` evidence for `mcmatan`, about `.github`, `scripts` and `src`, each with share 0.33.

### 7.4 People

- Anyone with **fewer than 2 pieces of evidence** is dropped (too little to say anything).
- A person is **active** if their last evidence is within **180 days** of "now".
- Their display name comes from GitHub if available, and their avatar is `https://github.com/<login>.png`.

### 7.5 Dependencies between modules (co-change)

Real code dependencies would need parsing imports. Instead, the code uses a simpler idea: **if two modules keep changing in the same PRs, they are probably connected.**

For every pair of modules A and B:

```
together = number of PRs that touched both A and B
jaccard  = together / (PRs touching A + PRs touching B - together)
```

If `together >= 4` and `jaccard >= 0.15`, an edge is added. It points from the module with **fewer** PRs to the module with **more** PRs, as in "the smaller one depends on the bigger, more central one".

### 7.6 Teams (community detection)

GitHub doesn't tell you who is on which team, so `infer_teams()` guesses:

1. For each person, add up their evidence share per module.
2. Build a graph of people (with networkx). Two people get an edge if they worked on the same modules. The edge weight is how much they overlap.
3. Run **Louvain community detection**, which finds groups of people more connected to each other than to the rest. (If that fails, it uses connected groups instead.)
4. Each group becomes a team named after its main module, for example `team:src/core` called "src/core crew".
5. People alone in a group of one go to "Occasional contributors".

Note: GitHub modules get no owning team (`team` is empty), so there are no `OWNS` edges in GitHub graphs.

---

## 8. Step 3b: The demo company instead of GitHub

File: `lastday/ingest/demo_company.py`.

Acme Pay is a **made-up** fintech, so the demo works with no API keys. Everything in it is invented.

- **5 teams**: Payments, Risk & Fraud, Platform, Data, Customer Success.
- **17 people**, each with a role and a list of "how active they are on which asset per month". Vikram Nair has `active = False` (he left).
- **21 assets**: systems (Payments Gateway, Ledger Reconciliation...), processes (Month-end Close, PCI DSS Audit, Chargeback Operations) and customers (Zenith Retail, Bluebird Airlines, Cobalt Foods). Each has a business weight and aliases ("recon", "ledger"...).
- **34 dependencies**, such as "Settlement Batch depends on Ledger Reconciliation".

### Two kinds of evidence

1. **Story evidence (`STORY`)**: 30 hand-written records with real text. These carry the demo's storylines:
   - **Priya Raman** answered almost all the Ledger Reconciliation questions (FX fee mismatch, duplicate bank files, why recon runs at 03:40...), wrote the ADR-014 idempotency decision, handled INC-231, and owns Zenith Retail escalations.
   - **Arjun and Meera** both know the Payments Gateway.
   - **Vikram** (already left) is the only one who wrote about the bank SFTP key rotation.
2. **Background evidence**: about 2,000 random records over 18 months. For each person and each asset they work on, every month the code draws a random number of events (a Poisson distribution around their monthly rate). Each event gets a kind based on the person's profile:
   - engineers: mostly commits, then reviews, PRs, Slack answers, tickets, on-call
   - managers: reviews, tickets, Slack answers, docs
   - customer success: mostly tickets and Slack answers

   Customers can't have commits, so code-type events about customers become tickets. Vikram's events stop 8 months ago. When Priya answers or reviews, 45% of the time the other person is from the Risk team. That is what makes her the **only bridge** between Payments and Risk.

The random generator uses a fixed seed (7), so the demo comes out the same every time.

---

## 8b. Step 3c: Slack and Notion (a connected company)

Files: `lastday/ingest/slack.py`, `lastday/ingest/notion.py`, `lastday/ingest/merge.py`, `lastday/connections.py`.

The demo company has made-up Slack and Notion data. These connectors read a real workspace and produce the same normalized dataset, so scoring, simulation, the exit interview, handover, Ask and the graph all work unchanged. You set them up in the **Connections** panel (top bar), or from the command line.

### Slack (`slack.py`)

There are two ways in. Both produce the same "raw" Slack data.

| | Upload an export (recommended) | Connect a bot |
|---|---|---|
| How | A workspace admin exports the workspace (Settings & administration, Workspace settings, Import/Export Data) and you upload the ZIP. | Create a Slack app for your own workspace with the scopes `channels:read`, `channels:history`, `users:read`, `users:read.email`, install it, invite it to your channels, and paste its `xoxb-` token. |
| What it reads | Public channels | Public channels the bot is in. With `channels:join` added, it joins them itself; otherwise those channels are skipped with a note. |
| Limits | None | About 50 calls a minute for an app made for your own workspace. Since May 2025, apps shared with other workspaces without Slack Marketplace approval may read only 15 messages a minute, so make it your own workspace's app. |

**What becomes evidence:** every reply inside a thread, by someone other than the person who started the thread, is one `slack_answer` (weight 1.3) for the person who replied. The person who asked is the `counterpart`, which creates `HELPED` edges.

- Several replies by the same person in one thread count once (their texts are joined).
- Skipped: bots, join/leave messages, the asker's own replies, and replies with no knowledge in them ("thanks", "+1", "lgtm", an emoji…).
- Up to 600 characters of the reply are kept, so Ghost Mode can quote it, and the channel name is kept, so the exit interview can say "You've answered 29 questions about X in #payments".
- Slack markup is cleaned: `<@U123>` becomes `@priya`, `<#C1|payments>` becomes `#payments`, links keep their label.
- **Which area is it about?** The question and the answer are searched for each area's name and aliases as whole words (up to 3 areas, best match first). Very common words like `src`, `tests` or `utils` are ignored. A **channel map** (Connections panel) can say "every answer in #parser-help is about src/parser".
- If you give the workspace URL, each answer links back to the Slack thread.
- **A deactivated Slack account marks that person as already left.** That is better than guessing from 180 days of silence.

### Notion (`notion.py`)

Setup: create an integration at notion.so/profile/integrations with "Read content" and "Read user information including email addresses", then open each page or teamspace to include and add the integration under ••• menu, Connections. Sub-pages are included automatically.

What it reads, through the Notion API (version `2026-03-11`, at most about 3 requests a second):

1. `/users`: names and emails.
2. `/search`: every page shared with the integration. Pages in the trash are skipped.
3. `/blocks/{page}/children`: the text, including toggles and nested lists up to 2 levels deep, up to 3,000 characters per page.
4. `/databases/{id}` or `/data_sources/{id}`: the name of the database a page lives in.

**What becomes evidence:**

- The page's creator gets a **`decision`** (weight 2.5) if the title starts with ADR, RFC or Decision (or contains something like "ADR-12"), or the page lives in a database whose name mentions decisions, ADRs or RFCs. Otherwise they get a **`doc`** (weight 1.6).
- Both kinds are "persistent", so they raise the area's written coverage (`doc_cov`) and keep covering it after the author leaves.
- If someone else edited the page last, they get a `doc` at half weight. (Notion's API only says who created a page and who edited it last, not everyone in between.)
- A page is linked to the areas named in its title; if the title names none, its text is searched. Pages about no area are listed in the panel and left out.

### Matching people across tools (`merge.py`)

**Areas.** If a GitHub repo is set, its modules (folders) are the areas and Slack/Notion are linked to them. Without one, every Slack channel with threads becomes an area (`#payments`), except chatter channels like #general and #random. Notion alone has no areas, so it needs GitHub or Slack too.

**People.** One person usually has a GitHub login, a Slack account and a Notion account. Accounts are joined in this order:

| Rule | Example |
|---|---|
| **manual**: a link you set in the Connections panel (always wins) | Slack `@carol.m` is GitHub `carol` |
| **email**: the same email address | Slack and Notion both say `priya@acme.com` |
| **handle**: a Slack handle equal to a GitHub login | Slack `@arjun` and GitHub `arjun` |
| **name**: the same full name of two words or more | "Priya Raman" on Slack and Notion |

- Automatic rules never put two accounts from the **same tool** into one person, so two different "John Smith"s stay two people.
- A person's id is their GitHub login if they have one, otherwise their name as a slug (`hana-sato`).
- When the rules can't decide, the panel shows a suggestion ("Maybe carol on GitHub?") but never applies it by itself.
- Your fixes are saved (`links` and `separate` in `data/connections/settings.json`) and used on every build.
- Someone with fewer than 2 pieces of evidence is left out, unless they wrote a doc or decision.
- **Active** means: not deactivated in Slack, and some evidence in any tool in the last 180 days.
- Teams are inferred again over all the evidence (the same community detection as for GitHub).

### Where it is stored (`connections.py`)

Everything lives in `data/connections/`, which is in `.gitignore`:

| File | What |
|---|---|
| `settings.json` | company name, GitHub repo, Slack mode and workspace URL, channel map, people fixes |
| `secrets.json` | the Slack and Notion tokens. Readable only by you, and **never sent to the browser**. |
| `slack_export.zip` | the uploaded export |
| `slack_raw.json`, `notion_raw.json` | the synced data |

Tokens can also come from `.env` (`SLACK_BOT_TOKEN`, `NOTION_TOKEN`); a token pasted in the panel wins.

### Building the company graph

**Build company graph** (or `python scripts/cli.py company --name Acme`) matches everything, writes it into its own graph `co__<name>` (for example `co__acme`), recomputes the scores, and opens it. You can then click people, run exit interviews and ask questions exactly as with the demo, now with real Slack answers and Notion docs as evidence.

---

## 9. Step 4: Writing the graph into FalkorDB

File: `lastday/ingest/loader.py`.

### The normalized dataset format

Every source produces this:

```python
{
  "meta":    {"name": ..., "source": "github" | "demo", "now": <unix time>, ...},
  "teams":   [{"id", "name"}],
  "people":  [{"id", "name", "team", "handle", "active", "role", "avatar"}],
  "assets":  [{"id", "name", "kind", "team", "aliases", "business", "description"}],
  "depends": [[from_asset_id, to_asset_id], ...],
  "evidence":[{"id", "kind", "author", "assets": [[asset_id, share], ...],
               "title", "text", "url", "ts", "channel", "counterpart"}],
}
```

The Slack and Notion connectors (section 8b) produce exactly this format. A new connector (Jira, Linear, Confluence...) would only need to do the same; everything after it works unchanged.

### What `load_dataset()` does

1. **Deletes** the old graph with the same name (fresh start).
2. **Creates indexes** on `id` for Person, Team, Asset and Evidence, so lookups are fast.
3. Saves a `Meta` node with the dataset's name, source and "now".
4. Writes, in batches of 400 rows using Cypher `UNWIND` (one query per batch instead of one per row):
   - `Team` nodes
   - `Person` nodes, plus `(Person)-[:MEMBER_OF]->(Team)`
   - `Asset` nodes (with `doc_cov = 0` to start), plus `(Team)-[:OWNS]->(Asset)` when a team is given
   - `(Asset)-[:DEPENDS_ON]->(Asset)`
   - `Evidence` nodes, with their `weight` (from `config.py` if not given) and `persistent` flag, plus `(Person)-[:AUTHORED]->(Evidence)`
   - `(Evidence)-[:ABOUT {share}]->(Asset)`
   - `(Person)-[:HELPED {n}]->(Person)`: how many times one person answered or reviewed another (from `counterpart`)

### The graph after loading

```
(:Person)-[:MEMBER_OF]->(:Team)-[:OWNS]->(:Asset)
(:Person)-[:AUTHORED]->(:Evidence)-[:ABOUT {share}]->(:Asset)
(:Person)-[:HELPED {n}]->(:Person)
(:Asset)-[:DEPENDS_ON]->(:Asset)
```

Added later by the engine and agent:

```
(:Person)-[:KNOWS {raw, score, n, last}]->(:Asset)     knowledge.py
(:Person)-[:COLLAB {w}]->(:Person)                     knowledge.py
(:Asset)-[:HANDED_TO]->(:Person)                       handover.py
(:Question)-[:FOR]->(:Person), (:Question)-[:ABOUT]->(:Asset)   interview.py
```

You can see all of this in the FalkorDB Browser at http://localhost:3000.

---

## 10. Step 5: Working out who knows what

File: `lastday/engine/knowledge.py`, function `recompute()`.

This runs **inside FalkorDB as Cypher**. It is called after every load, every interview answer and every handover.

### 10.1 Raw knowledge

For every person and every asset they have evidence about:

```
raw = sum of ( weight × share × exp(-(now - evidence_time) / 360 days) )
```

In Cypher:

```cypher
MATCH (p:Person)-[:AUTHORED]->(e:Evidence)-[r:ABOUT]->(a:Asset)
WITH p, a, sum(e.weight * r.share * exp(-1.0 * ($now - e.ts) / $tau)) AS raw,
     count(e) AS n, max(e.ts) AS last
CREATE (p)-[:KNOWS {raw: raw, n: n, last: last}]->(a)
```

**Example:** Priya's Slack answer about recon from 60 days ago:
`1.3 (slack_answer) × 1.0 (share) × exp(-60/360) = 1.3 × 0.85 ≈ 1.10`.
The same answer from two years ago would be worth `1.3 × exp(-730/360) ≈ 0.17`.

### 10.2 From raw to a 0–1 score

```
score = 1 - exp(-raw / saturation)      (capped at 0.97)
```

This curve grows fast at first, then flattens: the first few pieces of evidence matter a lot, and the 100th matters little.

**Saturation adapts to each graph.** The code takes the 90th percentile of all raw values (`p90`) and sets `saturation = max(2.0, 0.45 × p90)`. That makes someone at the 90th percentile score about 0.9. Without this, a big busy repo would give everyone a score near 1.0, and a small company would give everyone low scores. The cap of 0.97 stops `log(1 - score)` from becoming infinite later.

### 10.3 Written knowledge (`doc_cov`)

Docs, decisions and interview answers stay useful after their author leaves. For each asset:

```
d       = sum of weight × share × exp(-age / 720 days)   (only persistent evidence)
doc_cov = 0.5 × (1 - exp(-d / 5))
```

Written knowledge can add at most **50%** coverage. The idea is that a document can never fully replace a person.

### 10.4 Collaboration between people (`COLLAB`)

Two people get a `COLLAB` edge if:

- they both know the same asset with a score of at least 0.30 (weight adds `score1 × score2` for each shared asset), and/or
- one helped the other (weight adds `0.12` for each `HELPED` count).

These edges are used to find people who bridge teams (section 11.5).

---

## 11. Step 6: Simulating someone leaving

Files: `lastday/engine/simulate.py` and `lastday/engine/algorithms.py`.

This is the core of the product. You give it a list of people to "remove", and it tells you what happens.

### 11.1 Coverage

Coverage answers: "If someone has a question about this asset, what is the chance that at least one person still here (or a document) can answer it?"

```
chance nobody can answer = (1 - doc_cov) × (1 - score_1) × (1 - score_2) × ...
coverage                 = 1 - chance nobody can answer
```

This is called a **noisy-OR**. Only **active** people count; people who already left are never part of coverage.

**Example:** three people know an area, with scores 0.9, 0.3 and 0.2, and no docs.

- Before: nobody = 0.1 × 0.7 × 0.8 = 0.056, so coverage = **94%**.
- Remove the 0.9 person: nobody = 0.7 × 0.8 = 0.56, so coverage = **44%**. That is a drop of 50 points.

In the code, the multiplication is done as a sum of logarithms inside Cypher (`sum(log(1.0 - k.score))`), once with everyone and once without the removed people, in a single query (`coverage_table()`).

The same query also counts:

- **bus factor before / after**: how many people have a score of at least 0.30
- **share lost**: what fraction of all the raw knowledge about this asset belongs to the removed people (set to 0 if the asset has already been handed over to someone who is staying)

### 11.2 Colours (status)

`_status()` checks these rules **in order**:

| Status | Colour | Rule |
|---|---|---|
| `gap` | purple | Coverage was **already** below 40% before anyone left. Knowledge debt that exists today. |
| `orphaned` | red | Coverage after is below 40%, and it dropped by at least 12 points. |
| `fragile` | amber | Coverage after is below 65%, and it dropped by at least 12 points. |
| `fragile` | amber | It had 2+ real experts, now has 1 or 0, dropped 12+ points, and is below 75%. ("Only one expert left.") |
| `fragile` | amber | The leavers held at least half of all knowledge about it, and it dropped 12+ points. |
| `safe` | green | None of the above. |

With nobody removed, an asset is only ever `gap` or `safe`.

In the example above (94% → 44%), the asset is **fragile**: below 65% but not below 40%.

### 11.3 Cascade

If an asset is orphaned, everything that depends on it is at risk too:

```cypher
MATCH p = (a:Asset)-[:DEPENDS_ON*1..3]->(o:Asset)
WHERE o.id IN $orphaned AND NOT a.id IN $orphaned
RETURN a.id, o.id AS via, length(p) AS hops, [n IN nodes(p) | n.id] AS path
```

`*1..3` means "follow 1 to 3 dependency arrows". For each at-risk asset, the code keeps the **shortest** path. The UI uses the number of hops to make red visibly spread one step at a time.

### 11.4 Criticality (PageRank)

Not every area matters the same. `pagerank_assets()` runs FalkorDB's built-in `algo.pageRank` on the `DEPENDS_ON` edges. Assets that many other things depend on get a high rank. The values are scaled so the top one is 1.0.

```
criticality = 0.6 × pagerank + 0.4 × business weight
```

### 11.5 Bridges between teams (betweenness)

`betweenness_people()` runs FalkorDB's `algo.betweenness` on the `COLLAB` edges between active people. People with high betweenness sit on the shortest paths between everyone else: they **connect** the company. The top 8 are returned, and the Impact tab shows the top 5 as "People who connect the company".

`team_links()` then looks at every `COLLAB` edge between people of two different teams and asks: "What share of this team-to-team connection goes through the removed people?" At **60% or more**, the link is marked **severed**. That's how you get "Payments and Risk lose 100% of their connection" when Priya is removed.

If the FalkorDB server doesn't support `algo.pageRank` or `algo.betweenness`, both functions pull the same subgraph out with Cypher and run the networkx versions instead. The result says which one was used.

### 11.6 Risk and knowledge health

For each asset:

```
loss = max(drop, 0.6 × share_lost)   if status is not safe, or it inherited risk
       0.5 × drop                    otherwise
risk = criticality × loss  (+ 0.15 × criticality if it inherited risk)
```

The table is sorted by risk, so the most important problems come first.

**Knowledge health** is one number for the whole company: the average coverage, where each asset counts by `criticality + 0.05`. It is shown before and after, for example 87 → 76.

### 11.7 "Who covers while they're on leave?"

`who_covers(absent, assets)` uses the same coverage table and, for each asset, lists the best people who are **not** absent (top 4) and the experts who **are** absent. If nobody left has a score of at least 0.30, it is marked `uncovered`.

---

## 12. Step 7: Finding a successor

File: `lastday/engine/successors.py`, function `recommend(asset, removed)`.

For an asset and a list of people leaving, it collects candidates (active people who are not leaving) from four graph patterns:

| Signal | What it looks for in the graph | Weight |
|---|---|---|
| **Direct** | Their own KNOWS score for this asset | 0.50 |
| **Neighbour** | They know assets directly connected by `DEPENDS_ON` (either direction) with score ≥ 0.2. Sum of those scores ÷ 1.5, max 1. | 0.22 |
| **Mentored** | How many times the leaver answered or reviewed **their** work on this asset (`counterpart`). Divided by 4, max 1. | 0.18 |
| **Team owner** | They are on the team that `OWNS` this asset | 0.10 |
| **Load** | Number of areas where they already have a score ≥ 0.55. Every area above 2 subtracts 0.035. | penalty |

```
fit = 0.5×direct + 0.22×neighbour + 0.18×mentored + 0.10×team_owner - 0.035×max(0, load-2)
```

Each candidate also gets plain-English **reasons**, built from these templates:
"already has N% familiarity (M pieces of evidence)", "knows neighbouring systems: A, B, C", "the leaver answered/reviewed their work on this N times", "on the owning team", "careful: already the go-to person for N areas".

The top 3 are returned. In the demo, Rahul Verma comes first for Ledger Reconciliation.

---

## 13. Step 8: The agent (Ask, exit interview, handover)

Folder: `lastday/agent/`.

### 13.1 `ask.py` — answering questions

`ask(question)` first finds **people** and **assets** mentioned in the question:

- People match by id, handle, full name, or first name (3+ letters), as whole words.
- Assets match by id, name or any alias. Longer matches rank higher ("ledger reconciliation" beats "recon").

Then it picks a route, checking in this order:

| If the question has... | It does... | Example |
|---|---|---|
| people + assets + leave/cover words ("on leave", "vacation", "covers", "backup"...) | `who_covers` | "If Arjun and Meera are on leave Diwali week, who covers payments?" |
| people + leaving words ("leaves", "quits", "resigns", "left"...) | `simulate` | "What happens if Priya leaves?" |
| assets + "who knows / who owns / expert / who to ask" | top 5 people by KNOWS score; if only one real expert, adds "Bus factor is 1" and the next best successor | "Who knows the fraud engine?" |
| anything else | **Ghost Mode** | "How do I rotate the Unity Bank SFTP key?" |

**Ghost Mode** (`ghost()`):

1. Makes sure there is a **full-text index** on Evidence `title` and `text` (FalkorDB `db.idx.fulltext.createNodeIndex`).
2. Removes common words ("the", "how", "is"...) from the question, and searches the index with the rest (`db.idx.fulltext.queryNodes`). Only evidence that has text is searched.
3. Also adds the highest-weight evidence about any asset named in the question.
4. Ranks each result: `full-text score + 0.6 × words in common + 1.0 if it is about the asked-about asset`. Keeps the top 5 with rank ≥ 1.0.
5. Marks each source's author as "left" (not active) or "leaving" (in the removed list).
6. **With an LLM key:** sends the numbered sources to the LLM with instructions to answer only from them and cite like [1].
   **Without one:** quotes the top 2 sources directly: "According to Vikram Nair (who has left), <month year>: "..." [1]"
7. Also returns "who to ask next": the top 3 active people for that asset.
8. If nothing is found: "The trail runs out here: nobody wrote this down anywhere Last Day can see."

### 13.2 `interview.py` — the exit interview

**`build_questions(person)`**

1. Simulates that person leaving.
2. Picks the areas that become **orphaned or fragile** (up to 6, most risky first). If fewer than 3, it adds the areas they know best, up to 3.
3. For each area, it reads their evidence and everyone else's, and fills in question templates when the data supports them:

| Template | When it is used |
|---|---|
| "You've answered N questions about X in #channel; everyone else combined answered M. What are the three questions people keep asking, and what are the answers?" | They gave 3+ answers (Slack, issue, ticket) and others gave at most half as many |
| "You made the call in 'ADR-014...'. What would make that decision wrong, and what should someone check before changing it?" | For up to 2 decisions they made |
| "You handled 'INC-231...'. If it happens again at 3am, what's the first thing to check, and what is still not written down?" | For 1 incident they handled |
| "You reviewed N of the M changes to X. What do you check in a review here that a new reviewer would miss?" | They did 3+ reviews and at least half of all reviews |
| "You own the relationship with X. What do they care about that isn't in the CRM or the contract...?" | They have `customer_owner` evidence |
| "Almost nothing about X is written down. What would you tell your replacement on day one?" | Written coverage is below 8% and there are fewer than 3 questions so far |
| "X depends on A, B; C depends on it. What breaks most often in X, and how do you fix it?" | Fallback if there are fewer than 2 questions |

4. At most 3 questions per area. **Every question lists the evidence it came from** (title, date, channel).
5. Saves each question in the graph as a `Question` node with `FOR` (the person) and `ABOUT` (the asset). The question id is a hash of person + asset + text, so building the interview again finds the same questions and their saved answers.

**`record_answer(...)`**

1. Saves the answer as a new `Evidence` node: kind `interview`, weight 3.0, **persistent**, dated "now", linked to the person and the asset.
2. Marks the `Question` as answered.
3. Runs `recompute()` again. Because interview evidence is persistent, the asset's `doc_cov` goes up, so its coverage **after** the person leaves goes up too.
4. Simulates again and returns the new status. This is how red turns amber in front of you.

### 13.3 `handover.py` — handover tickets

**`draft(asset, removed, successor)`** builds a Markdown ticket:

- Title: "Handover: Ledger Reconciliation → Rahul Verma"
- Why: who is leaving, owning team
- Why this successor: the reasons from `recommend()`
- **Read first**: up to 8 of the leaver's best evidence about this asset (decisions, incidents, docs, answers, PRs)
- **Captured in the exit interview**: all answered questions about this asset
- **Neighbouring systems and who to ask**: each connected asset and its best remaining expert (or "nobody, flag this")
- **Checklist**: pair on a real change, shadow the next incident, become a required reviewer, mark the ticket done

With an LLM key, it also writes a 3-sentence summary.

**`accept(asset, successor, removed)`** — "Mark handover done":

1. Adds a `handover` evidence for the successor. Its weight is `1.2 × saturation`, so it counts the same at any graph size.
2. Adds `(Asset)-[:HANDED_TO]->(Person)`. While the successor is active and staying, this sets `share_lost` to 0 for that asset.
3. Recomputes and simulates again, so the asset turns green.

**`create_github_issue(repo, title, body)`** posts the ticket as a GitHub issue with the label `handover` (needs `GITHUB_TOKEN` with write access). The UI only shows this button for GitHub graphs.

### 13.4 `llm.py` — optional AI writing

- If `ANTHROPIC_API_KEY` is set, it calls the Anthropic Messages API (model from `LLM_MODEL`, default `claude-sonnet-4-5`).
- Else if `OPENAI_API_KEY` is set, it calls any OpenAI-compatible API (`OPENAI_BASE_URL`, default OpenAI; works with Groq, Together, Ollama...). Default model `gpt-4o-mini`.
- `LASTDAY_NO_LLM` turns it off.
- If anything goes wrong, it prints the error and returns nothing, and the code falls back to the extractive answer. **The LLM can never break the product.**

---

## 14. Step 9: The backtest (proof on real data)

File: `lastday/backtest.py`.

The question: **"If Last Day had existed before a real maintainer left, would its predictions have been right?"**

### `departures(raw)` — who went quiet?

Ranks the top 15 contributors by activity (PRs authored + reviews). A person "went quiet" if their last activity is more than 180 days before the repo's last PR. Use this to find a good person to test.

### `run(owner, repo, leaver, cutoff)`

1. **Get the data.** If no cutoff date is given, it fetches all history (up to the 4,000-item limit) and uses the leaver's **last activity** as the cutoff.
2. **Predict using only the past.** It keeps PRs and issues from the 730 days before the cutoff, builds the dataset with `until = cutoff` (nothing after that date can leak in), marks the leaver as still active, loads it into its own graph (`bt__owner__repo__login`), recomputes, and simulates the leaver leaving.
3. **Recommend successors** (top 3) for every orphaned or fragile module.
4. **Measure what really happened.** For every PR not by the leaver, from 1 year before the cutoff to 1 year after:
   - **first response time**: hours until someone other than the author (and not a bot) reviewed or commented
   - **unanswered**: no response within 14 days
   - **who took over**: after the cutoff, who authored, reviewed and commented the most in each module
5. **Compare.** Modules are grouped as "at risk" (predicted orphaned/fragile), "safe" or "gap". For each group: median first-response time before and after, the slowdown (after ÷ before), and the unanswered share. Modules with fewer than 3 PRs both before and after are skipped.
6. **Successor hit rate**: how often the person who actually took over was in Last Day's top 3.

The README reports that on the offline test repo (section 17), modules predicted at risk slowed down about **11.6×**, while modules predicted safe did not slow down (**0.9×**).

---

## 15. Step 10: How you use it (web, MCP, command line)

### `lastday/service.py` — the shared layer

All three ways of using Last Day call the same functions here:

- `seed_demo()`: build the demo, load it, recompute, create the full-text index
- `ingest_github(repo, since_days=730, refresh=False)`: fetch → build_dataset → load → recompute → full-text index. The graph name is `gh__owner__repo`, lowercased, with `-` and `.` changed to `_`.
- `overview(g)`: everything the UI needs to draw the graph: up to 70 people (most knowledgeable first), all assets, KNOWS edges with score ≥ 0.12, dependencies, teams and evidence counts
- `evidence()`, `knowers()`, `path_between()`: small lookups for the side panel
- Connections: `connect_slack_export()`, `connect_token()`, `sync_slack()`, `sync_notion()`, `save_people_links()`, `save_channel_map()`, `preview_company()` (match people without writing the graph) and `build_company()` (section 8b)
- **Background jobs:** GitHub ingest and backtests can take a minute, so `start_job()` runs them in a background thread, gives them an id, and keeps the last 30 progress messages. The UI checks the job every 1.2 seconds. Jobs are stored in memory, so they are lost if the server restarts.

### `lastday/server.py` — the web API

Built with Starlette, run with uvicorn on `127.0.0.1:8000` (change with `HOST` and `PORT`).

Every API function is wrapped by `api()`, which:

1. merges URL query parameters, the JSON body and URL path parts into one `params` dict
2. starts a fresh Cypher log
3. runs the function in a thread pool (so slow database calls don't block other requests)
4. returns `{"ok": true, "data": ..., "cypher": [...]}`, or `{"ok": false, "error": ...}` with status 400

| Route | What it does |
|---|---|
| `GET /` | the web page (`web/index.html`) |
| `GET /api/status` | is FalkorDB up, which client, which graphs exist, is there an LLM, is there a GitHub token |
| `POST /api/seed` | rebuild the demo |
| `POST /api/ingest` | start a GitHub ingest job |
| `POST /api/backtest` | start a backtest job |
| `POST /api/departures` | start a "who went quiet" job |
| `GET /api/job/{id}` | check a job's status and log |
| `/api/{g}/overview` | graph data for drawing |
| `POST /api/{g}/simulate` | remove people, get statuses |
| `POST /api/{g}/successors` | top successors for an asset |
| `POST /api/{g}/covers` | who covers while people are away |
| `POST /api/{g}/interview` | build the exit interview |
| `POST /api/{g}/answer` | save an interview answer |
| `POST /api/{g}/handover` | draft a handover ticket |
| `POST /api/{g}/handover/accept` | mark a handover done |
| `POST /api/{g}/handover/issue` | open the ticket as a GitHub issue |
| `POST /api/{g}/ask` | ask a question |
| `/api/{g}/evidence`, `/api/{g}/knowers` | lookups for the side panel |
| `GET /api/connections` | Slack / Notion / GitHub connection state (tokens only as set / not set) |
| `POST /api/connections/settings` | company name, GitHub repo, Slack workspace URL |
| `POST /api/connections/slack/export` | upload a Slack export ZIP (sent as the raw request body) |
| `POST /api/connections/token` | save a Slack or Notion token and start syncing (background job) |
| `POST /api/connections/sync` / `disconnect` | sync again / remove a source's token and data |
| `POST /api/connections/people` / `channels` | save people fixes / the channel → area map |
| `POST /api/connections/preview` / `build` | match people (job) / build the company graph (job) |
| `/static/...` | the files in `web/` |

`{g}` is the graph name, for example `demo` or `gh__falkordb__falkordb`.

On start (`main()`), if `LASTDAY_AUTOSEED` is `1` (the default) and there is no demo graph, it builds one. If FalkorDB isn't running, it prints "Start it with: docker compose up -d".

### `lastday/mcp_server.py` — tools for AI assistants

A small MCP server written by hand. It reads one JSON message per line from standard input and writes one JSON reply per line. It handles `initialize`, `tools/list`, `tools/call` and `ping`.

| Tool | What it does |
|---|---|
| `who_knows` | asks "who knows ..." through `ask()` |
| `simulate_departure` | summary, top 8 risks, top 5 team links |
| `who_covers` | coverage while people are away |
| `exit_interview` | builds the interview |
| `ask` | any question, with sources |
| `list_people` | everyone in the graph |

The graph it uses comes from `LASTDAY_GRAPH` (default `demo`). Replies are cut at 60,000 characters. Setup for Claude or Cursor:

```json
{"mcpServers": {"last-day": {"command": "/path/to/company_brain/.venv/bin/python",
  "args": ["-m", "lastday.mcp_server"], "cwd": "/path/to/company_brain",
  "env": {"LASTDAY_GRAPH": "demo"}}}}
```

### `scripts/cli.py` — terminal commands

```bash
python scripts/cli.py seed
python scripts/cli.py simulate priya
python scripts/cli.py ask "who covers payments if arjun and meera are on leave?"
python scripts/cli.py interview priya
python scripts/cli.py ingest FalkorDB/FalkorDB          # needs GITHUB_TOKEN
python scripts/cli.py departures owner/repo
python scripts/cli.py backtest owner/repo login [YYYY-MM-DD]

python scripts/cli.py connect-slack path/to/slack-export.zip  # or an unzipped export folder
python scripts/cli.py connect-slack --api                     # uses SLACK_BOT_TOKEN
python scripts/cli.py connect-notion                          # uses NOTION_TOKEN
python scripts/cli.py people --name Acme --repo owner/repo    # how accounts were matched
python scripts/cli.py company --name Acme                     # build the company graph
```

Add `--graph <name>` to use a graph other than `demo`.

---

## 16. Step 11: How the graph is drawn in the browser

Folder: `web/`. Plain HTML, CSS and JavaScript, no build step. **Cytoscape.js** draws the graph, and it is bundled in `web/vendor/` so the page works offline.

### `index.html` — the layout

- **Top bar**: graph picker, "Connections", "Load a GitHub repo", "Reset demo", status dots (FalkorDB, LLM)
- **Left**: "Leaving the company" / "On leave" switch, people search, people list grouped by team
- **Middle**: headline, knowledge health (before → after), the graph and its legend
- **Right**: five tabs: Impact, Exit interview, Handover, Ask, Backtest
- **Bottom**: the "Cypher behind this answer" drawer
- A dialog for loading a GitHub repo

### `app.js` — what happens, in order

**Starting up (`boot`)**

1. Calls `/api/status`. If FalkorDB is down, shows how to start it.
2. If there is no demo graph, calls `/api/seed`.
3. Fills the graph picker and loads the graph named in the URL (`?g=...`) or `demo`.

**Loading a graph (`loadGraph`)**

1. Gets `/overview`.
2. Gives each team a colour.
3. Draws the people list, with a small bar showing how much knowledge each person holds.
4. Draws the graph (`buildGraph`), runs a simulation with nobody removed, and fills the tabs.

**Drawing the graph (`buildGraph`)**

| Thing | How it looks |
|---|---|
| Person | small circle, coloured by team, first name below. Faded if they already left. |
| System | rounded square, size grows with business weight |
| Customer | diamond |
| Process | hexagon |
| KNOWS edge | thin line, thicker and darker when the score is higher |
| DEPENDS_ON edge | dashed arrow |

The layout is **cose**, a force-directed layout: connected nodes pull together and all nodes push apart. To make the picture look the same on every load (good for demos), each node starts at a position calculated from a hash of its id (`seedPos`).

Hovering a node fades everything except it and its neighbours. Clicking a person removes or restores them. Clicking an asset opens the Handover tab for it.

**Clicking a person (`togglePerson` → `runSim` → `paintGraph`)**

1. Adds or removes the person from `state.removed`. (People who already left can't be clicked.)
2. Calls `/simulate` with the removed list.
3. `paintGraph` then:
   - gives removed people a red ring ("leaving") or amber ring ("on leave")
   - makes their KNOWS edges dotted red (cut)
   - colours each asset by status: green safe, amber fragile, red orphaned, purple gap
   - **animates** it: orphaned turns red after 250 ms, fragile after 500 ms, and inherited assets after `700 + 380 × hops` ms. This is what makes red **spread** along dependencies.
   - highlights the cascade path edges in orange
4. Updates the headline, health numbers and tabs.

"On leave" mode uses the same simulation; only the wording, colours and the extra "Who covers" list change.

**The tabs**

- **Impact**: with nobody removed, shows areas only one person knows, areas that are already a gap, and the top people who connect the company. With people removed, shows affected areas (coverage bar before → after), team links that run through them, and a button to write their exit interview.
- **Exit interview**: builds the questions, shows the sources for each, lets you type and save answers. After saving, it re-runs the simulation and shows the coverage change, for example "Ledger Reconciliation: 24% → 47%".
- **Handover**: who knows the selected asset, the top 3 successors with reasons, "Draft handover", then "Mark handover done", "Copy as Markdown" or "Open as GitHub issue".
- **Ask**: suggested questions and a chat box. Ghost Mode answers show a "Ghost mode: answering from the trail of ..." tag and numbered sources. If the question was "What happens if X leaves?", the UI also selects X on the graph.
- **Backtest**: enter a repo, find who went quiet, run the backtest, see bar charts of response times, the successor hit rate and a per-module table. "Open the pre-departure graph" loads that graph with the leaver removed.

**The Connections dialog** (`openConnections()` and the functions after it):

- **Company**: name and optional GitHub repo. Saved as soon as you leave the field.
- **Slack**: upload an export ZIP, or paste a bot token; workspace URL for links; Sync again; Disconnect (click twice).
- **Notion**: paste the integration token; Sync again; Disconnect.
- **Check people** runs the matching in the background and shows:
  - a **People** table: each person's accounts with how they were matched ("by email", "by handle", "by manual"). × means "not the same person"; "Same person as…" joins an account to someone, with suggested matches first.
  - a **Slack channels** table: answers per channel, how many are linked to an area, and (with a GitHub repo) a dropdown to map a whole channel to an area.
  - a **Notion** line: how many pages are about an area, and which aren't.
- **Build company graph** writes the graph, closes the dialog and opens it.

**Other helpers**: `api()` calls the server and always updates the Cypher drawer. `poll()` checks a background job every 1.2 seconds. `md()` is a tiny Markdown-to-HTML converter for handover tickets. `esc()` escapes text so it can't break the HTML.

### `styles.css`

Dark theme, layout, chips, bars, tabs, drawer and dialog styling. No logic.

---

## 17. Tests

### `tests/make_fixture.py` — a fake repo for offline testing

Creates `data/cache/fakeorg__widgets.json`, shaped exactly like GitHub's GraphQL output, so no token is needed:

- 7 contributors. **alice** maintains `src/core` and `src/parser` and does most reviews there.
- alice **stops on 1 June 2024**.
- After that, PRs in core and parser wait 40–300 hours for a review (before: 2–30 hours), and 30% get no review at all.
- **carol** takes over core reviews and **erin** takes over parser.

### `tests/test_e2e.py` — 28 checks against a live FalkorDB

Run with `python tests/test_e2e.py` (FalkorDB must be running). It checks, among other things:

- the demo loads with 17 people and more than 1,500 pieces of evidence
- removing Priya makes Ledger Reconciliation and Zenith Retail **orphaned**, the bank SFTP is **already a gap**, at least 2 assets inherit risk, and Priya carries a big share of the Payments ↔ Risk link
- with nobody removed, everything is only safe or gap
- with Arjun and Meera away, **Priya** covers the gateway and the gateway is fragile or orphaned
- **Rahul** is the top successor for Ledger Reconciliation
- Ghost Mode finds the "03:40" answer and answers the SFTP question from **Vikram's** trail
- the exit interview has at least 5 questions and every one cites a source
- two interview answers raise coverage by more than 10 points
- interview + handover make Ledger Reconciliation **safe**
- the MCP server answers `tools/call`
- the backtest finds the cutoff near 1 June 2024, predicts core and parser at risk, at-risk modules slow down much more than safe ones, and the successor hit rate is at least 50%
- the fake repo can be ingested as its own graph with 7 people

It ends with `ALL 28 CHECKS PASSED`.

### `tests/make_connector_fixtures.py` and `tests/test_connectors.py` — Slack and Notion

The fixtures continue the fake repo's story: in Slack, alice answered most #core and #parser questions until she left (her account is deactivated), then carol, bob, erin and hana took over; in Notion, alice wrote ADR-7 and the planner-cache doc. hana is only in Slack and Notion, and carol's Slack handle (`carol.m`) differs from her GitHub login.

`python tests/test_connectors.py` has two parts:

1. **Runs anywhere (54 checks):** reads the fake Slack export; fetches the same data from a fake Slack API and a fake Notion API that the test serves on localhost (including pagination, a rate-limit retry, a channel the bot can't join and a trashed page); checks that the API and the export give the same messages; checks people matching (alice joined across all three, carol suggested but not auto-joined, a manual link, "keep separate"), noise filtering, ADR detection, channel maps and the no-GitHub mode; and drives every `/api/connections` route, including that tokens never reach the browser.
2. **Needs FalkorDB:** builds the company graph and checks that Notion docs raise written coverage, hana knows the parser from her Slack answers, Ghost Mode answers from alice's trail, bob's exit interview cites his #net answers, and removing bob lowers src/net coverage.

---

## 18. Every file at a glance

| File | Lines | Job |
|---|---|---|
| `docker-compose.yml` | 12 | Runs FalkorDB (port 6379) and FalkorDB Browser (port 3000) |
| `requirements.txt` | 4 | `falkordb`, `starlette`, `uvicorn`, `networkx` |
| `.env.example` | 15 | Template for settings and keys |
| `.gitignore` | 6 | Keeps `.venv`, `.env`, `data/cache/`, `data/connections/` and Python caches out of git |
| `lastday/__init__.py` | 12 | Loads `.env` |
| `lastday/config.py` | 53 | All scoring numbers |
| `lastday/db.py` | 253 | FalkorDB connection, fallback client, Cypher log |
| `lastday/ingest/github.py` | 329 | Fetch from GitHub, cache, normalize, co-change, teams |
| `lastday/ingest/demo_company.py` | 360 | The fictional Acme Pay dataset |
| `lastday/ingest/slack.py` | 331 | Slack export / Web API → thread answers as evidence |
| `lastday/ingest/notion.py` | 251 | Notion API → docs and decisions as evidence |
| `lastday/ingest/merge.py` | 342 | Match people across tools, link to areas, one dataset |
| `lastday/connections.py` | 137 | Connection settings, tokens and synced data on disk |
| `lastday/ingest/loader.py` | 125 | Normalized dataset → FalkorDB nodes and edges |
| `lastday/engine/knowledge.py` | 70 | KNOWS scores, saturation, doc coverage, COLLAB |
| `lastday/engine/simulate.py` | 198 | Coverage, statuses, cascade, team links, risk, health, who covers |
| `lastday/engine/algorithms.py` | 52 | PageRank and betweenness (FalkorDB, networkx fallback) |
| `lastday/engine/successors.py` | 69 | Successor ranking with reasons |
| `lastday/agent/ask.py` | 214 | Question routing and Ghost Mode |
| `lastday/agent/interview.py` | 182 | Exit interview questions and saving answers |
| `lastday/agent/handover.py` | 104 | Handover tickets, accepting them, GitHub issues |
| `lastday/agent/llm.py` | 52 | Optional Anthropic / OpenAI-compatible calls |
| `lastday/backtest.py` | 194 | Predict from the past, measure what really happened |
| `lastday/service.py` | 317 | Shared functions, background jobs, connections |
| `lastday/server.py` | 256 | Web API and serving the UI |
| `lastday/mcp_server.py` | 89 | MCP tools over stdio |
| `scripts/cli.py` | 126 | Terminal commands |
| `web/index.html` | 132 | Page layout |
| `web/app.js` | 960 | All front-end logic, graph drawing and the Connections dialog |
| `web/styles.css` | 289 | Styling |
| `web/vendor/cytoscape.min.js` | — | Graph drawing library (bundled) |
| `tests/make_fixture.py` | 100 | Fake GitHub repo for offline tests |
| `tests/test_e2e.py` | 158 | End-to-end checks |
| `tests/make_connector_fixtures.py` | 241 | Fake Slack and Notion data for the same story |
| `tests/test_connectors.py` | 447 | Slack / Notion / people-matching checks, with fake APIs |
| `data/connections/` | — | Your connections, tokens and synced Slack / Notion data (private, gitignored) |
| `data/cache/*.json` | — | Saved raw GitHub data |

---

## 19. Every setting you can change

### Environment variables (`.env`)

| Variable | Default | Used for |
|---|---|---|
| `FALKORDB_HOST` / `FALKORDB_PORT` | `localhost` / `6379` | Where FalkorDB runs |
| `FALKORDB_USERNAME` / `FALKORDB_PASSWORD` | none | FalkorDB login, if needed |
| `GITHUB_TOKEN` (or `GH_TOKEN`) | none | GitHub ingest and backtests; also creating handover issues |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` | none | Optional LLM |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | none | Optional LLM (any OpenAI-compatible API) |
| `LLM_MODEL` | `claude-sonnet-4-5` or `gpt-4o-mini` | Which model |
| `LASTDAY_NO_LLM` | not set | Set it to turn the LLM off |
| `PORT` / `HOST` | `8000` / `127.0.0.1` | Web server address |
| `LASTDAY_AUTOSEED` | `1` | Build the demo on start if missing |
| `LASTDAY_GRAPH` | `demo` | Which graph the MCP server uses |
| `LASTDAY_CACHE` | `data/cache` | Where raw GitHub data is saved |
| `SLACK_BOT_TOKEN` | none | Slack bot token, if not pasted in the Connections panel |
| `NOTION_TOKEN` | none | Notion integration token, if not pasted in the panel |
| `NOTION_VERSION` | `2026-03-11` | Notion API version header |
| `LASTDAY_CONNECTIONS` | `data/connections` | Where connection settings, tokens and synced data are kept |
| `SLACK_API_BASE` / `NOTION_API_BASE` | the real APIs | Only for tests (fake API servers) |

### Scoring knobs (`lastday/config.py`)

| Name | Value | Meaning |
|---|---|---|
| `TAU_PERSON` | 360 days | How fast a person's evidence fades |
| `TAU_DOC` | 720 days | How fast written knowledge fades |
| `SAT_FACTOR` | 0.45 | Saturation = 0.45 × 90th percentile of raw |
| `MIN_SATURATION` | 2.0 | Minimum saturation for tiny graphs |
| `MAX_SCORE` | 0.97 | Highest possible KNOWS score |
| `DOC_COVERAGE_CAP` | 0.5 | Written knowledge adds at most 50% coverage |
| `DOC_SATURATION` | 5.0 | How much written evidence it takes to approach that cap |
| `HANDOVER_STRENGTH` | 1.2 | A completed handover = 1.2 × saturation of evidence |
| `ORPHAN_BELOW` | 0.40 | Coverage under this = orphaned (or gap, if already) |
| `FRAGILE_BELOW` | 0.65 | Coverage under this = fragile |
| `MIN_DROP` | 0.12 | A departure must remove at least 12 points to count |
| `SINGLE_KNOWER_BELOW` | 0.75 | "Only one expert left" rule applies below this |
| `SHARE_LOST_FRAGILE` | 0.5 | Leavers held half of all knowledge = fragile |
| `KNOWS_THRESHOLD` | 0.30 | Score needed to count as "really knows it" |

### Numbers inside the code (not in config)

| Where | Value | Meaning |
|---|---|---|
| `github.py` | 40 PRs / 50 issues per page | Page size |
| `github.py` | 100 files, 30 reviews, 30 PR comments, 25 issue comments | Per-item limits |
| `github.py` | 4,000 items | Maximum PRs (and issues) fetched |
| `github.py` | top 40 modules | Maximum assets for a repo |
| `github.py` | 180 days | "Active" window for people |
| `github.py` | 2 pieces of evidence | Minimum to be kept as a person |
| `github.py` | 4 shared PRs and Jaccard 0.15 | Minimum for a co-change dependency |
| `service.py` | 730 days | Default GitHub history |
| `simulate.py` | 0.6 | Team link counts as "severed" |
| `simulate.py` | 1–3 hops | Cascade depth |

---

## 20. Limitations and known issues

### Limitations (by design)

- **Knowledge is guessed from activity.** If someone knows something but never wrote, reviewed or answered anything about it, Last Day can't see it. The exit interview exists to close that gap.
- **Demo data is synthetic.** Slack, docs, tickets and CRM data in Acme Pay are made up. Real Slack and Notion can now be connected (section 8b); tickets and CRM still have no connector.
- **Slack only counts answers inside threads.** Conversations that happen as separate messages in the channel, private channels and DMs are not read.
- **Notion only knows who created a page and who edited it last**, not everyone who contributed in between.
- **People matching can be wrong** when people use different emails, handles and names in each tool. Check the People table before building.
- **Code dependencies are guessed** from folders that change together, not from imports.
- **The GitHub connector only sees merged PRs, reviews, PR comments and issue replies.** It does not fetch commits, inline code-review comments, PR descriptions or review text. So on GitHub graphs, Ghost Mode can only quote issue replies.
- **GitHub modules have no owning team**, so the "on the owning team" successor signal never applies to repos.
- **Big repos are capped** at 4,000 PRs, which may cover less than the 730 days you ask for.
- **Background jobs live in memory** and are lost if the server restarts.

### Known issue: the bot filter

`WHAT_WE_BUILT.md` and `FEATURES.md` say that AI review bots are filtered by name patterns (`copilot`, `github-…`, `…-ai`, `…-agent`, `…-reviewer`), that Last Day asks GitHub whether each account is a bot, and that the repo dialog has a "Download fresh data" option with a 365-day default.

**None of that is in the code in this folder:**

- `is_bot()` only checks `[bot]`, `-bot`, `ghost` and exact names from the `BOTS` list. Accounts like `copilot-pull-request-reviewer`, `cubic-dev-ai` or `github-advanced-security` still count as people.
- The GraphQL queries don't ask for `__typename`, so GitHub is never asked whether an author is a bot.
- The "Load a GitHub repo" dialog has no refresh option and defaults to 730 days. The server accepts a `refresh` setting, but the UI never sends it.

To fix it:

1. add name patterns to `is_bot()`
2. add `__typename` to every `author { ... }` block in the two queries and drop authors whose type is `Bot`
3. add a "Download fresh data" checkbox that sends `refresh: true`
4. reload any repo that was ingested before the fix
