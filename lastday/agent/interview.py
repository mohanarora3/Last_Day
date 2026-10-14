"""Targeted exit interview.

Questions come from what the graph says will be lost, and every question cites
the evidence it was built from. Answers are written back into the graph as
persistent knowledge, so coverage goes up and red nodes turn amber/green.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

from ..config import EVIDENCE_WEIGHTS
from ..db import Graph
from ..engine.knowledge import graph_now, recompute
from ..engine.simulate import simulate

ANSWER_KINDS = ("slack_answer", "issue_answer", "ticket")


def _date(ts: float | None) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d %b %Y")


def _src(e: dict) -> dict:
    return {"title": e["title"], "url": e["url"], "kind": e["kind"], "date": _date(e["ts"]),
            "channel": e.get("channel") or ""}


def _qid(person: str, asset: str, text: str) -> str:
    return "q_" + hashlib.sha1(f"{person}|{asset}|{text}".encode()).hexdigest()[:10]


def _others_phrase(n_others: int) -> str:
    if n_others == 0:
        return "nobody else ever has"
    if n_others == 1:
        return "only one other person ever has"
    return f"everyone else combined answered {n_others}"


def build_questions(g: Graph, person: str, max_assets: int = 6) -> dict:
    who = g.q1("MATCH (p:Person {id: $p}) RETURN p.name AS name, p.team AS team", {"p": person})
    if not who:
        raise ValueError(f"unknown person {person}")
    sim = simulate(g, [person])
    targets = [a for a in sim["assets"] if a["status"] in ("orphaned", "fragile")][:max_assets]
    if len(targets) < 3:  # still interview them about whatever they know best
        extra = g.q("""MATCH (p:Person {id: $p})-[k:KNOWS]->(a:Asset) RETURN a.id AS id
                       ORDER BY k.score DESC LIMIT 4""", {"p": person})
        ids = {a["id"] for a in targets}
        by_id = {a["id"]: a for a in sim["assets"]}
        targets += [by_id[r["id"]] for r in extra if r["id"] not in ids and r["id"] in by_id][:3 - len(targets)]

    questions = []
    for a in targets:
        ev = g.q("""MATCH (p:Person {id: $p})-[:AUTHORED]->(e:Evidence)-[:ABOUT]->(a:Asset {id: $a})
                    RETURN e.id AS id, e.kind AS kind, e.title AS title, e.text AS text, e.url AS url, e.ts AS ts,
                           e.channel AS channel
                    ORDER BY e.ts DESC""", {"p": person, "a": a["id"]})
        others = g.q("""MATCH (o:Person)-[:AUTHORED]->(e:Evidence)-[:ABOUT]->(a:Asset {id: $a})
                        WHERE o.id <> $p
                        RETURN e.kind AS kind, count(e) AS n""", {"p": person, "a": a["id"]})
        other_n = {r["kind"]: r["n"] for r in others}
        deps = g.q("""MATCH (a:Asset {id: $a})
                      OPTIONAL MATCH (a)-[:DEPENDS_ON]->(d:Asset)
                      OPTIONAL MATCH (u:Asset)-[:DEPENDS_ON]->(a)
                      RETURN collect(DISTINCT d.name) AS deps, collect(DISTINCT u.name) AS users""",
                   {"a": a["id"]})[0]
        by_kind: dict[str, list] = {}
        for e in ev:
            by_kind.setdefault(e["kind"], []).append(e)
        qs = []

        answers = [e for k in ANSWER_KINDS for e in by_kind.get(k, [])]
        others_answers = sum(other_n.get(k, 0) for k in ANSWER_KINDS)
        if len(answers) >= 3 and others_answers <= len(answers) / 2:
            chan = next((e["channel"] for e in answers if e.get("channel")), "")
            where = f" in {chan}" if chan else ""
            rich = [e for e in answers if e.get("text")] or answers
            qs.append({
                "text": f"You've answered {len(answers)} questions about {a['name']}{where}; "
                        f"{_others_phrase(others_answers)}. What are the three questions people keep asking, "
                        f"and what are the answers?",
                "why": "you are the only person answering questions here",
                "sources": [_src(e) for e in rich[:3]],
            })
        for e in by_kind.get("decision", [])[:2]:
            qs.append({
                "text": f"You made the call in \"{e['title']}\". What would make that decision wrong, and "
                        f"what should someone check before changing it?",
                "why": "decision with no other owner",
                "sources": [_src(e)],
            })
        for e in by_kind.get("incident", [])[:1]:
            qs.append({
                "text": f"You handled \"{e['title']}\". If it happens again at 3am, what's the first thing to "
                        f"check, and what is still not written down?",
                "why": "incident knowledge",
                "sources": [_src(e)],
            })
        reviews = by_kind.get("review", [])
        total_reviews = len(reviews) + other_n.get("review", 0)
        if len(reviews) >= 3 and len(reviews) >= 0.5 * total_reviews:
            qs.append({
                "text": f"You reviewed {len(reviews)} of the {total_reviews} changes to {a['name']}. What do you "
                        f"check in a review here that a new reviewer would miss?",
                "why": "you are the main reviewer",
                "sources": [_src(e) for e in reviews[:2]],
            })
        for e in by_kind.get("customer_owner", [])[:1]:
            qs.append({
                "text": f"You own the relationship with {a['name']}. What do they care about that isn't in "
                        f"the CRM or the contract, and who should they call from now on?",
                "why": "customer relationship",
                "sources": [_src(e)],
            })
        if a["doc_cov"] < 0.08 and len(qs) < 3:
            qs.append({
                "text": f"Almost nothing about {a['name']} is written down. What would you tell your "
                        f"replacement on day one?",
                "why": "no documentation found",
                "sources": [_src(e) for e in ev[:2]],
            })
        if len(qs) < 2:
            parts = []
            if deps["deps"]:
                parts.append(f"{a['name']} depends on {', '.join(deps['deps'][:3])}")
            if deps["users"]:
                parts.append(f"{', '.join(deps['users'][:3])} {'depends' if len(deps['users']) == 1 else 'depend'} "
                             f"on {a['name'] if not parts else 'it'}")
            lead = (" and ".join(parts) + ". ") if parts else ""
            qs.append({
                "text": f"{lead}What breaks most often in {a['name']}, and how do you fix it?",
                "why": "dependency knowledge",
                "sources": [_src(e) for e in ev[:2]],
            })
        for q in qs[:3]:
            q.update({"id": _qid(person, a["id"], q["text"]), "asset": a["id"], "asset_name": a["name"],
                      "status_now": a["status"], "coverage_after": a["after"]})
            questions.append(q)

    # Persist the interview in the graph so it survives restarts and can be resumed.
    for q in questions:
        g.q("""MATCH (p:Person {id: $p}), (a:Asset {id: $a})
               MERGE (q:Question {id: $id})
               ON CREATE SET q.text = $text, q.why = $why, q.status = 'open', q.created = $now
               MERGE (q)-[:FOR]->(p)
               MERGE (q)-[:ABOUT]->(a)""",
            {"p": person, "a": q["asset"], "id": q["id"], "text": q["text"], "why": q["why"],
             "now": time.time()})
    status = {r["id"]: r for r in g.q("""MATCH (q:Question)-[:FOR]->(:Person {id: $p})
                                        RETURN q.id AS id, q.status AS status, q.answer AS answer""",
                                       {"p": person})}
    for q in questions:
        s = status.get(q["id"], {})
        q["status"] = s.get("status", "open")
        q["answer"] = s.get("answer")
    return {"person": {"id": person, **who}, "questions": questions,
            "summary": sim["summary"], "assets": [a["id"] for a in targets]}


def record_answer(g: Graph, person: str, asset: str, question_id: str, question: str, answer: str) -> dict:
    now = graph_now(g)
    eid = f"interview_{question_id}"
    g.q("""MATCH (p:Person {id: $p}), (a:Asset {id: $a})
           MERGE (e:Evidence {id: $eid})
           SET e.kind = 'interview', e.title = $title, e.text = $text, e.url = $url, e.ts = $now,
               e.weight = $w, e.persistent = true, e.author = $p, e.counterpart = '', e.channel = 'exit-interview'
           MERGE (p)-[:AUTHORED]->(e)
           MERGE (e)-[r:ABOUT]->(a) SET r.share = 1.0""",
        {"p": person, "a": asset, "eid": eid, "title": f"Exit interview: {question[:120]}",
         "text": f"Q: {question}\nA: {answer}", "url": f"lastday://interview/{question_id}", "now": now,
         "w": EVIDENCE_WEIGHTS["interview"]})
    g.q("""MATCH (q:Question {id: $id}) SET q.status = 'answered', q.answer = $answer, q.answered = $t""",
        {"id": question_id, "answer": answer, "t": time.time()})
    recompute(g)
    after = simulate(g, [person])
    a = next((x for x in after["assets"] if x["id"] == asset), None)
    return {"asset": a, "summary": after["summary"]}
