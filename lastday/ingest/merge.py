"""Combine GitHub, Slack and Notion into one normalized dataset (the format in loader.py).

1. Areas (assets). If a GitHub repo is connected, its modules and their co-change dependencies are the areas.
   Without one, every Slack channel with threads in it becomes an area ("#payments").
2. People. One person has a GitHub login, a Slack account and a Notion account. Accounts are joined by, in order:
     manual   a link set by hand in the Connections panel (always wins)
     email    the same email address (Slack and Notion give emails; GitHub only if public)
     handle   a Slack handle equal to a GitHub login
     name     the same full name of two words or more
   Automatic rules never put two accounts from the same tool into one person (two "John Smith"s stay two people).
   An account that matches nobody stays a person of its own.
3. Evidence. GitHub PRs / reviews / answers, Slack thread answers, Notion docs and decisions, rewritten to the
   merged person ids. Messages and pages are linked to areas by name and alias, or by a channel -> area map.
4. Teams. Community detection over everything (github.infer_teams), named after each group's main area.
"""
from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter, defaultdict

from . import github, notion, slack

DAY = 86400.0
SOURCES = ("github", "slack", "notion")
# Words too common in chat and docs to say which area a message is about.
GENERIC = {"root", "(root)", "src", "lib", "libs", "app", "apps", "main", "misc", "test", "tests", "spec", "specs",
           "util", "utils", "common", "scripts", "tools", "build", "github", ".github", "config", "configs",
           "example", "examples", "assets", "public", "static", "vendor", "deps", "bin", "data", "general",
           "random", "help", "team", "the", "and", "for", "with"}
# Channels that are chatter, not an area of knowledge (used only when channels become areas).
NOT_AREAS = {"general", "random", "social", "watercooler", "announcements", "announce", "off-topic", "offtopic",
             "introductions", "intros", "fun", "memes", "kudos", "celebrations"}
CHANNEL_FILLER = {"team", "help", "eng", "engineering", "dev", "devs", "ask", "discuss", "talk", "chat", "internal",
                  "public", "support", "questions", "alerts", "notifications"}


class AssetMatcher:
    """Which areas is a piece of text about? Whole-word matches of each area's id, name and aliases."""

    def __init__(self, assets: list[dict]):
        self.rx = []
        for a in assets:
            terms: set[str] = set()
            for t in (a["id"], a.get("name") or ""):
                t = t.lower().strip().lstrip("#")
                last = t.split("/")[-1]
                for v in (t, last, last.replace("_", " "), last.replace("-", " ")):
                    if len(v) >= 4 and v not in GENERIC:
                        terms.add(v)
            for al in a.get("aliases") or []:
                al = al.lower().strip().lstrip("#")
                if len(al) >= 3 and al not in GENERIC:
                    terms.add(al)
            if terms:
                alt = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
                self.rx.append((a["id"], re.compile(rf"(?<![a-z0-9])(?:{alt})(?![a-z0-9])")))

    def __call__(self, text: str, limit: int = 3) -> list[str]:
        low = (text or "").lower()
        hits = []
        for aid, rx in self.rx:
            found = rx.findall(low)
            if found:
                hits.append((max(len(x) for x in found), len(found), aid))
        hits.sort(reverse=True)
        return [aid for *_, aid in hits[:limit]]


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def norm_handle(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm_name(s)).strip("-") or "person"


# --------------------------------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------------------------------

def github_identities(base: dict | None) -> list[dict]:
    if not base:
        return []
    return [{"key": "github:" + p["id"], "source": "github", "id": p["id"], "name": p.get("name") or p["id"],
             "handles": [p["id"].lower()], "email": (p.get("email") or "").lower(), "deleted": False,
             "avatar": p.get("avatar") or "", "title": p.get("role") or ""} for p in base.get("people", [])]


