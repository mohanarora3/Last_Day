"""GitHub connector: PRs, reviews, PR discussion, issues and issue answers -> normalized dataset.

Uses the GraphQL API (needs GITHUB_TOKEN, any read-only token works for public repos).
Raw API responses are cached in data/cache so demos and backtests run offline afterwards.

Modules (the "assets" of a repo) are directories: `src/execution_plan`, `docs`, `tests` ...
Module dependencies are approximated by co-change coupling: modules changed in the same PRs again
and again depend on each other (edge goes from the less central to the more central module).
Teams are inferred with community detection on who-works-on-what.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

CACHE = Path(os.getenv("LASTDAY_CACHE", Path(__file__).resolve().parents[2] / "data" / "cache"))
BOTS = {"dependabot", "renovate", "github-actions", "codecov", "netlify", "vercel", "coveralls",
        "sonarcloud", "allcontributors", "pre-commit-ci", "mergify", "stale", "snyk-bot", "imgbot",
        "greenkeeper", "copilot", "claude", "coderabbitai", "sourcery-ai", "gemini-code-assist", "codspeed-hq"}
CONTAINERS = {"src", "lib", "libs", "packages", "pkg", "internal", "app", "apps", "crates", "modules", "plugins",
              "components", "services", "cmd", "source", "core", "python", "js"}

PR_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $name) {
    pullRequests(first: 40, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url createdAt mergedAt closedAt state
        author { login ... on User { name } }
        files(first: 100) { nodes { path } }
        reviews(first: 30) { nodes { author { login } submittedAt state } }
        comments(first: 30) { nodes { author { login } createdAt } }
      }
    }
  }
}"""

ISSUE_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $name) {
    issues(first: 50, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number title url createdAt closedAt
        author { login }
        labels(first: 10) { nodes { name } }
        comments(first: 25) { nodes { author { login ... on User { name } } createdAt body } }
      }
    }
  }
}"""


def ts(iso: str | None) -> float | None:
    if not iso:
        return None
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def is_bot(login: str | None) -> bool:
    if not login:
        return True
    l = login.lower()
    return l.endswith("[bot]") or l.endswith("-bot") or l in BOTS or l == "ghost"


def module_of(path: str) -> str:
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    if parts[0] in CONTAINERS and len(parts) > 2:
        return parts[0] + "/" + parts[1]
    return parts[0]


def _gql(query: str, variables: dict, token: str) -> dict:
    body = json.dumps({"query": query, "variables": variables}).encode()
    for attempt in range(6):
        req = urllib.request.Request("https://api.github.com/graphql", data=body,
                                     headers={"authorization": f"Bearer {token}", "content-type": "application/json",
                                              "user-agent": "last-day-hackathon"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < 5:
                time.sleep(2 + attempt * 3)
                continue
            raise RuntimeError(f"GitHub API error {e.code}: {e.read()[:300]!r}")
        if data.get("errors"):
            msg = json.dumps(data["errors"])[:400]
            if "RATE_LIMITED" in msg and attempt < 5:
                time.sleep(60)
                continue
            if data.get("data") is None:
                raise RuntimeError(f"GitHub GraphQL error: {msg}")
        return data["data"]
    raise RuntimeError("GitHub API kept failing")


def _fetch(kind: str, owner: str, repo: str, since: float, token: str,
           progress: Callable[[str], None] | None = None, max_items: int = 4000) -> list[dict]:
    query, field = (PR_QUERY, "pullRequests") if kind == "prs" else (ISSUE_QUERY, "issues")
    items, cursor = [], None
    while True:
        data = _gql(query, {"owner": owner, "name": repo, "cursor": cursor}, token)
        conn = data["repository"][field]
        nodes = conn["nodes"]
        items.extend(nodes)
        oldest = ts(nodes[-1]["createdAt"]) if nodes else 0
        if progress:
            progress(f"{kind}: {len(items)} fetched (back to {datetime.fromtimestamp(oldest or 0):%Y-%m-%d}), "
                     f"rate limit left {data['rateLimit']['remaining']}")
        if not conn["pageInfo"]["hasNextPage"] or (oldest and oldest < since) or len(items) >= max_items:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return items


def fetch_repo(owner: str, repo: str, since: float, token: str | None = None, refresh: bool = False,
               progress: Callable[[str], None] | None = None) -> dict:
    """Fetch (or load from cache) PRs and issues created after `since`."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{owner}__{repo}.json"
    if path.exists() and not refresh:
        raw = json.loads(path.read_text())
        if raw.get("since", 1e20) <= since + 86400:
            if progress:
                progress(f"loaded {len(raw['prs'])} PRs and {len(raw['issues'])} issues from cache")
            return raw
    token = token or os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not token:
        raise RuntimeError("Set GITHUB_TOKEN (a read-only token is enough) to fetch from GitHub.")
    prs = _fetch("prs", owner, repo, since, token, progress)
    issues = _fetch("issues", owner, repo, since, token, progress)
    raw = {"owner": owner, "repo": repo, "since": since, "fetched": time.time(), "prs": prs, "issues": issues}
    path.write_text(json.dumps(raw))
    return raw


