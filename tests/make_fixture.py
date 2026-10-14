"""Generate a fake GitHub repo (GraphQL-shaped) where a core maintainer leaves, for offline tests.

fakeorg/widgets: 'alice' maintains src/core and src/parser and reviews most PRs there. She stops on CUTOFF.
After that, PRs touching core/parser wait days for a first response; net/cli (bob) are unaffected.
'erin' picks up parser reviews afterwards; 'carol' picks up core.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

CUTOFF = datetime(2024, 6, 1, tzinfo=timezone.utc)
START = CUTOFF - timedelta(days=760)
END = CUTOFF + timedelta(days=380)

MODULES = {
    "src/core": ["src/core/engine.py", "src/core/plan.py", "src/core/exec.py"],
    "src/parser": ["src/parser/lexer.py", "src/parser/grammar.py"],
    "src/net": ["src/net/server.py", "src/net/proto.py"],
    "src/cli": ["src/cli/main.py"],
    "src/storage": ["src/storage/disk.py", "src/storage/wal.py"],
    "docs": ["docs/index.md", "docs/api.md"],
    "tests": ["tests/test_core.py", "tests/test_net.py"],
}
# who authors PRs in which module (weights)
AUTHORS = {
    "alice": {"src/core": 5, "src/parser": 4},
    "bob": {"src/net": 5, "src/cli": 3},
    "carol": {"src/storage": 5, "src/core": 1},
    "dave": {"docs": 4, "tests": 2},
    "erin": {"src/parser": 1.5, "tests": 1},
    "frank": {"src/cli": 1, "docs": 1},
    "gina": {"src/net": 1, "src/storage": 1},
}
REVIEWERS_BEFORE = {"src/core": ["alice"] * 8 + ["carol"], "src/parser": ["alice"] * 8 + ["erin"],
                    "src/net": ["bob"] * 5 + ["gina"], "src/cli": ["bob", "frank"], "src/storage": ["carol"] * 4 + ["gina"],
                    "docs": ["dave"], "tests": ["dave", "bob"]}
REVIEWERS_AFTER = {"src/core": ["carol"] * 3 + ["bob"], "src/parser": ["erin"] * 3 + ["carol"],
                   **{k: v for k, v in REVIEWERS_BEFORE.items() if k not in ("src/core", "src/parser")}}


def iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def build(seed: int = 3) -> dict:
    rng = random.Random(seed)
    prs, issues, n = [], [], 0
    day = START
    while day < END:
        for _ in range(rng.choice([0, 1, 1, 2])):
            authors = [a for a in AUTHORS if not (a == "alice" and day >= CUTOFF)]
            author = rng.choice(authors)
            mod = rng.choices(list(AUTHORS[author]), weights=list(AUTHORS[author].values()))[0]
            mods = [mod] + ([rng.choice(["tests", "docs"])] if rng.random() < 0.3 else [])
            files = [{"path": rng.choice(MODULES[m])} for m in mods]
            created = day + timedelta(hours=rng.random() * 20)
            after = created >= CUTOFF
            pool = (REVIEWERS_AFTER if after else REVIEWERS_BEFORE)[mod]
            reviewer = rng.choice([r for r in pool if r != author] or ["dave"])
            slow = after and mod in ("src/core", "src/parser")
            wait_h = rng.uniform(40, 300) if slow else rng.uniform(2, 30)
            reviews, comments = [], []
            if not (slow and rng.random() < 0.3):
                reviews.append({"author": {"login": reviewer}, "submittedAt": iso(created + timedelta(hours=wait_h)),
                                "state": "APPROVED"})
            if rng.random() < 0.3:
                comments.append({"author": {"login": rng.choice([r for r in pool if r != author] or authors)},
                                 "createdAt": iso(created + timedelta(hours=wait_h * 1.5))})
            n += 1
            merged = created + timedelta(hours=wait_h + 5) if reviews else None
            prs.append({"number": n, "title": f"{mod}: change {n}", "url": f"https://github.com/fakeorg/widgets/pull/{n}",
                        "createdAt": iso(created), "mergedAt": iso(merged) if merged else None, "closedAt": None,
                        "state": "MERGED" if merged else "OPEN", "author": {"login": author, "name": author.title()},
                        "files": {"nodes": files}, "reviews": {"nodes": reviews}, "comments": {"nodes": comments}})
        if rng.random() < 0.35:
            mod = rng.choice(["core", "parser", "net", "storage"])
            ans = {"core": "alice", "parser": "alice", "net": "bob", "storage": "carol"}[mod]
            if day >= CUTOFF and ans == "alice":
                ans = rng.choice(["carol", "erin"])
            n += 1
            issues.append({"number": n, "title": f"Question about {mod} behaviour", "url": f"https://github.com/fakeorg/widgets/issues/{n}",
                           "createdAt": iso(day), "closedAt": None, "author": {"login": "someuser"},
                           "labels": {"nodes": [{"name": mod}]},
                           "comments": {"nodes": [{"author": {"login": ans, "name": ans.title()}, "createdAt": iso(day + timedelta(hours=5)),
                                                   "body": f"In {mod}, this happens because of the planner cache; clear it with --reset-{mod}."}]}})
        day += timedelta(days=1)
    prs.sort(key=lambda p: p["createdAt"], reverse=True)
    issues.sort(key=lambda i: i["createdAt"], reverse=True)
    return {"owner": "fakeorg", "repo": "widgets", "since": 0.0, "fetched": 0, "prs": prs, "issues": issues}


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "data" / "cache" / "fakeorg__widgets.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    data = build()
    out.write_text(json.dumps(data))
    print(f"wrote {out} with {len(data['prs'])} PRs and {len(data['issues'])} issues; cutoff {CUTOFF.date()}")
