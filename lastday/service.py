"""High-level operations shared by the web API, CLI scripts and the MCP server."""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import connections as C
from .db import DB, Graph, get_db
from .engine.knowledge import recompute
from .engine.simulate import simulate, who_covers
from .engine.successors import recommend
from .ingest import demo_company, github, merge, notion, slack
from .ingest.loader import load_dataset
from .agent import ask as ask_mod
from .agent import handover as handover_mod
from .agent import interview as interview_mod
from . import backtest as backtest_mod

DEMO = "demo"


def graph(name: str) -> Graph:
    return get_db().graph(name)


def seed_demo() -> dict:
    g = graph(DEMO)
    stats = load_dataset(g, demo_company.build())
    stats.update(recompute(g))
    ask_mod.ensure_fulltext(g)
    return stats


def ingest_github(repo: str, since_days: int = 730, refresh: bool = False, progress=None) -> dict:
    owner, name = repo.strip().removeprefix("https://github.com/").strip("/").split("/")[:2]
    raw = github.fetch_repo(owner, name, since=time.time() - since_days * 86400, refresh=refresh, progress=progress)
    data = github.build_dataset(raw)
    gname = f"gh__{owner}__{name}".lower().replace("-", "_").replace(".", "_")
    g = graph(gname)
    if progress:
        progress("writing graph to FalkorDB")
    stats = load_dataset(g, data)
    stats.update(recompute(g))
    ask_mod.ensure_fulltext(g)
    return {"graph": gname, **stats}


def overview(g: Graph, max_people: int = 70) -> dict:
    meta = g.q1("MATCH (m:Meta {id: 'meta'}) RETURN m.name AS name, m.source AS source, m.now AS now, "
                "m.sources AS sources, m.repo AS repo") or {}
    people = g.q("""MATCH (p:Person)
                    OPTIONAL MATCH (p)-[k:KNOWS]->(:Asset)
                    RETURN p.id AS id, p.name AS name, p.team AS team, p.active AS active, p.role AS role,
                           p.avatar AS avatar, sum(k.score) AS knowledge, count(k) AS areas
                    ORDER BY knowledge DESC""")
    people = people[:max_people]
    keep = {p["id"] for p in people}
    assets = g.q("""MATCH (a:Asset) OPTIONAL MATCH (t:Team)-[:OWNS]->(a)
                    RETURN a.id AS id, a.name AS name, a.kind AS kind, t.id AS team, a.business AS business,
                           a.doc_cov AS doc_cov, a.description AS description""")
    knows = [k for k in g.q("""MATCH (p:Person)-[k:KNOWS]->(a:Asset) WHERE k.score >= 0.12
                               RETURN p.id AS source, a.id AS target, k.score AS score, k.n AS n""")
             if k["source"] in keep]
    deps = g.q("MATCH (a:Asset)-[:DEPENDS_ON]->(b:Asset) RETURN a.id AS source, b.id AS target")
    teams = g.q("MATCH (t:Team) RETURN t.id AS id, t.name AS name ORDER BY t.id")
    counts = g.q("MATCH (e:Evidence) RETURN e.kind AS kind, count(e) AS n ORDER BY n DESC")
    return {"meta": meta, "people": people, "assets": assets, "knows": knows, "depends": deps, "teams": teams,
            "evidence_counts": counts}


def evidence(g: Graph, person: str | None, asset: str | None, limit: int = 25) -> list[dict]:
    return g.q("""MATCH (p:Person)-[:AUTHORED]->(e:Evidence)-[:ABOUT]->(a:Asset)
                  WHERE ($p = '' OR p.id = $p) AND ($a = '' OR a.id = $a)
                  RETURN e.id AS id, e.kind AS kind, e.title AS title, e.text AS text, e.url AS url, e.ts AS ts,
                         e.channel AS channel, p.name AS author, a.name AS asset
                  ORDER BY e.weight DESC, e.ts DESC LIMIT $limit""",
               {"p": person or "", "a": asset or "", "limit": limit})


def knowers(g: Graph, asset: str, limit: int = 6) -> list[dict]:
    return g.q("""MATCH (p:Person)-[k:KNOWS]->(a:Asset {id: $a}) WHERE k.score >= 0.05
                  OPTIONAL MATCH (p)-[:AUTHORED]->(e:Evidence)-[:ABOUT]->(a)
                  WITH p, k, e ORDER BY e.weight DESC, e.ts DESC
                  WITH p, k, collect(e.kind + ': ' + e.title)[0] AS example
                  RETURN p.id AS id, p.name AS name, p.active AS active, k.score AS score, k.n AS n, example
                  ORDER BY score DESC LIMIT $limit""", {"a": asset, "limit": limit})


