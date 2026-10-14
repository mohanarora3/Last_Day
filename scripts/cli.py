"""Last Day command line.

  python scripts/cli.py seed                                  # build the Acme Pay demo graph
  python scripts/cli.py simulate priya                        # what if Priya leaves?
  python scripts/cli.py ask "who covers payments if arjun and meera are on leave?"
  python scripts/cli.py interview priya
  python scripts/cli.py ingest FalkorDB/FalkorDB              # needs GITHUB_TOKEN
  python scripts/cli.py departures owner/repo                 # who went quiet?
  python scripts/cli.py backtest owner/repo login [YYYY-MM-DD]

Slack + Notion (saved in data/connections/, then one company graph):
  python scripts/cli.py connect-slack path/to/slack-export.zip  # or an unzipped export folder
  python scripts/cli.py connect-slack --api                     # uses SLACK_BOT_TOKEN
  python scripts/cli.py connect-notion                          # uses NOTION_TOKEN
  python scripts/cli.py people                                  # how accounts were matched into people
  python scripts/cli.py company --name Acme [--repo owner/repo] # build the company graph
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lastday import service as S  # noqa: E402


def pct(x):
    return f"{round(x * 100)}%"


def main(argv):
    if not argv:
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    graph = "demo"
    if "--graph" in args:
        i = args.index("--graph")
        graph = args[i + 1]
        args = args[:i] + args[i + 2:]
    g = S.graph(graph) if cmd in ("simulate", "ask", "interview") else None
    log = print
    if cmd == "seed":
        print(json.dumps(S.seed_demo(), indent=2))
    elif cmd == "simulate":
        r = S.simulate(g, args)
        s = r["summary"]
        print(f"Removing {', '.join(p['name'] for p in r['removed'])}: {s['orphaned']} orphaned, {s['fragile']} fragile, "
              f"{s['inherited']} inherit risk; health {pct(s['health_before'])} -> {pct(s['health_after'])}")
        for a in r["assets"][:12]:
            if a["status"] != "safe" or a["inherited"]:
                via = f"  (via {a['inherited']['via']})" if a["inherited"] else ""
                print(f"  {a['status']:9s} {a['name']:28s} {pct(a['before']):>5s} -> {pct(a['after']):>5s}{via}")
        for l in r["team_links"][:3]:
            print(f"  link {l['teams'][0]} <-> {l['teams'][1]}: {pct(l['share_lost'])} lost")
        print("  algorithms:", r["algorithms"])
    elif cmd == "ask":
        r = S.ask_mod.ask(g, " ".join(args))
        print(r["answer"])
        for s in r.get("sources", []):
            print(f"  [{s['n']}] {s['title']} ({s['author']}, {s['date']})")
    elif cmd == "interview":
        r = S.interview_mod.build_questions(g, args[0])
        for q in r["questions"]:
            print(f"\n[{q['asset_name']}] {q['text']}")
            for s in q["sources"]:
                print(f"   - {s['kind']}: {s['title']} {s['date']}")
    elif cmd == "ingest":
        print(json.dumps(S.ingest_github(args[0], progress=log), indent=2))
    elif cmd == "departures":
        for d in S.find_departures(args[0], progress=log):
            last = datetime.fromtimestamp(d["last"], tz=timezone.utc).date()
            print(f"  {d['login']:24s} activity {d['activity']:5d}  last seen {last}  "
                  f"{'WENT QUIET' if d['went_quiet'] else ''}")
    elif cmd == "backtest":
        cutoff = None
        if len(args) > 2:
            cutoff = datetime.strptime(args[2], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        r = S.run_backtest(args[0], args[1], cutoff, progress=log)
        print(json.dumps({k: r[k] for k in ("repo", "leaver", "groups", "successor_hit_rate", "successor_checked",
                                            "prediction")}, indent=2, default=str))
        for m in r["modules"][:15]:
            print(f"  {m['status']:9s} {m['module']:30s} {m['pre_median_h']}h -> {m['post_median_h']}h  "
                  f"successor: {m['actual_top_reviewer']} {'(hit)' if m['successor_hit'] else ''}")
    elif cmd == "connect-slack":
        if not args:
            print("Give the export .zip (or folder), or --api to use SLACK_BOT_TOKEN.")
            return
        if args[0] == "--api":
            S.C.update_settings(slack={"mode": "api"})
            print(json.dumps(S.sync_slack(progress=log), indent=2, default=str))
        else:
            st = S.connect_slack_export(args[0])
            print(json.dumps(st["slack"]["summary"], indent=2, default=str))
    elif cmd == "connect-notion":
        print(json.dumps(S.sync_notion(progress=log), indent=2, default=str))
    elif cmd in ("people", "company"):
        opts = {}
        while args and args[0].startswith("--") and len(args) > 1:
            opts[args[0][2:]] = args[1]
            args = args[2:]
        if opts:
            S.save_connection_settings(opts.get("name"), opts.get("repo"))
        if cmd == "people":
            r = S.preview_company(progress=log)
            for p in r["people"]:
                accts = ", ".join(f"{a['source']}:{a['label']}" + (f" ({a['matched_by']})" if a["matched_by"] else "")
                                  for a in p["accounts"])
                flag = "" if p["kept"] else "  [too little activity]"
                hint = f"  maybe GitHub {', '.join(p['suggest'])}?" if p.get("suggest") else ""
                print(f"  {p['id']:20s} {p['name']:22s} {accts}{flag}{hint}")
            for ch in r["channels"]:
                print(f"  #{ch['channel']:20s} {ch['answers']:5d} answers, {ch['linked']:5d} linked to an area")
        else:
            r = S.build_company(progress=log)
            c = r["counts"]
            print(f"Built graph {r['graph']}: {c['people']} people, {c['assets']} areas, {c['evidence']} pieces of "
                  f"evidence {c['by_kind']}. Open it in the UI or use --graph {r['graph']}.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
