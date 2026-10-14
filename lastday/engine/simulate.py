"""Departure / leave simulation.

coverage(asset) = 1 - (1 - doc_cov) * prod over remaining people (1 - KNOWS.score)
i.e. the probability that at least one remaining person (or something written
down) can answer a question about the asset. Remove people -> recompute.
"""
from __future__ import annotations

import math

from .. import config as C
from ..db import Graph
from .algorithms import betweenness_people, pagerank_assets


def _cov(log_people: float | None, doc: float | None) -> float:
    people_miss = math.exp(log_people or 0.0)
    return 1.0 - people_miss * (1.0 - (doc or 0.0))


def _status(before: float, after: float, bus_before: int = 0, bus_after: int = 0, share: float = 0.0) -> str:
    drop = before - after
    if before < C.ORPHAN_BELOW:
        return "gap"                     # knowledge debt that already exists today
    if after < C.ORPHAN_BELOW and drop >= C.MIN_DROP:
        return "orphaned"
    if after < C.FRAGILE_BELOW and drop >= C.MIN_DROP:
        return "fragile"
    if bus_before >= 2 and bus_after <= 1 and drop >= C.MIN_DROP and after < C.SINGLE_KNOWER_BELOW:
        return "fragile"                 # only one person left who really knows it
    if share >= C.SHARE_LOST_FRAGILE and drop >= C.MIN_DROP:
        return "fragile"                 # most of what is known about it walks out the door
    return "safe"


def coverage_table(g: Graph, removed: list[str]) -> list[dict]:
    rows = g.q("""
        MATCH (a:Asset)
        OPTIONAL MATCH (p:Person)-[k:KNOWS]->(a) WHERE p.active = true
        WITH a,
             sum(log(1.0 - k.score)) AS lb, sum(k.raw) AS raw_all,
             sum(CASE WHEN p.id IN $removed THEN k.raw ELSE 0.0 END) AS raw_removed,
             sum(CASE WHEN p.id IN $removed THEN 0.0 ELSE log(1.0 - k.score) END) AS la,
             collect(CASE WHEN k.score >= $t THEN p.id ELSE null END) AS knowers_before,
             collect(CASE WHEN k.score >= $t AND NOT p.id IN $removed THEN p.id ELSE null END) AS knowers_after
        OPTIONAL MATCH (t:Team)-[:OWNS]->(a)
        OPTIONAL MATCH (a)-[:HANDED_TO]->(h:Person) WHERE h.active = true AND NOT h.id IN $removed
        RETURN a.id AS id, a.name AS name, a.kind AS kind, a.business AS business, a.doc_cov AS doc,
               h.name AS handed_to,
               a.description AS description, t.id AS team, lb, la, knowers_before, knowers_after,
               raw_all, raw_removed
    """, {"removed": removed, "t": C.KNOWS_THRESHOLD})
    out = []
    for r in rows:
        before, after = _cov(r["lb"], r["doc"]), _cov(r["la"], r["doc"])
        bb = len([x for x in r["knowers_before"] if x])
        ba = len([x for x in r["knowers_after"] if x])
        share = (r["raw_removed"] or 0.0) / r["raw_all"] if r["raw_all"] else 0.0
        if r["handed_to"]:
            share = 0.0  # a completed handover means the concentration has been dealt with
        out.append({
            "id": r["id"], "name": r["name"], "kind": r["kind"], "team": r["team"],
            "business": r["business"] or 0.0, "doc_cov": round(r["doc"] or 0.0, 3),
            "description": r["description"] or "", "handed_to": r["handed_to"],
            "before": round(before, 3), "after": round(after, 3), "drop": round(before - after, 3),
            "bus_factor_before": bb, "bus_factor_after": ba, "share_lost": round(share, 3),
            "status": _status(before, after, bb, ba, share) if removed else ("gap" if before < C.ORPHAN_BELOW else "safe"),
        })
    return out


def cascade(g: Graph, orphaned: list[str]) -> dict[str, dict]:
    """Assets that depend (1-3 hops) on something orphaned inherit the risk."""
    if not orphaned:
        return {}
    rows = g.q("""
        MATCH p = (a:Asset)-[:DEPENDS_ON*1..3]->(o:Asset)
        WHERE o.id IN $orph AND NOT a.id IN $orph
        RETURN a.id AS id, o.id AS via, length(p) AS hops, [n IN nodes(p) | n.id] AS path
    """, {"orph": orphaned})
    best: dict[str, dict] = {}
    for r in rows:
        cur = best.get(r["id"])
        if cur is None or r["hops"] < cur["hops"]:
            best[r["id"]] = {"via": r["via"], "hops": r["hops"], "path": r["path"],
                             "count": (cur or {}).get("count", 0) + 1}
        else:
            cur["count"] += 1
    return best