def path_between(g: Graph, person: str, asset: str) -> list[dict]:
    """The explainable path: person -> evidence -> asset, and person -> asset -> dependents."""
    return g.q("""MATCH (p:Person {id: $p})-[:AUTHORED]->(e:Evidence)-[:ABOUT]->(a:Asset {id: $a})
                  RETURN e.kind AS kind, count(e) AS n, max(e.ts) AS last ORDER BY n DESC""",
               {"p": person, "a": asset})


# ---------------------------------------------------------------------------------------------
# Background jobs (GitHub ingestion and backtests can take a minute)
# ---------------------------------------------------------------------------------------------
JOBS: dict[str, dict] = {}


def start_job(kind: str, fn, *args, **kwargs) -> str:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "status": "running", "log": [], "result": None, "error": None,
           "started": time.time()}
    JOBS[jid] = job

    def progress(msg):
        job["log"].append(msg)
        job["log"] = job["log"][-30:]

    def run():
        try:
            job["result"] = fn(*args, progress=progress, **kwargs)
            job["status"] = "done"
        except Exception as e:
            traceback.print_exc()
            job["error"] = str(e)
            job["status"] = "error"

    threading.Thread(target=run, daemon=True).start()
    return jid


def run_backtest(repo: str, leaver: str, cutoff: float | None = None, progress=None, **kw) -> dict:
    owner, name = repo.strip().removeprefix("https://github.com/").strip("/").split("/")[:2]
    return backtest_mod.run(owner, name, leaver, cutoff=cutoff, progress=progress, **kw)


def find_departures(repo: str, progress=None) -> list[dict]:
    owner, name = repo.strip().removeprefix("https://github.com/").strip("/").split("/")[:2]
    raw = github.fetch_repo(owner, name, since=0.0, progress=progress)
    return backtest_mod.departures(raw)


# ---------------------------------------------------------------------------------------------
# Connections: Slack + Notion (+ an optional GitHub repo) -> one company graph
# ---------------------------------------------------------------------------------------------

def _repo(repo: str) -> tuple[str, str]:
    parts = repo.strip().removeprefix("https://github.com/").strip("/").split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        raise ValueError(f"'{repo}' is not a GitHub repo. Use owner/repo, e.g. FalkorDB/FalkorDB.")
    return parts[0], parts[1]


def save_connection_settings(name: str | None = None, github_repo: str | None = None,
                             workspace_url: str | None = None) -> dict:
    patch: dict = {}
    if name is not None:
        patch["name"] = name.strip()[:80]
    if github_repo is not None:
        if github_repo.strip():
            _repo(github_repo)
        patch["github_repo"] = github_repo.strip()
    if workspace_url is not None:
        patch["slack"] = {"workspace_url": workspace_url.strip()}
    C.update_settings(**patch)
    return C.public_state()


def connect_slack_export(path: str | Path, move: bool = False) -> dict:
    """Use a Slack export (ZIP or unzipped folder). The upload route moves its temp file; the CLI copies."""
    path = Path(path)
    s = C.load_settings()
    raw = slack.load_export(path, team_name=s.get("name") or "", domain=C.slack_domain(s["slack"]["workspace_url"]))
    C.DIR.mkdir(parents=True, exist_ok=True)
    final = C.path("slack_export.zip")
    if path.is_dir():
        if final.exists():
            final.unlink()
        shutil.make_archive(str(final.with_suffix("")), "zip", path)
    elif path.resolve() != final.resolve():
        (os.replace if move else shutil.copyfile)(path, final)
    os.chmod(final, 0o600)
    C.write_raw("slack", raw)
    C.update_settings(slack={"mode": "export", "synced": time.time(), "summary": slack.summary(raw)})
    return C.public_state()


def sync_slack(progress=None) -> dict:
    s = C.load_settings()
    if s["slack"]["mode"] == "export":
        if not C.path("slack_export.zip").exists():
            raise ValueError("Upload a Slack export first.")
        raw = slack.load_export(C.path("slack_export.zip"), team_name=s.get("name") or "",
                                domain=C.slack_domain(s["slack"]["workspace_url"]))
    else:
        token = C.secret("slack_token")
        if not token:
            raise ValueError("Add a Slack bot token (xoxb-...) or upload an export first.")
        raw = slack.fetch_api(token, progress=progress)
    C.write_raw("slack", raw)
    summary = slack.summary(raw)
    C.update_settings(slack={"mode": s["slack"]["mode"] or "api", "synced": time.time(), "summary": summary})
    return summary


