"""Fake Slack and Notion data for the fakeorg/widgets test repo (see make_fixture.py), so the connectors can be
tested offline.

The story matches the GitHub fixture: alice maintained src/core and src/parser and left on 2024-06-01.
  * In Slack, alice answered most #core and #parser questions before she left (her account is now deactivated);
    afterwards carol and bob answer #core, erin and hana answer #parser. bob and gina answer #net.
    hana is only in Slack and Notion, not on GitHub. carol's Slack handle is "carol.m", so she is not matched to
    her GitHub login automatically (only by hand), but her Slack and Notion accounts share an email.
  * In Notion, alice wrote ADR-7 (why the parser rejects trailing commas) and the core planner cache doc,
    which carol edited later. bob wrote the net protocol notes, carol the storage WAL notes, hana the parser
    onboarding guide. dave's offsite agenda is about no area. One page is in the trash.

slack_api() / notion_api() return the data in the shape the real APIs return; write_slack_export() writes it as a
Slack export ZIP. tests/test_connectors.py serves the same data from fake Slack and Notion APIs.
"""
from __future__ import annotations

import json
import random
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

CUTOFF = datetime(2024, 6, 1, tzinfo=timezone.utc)
START = CUTOFF - timedelta(days=700)
END = CUTOFF + timedelta(days=360)

USERS = [
    # id, handle, display, real name, email, deleted, title
    ("U01ALICE", "alice", "alice", "Alice Novak", "alice@widgets.dev", True, "Core maintainer"),
    ("U02BOB", "bob", "bob", "Bob Okoro", "bob@widgets.dev", False, "Network engineer"),
    ("U03CAROL", "carol.m", "Carol M", "Carol Mendes", "carol@widgets.dev", False, "Storage engineer"),
    ("U04DAVE", "dave", "dave", "Dave Kim", "dave@widgets.dev", False, "Docs and tests"),
    ("U05ERIN", "erin", "erin", "Erin Walsh", "erin@widgets.dev", False, ""),
    ("U06FRANK", "frank", "frank", "Frank Li", "frank@widgets.dev", False, ""),
    ("U07GINA", "gina", "gina", "Gina Rossi", "gina@widgets.dev", False, ""),
    ("U08HANA", "hana", "hana", "Hana Sato", "hana@widgets.dev", False, "Support engineer"),
]
BOTS = [("U09DEPLOY", "deploybot"), ("USLACKBOT", "slackbot")]
CHANNELS = [("C01CORE", "core", "Query engine core"), ("C02PARSER", "parser", "Lexer and grammar"),
            ("C03NET", "net", "Server and wire protocol"), ("C04GENERAL", "general", "Everything else"),
            ("C05ALERTS", "deploy-alerts", "Deploy notifications")]

QUESTIONS = {
    "core": ["Why is the planner cache invalidated after every write?", "How do I profile a slow query?",
             "Is exec.py safe to call from two threads?", "What does the core engine do on a plan miss?",
             "Where are query plans stored between runs?"],
    "parser": ["Why does the lexer choke on unicode identifiers?", "How do I add a keyword to the grammar?",
               "The parser error messages point to the wrong column, known issue?",
               "Can the grammar support optional semicolons?"],
    "net": ["Why does the server drop idle connections after 30s?", "How is the wire protocol versioned?",
            "Can the net layer run behind a TLS proxy?"],
    "general": ["Anyone up for lunch?", "Who has the conference room booking?", "Office closed on Friday?"],
}
ANSWERS = {
    "core": ["Writes bump the schema epoch, and the planner cache is keyed on it. Clear it with --reset-core.",
             "Run with PROFILE=1 and read exec stats; most slow plans are a missing index on the label scan.",
             "Not safe: exec.py holds a per-graph lock, take a reader handle per thread instead.",
             "On a plan miss core builds the plan, then stores it in the planner cache keyed by query hash.",
             "Plans live in the planner cache in memory only; they are rebuilt after a restart."],
    "parser": ["The lexer only accepts NFC-normalised identifiers; normalise before passing the query in.",
               "Add it to grammar.py KEYWORDS and regenerate the tables with make grammar.",
               "Yes, the column is counted in bytes not characters; fixed in the lexer rewrite branch.",
               "Not without a grammar change: semicolons end statements, making them optional is ambiguous."],
    "net": ["The idle timeout is net.idle_seconds in server config, default 30.",
            "Each frame carries a version byte; proto.py rejects frames newer than the server.",
            "Yes, terminate TLS at the proxy and set net.trust_proxy=true so client IPs are kept."],
    "general": ["sure, 12:30?", "Frank has it this week I think", "yes, holiday"],
}
ANSWERERS = {  # channel -> (before the cutoff, after the cutoff), as (user, weight) lists
    "core": ([("U01ALICE", 75), ("U03CAROL", 25)], [("U03CAROL", 70), ("U02BOB", 30)]),
    "parser": ([("U01ALICE", 80), ("U05ERIN", 20)], [("U05ERIN", 50), ("U08HANA", 50)]),
    "net": ([("U02BOB", 70), ("U07GINA", 30)], [("U02BOB", 70), ("U07GINA", 30)]),
    "general": ([(u[0], 1) for u in USERS[1:]], [(u[0], 1) for u in USERS[1:]]),
}
SPECIAL = {  # the thread Ghost Mode should find
    "channel": "parser", "asker": "U04DAVE", "answerer": "U01ALICE", "when": CUTOFF - timedelta(days=200),
    "question": "Why does the parser reject trailing commas in lists?",
    "answer": "Because of ADR-7: trailing commas made the grammar ambiguous with tuple literals. "
              "Use --lenient-commas in the parser config if you really need them.",
}


