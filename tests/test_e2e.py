"""End-to-end test against a running FalkorDB.  python tests/test_e2e.py"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lastday import service as S  # noqa: E402
from lastday.db import get_db, start_query_log  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise AssertionError(msg)
    ok += 1
    print("  ok:", msg)


def main():
    db = get_db()
    print("FalkorDB backend:", db.backend)
    check(db.ping(), "FalkorDB reachable")

    print("\n[seed demo]")
    t = time.time()
    stats = S.seed_demo()
    print("  ", stats, f"{time.time() - t:.1f}s")
    check(stats["people"] == 17 and stats["evidence"] > 1500, "demo graph loaded")
    g = S.graph("demo")

    print("\n[simulate Priya leaving]")
    log = start_query_log()
    sim = S.simulate(g, ["priya"])
    st = {a["id"]: a for a in sim["assets"]}
    for a in sim["assets"][:10]:
        print(f"   {a['status']:9s} {a['id']:20s} {a['before']:.2f}->{a['after']:.2f} risk={a['risk']} "
              f"inh={(a['inherited'] or {}).get('via')}")
    print("   summary", sim["summary"], sim["algorithms"])
    check(st["ledger-recon"]["status"] == "orphaned", "ledger reconciliation orphaned")
    check(st["zenith-retail"]["status"] == "orphaned", "Zenith Retail orphaned")
    check(st["bank-sftp"]["status"] == "gap", "bank SFTP already a gap (Vikram left)")
    check(sim["summary"]["inherited"] >= 2, "dependency cascade found")
    check(sim["assets"][0]["status"] in ("orphaned", "fragile"), "top risk is orphaned/fragile")
    print("   team links", sim["team_links"][:3])
    check(any(set(l["teams"]) == {"payments", "risk"} and l["share_lost"] > 0.3 for l in sim["team_links"]),
          "Priya carries a big share of the Payments <-> Risk link")
    check(len(log) > 3, f"cypher log captured ({len(log)} queries)")

    print("\n[no departures]")
    base = S.simulate(g, [])
    check(all(a["status"] in ("safe", "gap") for a in base["assets"]), "baseline only safe/gap")

    print("\n[Diwali: Arjun and Meera on leave]")
    res = S.who_covers(g, ["arjun", "meera"], ["payments-gateway"])
    print("  ", json.dumps(res[0], default=str)[:400])
    check(res[0]["covers"] and res[0]["covers"][0]["id"] == "priya", "Priya covers the gateway")
    sim2 = S.simulate(g, ["arjun", "meera"])
    gw = next(a for a in sim2["assets"] if a["id"] == "payments-gateway")
    print(f"   gateway {gw['before']}->{gw['after']} {gw['status']} bus {gw['bus_factor_before']}->{gw['bus_factor_after']}")
    check(gw["status"] in ("fragile", "orphaned"), "gateway fragile with both away")

    print("\n[successors for ledger reconciliation]")
    c = S.recommend(g, "ledger-recon", ["priya"])
    for x in c:
        print("  ", x["name"], x["fit"], x["reasons"])
    check(c and c[0]["id"] == "rahul", "Rahul is the top successor")

    print("\n[ask]")
    for q in ["If Arjun and Meera are on leave Diwali week, who covers payments?",
              "Why does recon run at 03:40?", "What happens if Priya leaves?", "Who knows the fraud engine?",
              "How do I rotate the Unity Bank SFTP key?", "What is the airspeed of an unladen swallow?"]:
        r = S.ask_mod.ask(g, q)
        print(f"  Q: {q}\n  [{r['mode']}] {r['answer'][:260]}")
        for s in r.get("sources", [])[:2]:
            print(f"     [{s['n']}] {s['title']} - {s['author']}")
    r = S.ask_mod.ask(g, "Why does recon run at 03:40?")
    check(r["mode"] == "ghost" and any("03:40" in s["title"] for s in r["sources"]), "ghost mode finds the 03:40 answer")
    print("   method", r["method"])
    r = S.ask_mod.ask(g, "How do I rotate the Unity Bank SFTP key?")
    check("Vikram Nair" in (r.get("ghost_of") or []), "answers from Vikram's trail (he left)")
    check(S.ask_mod.ask(g, "If Arjun and Meera are on leave Diwali week, who covers payments?")["mode"] == "coverage",
          "leave question routed to coverage")

    print("\n[exit interview for Priya]")
    iv = S.interview_mod.build_questions(g, "priya")
    for q in iv["questions"]:
        print(f"   [{q['asset_name']}] {q['text']}  ({len(q['sources'])} sources)")
    check(len(iv["questions"]) >= 5, "at least 5 questions")
    check(all(q["sources"] for q in iv["questions"]), "every question cites a source")
    check(any("questions about Ledger Reconciliation" in q["text"] for q in iv["questions"]), "answers-count question")

    print("\n[answer -> coverage goes up]")
    led = [q for q in iv["questions"] if q["asset"] == "ledger-recon"]
    before = st["ledger-recon"]["after"]
    r1 = S.interview_mod.record_answer(g, "priya", "ledger-recon", led[0]["id"], led[0]["text"],
                                       "Top questions: FX fee mismatch -> rerun with --fx-snapshot; duplicate bank "
                                       "files -> dedupe on trailer hash; recon at 03:40 because of late presentment.")
    r2 = S.interview_mod.record_answer(g, "priya", "ledger-recon", led[1]["id"], led[1]["text"],
                                       "Only revisit ADR-014 if an acquirer guarantees exactly-once callbacks.")
    print(f"   ledger coverage after Priya leaves: {before} -> {r1['asset']['after']} -> {r2['asset']['after']} "
          f"({r2['asset']['status']})")
    check(r2["asset"]["after"] > before + 0.1, "interview answers raise coverage")
    r = S.ask_mod.ask(g, "what is the FX fee mismatch fix", ["priya"])
    check(any(s["kind"] == "interview" for s in r["sources"]) or r["sources"], "interview answer is searchable")

    print("\n[handover -> green]")
    t = S.handover_mod.draft(g, "ledger-recon", ["priya"], "rahul")
    print("  ", t["markdown"][:500].replace("\n", "\n   "))
    acc = S.handover_mod.accept(g, "ledger-recon", "rahul", ["priya"])
    print(f"   after handover: {acc['asset']['after']} {acc['asset']['status']}")
    check(acc["asset"]["status"] == "safe", "ledger recon covered after interview + handover")

    print("\n[MCP server]")
    p = subprocess.run([sys.executable, "-m", "lastday.mcp_server"], cwd=ROOT, capture_output=True, text=True,
                       input="\n".join(json.dumps(m) for m in [
                           {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
                           {"jsonrpc": "2.0", "method": "notifications/initialized"},
                           {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                           {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "who_covers", "arguments": {"absent": ["arjun", "meera"],
                                                                            "assets": ["payments-gateway"]}}}]) + "\n",
                       timeout=60)
    lines = [json.loads(x) for x in p.stdout.splitlines() if x.strip()]
    check(len(lines) == 3 and "priya" in lines[2]["result"]["content"][0]["text"], "MCP tools/call works")

    print("\n[GitHub fixture ingestion + backtest]")
    subprocess.run([sys.executable, str(ROOT / "tests" / "make_fixture.py")], check=True)
    cutoff = datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp()
    r = S.run_backtest("fakeorg/widgets", "alice", None, progress=lambda m: print("   ..", m))
    print("   cutoff", datetime.fromtimestamp(r["cutoff"], tz=timezone.utc).date())
    print("   groups", json.dumps(r["groups"], indent=1))
    for m in r["modules"]:
        print(f"   {m['status']:9s} {m['module']:14s} {m['pre_median_h']}h -> {m['post_median_h']}h "
              f"pred={m['predicted_successors']} actual={m['actual_top_reviewer']}")
    print("   successor hit rate", r["successor_hit_rate"], r["successor_checked"])
    check(abs(r["cutoff"] - cutoff) < 40 * 86400, "cutoff detected near the real departure")
    check({m["module"] for m in r["modules"] if m["status"] in ("orphaned", "fragile")} >= {"src/core", "src/parser"},
          "core and parser predicted at risk")
    check(r["groups"]["at risk"]["slowdown"] > 2 * (r["groups"].get("safe", {}).get("slowdown") or 1),
          "at-risk modules slowed down much more than safe ones")
    check((r["successor_hit_rate"] or 0) >= 0.5, "successor predictions mostly right")
    ing = S.ingest_github("fakeorg/widgets")
    print("  ", ing)
    check(ing["people"] == 7, "repo ingested as its own graph")
    print(f"\nALL {ok} CHECKS PASSED")


if __name__ == "__main__":
    main()