def sync_notion(progress=None) -> dict:
    token = C.secret("notion_token")
    if not token:
        raise ValueError("Add a Notion integration token first.")
    raw = notion.fetch_api(token, progress=progress)
    C.write_raw("notion", raw)
    summary = notion.summary(raw)
    C.update_settings(notion={"synced": time.time(), "summary": summary})
    return summary


def connect_token(source: str, token: str) -> str:
    """Save a token and start syncing in the background. Returns the job id."""
    token = (token or "").strip()
    if source == "slack":
        if not token.startswith("xox"):
            raise ValueError("A Slack bot token starts with xoxb- (find it under OAuth & Permissions in your app).")
        C.set_secret("slack_token", token)
        C.update_settings(slack={"mode": "api"})
        return start_job("slack sync", sync_slack)
    if source == "notion":
        if not re.match(r"^(ntn_|secret_)", token):
            raise ValueError("A Notion integration token starts with ntn_ (or secret_ for older integrations).")
        C.set_secret("notion_token", token)
        return start_job("notion sync", sync_notion)
    raise ValueError(f"unknown source {source}")


def start_sync(source: str) -> str:
    if source == "slack":
        return start_job("slack sync", sync_slack)
    if source == "notion":
        return start_job("notion sync", sync_notion)
    raise ValueError(f"unknown source {source}")


def disconnect(source: str) -> dict:
    if source not in ("slack", "notion"):
        raise ValueError(f"unknown source {source}")
    C.clear(source)
    return C.public_state()


def save_people_links(links: dict | None = None, separate: list | None = None) -> dict:
    """Manual fixes from the 'Check people' table."""
    C.update_settings(links={k: v for k, v in (links or {}).items() if k and v and k != v},
                      separate=sorted({k for k in (separate or []) if k}))
    return C.public_state()


def save_channel_map(channel_map: dict | None) -> dict:
    clean = {}
    for ch, ids in (channel_map or {}).items():
        ids = [ids] if isinstance(ids, str) else list(ids or [])
        ids = [i for i in ids if i]
        if ids:
            clean[ch.lstrip("#")] = ids
    C.update_settings(channel_map=clean)
    return C.public_state()


def company_merge(progress=None) -> dict:
    s = C.load_settings()
    base = None
    if s["github_repo"]:
        owner, name = _repo(s["github_repo"])
        raw = github.fetch_repo(owner, name, since=time.time() - 730 * 86400, progress=progress)
        base = github.build_dataset(raw)
    slack_raw, notion_raw = C.read_raw("slack"), C.read_raw("notion")
    if slack_raw and s["slack"]["workspace_url"]:
        slack_raw["team"]["domain"] = C.slack_domain(s["slack"]["workspace_url"])
    if progress:
        progress("matching people across " + ", ".join(
            x for x, on in (("GitHub", base), ("Slack", slack_raw), ("Notion", notion_raw)) if on))
    return merge.build(s["name"], base, slack_raw, notion_raw, s["channel_map"], s["links"], s["separate"])


def _summary(m: dict) -> dict:
    return {k: m[k] for k in ("people", "channels", "notion", "assets", "counts")}


def preview_company(progress=None) -> dict:
    """Match people and link messages to areas, without writing the graph."""
    return _summary(company_merge(progress))


def company_graph_name(name: str) -> str:
    return "co__" + (re.sub(r"[^a-z0-9]+", "_", (name or "company").lower()).strip("_") or "company")


def build_company(progress=None) -> dict:
    try:
        reachable = get_db().ping()
    except Exception:
        reachable = False
    if not reachable:
        raise RuntimeError("Can't reach FalkorDB. Start it with `docker compose up -d`, then build again.")
    m = company_merge(progress)
    gname = company_graph_name(m["dataset"]["meta"]["name"])
    g = graph(gname)
    if progress:
        progress(f"writing {m['counts']['evidence']} pieces of evidence to FalkorDB")
    stats = load_dataset(g, m["dataset"])
    stats.update(recompute(g))
    ask_mod.ensure_fulltext(g)
    C.update_settings(graph=gname, built=time.time())
    return {"graph": gname, "stats": stats, **_summary(m)}


__all__ = ["graph", "seed_demo", "ingest_github", "overview", "evidence", "simulate", "who_covers", "recommend",
           "interview_mod", "handover_mod", "ask_mod", "start_job", "JOBS", "run_backtest", "find_departures",
           "path_between", "recompute", "DB", "connect_slack_export", "connect_token", "start_sync", "disconnect",
           "save_connection_settings", "save_people_links", "save_channel_map", "preview_company", "build_company"]