# ----------------------------------------------------------------------------------------------
# Raw GitHub data -> normalized dataset
# ----------------------------------------------------------------------------------------------

def pr_modules(pr: dict) -> Counter:
    return Counter(module_of(f["path"]) for f in (pr.get("files") or {}).get("nodes", []) if f and f.get("path"))


def first_response(pr: dict) -> float | None:
    """Seconds until someone other than the author reviewed or commented."""
    author = (pr.get("author") or {}).get("login")
    created = ts(pr["createdAt"])
    times = []
    for r in (pr.get("reviews") or {}).get("nodes", []):
        lg = (r.get("author") or {}).get("login")
        if lg and lg != author and not is_bot(lg) and r.get("submittedAt"):
            times.append(ts(r["submittedAt"]))
    for c in (pr.get("comments") or {}).get("nodes", []):
        lg = (c.get("author") or {}).get("login")
        if lg and lg != author and not is_bot(lg) and c.get("createdAt"):
            times.append(ts(c["createdAt"]))
    times = [t for t in times if t and t >= created]
    return (min(times) - created) if times else None


def build_dataset(raw: dict, now: float | None = None, until: float | None = None, max_modules: int = 40,
                  active_days: int = 180) -> dict:
    """Normalize. `until` cuts off everything after a date (used by the backtest)."""
    until = until or 1e20
    owner, repo = raw["owner"], raw["repo"]
    prs = [p for p in raw["prs"] if ts(p["createdAt"]) < until]
    issues = [i for i in raw["issues"] if ts(i["createdAt"]) < until]
    if now is None:
        # "now" is the repo's own clock: a dormant repo is judged relative to its last activity.
        latest = max([ts(p["createdAt"]) for p in prs] + [ts(i["createdAt"]) for i in issues] or [time.time()])
        now = min(until, time.time(), latest + 86400)

    # ---- modules ----
    mod_count: Counter = Counter()
    for p in prs:
        for m in pr_modules(p):
            mod_count[m] += 1
    modules = [m for m, _ in mod_count.most_common(max_modules)]
    mset = set(modules)

    evidence, names, last_seen = [], {}, defaultdict(float)
    n = 0

    def add(kind, author, assets, title, url, t, text="", counterpart=None):
        nonlocal n
        if not author or is_bot(author) or not assets or not t or t >= until:
            return
        n += 1
        evidence.append({"id": f"gh{n}", "kind": kind, "author": author, "assets": assets, "title": title,
                         "url": url, "ts": t, "text": text, "counterpart": counterpart})
        last_seen[author] = max(last_seen[author], t)

    cochange: Counter = Counter()
    for p in prs:
        mods = pr_modules(p)
        total = sum(mods.values()) or 1
        shares = [[m, max(0.2, c / total)] for m, c in mods.items() if m in mset]
        if not shares:
            continue
        ms = sorted(m for m, _ in shares)
        for i in range(len(ms)):
            for j in range(i + 1, len(ms)):
                cochange[(ms[i], ms[j])] += 1
        author = (p.get("author") or {}).get("login")
        if author and (p.get("author") or {}).get("name"):
            names[author] = p["author"]["name"]
        if p.get("mergedAt"):
            add("pr", author, shares, f"PR #{p['number']}: {p['title']}", p["url"], ts(p["mergedAt"]))
        seen = set()
        for r in (p.get("reviews") or {}).get("nodes", []):
            lg = (r.get("author") or {}).get("login")
            if lg and lg != author and lg not in seen:
                seen.add(lg)
                add("review", lg, shares, f"Reviewed PR #{p['number']}: {p['title']}", p["url"],
                    ts(r.get("submittedAt")), counterpart=author)
        cseen = set()
        for c in (p.get("comments") or {}).get("nodes", []):
            lg = (c.get("author") or {}).get("login")
            if lg and lg != author and lg not in seen and lg not in cseen:
                cseen.add(lg)
                add("issue_answer", lg, shares, f"Discussed PR #{p['number']}: {p['title']}", p["url"],
                    ts(c.get("createdAt")), counterpart=author)

    # issues -> modules by label / name mention
    mod_terms = {m: {m.split("/")[-1].lower().replace("_", " "), m.split("/")[-1].lower()} for m in modules
                 if m != "(root)"}
    for i in issues:
        text = (i["title"] + " " + " ".join(l["name"] for l in (i.get("labels") or {}).get("nodes", []))).lower()
        hit = [m for m, terms in mod_terms.items() if any(len(t) >= 4 and re.search(rf"\b{re.escape(t)}\b", text)
                                                           for t in terms)]
        if not hit:
            continue
        author = (i.get("author") or {}).get("login")
        answered = set()
        for c in (i.get("comments") or {}).get("nodes", []):
            lg = (c.get("author") or {}).get("login")
            if not lg or lg == author or lg in answered:
                continue
            answered.add(lg)
            if (c.get("author") or {}).get("name"):
                names[lg] = c["author"]["name"]
            body = re.sub(r"\s+", " ", c.get("body") or "")[:600]
            add("issue_answer", lg, [[m, 1.0 / len(hit)] for m in hit], f"Answered issue #{i['number']}: {i['title']}",
                i["url"], ts(c.get("createdAt")), text=body, counterpart=author)

    # ---- people ----
    counts = Counter(e["author"] for e in evidence)
    people_ids = [p for p, c in counts.items() if c >= 2]
    keep = set(people_ids)
    evidence = [e for e in evidence if e["author"] in keep]
    for e in evidence:
        if e.get("counterpart") not in keep:
            e["counterpart"] = None

    # ---- inferred teams (community detection on shared modules) ----
    teams, team_of = infer_teams(evidence, people_ids)

    # ---- dependencies from co-change coupling ----
    depends = []
    for (a, b), c in cochange.items():
        jacc = c / (mod_count[a] + mod_count[b] - c)
        if c >= 4 and jacc >= 0.15:
            lo, hi = (a, b) if mod_count[a] <= mod_count[b] else (b, a)
            depends.append([lo, hi])

    assets = [{"id": m, "name": m, "kind": "module", "team": "", "aliases": [m.split("/")[-1]],
               "business": round(min(1.0, mod_count[m] / (mod_count.most_common(1)[0][1] or 1)), 3),
               "description": f"{mod_count[m]} PRs touched this module"} for m in modules]
    people = [{"id": p, "name": names.get(p) or p, "handle": "@" + p, "team": team_of.get(p, ""),
               "active": last_seen[p] >= now - active_days * 86400, "role": "contributor",
               "avatar": f"https://github.com/{p}.png?size=64"} for p in people_ids]
    return {"meta": {"name": f"{owner}/{repo}", "source": "github", "now": now, "repo": f"{owner}/{repo}",
                     "sources": "prs,reviews,pr-discussion,issues"},
            "teams": teams, "people": people, "assets": assets, "depends": depends, "evidence": evidence}


