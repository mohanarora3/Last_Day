"""Ask the company brain.

Routes a plain-English question to the right graph tool:
  * "If Arjun and Meera are on leave Diwali week, who covers payments?"  -> who_covers
  * "What happens if Priya leaves?"                                        -> simulate
  * "Who knows the fraud engine?"                                         -> who_knows
  * anything else                                                         -> Ghost Mode: answer from the
    evidence trail (including people who left), with numbered sources.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .. import config as C
from ..db import Graph
from ..engine.simulate import simulate, who_covers
from ..engine.successors import recommend
from . import llm

STOP = set("""a an the is are was were be been to of in on for and or but if then what who whom which why how
when where do does did can could should would will i we you it this that these those there here my our your
with about from at by as into any some all me us them they he she his her its out not no yes so just than
too very s t don doesn isn aren wasn weren happen happens""".split())


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-zA-Z0-9]+", text.lower()) if w not in STOP and len(w) > 1]


def _match_people(g: Graph, text: str) -> list[dict]:
    low = text.lower()
    out = []
    for p in g.q("MATCH (p:Person) RETURN p.id AS id, p.name AS name, p.handle AS handle, p.active AS active"):
        names = {p["id"].lower(), (p["handle"] or "").lower().lstrip("@"), (p["name"] or "").lower()}
        first = (p["name"] or "").split(" ")[0].lower()
        if len(first) >= 3:
            names.add(first)
        if any(n and re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", low) for n in names):
            out.append(p)
    return out


def _match_assets(g: Graph, text: str) -> list[dict]:
    low = text.lower()
    hits = []
    for a in g.q("MATCH (a:Asset) RETURN a.id AS id, a.name AS name, a.aliases AS aliases"):
        terms = [a["id"].lower(), (a["name"] or "").lower()] + [x.lower() for x in (a["aliases"] or [])]
        best = 0
        for t in terms:
            if t and re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low):
                best = max(best, len(t))
        if best:
            hits.append((best, a))
    hits.sort(key=lambda x: -x[0])
    return [a for _, a in hits]


def _date(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %Y") if ts else ""


def ensure_fulltext(g: Graph) -> bool:
    try:
        g.q("CALL db.idx.fulltext.createNodeIndex('Evidence', 'title', 'text')")
    except Exception as e:
        if "already" not in str(e).lower():
            return False
    return True


def ghost(g: Graph, question: str, removed: list[str] | None = None, assets: list[dict] | None = None) -> dict:
    removed = removed or []
    assets = assets if assets is not None else _match_assets(g, question)
    toks = _tokens(question)
    hits: dict[str, dict] = {}
    method = "falkordb:fulltext"
    if toks and ensure_fulltext(g):
        try:
            rows = g.q("""CALL db.idx.fulltext.queryNodes('Evidence', $q) YIELD node, score
                          WHERE node.text <> ''
                          OPTIONAL MATCH (p:Person)-[:AUTHORED]->(node)
                          OPTIONAL MATCH (node)-[:ABOUT]->(a:Asset)
                          RETURN node.id AS id, node.title AS title, node.text AS text, node.url AS url,
                                 node.ts AS ts, node.kind AS kind, score, p.id AS author, p.name AS author_name,
                                 p.active AS active, collect(a.id) AS assets
                          ORDER BY score DESC LIMIT 12""", {"q": "|".join(toks[:12])})
            for r in rows:
                hits[r["id"]] = r
        except Exception:
            method = "keyword"
    else:
        method = "keyword"
    if assets:
        ids = [a["id"] for a in assets[:3]]
        for r in g.q("""MATCH (e:Evidence)-[:ABOUT]->(a:Asset) WHERE a.id IN $ids AND e.text <> ''
                        OPTIONAL MATCH (p:Person)-[:AUTHORED]->(e)
                        RETURN e.id AS id, e.title AS title, e.text AS text, e.url AS url, e.ts AS ts,
                               e.kind AS kind, e.weight AS score, p.id AS author, p.name AS author_name,
                               p.active AS active, collect(a.id) AS assets
                        ORDER BY e.weight DESC LIMIT 15""", {"ids": ids}):
            if r["id"] not in hits:
                hits[r["id"]] = {**r, "score": 0.0}
    # rank: fulltext score + keyword overlap + bonus when it is about the asset in question
    aid = {a["id"] for a in assets[:3]}
    tokset = set(toks)
    for h in hits.values():
        overlap = len(tokset & set(_tokens((h["title"] or "") + " " + (h["text"] or ""))))
        h["rank"] = float(h.get("score") or 0) + 0.6 * overlap + (1.0 if aid & set(h.get("assets") or []) else 0)
    ranked = sorted(hits.values(), key=lambda h: -h["rank"])[:5]
    ranked = [h for h in ranked if h["rank"] >= 1.0]

    sources = [{"n": i + 1, "title": h["title"], "text": h["text"], "url": h["url"], "kind": h["kind"],
                "date": _date(h["ts"]), "author": h["author_name"] or "unknown",
                "author_gone": (h["author"] in removed) or (h["active"] is False),
                "author_status": "left" if h["active"] is False else ("leaving" if h["author"] in removed else None)}
               for i, h in enumerate(ranked)]
    who_now = []
    if assets:
        for r in g.q("""MATCH (p:Person)-[k:KNOWS]->(a:Asset {id: $a}) WHERE p.active = true AND NOT p.id IN $r
                        RETURN p.id AS id, p.name AS name, k.score AS score ORDER BY score DESC LIMIT 3""",
                     {"a": assets[0]["id"], "r": removed}):
            who_now.append({"id": r["id"], "name": r["name"], "score": round(r["score"], 2)})

    if not sources:
        answer = ("The trail runs out here: nobody wrote this down anywhere Last Day can see. "
                  + (f"Closest people to ask: {', '.join(w['name'] for w in who_now)}." if who_now
                     else "Nobody active has worked on this either. Flag it as a knowledge gap."))
        return {"mode": "ghost", "answer": answer, "sources": [], "who_to_ask": who_now, "method": method,
                "assets": [a["id"] for a in assets], "llm": False}

    ctx = "\n\n".join(f"[{s['n']}] ({s['kind']}, {s['author']}"
                      f"{' - has left' if s['author_status'] == 'left' else ' - is leaving' if s['author_status'] == 'leaving' else ''}"
                      f", {s['date']}) {s['title']}\n{s['text']}"
                      for s in sources)
    answer = llm.complete(
        "You are Last Day, a company brain. Answer ONLY from the numbered sources, cite them like [1]. "
        "If a source author has left, say 'according to <name> (who has left)'. If the sources don't answer "
        "the question, say so plainly. Be concise: 2-5 sentences.",
        f"Question: {question}\n\nSources:\n{ctx}", max_tokens=350)
    used_llm = bool(answer)
    if not answer:
        parts = []
        for s in sources[:2]:
            who = {"left": f"{s['author']} (who has left)", "leaving": f"{s['author']} (who is leaving)"}.get(
                s["author_status"], s["author"])
            parts.append(f"According to {who}, {s['date']}: \"{s['text']}\" [{s['n']}]")
        answer = "\n\n".join(parts)
    return {"mode": "ghost", "answer": answer, "sources": sources, "who_to_ask": who_now, "method": method,
            "assets": [a["id"] for a in assets], "llm": used_llm,
            "ghost_of": sorted({s["author"] for s in sources if s["author_gone"]})}


LEAVE_RX = re.compile(r"\b(on leave|leave|vacation|holiday|away|out of office|ooo|off|absent|offsite|sick)\b")
COVER_RX = re.compile(r"\b(who covers|who can cover|cover|covers|who handles|who can handle|backup|who is left)\b")
DEPART_RX = re.compile(r"\b(leaves|quits|quit|resigns|resign|leaving|left|departs|exits|fired|laid off|"
                       r"leaves the company|what if .* (go|goes|gone))\b")
WHO_KNOWS_RX = re.compile(r"\b(who knows|who owns|who to ask|who should i ask|expert|experts|who understands)\b")


def ask(g: Graph, question: str, removed: list[str] | None = None) -> dict:
    removed = removed or []
    q = question.lower()
    people = _match_people(g, question)
    assets = _match_assets(g, question)
    pids = [p["id"] for p in people]

    if people and assets and (LEAVE_RX.search(q) or COVER_RX.search(q)):
        res = who_covers(g, pids, [a["id"] for a in assets[:3]])
        lines = []
        for r in res:
            if r["uncovered"]:
                lines.append(f"**{r['name']}**: nobody. With {', '.join(p['name'] for p in people)} out, "
                             f"coverage drops from {round(r['coverage_before']*100)}% to "
                             f"{round(r['coverage_after']*100)}%.")
            else:
                best = r["covers"][0]
                rest = ", ".join(c["name"] for c in r["covers"][1:3])
                lines.append(f"**{r['name']}**: {best['name']} ({round(best['score']*100)}% familiarity, "
                             f"{best['evidence']} pieces of evidence)" + (f", then {rest}" if rest else "")
                             + f". Coverage {round(r['coverage_before']*100)}% → {round(r['coverage_after']*100)}%.")
        return {"mode": "coverage", "answer": "\n".join(lines), "absent": people, "results": res}

    if people and DEPART_RX.search(q):
        sim = simulate(g, pids)
        s = sim["summary"]
        top = [a for a in sim["assets"] if a["status"] == "orphaned"][:4]
        names = ", ".join(p["name"] for p in people)
        txt = (f"If {names} left today, {s['orphaned']} areas would be orphaned, {s['fragile']} would become "
               f"fragile and {s['inherited']} more would inherit the risk through dependencies. Knowledge health "
               f"goes from {round(s['health_before']*100)} to {round(s['health_after']*100)}.")
        if top:
            txt += " Most critical: " + ", ".join(f"{a['name']} ({round(a['before']*100)}%→{round(a['after']*100)}%)"
                                                   for a in top) + "."
        for l in s["severed_links"]:
            txt += f" They carry {round(l['share_lost']*100)}% of the {l['teams'][0]} ↔ {l['teams'][1]} connection."
        return {"mode": "simulate", "answer": txt, "simulation": sim, "removed": people}

    if assets and WHO_KNOWS_RX.search(q):
        a = assets[0]
        rows = g.q("""MATCH (p:Person)-[k:KNOWS]->(a:Asset {id: $a}) WHERE p.active = true AND NOT p.id IN $r
                      RETURN p.id AS id, p.name AS name, p.team AS team, k.score AS score, k.n AS n
                      ORDER BY score DESC LIMIT 5""", {"a": a["id"], "r": removed})
        if not rows:
            return {"mode": "who_knows", "answer": f"Nobody active knows {a['name']}. That's a gap.", "people": []}
        txt = f"For **{a['name']}**: " + "; ".join(
            f"{r['name']} ({round(r['score']*100)}%, {r['n']} pieces of evidence)" for r in rows) + "."
        if rows[0]["score"] >= C.KNOWS_THRESHOLD and (len(rows) < 2 or rows[1]["score"] < C.KNOWS_THRESHOLD):
            succ = recommend(g, a["id"], [rows[0]["id"]], k=1)
            if succ:
                txt += f" Bus factor is 1. If {rows[0]['name']} is out, next best: {succ[0]['name']}."
        return {"mode": "who_knows", "answer": txt, "people": rows, "asset": a}

    return ghost(g, question, removed, assets)
