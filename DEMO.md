# Demo script (about 3 minutes)

Before recording, click **Reset demo** so the Acme Pay graph is fresh.

**0:00 The problem (15s).** "Every company has lost the one person who knew why something works. You find out after they've left. Last Day tells you before."

**0:15 Click Priya Raman (40s).** Let the animation play: her node goes dark, three areas go red, then the orange cascade spreads along dependencies. Read the headline. "She's the only person who really knows ledger reconciliation, she owns our biggest customer's escalations, and she's the only link between Payments and Risk. Nobody would have guessed that from the org chart."

**0:55 Impact tab (15s).** "Ranked by FalkorDB PageRank on the dependency graph times the coverage lost. Knowledge health drops from 87 to 76."

**1:10 Exit interview (35s).** Click **Write their exit interview**. Read the first question aloud, then point at its sources. "Every question comes from the graph and cites where it came from." Paste an answer and save. "Watch Ledger Reconciliation." It turns amber.

**1:45 Handover (25s).** In the Handover tab: "Rahul is the best successor. He knows the neighbouring systems, and Priya answered his questions 7 times." Draft the ticket, then click **Mark handover done**. It turns green.

**2:10 Ask (30s).** Use two suggested questions: the Diwali leave one ("who covers payments?") and the SFTP key one. Point at the Ghost Mode tag. "Vikram left 8 months ago. Last Day still answers from his trail and tells you he's gone."

**2:40 Proof (20s).** Open the **Backtest** tab with a real repo result. "We rebuilt the graph from data before a maintainer left. The areas we flagged really did slow down afterwards." Then open the Cypher drawer: "Every answer shows the exact queries FalkorDB ran."

**3:00 Close.** "Last Day: know what your company forgets before someone leaves."

## One-paragraph submission summary

Last Day is a company brain that answers the question nobody can answer today: *what do we lose if this person leaves?* It builds a FalkorDB graph from GitHub activity, Slack answers, docs, tickets, incidents, decisions and customer ownership. It scores who knows what with recency-weighted Cypher aggregations, then simulates departures: coverage recomputation, dependency cascades over variable-length paths, criticality from `algo.pageRank`, and team bridges from `algo.betweenness`. Its agent then acts. It writes a targeted exit interview where every question cites its source, writes the answers back into the graph (red nodes turn green), recommends successors by graph proximity, drafts handover tickets, and answers questions from the trail of people who already left (Ghost Mode, on FalkorDB full-text search). Every answer shows the Cypher behind it. A backtest on real repositories checks the predictions against what actually happened, and an MCP server exposes the same tools to any agent.