def match_people(accounts: list[dict], links: dict[str, str] | None = None,
                 separate: list[str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Union accounts into people. Returns (account key -> group root key, account key -> how it was matched)."""
    links, sep = links or {}, set(separate or [])
    keys = [a["key"] for a in accounts]
    by_key = {a["key"]: a for a in accounts}
    parent = {k: k for k in keys}
    srcs = {k: {by_key[k]["source"]} for k in keys}
    how: dict[str, str] = {}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b, why, force=False):
        """Attach account b's group to account a's group; `why` is recorded for b."""
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if not force and srcs[ra] & srcs[rb]:
            return  # automatic rules never merge two accounts from the same tool
        if rb.startswith("github:") and not ra.startswith("github:"):
            ra, rb = rb, ra  # keep a GitHub account as the root, so the person id stays the login
        parent[rb] = ra
        srcs[ra] |= srcs[rb]
        how.setdefault(b, why)

    for a, b in links.items():
        if a in parent and b in parent and a != b:
            union(b, a, "manual", force=True)

    auto = [a for a in accounts if a["key"] not in sep]
    groups: dict[str, list[str]] = defaultdict(list)
    for a in auto:
        if a.get("email"):
            groups[a["email"]].append(a["key"])
    for ks in groups.values():
        for k in ks[1:]:
            union(ks[0], k, "email")

    logins = {norm_handle(a["id"]): a["key"] for a in accounts if a["source"] == "github" and a["key"] not in sep}
    for a in auto:
        if a["source"] == "slack":
            for h in a.get("handles") or []:
                g = logins.get(norm_handle(h))
                if g:
                    union(g, a["key"], "handle")
                    break

    names: dict[str, list[str]] = defaultdict(list)
    for a in auto:
        n = norm_name(a.get("name") or "")
        if len(n.split()) >= 2:
            names[n].append(a["key"])
    for ks in names.values():
        for k in ks[1:]:
            union(ks[0], k, "name")

    return {k: find(k) for k in keys}, how


# --------------------------------------------------------------------------------------------------
# Areas when there is no GitHub repo: Slack channels
# --------------------------------------------------------------------------------------------------

def channel_assets(slack_raw: dict) -> list[dict]:
    rows = []
    for ch in slack_raw.get("channels", []):
        name = ch["name"]
        threads = sum(1 for m in ch.get("messages", []) if m.get("thread_ts") and m.get("thread_ts") == m.get("ts"))
        if name.lower() in NOT_AREAS or not threads:
            continue
        words = [w for w in re.split(r"[-_]", name.lower()) if len(w) >= 4 and w not in CHANNEL_FILLER]
        rows.append({"id": "ch-" + name, "name": "#" + name, "kind": "system", "team": "",
                     "aliases": sorted({name, name.replace("-", " "), *words}),
                     "threads": threads, "description": ch.get("purpose") or f"Slack channel #{name}"})
    top = max((r["threads"] for r in rows), default=1) or 1
    for r in rows:
        r["business"] = round(min(1.0, r.pop("threads") / top), 3)
    return rows


# --------------------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------------------

def build(name: str = "", base: dict | None = None, slack_raw: dict | None = None, notion_raw: dict | None = None,
          channel_map: dict[str, list[str]] | None = None, links: dict[str, str] | None = None,
          separate: list[str] | None = None, active_days: int = 180) -> dict:
    """Returns {"dataset": normalized dataset, "people": [...], "accounts": [...], "channels": [...],
    "notion": {...}, "assets": [...], "counts": {...}}."""
    if not base and not slack_raw:
        raise ValueError("Connect a GitHub repo or Slack first: Notion pages need areas (systems, modules or "
                         "channels) to be about.")
    channel_map = channel_map or {}

    # ---- areas ----
    if base:
        assets = [dict(a) for a in base.get("assets", [])]
        depends = [list(d) for d in base.get("depends", [])]
    else:
        assets = channel_assets(slack_raw)
        depends = []
        channel_map = {**{a["id"][3:]: [a["id"]] for a in assets}, **channel_map}
    known = {a["id"] for a in assets}
    channel_map = {ch: [a for a in ids if a in known] for ch, ids in channel_map.items()}
    match = AssetMatcher(assets)

    # ---- evidence from each source (authors still as account keys) ----
    evidence = []
    for e in (base or {}).get("evidence", []):
        evidence.append({**e, "author": "github:" + e["author"],
                         "counterpart": ("github:" + e["counterpart"]) if e.get("counterpart") else None})
    channel_stats, notion_stats = [], {}
    if slack_raw:
        ev, channel_stats = slack.evidence(slack_raw, match, channel_map)
        evidence += ev
    if notion_raw:
        ev, notion_stats = notion.evidence(notion_raw, match)
        evidence += ev

    # ---- people ----
    accounts = (github_identities(base) + (slack.identities(slack_raw) if slack_raw else [])
                + (notion.identities(notion_raw) if notion_raw else []))
    by_key = {a["key"]: a for a in accounts}
    root, how = match_people(accounts, links, separate)
    members: dict[str, list[dict]] = defaultdict(list)
    for a in accounts:
        members[root[a["key"]]].append(a)

    pid: dict[str, str] = {}
    taken = {a["id"] for a in accounts if a["source"] == "github"}
    for r, accs in members.items():
        gh = [a for a in accs if a["source"] == "github"]
        if gh:
            pid[r] = gh[0]["id"]
            continue
        best = max(accs, key=lambda a: (len(norm_name(a["name"]).split()), a["source"] == "slack"))
        cand, i = slug(best["name"]), 2
        while cand in taken:
            cand, i = f"{slug(best['name'])}-{i}", i + 1
        taken.add(cand)
        pid[r] = cand
    person_of = {k: pid[r] for k, r in root.items()}

    for e in evidence:
        e["author"] = person_of.get(e["author"])
        e["counterpart"] = person_of.get(e["counterpart"]) if e.get("counterpart") else None
    evidence = [e for e in evidence if e["author"]]

    now = (base or {}).get("meta", {}).get("now") or 0.0
    latest = max([e["ts"] for e in evidence] or [time.time()])
    now = min(time.time(), max(now, latest + DAY))

    count = Counter(e["author"] for e in evidence)
    per_source: dict[str, Counter] = defaultdict(Counter)
    last: dict[str, float] = defaultdict(float)
    persistent = set()
    for e in evidence:
        src = "slack" if e["id"].startswith("sl_") else "notion" if e["id"].startswith("no_") else "github"
        per_source[e["author"]][src] += 1
        last[e["author"]] = max(last[e["author"]], e["ts"])
        if e["kind"] in ("doc", "decision"):
            persistent.add(e["author"])
    base_ids = {p["id"] for p in (base or {}).get("people", [])}
    base_people = {p["id"]: p for p in (base or {}).get("people", [])}

    people, rows, keep = [], [], set()
    for r, accs in members.items():
        p = pid[r]
        kept = p in base_ids or count[p] >= 2 or p in persistent
        slack_accs = [a for a in accs if a["source"] == "slack"]
        left_slack = bool(slack_accs) and all(a["deleted"] for a in slack_accs)
        active = (not left_slack) and last[p] >= now - active_days * DAY
        named = sorted(accs, key=lambda a: (len(norm_name(a["name"]).split()), a["source"] == "github"), reverse=True)
        display = named[0]["name"] if named else p
        gh = next((a for a in accs if a["source"] == "github"), None)
        sl = slack_accs[0] if slack_accs else None
        if kept and p not in keep:
            keep.add(p)
            bp = base_people.get(p, {})
            people.append({"id": p, "name": display, "team": "",
                           "handle": ("@" + gh["id"]) if gh else ("@" + sl["handles"][0]) if sl and sl["handles"]
                           else "@" + p,
                           "active": active, "role": bp.get("role") or (sl or {}).get("title") or "",
                           "avatar": (gh or {}).get("avatar") or (sl or {}).get("avatar") or ""})
        rows.append({"id": p, "key": (gh or accs[0])["key"], "name": display, "kept": kept, "active": active,
                     "left_slack": left_slack,
                     "evidence": {s: per_source[p][s] for s in SOURCES},
                     "accounts": [{"key": a["key"], "source": a["source"],
                                   "label": a["id"] if a["source"] == "github" else
                                   (("@" + a["handles"][0]) if a.get("handles") else a["name"]),
                                   "name": a["name"], "email": a.get("email") or "",
                                   "matched_by": how.get(a["key"], "")} for a in accs]})

    evidence = [e for e in evidence if e["author"] in keep]
    for e in evidence:
        if e.get("counterpart") not in keep:
            e["counterpart"] = None

    # Likely matches the rules didn't make (shown as suggestions in the Connections panel, never applied
    # automatically): a GitHub login that is the start of a Slack handle ("carol" / "carol.m"), or that equals
    # someone's first name.
    gh_people = {norm_handle(a["id"]): person_of[a["key"]] for a in accounts if a["source"] == "github"}
    for r in rows:
        if any(x["source"] == "github" for x in r["accounts"]):
            continue
        hints = []
        for x in r["accounts"]:
            acc = by_key[x["key"]]
            first = (norm_name(acc["name"]).split() or [""])[0]
            for h in [norm_handle(h) for h in acc.get("handles") or []] + [first]:
                for login, p in gh_people.items():
                    if len(login) >= 3 and h and (h == login or (len(h) > len(login) and h.startswith(login))):
                        hints.append(p)
        r["suggest"] = list(dict.fromkeys(hints))[:3]

    # ---- teams ----
    names = {a["id"]: a["name"] for a in assets}
    teams, team_of = github.infer_teams(evidence, sorted(keep))
    for t in teams:
        if t["id"].startswith("team:"):
            aid = re.sub(r"-\d+$", "", t["id"][5:]) if t["id"][5:] not in names else t["id"][5:]
            t["name"] = f"{names.get(aid, aid)} crew"
    for p in people:
        p["team"] = team_of.get(p["id"], "")

    sources = [s for s, on in (("github", base), ("slack", slack_raw), ("notion", notion_raw)) if on]
    meta = {"name": name or (base or {}).get("meta", {}).get("name") or "Connected company", "source": "connected",
            "now": now, "sources": ",".join(sources)}
    if base and base.get("meta", {}).get("repo"):
        meta["repo"] = base["meta"]["repo"]
    rows.sort(key=lambda r: (not r["kept"], -sum(r["evidence"].values()), r["name"].lower()))
    kinds = Counter(e["kind"] for e in evidence)
    return {
        "dataset": {"meta": meta, "teams": teams, "people": people, "assets": assets, "depends": depends,
                    "evidence": evidence},
        "people": rows,
        "channels": channel_stats,
        "notion": notion_stats,
        "assets": [{"id": a["id"], "name": a["name"]} for a in assets],
        "counts": {"people": len(people), "assets": len(assets), "evidence": len(evidence),
                   "by_kind": dict(kinds), "accounts": len(accounts),
                   "merged": sum(1 for r in rows if len(r["accounts"]) > 1), "sources": sources},
    }
