"""Tests for the Slack and Notion connectors.   python tests/test_connectors.py

Part 1 runs anywhere: it reads a fake Slack export, fetches the same data from fake Slack and Notion APIs
(served on localhost by this script), matches people across GitHub, Slack and Notion, and drives the
/api/connections routes of the web server.
Part 2 needs FalkorDB running (docker compose up -d): it builds the company graph and checks that Slack answers and
Notion docs change coverage, Ghost Mode, the exit interview and who-knows answers.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
TMP = Path(tempfile.mkdtemp(prefix="lastday-conn-"))
os.environ["LASTDAY_CONNECTIONS"] = str(TMP / "connections")  # never touch your real connections
os.environ.pop("SLACK_BOT_TOKEN", None)
os.environ.pop("NOTION_TOKEN", None)

import make_connector_fixtures as FX  # noqa: E402
import make_fixture  # noqa: E402
from lastday import connections as C  # noqa: E402
from lastday import service as S  # noqa: E402
from lastday.ingest import github, merge, notion, slack  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1
    print("  ok:", msg)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve(handler) -> str:
    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}"


def page_of(items, cursor, size):
    start = int(cursor or 0)
    chunk = items[start:start + size]
    return chunk, (str(start + size) if start + size < len(items) else "")


# ------------------------------------------------------------------------------------------------
# Fake Slack Web API
# ------------------------------------------------------------------------------------------------
SL = FX.slack_api()
SLACK_CALLS: Counter = Counter()
SLACK_FAIL_ONCE = {"conversations.history"}


class FakeSlack(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, code=200, headers=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        method = u.path.rsplit("/", 1)[-1]
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if self.headers.get("authorization") != "Bearer xoxb-test":
            return self.send({"ok": False, "error": "invalid_auth"})
        SLACK_CALLS[method] += 1
        if method in SLACK_FAIL_ONCE:
            SLACK_FAIL_ONCE.discard(method)
            return self.send({"ok": False, "error": "ratelimited"}, 429, {"Retry-After": "0"})
        if method == "auth.test":
            return self.send({"ok": True, "team": "Widgets Inc", "team_id": "T01WIDGETS",
                              "url": "https://widgets.slack.com/"})
        if method == "users.list":
            items, nxt = page_of(SL["users"], q.get("cursor"), 4)
            return self.send({"ok": True, "members": items, "response_metadata": {"next_cursor": nxt}})
        if method == "conversations.list":
            items, nxt = page_of(SL["channels"], q.get("cursor"), 2)
            return self.send({"ok": True, "channels": items, "response_metadata": {"next_cursor": nxt}})
        if method == "conversations.join":
            return self.send({"ok": False, "error": "missing_scope"})
        if method == "conversations.history":
            msgs = [m for m in SL["messages"][q["channel"]]
                    if not (m.get("thread_ts") and m["thread_ts"] != m["ts"])
                    and float(m["ts"]) >= float(q.get("oldest") or 0)]
            msgs = sorted(msgs, key=lambda m: -float(m["ts"]))
            items, nxt = page_of(msgs, q.get("cursor"), min(int(q.get("limit", 100)), 50))
            return self.send({"ok": True, "messages": items, "has_more": bool(nxt),
                              "response_metadata": {"next_cursor": nxt}})
        if method == "conversations.replies":
            msgs = [m for m in SL["messages"][q["channel"]] if m.get("thread_ts") == q["ts"]]
            items, nxt = page_of(msgs, q.get("cursor"), 20)
            return self.send({"ok": True, "messages": items, "response_metadata": {"next_cursor": nxt}})
        return self.send({"ok": False, "error": "unknown_method"})


# ------------------------------------------------------------------------------------------------
# Fake Notion API
# ------------------------------------------------------------------------------------------------
NO = FX.notion_api()
NOTION_FAIL_ONCE = {"search"}


class FakeNotion(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, code=200, headers=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def guard(self):
        if self.headers.get("authorization") != "Bearer ntn_test":
            self.send({"object": "error", "status": 401, "message": "API token is invalid."}, 401)
            return False
        if not self.headers.get("notion-version"):
            self.send({"object": "error", "status": 400, "message": "Notion-Version header missing"}, 400)
            return False
        return True

    def do_GET(self):
        if not self.guard():
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = u.path.strip("/").split("/")[1:]  # drop "v1"
        if parts == ["users", "me"]:
            return self.send({"object": "user", "id": "n-bot", "type": "bot",
                              "bot": {"workspace_name": NO["workspace"]}})
        if parts == ["users"]:
            items, nxt = page_of(NO["users"], q.get("start_cursor"), 2)
            return self.send({"object": "list", "results": items, "has_more": bool(nxt), "next_cursor": nxt or None})
        if len(parts) == 3 and parts[0] == "blocks" and parts[2] == "children":
            items, nxt = page_of(NO["blocks"].get(parts[1], []), q.get("start_cursor"), 2)
            return self.send({"object": "list", "results": items, "has_more": bool(nxt), "next_cursor": nxt or None})
        if len(parts) == 2 and parts[0] == "databases":
            if parts[1] in NO["databases"]:
                return self.send(NO["databases"][parts[1]])
            return self.send({"object": "error", "status": 404, "message": "not found"}, 404)
        return self.send({"object": "error", "status": 404, "message": "not found"}, 404)

    def do_POST(self):
        if not self.guard():
            return
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        if urlparse(self.path).path.endswith("/search"):
            if "search" in NOTION_FAIL_ONCE:
                NOTION_FAIL_ONCE.discard("search")
                return self.send({"object": "error", "status": 429, "message": "rate limited"}, 429,
                                 {"Retry-After": "0"})
            assert body.get("filter", {}).get("value") == "page"
            items, nxt = page_of(NO["pages"], body.get("start_cursor"), 3)
            return self.send({"object": "list", "results": items, "has_more": bool(nxt), "next_cursor": nxt or None})
        return self.send({"object": "error", "status": 404, "message": "not found"}, 404)


def validate(ds: dict):
    """The normalized dataset contract from loader.py."""
    people = {p["id"] for p in ds["people"]}
    assets = {a["id"] for a in ds["assets"]}
    ids = [e["id"] for e in ds["evidence"]]
    assert len(ids) == len(set(ids)), "evidence ids must be unique"
    for e in ds["evidence"]:
        assert e["author"] in people, f"unknown author {e['author']}"
        assert e.get("counterpart") in people or e.get("counterpart") is None, e
        for a, share in e["assets"]:
            assert a in assets and 0 < share <= 1, (a, share)
        assert e["ts"] > 0
    for a, b in ds["depends"]:
        assert a in assets and b in assets
    return True


def http(base, path, body=None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(base + path, data=data, headers={"content-type": "application/json"},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code


def wait_job(base, jid, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        j, _ = http(base, f"/api/job/{jid}")
        if j["data"]["status"] != "running":
            return j["data"]
        time.sleep(0.3)
    raise AssertionError("job timed out")


def part1() -> dict:
    print("\n[fixtures]")
    gh_raw = make_fixture.build()
    cache = ROOT / "data" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "fakeorg__widgets.json").write_text(json.dumps(gh_raw))
    zip_path = FX.write_slack_export(SL, TMP / "slack_export_widgets.zip")
    base = github.build_dataset(gh_raw)
    print("  ", len(base["people"]), "GitHub people,", len(base["evidence"]), "GitHub evidence")

    print("\n[Slack export]")
    exp = slack.load_export(zip_path, team_name="Widgets Inc", domain="widgets")
    check(len(slack.identities(exp)) == 8, "8 human Slack accounts (bots left out)")
    check(len(exp["channels"]) == 5 and all(c["messages"] for c in exp["channels"]), "5 channels with messages")
    alice = next(i for i in slack.identities(exp) if i["id"] == "U01ALICE")
    check(alice["deleted"] and alice["email"] == "alice@widgets.dev", "deactivated account and email read")
    check(slack.clean_text("<@U02BOB> see <https://x.dev/a|the docs> in <#C01CORE|core> &amp; <!here>",
                           {"U02BOB": "bob"}) == "@bob see the docs in #core & @here", "Slack markup cleaned")

    print("\n[Slack Web API]")
    slack.API = serve(FakeSlack) + "/api"
    try:
        slack.fetch_api("xoxb-wrong", since_days=4000)
        check(False, "bad token must fail")
    except slack.SlackError as e:
        check("invalid_auth" in str(e), "a wrong token gives a clear error")
    log = []
    api_raw = slack.fetch_api("xoxb-test", since_days=4000, progress=log.append)
    check(api_raw["team"]["domain"] == "widgets", "workspace domain read from auth.test")
    check(SLACK_CALLS["users.list"] >= 3 and SLACK_CALLS["conversations.list"] >= 3, "pagination followed")
    check(not SLACK_FAIL_ONCE, "a 429 was retried")
    alerts = next(c for c in api_raw["channels"] if c["name"] == "deploy-alerts")
    check(alerts["skipped"] and not alerts["messages"], "channel the bot can't join is skipped, with a reason")
    for ch in exp["channels"]:
        if ch["name"] == "deploy-alerts":
            continue
        a = {m["ts"] for m in next(c for c in api_raw["channels"] if c["name"] == ch["name"])["messages"]}
        b = {m["ts"] for m in ch["messages"]}
        assert a == b, f"#{ch['name']}: API and export differ ({len(a)} vs {len(b)})"
    check(True, "the API returns the same messages as the export, threads included")

    print("\n[Notion API]")
    notion.API = serve(FakeNotion) + "/v1"
    notion.MIN_INTERVAL = 0
    try:
        notion.fetch_api("ntn_wrong")
        check(False, "bad token must fail")
    except notion.NotionError as e:
        check("401" in str(e), "a wrong token gives a clear error")
    nraw = notion.fetch_api("ntn_test")
    titles = {p["title"]: p for p in nraw["pages"]}
    check(len(nraw["pages"]) == 6 and "Old storage notes" not in titles, "6 pages read, the trashed one skipped")
    check(not NOTION_FAIL_ONCE, "a 429 was retried")
    check(titles["ADR-7: Parser rejects trailing commas"]["parent_title"] == "Decisions", "database title read")
    check("--reset-core" in titles["How the core planner cache works"]["text"], "nested blocks read (toggle)")
    check(len(notion.identities(nraw)) == 5, "5 Notion people (bot left out)")

    print("\n[merge: GitHub + Slack + Notion]")
    m = merge.build("Widgets", base, exp, nraw)
    ds = m["dataset"]
    check(validate(ds), "dataset follows the loader contract")
    rows = {r["id"]: r for r in m["people"]}
    a = rows["alice"]
    check({x["source"] for x in a["accounts"]} == {"github", "slack", "notion"}, "alice matched across all three")
    check({x["matched_by"] for x in a["accounts"]} >= {"handle", "email"}, "matched by Slack handle and by email")
    check(not a["active"] and a["left_slack"], "alice is marked as left (Slack account deactivated)")
    check("hana-sato" in rows and {x["source"] for x in rows["hana-sato"]["accounts"]} == {"slack", "notion"},
          "hana (Slack + Notion only) becomes her own person")
    check("carol-mendes" in rows and rows["carol"]["accounts"][0]["source"] == "github",
          "carol's Slack handle differs, so she is not auto-matched to GitHub")
    check(rows["carol-mendes"]["suggest"] == ["carol"], "...but GitHub carol is suggested as a likely match")
    kinds = Counter(e["kind"] for e in ds["evidence"])
    check(kinds["slack_answer"] > 50 and kinds["decision"] == 1 and kinds["doc"] >= 4,
          f"evidence: {kinds['slack_answer']} Slack answers, 1 decision, {kinds['doc']} docs")
    texts = [e["text"] for e in ds["evidence"] if e["kind"] == "slack_answer"]
    check(not any(t.strip(" !") in ("thanks", "+1") for t in texts), "'thanks' and '+1' replies are not answers")
    check(not any(e["author"] in ("deploybot",) for e in ds["evidence"]), "bots are not people")
    adr = next(e for e in ds["evidence"] if e["kind"] == "decision")
    check(adr["author"] == "alice" and adr["assets"][0][0] == "src/parser", "ADR-7 is alice's decision on src/parser")
    edit = next(e for e in ds["evidence"] if e["id"] == "no_p-cache_edit")
    check(edit["author"] == "carol-mendes" and edit["weight"] < 1.6, "the last editor gets a smaller doc credit")
    special = next(e for e in ds["evidence"] if "trailing commas" in e["title"] and e["kind"] == "slack_answer")
    check(special["counterpart"] == "dave" and special["channel"] == "#parser" and
          special["url"].startswith("https://widgets.slack.com/archives/C02PARSER/p"),
          "Slack answer has the asker as counterpart, channel and a permalink")
    core = next(c for c in m["channels"] if c["channel"] == "core")
    check(0 < core["linked"] < core["answers"], "#core: only answers that name an area are linked by default")
    check("Q3 offsite agenda" in m["notion"]["unlinked"], "a page about no area is left out")
    check(ds["meta"]["sources"] == "github,slack,notion" and ds["meta"]["repo"] == "fakeorg/widgets", "meta")
    check(any(t["name"].endswith("crew") for t in ds["teams"]), "teams inferred over all sources")

    m2 = merge.build("Widgets", base, exp, nraw, channel_map={"core": ["src/core"]},
                     links={"slack:U03CAROL": "github:carol"})
    rows2 = {r["id"]: r for r in m2["people"]}
    check("carol-mendes" not in rows2 and {x["source"] for x in rows2["carol"]["accounts"]} ==
          {"github", "slack", "notion"}, "a manual link joins carol's Slack (and, by email, Notion) to GitHub")
    check([x["matched_by"] for x in rows2["carol"]["accounts"] if x["source"] == "slack"] == ["manual"],
          "the manual link is shown as manual")
    core2 = next(c for c in m2["channels"] if c["channel"] == "core")
    check(core2["linked"] == core2["answers"], "with #core mapped to src/core every #core answer is linked")
    m3 = merge.build("Widgets", base, exp, nraw, separate=["slack:U01ALICE"])
    check({x["source"] for x in {r["id"]: r for r in m3["people"]}["alice"]["accounts"]} == {"github"},
          "'keep separate' stops automatic matching")

    print("\n[merge: Slack (+ Notion) without GitHub]")
    m4 = merge.build("Widgets chat", None, exp, nraw)
    names = {a["name"] for a in m4["dataset"]["assets"]}
    check({"#core", "#parser", "#net"} <= names and "#general" not in names, "channels become areas (not #general)")
    check(validate(m4["dataset"]), "dataset follows the loader contract")
    check(any(e["kind"] == "decision" for e in m4["dataset"]["evidence"]), "Notion pages link to channel areas")
    try:
        merge.build("x", None, None, nraw)
        check(False, "Notion alone must ask for areas")
    except ValueError:
        check(True, "Notion alone asks for GitHub or Slack first")

    print("\n[web routes]")
    from lastday import server
    import uvicorn
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            http(base_url, "/api/connections")
            break
        except Exception:
            time.sleep(0.1)
    st, _ = http(base_url, "/api/connections")
    check(st["ok"] and not st["data"]["slack"]["connected"], "GET /api/connections")
    st, _ = http(base_url, "/api/connections/settings", {"name": "Widgets", "github_repo": "fakeorg/widgets",
                                                          "workspace_url": "https://widgets.slack.com"})
    check(st["ok"] and st["data"]["github_repo"] == "fakeorg/widgets", "settings saved")
    st, _ = http(base_url, "/api/connections/settings", {"github_repo": "not a repo"})
    check(not st["ok"] and "owner/repo" in st["error"], "a bad repo name is refused")
    st, _ = http(base_url, "/api/connections/slack/export", raw=b"not a zip")
    check(not st["ok"] and "ZIP" in st["error"], "a non-ZIP upload is refused clearly")
    st, _ = http(base_url, "/api/connections/slack/export", raw=zip_path.read_bytes())
    check(st["ok"] and st["data"]["slack"]["connected"] and st["data"]["slack"]["mode"] == "export",
          "Slack export uploaded")
    check(C.path("slack_export.zip").exists() and not C.path("slack_export.uploading").exists(), "upload stored")
    st, _ = http(base_url, "/api/connections/token", {"source": "notion", "token": "hello"})
    check(not st["ok"] and "ntn_" in st["error"], "a malformed Notion token is refused")
    st, _ = http(base_url, "/api/connections/token", {"source": "notion", "token": "ntn_test"})
    job = wait_job(base_url, st["data"]["job"])
    check(job["status"] == "done" and job["result"]["pages"] == 6, "Notion connected and synced in the background")
    st, _ = http(base_url, "/api/connections")
    check("ntn_test" not in json.dumps(st) and st["data"]["notion"]["token_set"], "the token never reaches the browser")
    check(oct(C.path("secrets.json").stat().st_mode)[-3:] == "600", "secrets.json is readable only by you")
    st, _ = http(base_url, "/api/connections/preview", {})
    pv = wait_job(base_url, st["data"]["job"])
    check(pv["status"] == "done" and pv["result"]["counts"]["merged"] >= 6, "preview matches people")
    http(base_url, "/api/connections/people", {"links": {"slack:U03CAROL": "github:carol"}, "separate": []})
    http(base_url, "/api/connections/channels", {"channel_map": {"#core": "src/core"}})
    st, _ = http(base_url, "/api/connections/preview", {})
    pv = wait_job(base_url, st["data"]["job"])
    rows = {r["id"]: r for r in pv["result"]["people"]}
    check("carol-mendes" not in rows and C.load_settings()["channel_map"] == {"core": ["src/core"]},
          "people links and channel map saved and used")
    st, _ = http(base_url, "/api/connections/disconnect", {"source": "notion"})
    check(st["ok"] and not st["data"]["notion"]["connected"] and not st["data"]["notion"]["token_set"],
          "Notion disconnected (token and data removed)")
    C.write_raw("notion", nraw)
    return {"base_url": base_url, "nraw": nraw, "exp": exp, "zip": zip_path}


def part2(ctx: dict):
    from lastday.db import get_db
    print("\n[FalkorDB: build the company graph]")
    try:
        reachable = get_db().ping()
    except Exception:
        reachable = False
    if not reachable:
        print("  skipped: FalkorDB isn't reachable. Start it with `docker compose up -d` and run this again.")
        return
    st, _ = http(ctx["base_url"], "/api/connections/build", {})
    res = wait_job(ctx["base_url"], st["data"]["job"], timeout=300)
    check(res["status"] == "done", f"company graph built: {res.get('error') or res['result']['graph']}")
    g = S.graph(res["result"]["graph"])
    ov = S.overview(g)
    ppl = {p["id"]: p for p in ov["people"]}
    check("hana-sato" in ppl and ppl["alice"]["active"] is False, "graph has hana, and alice as already left")
    kinds = {r["kind"]: r["n"] for r in ov["evidence_counts"]}
    check(kinds.get("slack_answer", 0) > 50 and kinds.get("decision") == 1, "Slack and Notion evidence loaded")
    doc = g.q1("MATCH (a:Asset {id: 'src/parser'}) RETURN a.doc_cov AS d")["d"]
    check(doc > 0.05, f"Notion docs give src/parser written coverage ({doc:.2f})")
    hana = g.q1("MATCH (:Person {id: 'hana-sato'})-[k:KNOWS]->(:Asset {id: 'src/parser'}) RETURN k.score AS s")
    check(hana and hana["s"] > 0.2, "hana knows src/parser from Slack answers and her Notion guide")
    r = S.ask_mod.ask(g, "Why does the parser reject trailing commas?")
    check(r["mode"] == "ghost" and "Alice Novak" in (r.get("ghost_of") or []),
          "Ghost Mode answers from alice's Slack answer / ADR (she left)")
    check(any(s["kind"] in ("slack_answer", "decision") for s in r["sources"]), "sources are Slack / Notion")
    r = S.ask_mod.ask(g, "who knows the parser?")
    check(r["mode"] == "who_knows" and any(p["id"] in ("erin", "hana-sato") for p in r["people"]),
          "who knows the parser: erin / hana")
    iv = S.interview_mod.build_questions(g, "bob")
    srcs = [s for q in iv["questions"] for s in q["sources"]]
    check(any(s["kind"] == "slack_answer" and s["channel"] == "#net" for s in srcs),
          "bob's exit interview cites his #net Slack answers")
    sim = S.simulate(g, ["bob"])
    net = next(a for a in sim["assets"] if a["id"] == "src/net")
    check(net["drop"] > 0, f"removing bob drops src/net coverage ({net['before']} -> {net['after']})")

    print("\n[FalkorDB: Slack + Notion without GitHub]")
    S.save_connection_settings(name="Widgets chat", github_repo="")
    res = S.build_company()
    g2 = S.graph(res["graph"])
    names = {a["name"] for a in S.overview(g2)["assets"]}
    check({"#core", "#parser", "#net"} <= names, "channel areas in their own graph")
    print(f"\nALL {ok} CHECKS PASSED")


if __name__ == "__main__":
    ctx = part1()
    print(f"\npart 1: {ok} checks passed")
    part2(ctx)
