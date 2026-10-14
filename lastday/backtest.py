"""Backtest: would Last Day have predicted what actually happened when a maintainer left?

1. Take a real repo and a contributor who really stopped contributing.
2. Build the graph using ONLY data from before their departure (the cutoff).
3. Simulate their departure -> predicted orphaned / fragile / safe modules + recommended successors.
4. Look at what actually happened in the year after:
   - time until a PR got its first human response, before vs after, per predicted status
   - share of PRs nobody responded to within 14 days
   - who actually took over each at-risk module: most PRs authored + reviews + comments afterwards
     (successor hit rate = how often that person was in our top-3 recommendation)
"""
from __future__ import annotations

import statistics
import time
from collections import Counter, defaultdict
from typing import Callable

from .db import DB
from .engine.knowledge import recompute
from .engine.simulate import simulate
from .engine.successors import recommend
from .ingest.github import build_dataset, fetch_repo, first_response, is_bot, pr_modules, ts
from .ingest.loader import load_dataset

DAY = 86400


def last_activity(raw: dict, login: str) -> float | None:
    last = None
    for p in raw["prs"]:
        if (p.get("author") or {}).get("login") == login:
            for t in (p.get("mergedAt"), p.get("createdAt")):
                if t:
                    last = max(last or 0, ts(t))
        for r in (p.get("reviews") or {}).get("nodes", []):
            if (r.get("author") or {}).get("login") == login and r.get("submittedAt"):
                last = max(last or 0, ts(r["submittedAt"]))
        for c in (p.get("comments") or {}).get("nodes", []):
            if (c.get("author") or {}).get("login") == login and c.get("createdAt"):
                last = max(last or 0, ts(c["createdAt"]))
    for i in raw["issues"]:
        for c in (i.get("comments") or {}).get("nodes", []):
            if (c.get("author") or {}).get("login") == login and c.get("createdAt"):
                last = max(last or 0, ts(c["createdAt"]))
    return last


def departures(raw: dict, top: int = 15, quiet_days: int = 180) -> list[dict]:
    """Top contributors ranked by activity, with whether they went quiet while the repo kept going."""
    act: Counter = Counter()
    first, last = {}, {}

    def see(login, t):
        if not login or is_bot(login) or not t:
            return
        act[login] += 1
        first[login] = min(first.get(login, t), t)
        last[login] = max(last.get(login, t), t)

    repo_last = 0.0
    for p in raw["prs"]:
        repo_last = max(repo_last, ts(p["createdAt"]))
        see((p.get("author") or {}).get("login"), ts(p.get("mergedAt") or p["createdAt"]))
        for r in (p.get("reviews") or {}).get("nodes", []):
            see((r.get("author") or {}).get("login"), ts(r.get("submittedAt")))
    out = []
    for login, n in act.most_common(top):
        gone = repo_last - last[login] > quiet_days * DAY
        out.append({"login": login, "activity": n, "first": first[login], "last": last[login],
                    "went_quiet": gone, "quiet_days": max(0, round((repo_last - last[login]) / DAY))})
    return out


def _median_h(xs):
    return round(statistics.median(xs) / 3600, 1) if xs else None