def _ts(d: datetime, n: int) -> str:
    return f"{d.timestamp():.0f}.{n:06d}"


def slack_api(seed: int = 11) -> dict:
    """Users, channels and messages shaped like the Slack Web API returns them."""
    rng = random.Random(seed)
    users = [{"id": uid, "name": h, "deleted": deleted, "real_name": real, "is_bot": False, "is_app_user": False,
              "profile": {"real_name": real, "display_name": disp, "email": email, "title": title, "image_72": ""}}
             for uid, h, disp, real, email, deleted, title in USERS]
    users += [{"id": uid, "name": h, "deleted": False, "real_name": h, "is_bot": True, "profile": {"real_name": h}}
              for uid, h in BOTS]
    humans = [u[0] for u in USERS]
    msgs = {c[0]: [] for c in CHANNELS}
    n = 0

    def thread(cid, chan, when, asker, question, replies):
        nonlocal n
        n += 1
        parent = {"type": "message", "user": asker, "text": question, "ts": _ts(when, n)}
        out = []
        for i, (user, text) in enumerate(replies):
            n += 1
            out.append({"type": "message", "user": user, "text": text, "ts": _ts(when + timedelta(minutes=7 + 9 * i), n),
                        "thread_ts": parent["ts"], "parent_user_id": asker})
        if out:
            parent.update(thread_ts=parent["ts"], reply_count=len(out), latest_reply=out[-1]["ts"])
        msgs[cid] += [parent] + out

    day = START
    while day < END:
        for cid, chan, _ in CHANNELS[:4]:
            if rng.random() > {"core": 0.45, "parser": 0.4, "net": 0.3, "general": 0.15}[chan]:
                continue
            when = day + timedelta(hours=9 + rng.random() * 8)
            phase = 1 if when >= CUTOFF else 0
            pool = ANSWERERS[chan][phase]
            answerer = rng.choices([u for u, _ in pool], weights=[w for _, w in pool])[0]
            asker = rng.choice([u for u in humans if u != answerer and not (u == "U01ALICE" and phase)])
            i = rng.randrange(len(QUESTIONS[chan]))
            q = QUESTIONS[chan][i]
            a = ANSWERS[chan][i % len(ANSWERS[chan])]
            replies = [(answerer, a)]
            r = rng.random()
            if r < 0.25:
                replies.append((asker, "thanks!"))                       # the asker: never counted
            elif r < 0.4:
                other = rng.choice([u for u in humans if u not in (asker, answerer) and not (u == "U01ALICE" and phase)])
                replies.append((other, "+1"))                            # noise: filtered out
            elif r < 0.5:
                other = rng.choice([u for u, _ in pool if u != answerer] or [answerer])
                if other != answerer:
                    replies.append((other, f"Also worth knowing: {ANSWERS[chan][(i + 1) % len(ANSWERS[chan])]}"))
            thread(cid, chan, when, asker, f"<@{answerer}> {q}" if rng.random() < 0.2 else q, replies)
        if rng.random() < 0.2:
            n += 1
            msgs["C01CORE"].append({"type": "message", "subtype": "bot_message", "bot_id": "B01", "user": "U09DEPLOY",
                                    "text": "Deployed core v1.2.3", "ts": _ts(day + timedelta(hours=18), n)})
            n += 1
            msgs["C05ALERTS"].append({"type": "message", "subtype": "bot_message", "bot_id": "B01",
                                      "user": "U09DEPLOY", "text": "deploy ok", "ts": _ts(day + timedelta(hours=19), n)})
        day += timedelta(days=1)
    n += 1
    msgs["C02PARSER"].append({"type": "message", "subtype": "channel_join", "user": "U08HANA",
                              "text": "<@U08HANA> has joined the channel", "ts": _ts(CUTOFF + timedelta(days=3), n)})
    s = SPECIAL
    thread("C02PARSER", "parser", s["when"], s["asker"], s["question"], [(s["answerer"], s["answer"])])
    for cid in msgs:
        msgs[cid].sort(key=lambda m: float(m["ts"]))
    channels = [{"id": cid, "name": name, "is_archived": False, "is_member": cid != "C05ALERTS",
                 "purpose": {"value": purpose}, "topic": {"value": ""}} for cid, name, purpose in CHANNELS]
    return {"team": {"id": "T01WIDGETS", "name": "Widgets Inc", "domain": "widgets"}, "users": users,
            "channels": channels, "messages": msgs}