def team_links(g: Graph, removed: list[str]) -> list[dict]:
    """How much of each team-to-team connection runs through the removed people."""
    rows = g.q("""
        MATCH (x:Person)-[c:COLLAB]-(y:Person)
        WHERE x.active = true AND y.active = true AND x.team < y.team
        RETURN x.id AS a, x.team AS ta, y.id AS b, y.team AS tb, c.w AS w
    """)
    pairs: dict[tuple, dict] = {}
    rem = set(removed)
    for r in rows:
        key = (r["ta"], r["tb"])
        d = pairs.setdefault(key, {"total": 0.0, "lost": 0.0, "carriers": {}})
        d["total"] += r["w"]
        if r["a"] in rem or r["b"] in rem:
            d["lost"] += r["w"]
            for p in (r["a"], r["b"]):
                if p in rem:
                    d["carriers"][p] = d["carriers"].get(p, 0.0) + r["w"]
    out = []
    for (ta, tb), d in pairs.items():
        if d["total"] <= 0:
            continue
        share = d["lost"] / d["total"]
        out.append({"teams": [ta, tb], "share_lost": round(share, 3), "total": round(d["total"], 3),
                    "carriers": sorted(d["carriers"], key=lambda p: -d["carriers"][p]),
                    "severed": share >= 0.6})
    return sorted(out, key=lambda x: -x["share_lost"])


def simulate(g: Graph, removed: list[str]) -> dict:
    table = coverage_table(g, removed)
    pr, pr_src = pagerank_assets(g)
    mx = max(pr.values()) if pr else 1.0
    for a in table:
        a["pagerank"] = round(pr.get(a["id"], 0.0) / (mx or 1.0), 3)
        a["criticality"] = round(0.6 * a["pagerank"] + 0.4 * a["business"], 3)

    orphaned = [a["id"] for a in table if a["status"] == "orphaned"]
    inherited = cascade(g, orphaned)
    for a in table:
        inh = inherited.get(a["id"])
        a["inherited"] = inh
        loss = max(a["drop"], 0.6 * a["share_lost"]) if a["status"] != "safe" or inh else 0.5 * a["drop"]
        a["risk"] = round(a["criticality"] * loss + (0.15 * a["criticality"] if inh else 0.0), 4)

    bc, bc_src = betweenness_people(g)
    bmx = max(bc.values()) if bc else 1.0
    links = team_links(g, removed) if removed else []

    people = {r["id"]: r for r in g.q("MATCH (p:Person) RETURN p.id AS id, p.name AS name, p.team AS team, "
                                       "p.active AS active, p.role AS role")}
    brokers = sorted(({"id": pid, "name": people.get(pid, {}).get("name", pid),
                       "team": people.get(pid, {}).get("team"),
                       "betweenness": round(v / (bmx or 1.0), 3), "removed": pid in removed}
                      for pid, v in bc.items()), key=lambda x: -x["betweenness"])[:8]

    # Knowledge health: criticality-weighted coverage, before and after.
    wsum = sum(a["criticality"] + 0.05 for a in table) or 1.0
    health_before = sum((a["criticality"] + 0.05) * a["before"] for a in table) / wsum
    health_after = sum((a["criticality"] + 0.05) * a["after"] for a in table) / wsum

    by_status: dict[str, int] = {}
    for a in table:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1

    table.sort(key=lambda a: (-a["risk"], -a["criticality"]))
    return {
        "removed": [{"id": p, "name": people.get(p, {}).get("name", p), "team": people.get(p, {}).get("team")}
                    for p in removed],
        "assets": table,
        "summary": {
            "orphaned": by_status.get("orphaned", 0), "fragile": by_status.get("fragile", 0),
            "gap": by_status.get("gap", 0), "safe": by_status.get("safe", 0),
            "inherited": len(inherited),
            "health_before": round(health_before, 3), "health_after": round(health_after, 3),
            "severed_links": [l for l in links if l["severed"]],
        },
        "team_links": links[:10],
        "brokers": brokers,
        "algorithms": {"pagerank": pr_src, "betweenness": bc_src},
    }


def who_covers(g: Graph, absent: list[str], asset_ids: list[str]) -> list[dict]:
    """'If A and B are on leave, who covers X?'"""
    out = []
    table = {a["id"]: a for a in coverage_table(g, absent)}
    for aid in asset_ids:
        rows = g.q("""
            MATCH (p:Person)-[k:KNOWS]->(a:Asset {id: $aid})
            WHERE p.active = true AND k.score >= 0.1
            RETURN p.id AS id, p.name AS name, p.team AS team, k.score AS score, k.n AS n,
                   p.id IN $absent AS absent
            ORDER BY score DESC LIMIT 8
        """, {"aid": aid, "absent": absent})
        a = table.get(aid, {"name": aid, "before": 0, "after": 0, "status": "gap"})
        cover = [r for r in rows if not r["absent"]]
        out.append({
            "asset": aid, "name": a["name"], "coverage_before": a["before"], "coverage_after": a["after"],
            "status": a["status"],
            "covers": [{"id": r["id"], "name": r["name"], "team": r["team"], "score": round(r["score"], 3),
                        "evidence": r["n"]} for r in cover[:4]],
            "absent_experts": [{"id": r["id"], "name": r["name"], "score": round(r["score"], 3)}
                               for r in rows if r["absent"]],
            "uncovered": not cover or cover[0]["score"] < C.KNOWS_THRESHOLD,
        })
    return out