def run(owner: str, repo: str, leaver: str, cutoff: float | None = None, before_days: int = 730,
        after_days: int = 365, refresh: bool = False, db: DB | None = None,
        progress: Callable[[str], None] | None = None) -> dict:
    progress = progress or (lambda m: None)
    db = db or DB()
    # With a known cutoff we only need ~before_days of history before it; otherwise fetch everything
    # (bounded by the connector's max_items) so we can find when the person went quiet.
    since = (cutoff - (before_days + 30) * DAY) if cutoff else 0.0
    raw = fetch_repo(owner, repo, since=since, refresh=refresh, progress=progress)
    if cutoff is None:
        cutoff = last_activity(raw, leaver)
        if not cutoff:
            raise ValueError(f"{leaver} has no activity in the fetched data")
        progress(f"{leaver} was last active on {time.strftime('%Y-%m-%d', time.gmtime(cutoff))}; using that as cutoff")
    window = {**raw, "prs": [p for p in raw["prs"] if ts(p["createdAt"]) >= cutoff - before_days * DAY],
              "issues": [i for i in raw["issues"] if ts(i["createdAt"]) >= cutoff - before_days * DAY]}

    # ---- 1-3: predict using only pre-cutoff data ----
    data = build_dataset(window, now=cutoff, until=cutoff)
    if leaver not in {p["id"] for p in data["people"]}:
        raise ValueError(f"{leaver} not found among contributors before the cutoff")
    for p in data["people"]:
        if p["id"] == leaver:
            p["active"] = True  # they are still there on the cutoff date
    gname = f"bt__{owner}__{repo}__{leaver}".lower().replace("-", "_").replace(".", "_")
    g = db.graph(gname)
    progress("loading pre-departure graph into FalkorDB")
    load_dataset(g, data)
    recompute(g)
    sim = simulate(g, [leaver])
    pred = {a["id"]: a for a in sim["assets"]}
    succ = {}
    for a in sim["assets"]:
        if a["status"] in ("orphaned", "fragile"):
            succ[a["id"]] = [c["id"] for c in recommend(g, a["id"], [leaver], k=3)]

    # ---- 4: what actually happened ----
    progress("measuring what actually happened after the cutoff")
    per_mod = defaultdict(lambda: {"pre": [], "post": [], "pre_n": 0, "post_n": 0, "pre_unanswered": 0,
                                   "post_unanswered": 0, "post_reviewers": Counter()})
    for p in raw["prs"]:
        created = ts(p["createdAt"])
        if (p.get("author") or {}).get("login") == leaver:
            continue
        if cutoff - 365 * DAY <= created < cutoff:
            phase = "pre"
        elif cutoff <= created < cutoff + after_days * DAY:
            phase = "post"
        else:
            continue
        fr = first_response(p)
        author = (p.get("author") or {}).get("login")
        for m in pr_modules(p):
            if m not in pred:
                continue
            d = per_mod[m]
            d[f"{phase}_n"] += 1
            if fr is not None:
                d[phase].append(fr)
            if fr is None or fr > 14 * DAY:
                d[f"{phase}_unanswered"] += 1
            if phase == "post":
                if author and author != leaver and not is_bot(author):
                    d["post_reviewers"][author] += 1  # authoring changes is taking over too
                for r in (p.get("reviews") or {}).get("nodes", []) + (p.get("comments") or {}).get("nodes", []):
                    lg = (r.get("author") or {}).get("login")
                    if lg and lg != author and lg != leaver and not is_bot(lg):
                        d["post_reviewers"][lg] += 1

    rows, groups = [], defaultdict(lambda: {"pre": [], "post": [], "pre_n": 0, "post_n": 0,
                                            "pre_unanswered": 0, "post_unanswered": 0, "modules": 0})
    hits, hit_total = 0, 0
    for m, a in pred.items():
        d = per_mod.get(m)
        if not d or (d["pre_n"] < 3 and d["post_n"] < 3):
            continue
        status = a["status"]
        grp = "at risk" if status in ("orphaned", "fragile") else ("gap" if status == "gap" else "safe")
        G = groups[grp]
        G["modules"] += 1
        for k in ("pre", "post"):
            G[k] += d[k]
            G[f"{k}_n"] += d[f"{k}_n"]
            G[f"{k}_unanswered"] += d[f"{k}_unanswered"]
        actual = [x for x, _ in d["post_reviewers"].most_common(1)]
        hit = None
        if m in succ and actual:
            hit_total += 1
            hit = actual[0] in succ[m]
            hits += int(hit)
        rows.append({
            "module": m, "status": status, "before": a["before"], "after": a["after"],
            "pre_prs": d["pre_n"], "post_prs": d["post_n"],
            "pre_median_h": _median_h(d["pre"]), "post_median_h": _median_h(d["post"]),
            "pre_unanswered": round(d["pre_unanswered"] / d["pre_n"], 3) if d["pre_n"] else None,
            "post_unanswered": round(d["post_unanswered"] / d["post_n"], 3) if d["post_n"] else None,
            "predicted_successors": succ.get(m, []), "actual_top_reviewer": actual[0] if actual else None,
            "successor_hit": hit,
        })
    summary = {}
    for grp, G in groups.items():
        pre_m, post_m = _median_h(G["pre"]), _median_h(G["post"])
        summary[grp] = {
            "modules": G["modules"], "pre_prs": G["pre_n"], "post_prs": G["post_n"],
            "pre_median_h": pre_m, "post_median_h": post_m,
            "slowdown": round(post_m / pre_m, 2) if pre_m and post_m else None,
            "pre_unanswered": round(G["pre_unanswered"] / G["pre_n"], 3) if G["pre_n"] else None,
            "post_unanswered": round(G["post_unanswered"] / G["post_n"], 3) if G["post_n"] else None,
        }
    rows.sort(key=lambda r: (r["status"] not in ("orphaned", "fragile"), -(r["before"] - r["after"])))
    return {
        "repo": f"{owner}/{repo}", "leaver": leaver, "cutoff": cutoff, "graph": gname,
        "groups": summary, "modules": rows,
        "successor_hit_rate": round(hits / hit_total, 3) if hit_total else None,
        "successor_checked": hit_total, "prediction": sim["summary"],
    }
