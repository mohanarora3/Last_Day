"""Turn raw evidence into knowledge edges, entirely inside FalkorDB.

KNOWS.score for (person, asset) = 1 - exp(-raw / SATURATION), where
raw = sum over that person's evidence about the asset of  weight(kind) * share * exp(-age / TAU).
Recent, deep evidence (decisions, incidents, reviews) counts more than an old typo fix.
"""
from __future__ import annotations

import time

from .. import config as C
from ..db import Graph


def graph_now(g: Graph) -> float:
    row = g.q1("MATCH (m:Meta {id: 'meta'}) RETURN m.now AS now")
    return float(row["now"]) if row and row.get("now") else time.time()


def recompute(g: Graph) -> dict:
    now = graph_now(g)
    g.q("MATCH (:Person)-[k:KNOWS]->(:Asset) DELETE k")
    g.q("""
        MATCH (p:Person)-[:AUTHORED]->(e:Evidence)-[r:ABOUT]->(a:Asset)
        WITH p, a,
             sum(e.weight * r.share * exp(-1.0 * ($now - e.ts) / $tau)) AS raw,
             count(e) AS n, max(e.ts) AS last
        CREATE (p)-[:KNOWS {raw: raw, n: n, last: last}]->(a)
    """, {"now": now, "tau": C.TAU_PERSON})
    raws = sorted(r["raw"] for r in g.q("MATCH (:Person)-[k:KNOWS]->(:Asset) RETURN k.raw AS raw"))
    p90 = raws[int(0.9 * (len(raws) - 1))] if raws else 1.0
    sat = max(C.MIN_SATURATION, C.SAT_FACTOR * p90)
    g.q("""
        MATCH (:Person)-[k:KNOWS]->(:Asset)
        WITH k, 1.0 - exp(-1.0 * k.raw / $sat) AS s
        SET k.score = CASE WHEN s > $maxs THEN $maxs ELSE s END
    """, {"sat": sat, "maxs": C.MAX_SCORE})
    g.q("MATCH (m:Meta {id: 'meta'}) SET m.saturation = $sat", {"sat": sat})

    # Written knowledge (docs, decisions, captured interview answers) that outlives its author.
    g.q("""
        MATCH (a:Asset)
        OPTIONAL MATCH (e:Evidence)-[r:ABOUT]->(a) WHERE e.persistent = true
        WITH a, sum(e.weight * r.share * exp(-1.0 * ($now - e.ts) / $tau)) AS d
        SET a.doc_cov = $cap * (1.0 - exp(-1.0 * d / $dsat))
    """, {"now": now, "tau": C.TAU_DOC, "cap": C.DOC_COVERAGE_CAP, "dsat": C.DOC_SATURATION})

    # Collaboration graph between people: shared knowledge + helping each other.
    g.q("MATCH (:Person)-[c:COLLAB]->(:Person) DELETE c")
    g.q("""
        MATCH (p1:Person)-[k1:KNOWS]->(a:Asset)<-[k2:KNOWS]-(p2:Person)
        WHERE p1.id < p2.id AND k1.score >= $t AND k2.score >= $t
        WITH p1, p2, sum(k1.score * k2.score) AS w
        CREATE (p1)-[:COLLAB {w: w}]->(p2)
    """, {"t": C.KNOWS_THRESHOLD})
    g.q("""
        MATCH (x:Person)-[h:HELPED]-(y:Person) WHERE x.id < y.id
        WITH x, y, sum(h.n) AS n
        MERGE (x)-[c:COLLAB]->(y)
        ON CREATE SET c.w = 0.0
        SET c.w = c.w + 0.12 * n
    """)
    knows = g.q1("MATCH (:Person)-[k:KNOWS]->(:Asset) RETURN count(k) AS n")["n"]
    collab = g.q1("MATCH (:Person)-[c:COLLAB]->(:Person) RETURN count(c) AS n")["n"]
    return {"now": now, "saturation": round(sat, 3), "knows": knows, "collab": collab}


def saturation(g: Graph) -> float:
    row = g.q1("MATCH (m:Meta {id: 'meta'}) RETURN m.saturation AS s")
    return float(row["s"]) if row and row.get("s") else 3.0