def infer_teams(evidence: list[dict], people: list[str]) -> tuple[list[dict], dict]:
    import networkx as nx
    w: dict[str, Counter] = defaultdict(Counter)
    for e in evidence:
        for item in e["assets"]:
            m, s = (item, 1.0) if isinstance(item, str) else item
            w[e["author"]][m] += s
    G = nx.Graph()
    G.add_nodes_from(people)
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            shared = sum(min(w[a][m], w[b][m]) for m in w[a].keys() & w[b].keys())
            if shared > 0:
                G.add_edge(a, b, weight=shared)
    try:
        comms = nx.community.louvain_communities(G, weight="weight", seed=7)
    except Exception:
        comms = [set(c) for c in nx.connected_components(G)]
    teams, team_of = [], {}
    for c in sorted(comms, key=len, reverse=True):
        if len(c) < 2:
            for p in c:
                team_of[p] = "occasional"
            continue
        top = Counter()
        for p in c:
            top.update(w[p])
        name = top.most_common(1)[0][0] if top else "misc"
        tid = "team:" + name
        if any(t["id"] == tid for t in teams):
            tid += f"-{len(teams)}"
        teams.append({"id": tid, "name": f"{name} crew"})
        for p in c:
            team_of[p] = tid
    if "occasional" in team_of.values():
        teams.append({"id": "occasional", "name": "Occasional contributors"})
    return teams, team_of
