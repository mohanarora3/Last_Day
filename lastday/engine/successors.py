"""Who should take over an orphaned asset? Ranked by graph proximity, with reasons."""
from __future__ import annotations

from .. import config as C
from ..db import Graph


def recommend(g: Graph, asset_id: str, removed: list[str], k: int = 3) -> list[dict]:
    params = {"aid": asset_id, "removed": removed}
    cands: dict[str, dict] = {}

    def cand(pid, name, team):
        return cands.setdefault(pid, {"id": pid, "name": name, "team": team, "direct": 0.0, "neighbor": 0.0,
                                      "neighbor_via": [], "team_owner": False, "mentored": 0, "load": 0,
                                      "reasons": []})

    for r in g.q("""MATCH (c:Person)-[k:KNOWS]->(a:Asset {id: $aid})
                    WHERE c.active = true AND NOT c.id IN $removed
                    RETURN c.id AS id, c.name AS name, c.team AS team, k.score AS s, k.n AS n""", params):
        c = cand(r["id"], r["name"], r["team"])
        c["direct"] = r["s"]
        c["direct_n"] = r["n"]

    for r in g.q("""MATCH (c:Person)-[k:KNOWS]->(n:Asset)-[:DEPENDS_ON]-(a:Asset {id: $aid})
                    WHERE c.active = true AND NOT c.id IN $removed AND k.score >= 0.2
                    RETURN c.id AS id, c.name AS name, c.team AS team, sum(k.score) AS s,
                           collect(n.name) AS via""", params):
        c = cand(r["id"], r["name"], r["team"])
        c["neighbor"] = min(1.0, r["s"] / 1.5)
        c["neighbor_via"] = r["via"][:3]

    for r in g.q("""MATCH (c:Person)-[:MEMBER_OF]->(:Team)-[:OWNS]->(a:Asset {id: $aid})
                    WHERE c.active = true AND NOT c.id IN $removed
                    RETURN c.id AS id, c.name AS name, c.team AS team""", params):
        cand(r["id"], r["name"], r["team"])["team_owner"] = True

    # People the leaver taught about *this* asset (answered their questions, reviewed their work).
    for r in g.q("""MATCH (l:Person)-[:AUTHORED]->(e:Evidence)-[:ABOUT]->(a:Asset {id: $aid})
                    WHERE l.id IN $removed AND e.counterpart <> ''
                    WITH e.counterpart AS cid, count(e) AS n
                    MATCH (c:Person {id: cid}) WHERE c.active = true AND NOT c.id IN $removed
                    RETURN c.id AS id, c.name AS name, c.team AS team, n""", params):
        cand(r["id"], r["name"], r["team"])["mentored"] = r["n"]

    load = {r["id"]: r["n"] for r in g.q("""
        MATCH (p:Person)-[k:KNOWS]->(:Asset)
        WHERE p.active = true AND NOT p.id IN $removed AND k.score >= 0.55
        RETURN p.id AS id, count(k) AS n""", params)}

    ranked = []
    for c in cands.values():
        c["load"] = load.get(c["id"], 0)
        s = (0.5 * c["direct"] + 0.22 * c["neighbor"] + 0.1 * (1 if c["team_owner"] else 0)
             + 0.18 * min(1.0, c["mentored"] / 4) - 0.035 * max(0, c["load"] - 2))
        c["fit"] = round(s, 3)
        if c["direct"] >= 0.05:
            c["reasons"].append(f"already has {round(c['direct'] * 100)}% familiarity "
                                f"({c.get('direct_n', 0)} pieces of evidence)")
        if c["neighbor"] > 0:
            c["reasons"].append("knows neighbouring systems: " + ", ".join(c["neighbor_via"]))
        if c["mentored"]:
            c["reasons"].append(f"the leaver answered/reviewed their work on this {c['mentored']} times")
        if c["team_owner"]:
            c["reasons"].append("on the owning team")
        if c["load"] > 2:
            c["reasons"].append(f"careful: already the go-to person for {c['load']} areas")
        ranked.append(c)
    ranked.sort(key=lambda c: -c["fit"])
    return ranked[:k]
