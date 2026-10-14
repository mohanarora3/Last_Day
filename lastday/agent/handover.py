"""Handover tickets: successor + reading list + captured answers + what to pair on."""
from __future__ import annotations

import json
import os
import urllib.request

from ..config import HANDOVER_STRENGTH
from ..db import Graph
from ..engine.knowledge import graph_now, recompute, saturation
from ..engine.simulate import simulate
from ..engine.successors import recommend
from . import llm
from .interview import _date


def draft(g: Graph, asset_id: str, removed: list[str], successor: str | None = None) -> dict:
    a = g.q1("""MATCH (a:Asset {id: $a}) OPTIONAL MATCH (t:Team)-[:OWNS]->(a)
                RETURN a.id AS id, a.name AS name, a.description AS description, t.name AS team""",
             {"a": asset_id})
    if not a:
        raise ValueError(f"unknown asset {asset_id}")
    cands = recommend(g, asset_id, removed)
    succ = next((c for c in cands if c["id"] == successor), None) if successor else (cands[0] if cands else None)
    if successor and not succ:
        row = g.q1("MATCH (p:Person {id: $p}) RETURN p.id AS id, p.name AS name, p.team AS team", {"p": successor})
        succ = {**row, "reasons": ["chosen manually"], "fit": None} if row else None
    leavers = g.q("MATCH (p:Person) WHERE p.id IN $r RETURN p.id AS id, p.name AS name", {"r": removed})
    leaver_names = ", ".join(l["name"] for l in leavers) or "the previous owner"

    reading = g.q("""MATCH (p:Person)-[:AUTHORED]->(e:Evidence)-[:ABOUT]->(a:Asset {id: $a})
                     WHERE p.id IN $r AND e.kind IN ['decision', 'incident', 'doc', 'slack_answer', 'interview',
                                                     'issue_answer', 'pr']
                     RETURN e.kind AS kind, e.title AS title, e.url AS url, e.ts AS ts, e.weight AS w
                     ORDER BY e.weight DESC, e.ts DESC LIMIT 8""", {"a": asset_id, "r": removed})
    answers = g.q("""MATCH (q:Question)-[:ABOUT]->(a:Asset {id: $a}) WHERE q.status = 'answered'
                     RETURN q.text AS q, q.answer AS answer""", {"a": asset_id})
    deps = g.q("""MATCH (a:Asset {id: $a})-[:DEPENDS_ON]-(n:Asset)
                  OPTIONAL MATCH (p:Person)-[k:KNOWS]->(n) WHERE p.active = true AND NOT p.id IN $r
                  WITH n, p, k ORDER BY k.score DESC
                  WITH n, collect(p.name)[0] AS expert
                  RETURN n.name AS name, expert""", {"a": asset_id, "r": removed})

    sname = succ["name"] if succ else "TBD"
    lines = [f"## Handover: {a['name']} → {sname}", "",
             f"**Why:** {leaver_names} is leaving and {a['name']} loses most of its coverage.",
             f"**Owning team:** {a.get('team') or 'unknown'}", ""]
    if succ and succ.get("reasons"):
        lines += ["**Why this successor:**"] + [f"- {r}" for r in succ["reasons"]] + [""]
    lines += ["### Read first"]
    lines += [f"- [{r['kind']}] {r['title']} ({_date(r['ts'])}) {r['url']}" for r in reading] or ["- (nothing found)"]
    if answers:
        lines += ["", "### Captured in the exit interview"]
        for r in answers:
            lines += [f"- **Q:** {r['q']}", f"  **A:** {r['answer']}"]
    if deps:
        lines += ["", "### Neighbouring systems and who to ask"]
        lines += [f"- {d['name']}: {d['expert'] or 'nobody, flag this'}" for d in deps]
    lines += ["", "### Checklist",
              f"- [ ] Pair with {leaver_names} on one real change to {a['name']} before their last day",
              "- [ ] Shadow the next incident / on-call for this area",
              f"- [ ] Become a required reviewer for {a['name']}",
              "- [ ] Mark this ticket done in Last Day so coverage updates"]
    body = "\n".join(lines)

    summary = llm.complete(
        "You write crisp, friendly engineering handover summaries. 3 sentences max. Use only the facts given.",
        body, max_tokens=250)
    return {"asset": a, "successor": succ, "candidates": cands, "title": f"Handover: {a['name']} → {sname}",
            "markdown": body, "summary": summary}


def accept(g: Graph, asset_id: str, successor: str, removed: list[str]) -> dict:
    """Successor completed the handover: record it as evidence and recompute coverage."""
    now = graph_now(g)
    weight = HANDOVER_STRENGTH * saturation(g)  # meaningful at any graph scale
    g.q("""MATCH (p:Person {id: $s}), (a:Asset {id: $a})
           MERGE (e:Evidence {id: $eid})
           SET e.kind = 'handover', e.title = $title, e.text = '', e.url = '', e.ts = $now, e.weight = $w,
               e.persistent = false, e.author = $s, e.counterpart = '', e.channel = 'handover'
           MERGE (p)-[:AUTHORED]->(e)
           MERGE (e)-[r:ABOUT]->(a) SET r.share = 1.0
           MERGE (a)-[:HANDED_TO]->(p)""",
        {"s": successor, "a": asset_id, "eid": f"handover_{asset_id}_{successor}",
         "title": f"Completed handover of {asset_id}", "now": now, "w": weight})
    recompute(g)
    sim = simulate(g, removed)
    return {"asset": next((x for x in sim["assets"] if x["id"] == asset_id), None), "summary": sim["summary"]}


def create_github_issue(repo: str, title: str, body: str) -> dict:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return {"ok": False, "error": "GITHUB_TOKEN not set"}
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/issues",
                                 data=json.dumps({"title": title, "body": body, "labels": ["handover"]}).encode(),
                                 headers={"authorization": f"Bearer {token}",
                                          "accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        return {"ok": True, "url": data.get("html_url")}
    except Exception as e:
        return {"ok": False, "error": str(e)}
