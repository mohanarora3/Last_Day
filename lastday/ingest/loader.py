"""Load a normalized dataset into a FalkorDB graph.

Normalized dataset (produced by every connector: demo company, GitHub, ...):

{
  "meta":   {"name": str, "source": str, "now": unix_ts, ...},
  "teams":  [{"id", "name"}],
  "people": [{"id", "name", "team", "handle", "active": bool, "role"}],
  "assets": [{"id", "name", "kind": system|module|customer|process, "team", "aliases": [], "business": 0..1,
              "description"}],
  "depends": [[from_asset_id, to_asset_id], ...],          # from DEPENDS_ON to
  "evidence": [{"id", "kind", "author", "assets": [[asset_id, share], ...] | [asset_id, ...],
                "title", "text", "url", "ts", "channel", "counterpart"}],
}

Graph model
  (:Person)-[:MEMBER_OF]->(:Team)-[:OWNS]->(:Asset)
  (:Person)-[:AUTHORED]->(:Evidence)-[:ABOUT {share}]->(:Asset)
  (:Person)-[:HELPED {n}]->(:Person)              # answered / reviewed someone
  (:Asset)-[:DEPENDS_ON]->(:Asset)
  derived by the engine: (:Person)-[:KNOWS {score, raw, n, last}]->(:Asset), (:Person)-[:COLLAB {w}]->(:Person)
"""
from __future__ import annotations

from ..config import EVIDENCE_WEIGHTS, PERSISTENT_KINDS
from ..db import Graph


def _chunks(items, n=400):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def create_indexes(g: Graph) -> None:
    for label, prop in [("Person", "id"), ("Team", "id"), ("Asset", "id"), ("Evidence", "id")]:
        try:
            g.q(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception:
            pass  # already exists


def load_dataset(g: Graph, data: dict, reset: bool = True) -> dict:
    if reset:
        g.delete()
    create_indexes(g)
    meta = data.get("meta", {})
    g.q("MERGE (m:Meta {id: 'meta'}) SET m += $meta", {"meta": {k: v for k, v in meta.items()
                                                                 if isinstance(v, (str, int, float, bool))}})

    teams = [{"id": t["id"], "name": t.get("name", t["id"])} for t in data.get("teams", [])]
    for ch in _chunks(teams):
        g.q("UNWIND $rows AS r MERGE (t:Team {id: r.id}) SET t.name = r.name", {"rows": ch})

    people = [{
        "id": p["id"], "name": p.get("name", p["id"]), "team": p.get("team") or "",
        "handle": p.get("handle") or p["id"], "active": bool(p.get("active", True)),
        "role": p.get("role") or "", "avatar": p.get("avatar") or "",
    } for p in data.get("people", [])]
    for ch in _chunks(people):
        g.q("""UNWIND $rows AS r
               MERGE (p:Person {id: r.id})
               SET p.name = r.name, p.handle = r.handle, p.active = r.active, p.role = r.role,
                   p.team = r.team, p.avatar = r.avatar
               WITH p, r WHERE r.team <> ''
               MERGE (t:Team {id: r.team})
               MERGE (p)-[:MEMBER_OF]->(t)""", {"rows": ch})

    assets = [{
        "id": a["id"], "name": a.get("name", a["id"]), "kind": a.get("kind", "system"),
        "team": a.get("team") or "", "aliases": a.get("aliases") or [],
        "business": float(a.get("business", 0.0)), "description": a.get("description") or "",
    } for a in data.get("assets", [])]
    for ch in _chunks(assets):
        g.q("""UNWIND $rows AS r
               MERGE (a:Asset {id: r.id})
               SET a.name = r.name, a.kind = r.kind, a.aliases = r.aliases, a.business = r.business,
                   a.description = r.description, a.doc_cov = 0.0
               WITH a, r WHERE r.team <> ''
               MERGE (t:Team {id: r.team})
               MERGE (t)-[:OWNS]->(a)""", {"rows": ch})

    deps = [{"a": a, "b": b} for a, b in data.get("depends", [])]
    for ch in _chunks(deps):
        g.q("""UNWIND $rows AS r
               MATCH (x:Asset {id: r.a}), (y:Asset {id: r.b})
               MERGE (x)-[:DEPENDS_ON]->(y)""", {"rows": ch})

    ev_rows, about_rows, helped = [], [], {}
    for e in data.get("evidence", []):
        kind = e["kind"]
        ev_rows.append({
            "id": e["id"], "kind": kind, "author": e.get("author") or "",
            "title": (e.get("title") or "")[:300], "text": (e.get("text") or "")[:2000],
            "url": e.get("url") or "", "ts": float(e.get("ts", 0)), "channel": e.get("channel") or "",
            "weight": float(e.get("weight", EVIDENCE_WEIGHTS.get(kind, 1.0))),
            "persistent": kind in PERSISTENT_KINDS, "counterpart": e.get("counterpart") or "",
        })
        for item in e.get("assets", []):
            aid, share = (item, 1.0) if isinstance(item, str) else (item[0], float(item[1]))
            about_rows.append({"e": e["id"], "a": aid, "share": share})
        cp = e.get("counterpart")
        if cp and e.get("author") and cp != e.get("author"):
            key = (e["author"], cp)
            helped[key] = helped.get(key, 0) + 1

    for ch in _chunks(ev_rows):
        g.q("""UNWIND $rows AS r
               CREATE (e:Evidence {id: r.id, kind: r.kind, title: r.title, text: r.text, url: r.url,
                                   ts: r.ts, channel: r.channel, weight: r.weight, persistent: r.persistent,
                                   author: r.author, counterpart: r.counterpart})
               WITH e, r WHERE r.author <> ''
               MATCH (p:Person {id: r.author})
               CREATE (p)-[:AUTHORED]->(e)""", {"rows": ch})
    for ch in _chunks(about_rows):
        g.q("""UNWIND $rows AS r
               MATCH (e:Evidence {id: r.e}), (a:Asset {id: r.a})
               CREATE (e)-[:ABOUT {share: r.share}]->(a)""", {"rows": ch})
    helped_rows = [{"a": a, "b": b, "n": n} for (a, b), n in helped.items()]
    for ch in _chunks(helped_rows):
        g.q("""UNWIND $rows AS r
               MATCH (x:Person {id: r.a}), (y:Person {id: r.b})
               MERGE (x)-[h:HELPED]->(y) SET h.n = r.n""", {"rows": ch})

    return {"teams": len(teams), "people": len(people), "assets": len(assets), "depends": len(deps),
            "evidence": len(ev_rows), "about": len(about_rows), "helped": len(helped_rows)}
