"""Graph algorithms as engine tools.

PageRank and betweenness run inside FalkorDB (algo.pageRank / algo.betweenness).
If the server build doesn't expose a procedure, we fall back to networkx on the
same subgraph pulled out with Cypher, and report which path was used.
"""
from __future__ import annotations

from ..db import Graph


def pagerank_assets(g: Graph) -> tuple[dict[str, float], str]:
    """Criticality of each asset: things many other things depend on rank high."""
    try:
        rows = g.q("""CALL algo.pageRank('Asset', 'DEPENDS_ON') YIELD node, score
                      RETURN node.id AS id, score""")
        if rows:
            return {r["id"]: float(r["score"]) for r in rows}, "falkordb:algo.pageRank"
    except Exception:
        pass
    import networkx as nx
    G = nx.DiGraph()
    for r in g.q("MATCH (a:Asset) RETURN a.id AS id"):
        G.add_node(r["id"])
    for r in g.q("MATCH (a:Asset)-[:DEPENDS_ON]->(b:Asset) RETURN a.id AS a, b.id AS b"):
        G.add_edge(r["a"], r["b"])
    if not G.number_of_nodes():
        return {}, "networkx:pagerank"
    return nx.pagerank(G), "networkx:pagerank"


def betweenness_people(g: Graph) -> tuple[dict[str, float], str]:
    """Brokers: people who sit on the shortest paths between everyone else (COLLAB graph)."""
    try:
        rows = g.q("""CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['COLLAB']})
                      YIELD node, score
                      WHERE node.active = true
                      RETURN node.id AS id, score""")
        if rows:
            return {r["id"]: float(r["score"]) for r in rows}, "falkordb:algo.betweenness"
    except Exception:
        pass
    import networkx as nx
    G = nx.Graph()
    for r in g.q("MATCH (p:Person) WHERE p.active = true RETURN p.id AS id"):
        G.add_node(r["id"])
    for r in g.q("""MATCH (x:Person)-[c:COLLAB]->(y:Person) WHERE x.active = true AND y.active = true
                    RETURN x.id AS a, y.id AS b, c.w AS w"""):
        G.add_edge(r["a"], r["b"], weight=r["w"])
    if not G.number_of_nodes():
        return {}, "networkx:betweenness"
    return nx.betweenness_centrality(G), "networkx:betweenness"