def write_slack_export(data: dict, out: Path) -> Path:
    """Write the data as a Slack export ZIP: users.json, channels.json, <channel>/<YYYY-MM-DD>.json."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("users.json", json.dumps(data["users"]))
        z.writestr("channels.json", json.dumps([{k: v for k, v in c.items() if k != "is_member"}
                                                for c in data["channels"]]))
        names = {c["id"]: c["name"] for c in data["channels"]}
        for cid, msgs in data["messages"].items():
            days: dict[str, list] = {}
            for m in msgs:
                d = datetime.fromtimestamp(float(m["ts"]), tz=timezone.utc).strftime("%Y-%m-%d")
                days.setdefault(d, []).append(m)
            for d, ms in days.items():
                z.writestr(f"{names[cid]}/{d}.json", json.dumps(ms))
    return out


def _rt(text: str) -> list:
    return [{"type": "text", "plain_text": text, "text": {"content": text}}]


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def notion_api() -> dict:
    """Users, pages, blocks and databases shaped like the Notion API returns them."""
    users = [{"object": "user", "id": f"n-{h}", "type": "person", "name": real, "person": {"email": email}}
             for _, h, _, real, email, _, _ in USERS if h in ("alice", "bob", "carol.m", "dave", "hana")]
    users.append({"object": "user", "id": "n-bot", "type": "bot", "name": "Last Day", "bot": {}})
    uid = {u["name"].split()[0].lower(): u["id"] for u in users}

    def page(pid, title, creator, created, editor, edited, parent=None, trash=False):
        return {"object": "page", "id": pid, "url": f"https://www.notion.so/{pid}",
                "created_time": _iso(created), "last_edited_time": _iso(edited),
                "created_by": {"object": "user", "id": uid[creator]},
                "last_edited_by": {"object": "user", "id": uid[editor]},
                "parent": parent or {"type": "workspace", "workspace": True}, "in_trash": trash,
                "properties": {"title": {"id": "title", "type": "title", "title": _rt(title)}}}

    def para(bid, text, children=False):
        return {"object": "block", "id": bid, "type": "paragraph", "has_children": children,
                "paragraph": {"rich_text": _rt(text)}}

    c = CUTOFF
    pages = [
        page("p-adr7", "ADR-7: Parser rejects trailing commas", "alice", c - timedelta(days=450), "alice",
             c - timedelta(days=448), {"type": "database_id", "database_id": "db-decisions"}),
        page("p-cache", "How the core planner cache works", "alice", c - timedelta(days=270), "carol",
             c + timedelta(days=75)),
        page("p-onboard", "Parser onboarding guide", "hana", c + timedelta(days=90), "hana", c + timedelta(days=95)),
        page("p-net", "Net module: wire protocol notes", "bob", c - timedelta(days=140), "bob", c - timedelta(days=140)),
        page("p-offsite", "Q3 offsite agenda", "dave", c + timedelta(days=30), "dave", c + timedelta(days=31)),
        page("p-wal", "Storage engine: WAL format", "carol", c - timedelta(days=180), "carol", c - timedelta(days=170)),
        page("p-old", "Old storage notes", "carol", c - timedelta(days=600), "carol", c - timedelta(days=600),
             trash=True),
    ]
    blocks = {
        "p-adr7": [para("b1", "Context: trailing commas in list literals were ambiguous with tuple literals."),
                   para("b2", "Decision: the grammar rejects trailing commas. --lenient-commas turns them back on."),
                   para("b3", "Revisit if tuple literals are removed from the language.")],
        "p-cache": [para("b4", "The planner cache in src/core is keyed by query hash and schema epoch."),
                    {"object": "block", "id": "b5", "type": "toggle", "has_children": True,
                     "toggle": {"rich_text": _rt("Invalidation details")}},
                    para("b6", "Every write bumps the epoch, so cached plans for that graph are dropped.")],
        "b5": [para("b7", "Use --reset-core to clear the planner cache by hand after a schema migration.")],
        "p-onboard": [para("b8", "Start with lexer.py, then grammar.py. Run make grammar after editing keywords.")],
        "p-net": [para("b9", "Frames carry a version byte. Idle connections close after net.idle_seconds.")],
        "p-offsite": [para("b10", "Day 1: roadmap. Day 2: hiking.")],
        "p-wal": [para("b11", "WAL segments are 64 MB; disk.py fsyncs every commit unless wal.async is on.")],
        "p-old": [para("b12", "Superseded.")],
    }
    databases = {"db-decisions": {"object": "database", "id": "db-decisions", "title": _rt("Decisions")}}
    return {"workspace": "Widgets Inc", "users": users, "pages": pages, "blocks": blocks, "databases": databases}


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "data" / "cache" / "fixtures" / "slack_export_widgets.zip"
    write_slack_export(slack_api(), out)
    n = notion_api()
    (out.parent / "notion_api_widgets.json").write_text(json.dumps(n))
    print(f"wrote {out} and notion_api_widgets.json ({len(n['pages'])} Notion pages)")
