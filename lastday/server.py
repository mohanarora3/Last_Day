"""Web API + UI.  Run:  python -m lastday.server   (then open http://localhost:8000)"""
from __future__ import annotations

import os
import traceback
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import service as S
from .agent import llm
from .db import get_db, start_query_log

WEB = Path(__file__).resolve().parents[1] / "web"


def api(fn):
    async def handler(request: Request):
        body = {}
        if request.method == "POST":
            try:
                body = await request.json()
            except Exception:
                body = {}
        params = {**dict(request.query_params), **body, **request.path_params}
        log: list = []

        def run():
            nonlocal log
            log = start_query_log()  # per-request Cypher log, shown in the UI's query drawer
            return fn(params)
        try:
            res = await run_in_threadpool(run)
            return JSONResponse({"ok": True, "data": res, "cypher": log})
        except Exception as e:
            traceback.print_exc()
            return JSONResponse({"ok": False, "error": str(e), "cypher": log}, status_code=400)
    return handler


def _g(p):
    return S.graph(p.get("g") or S.DEMO)


def _list(v):
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return v
    return [x for x in str(v).split(",") if x]


def status(p):
    db = get_db()
    return {"falkordb": db.ping(), "backend": db.backend, "host": f"{db.host}:{db.port}",
            "graphs": db.list_graphs(), "llm": llm.provider(), "github_token": bool(os.getenv("GITHUB_TOKEN"))}


def seed(p):
    return S.seed_demo()


def overview(p):
    return S.overview(_g(p))


def sim(p):
    return S.simulate(_g(p), _list(p.get("removed")))


def successors(p):
    return S.recommend(_g(p), p["asset"], _list(p.get("removed")), k=int(p.get("k", 3)))


def covers(p):
    return S.who_covers(_g(p), _list(p.get("absent")), _list(p.get("assets")))


def interview(p):
    return S.interview_mod.build_questions(_g(p), p["person"])


def answer(p):
    return S.interview_mod.record_answer(_g(p), p["person"], p["asset"], p["question_id"], p["question"],
                                         p["answer"])


def handover(p):
    return S.handover_mod.draft(_g(p), p["asset"], _list(p.get("removed")), p.get("successor") or None)


def handover_accept(p):
    return S.handover_mod.accept(_g(p), p["asset"], p["successor"], _list(p.get("removed")))


def handover_issue(p):
    return S.handover_mod.create_github_issue(p["repo"], p["title"], p["body"])


def ask(p):
    return S.ask_mod.ask(_g(p), p["question"], _list(p.get("removed")))


def evidence(p):
    return S.evidence(_g(p), p.get("person"), p.get("asset"), int(p.get("limit", 25)))


def knowers(p):
    return S.knowers(_g(p), p["asset"])


def ingest(p):
    return {"job": S.start_job("ingest", S.ingest_github, p["repo"], int(p.get("since_days", 730)),
                               bool(p.get("refresh", False)))}


def backtest(p):
    cutoff = float(p["cutoff"]) if p.get("cutoff") else None
    return {"job": S.start_job("backtest", S.run_backtest, p["repo"], p["leaver"], cutoff)}


def departures(p):
    return {"job": S.start_job("departures", S.find_departures, p["repo"])}


def job(p):
    j = S.JOBS.get(p["id"])
    if not j:
        raise ValueError("unknown job")
    return j


def index(request):
    return FileResponse(WEB / "index.html")


# ---- connections: Slack + Notion ----------------------------------------------------------------

MAX_UPLOAD = 2 * 1024 ** 3


def connections(p):
    return S.C.public_state()


def connection_settings(p):
    return S.save_connection_settings(p.get("name"), p.get("github_repo"), p.get("workspace_url"))


def connection_token(p):
    return {"job": S.connect_token(p["source"], p.get("token") or "")}


def connection_sync(p):
    return {"job": S.start_sync(p["source"])}


def connection_disconnect(p):
    return S.disconnect(p["source"])


def connection_people(p):
    return S.save_people_links(p.get("links") or {}, p.get("separate") or [])


def connection_channels(p):
    return S.save_channel_map(p.get("channel_map") or {})


def connection_preview(p):
    return {"job": S.start_job("preview", S.preview_company)}


def connection_build(p):
    return {"job": S.start_job("build company", S.build_company)}


async def slack_export_upload(request: Request):
    """The browser sends the export ZIP as the raw request body (no multipart parsing needed)."""
    S.C.DIR.mkdir(parents=True, exist_ok=True)
    tmp = S.C.path("slack_export.uploading")
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise ValueError("That file is larger than 2 GB.")
                f.write(chunk)
        if not size:
            raise ValueError("No file was received.")
        res = await run_in_threadpool(S.connect_slack_export, tmp, True)
        return JSONResponse({"ok": True, "data": res, "cypher": []})
    except Exception as e:
        traceback.print_exc()
        tmp.unlink(missing_ok=True)
        return JSONResponse({"ok": False, "error": str(e), "cypher": []}, status_code=400)


routes = [
    Route("/", index),
    Route("/api/connections", api(connections)),
    Route("/api/connections/settings", api(connection_settings), methods=["POST"]),
    Route("/api/connections/slack/export", slack_export_upload, methods=["POST"]),
    Route("/api/connections/token", api(connection_token), methods=["POST"]),
    Route("/api/connections/sync", api(connection_sync), methods=["POST"]),
    Route("/api/connections/disconnect", api(connection_disconnect), methods=["POST"]),
    Route("/api/connections/people", api(connection_people), methods=["POST"]),
    Route("/api/connections/channels", api(connection_channels), methods=["POST"]),
    Route("/api/connections/preview", api(connection_preview), methods=["POST"]),
    Route("/api/connections/build", api(connection_build), methods=["POST"]),
    Route("/api/status", api(status)),
    Route("/api/seed", api(seed), methods=["POST"]),
    Route("/api/ingest", api(ingest), methods=["POST"]),
    Route("/api/backtest", api(backtest), methods=["POST"]),
    Route("/api/departures", api(departures), methods=["POST"]),
    Route("/api/job/{id}", api(job)),
    Route("/api/{g}/overview", api(overview), methods=["GET", "POST"]),
    Route("/api/{g}/simulate", api(sim), methods=["POST"]),
    Route("/api/{g}/successors", api(successors), methods=["POST"]),
    Route("/api/{g}/covers", api(covers), methods=["POST"]),
    Route("/api/{g}/interview", api(interview), methods=["POST"]),
    Route("/api/{g}/answer", api(answer), methods=["POST"]),
    Route("/api/{g}/handover", api(handover), methods=["POST"]),
    Route("/api/{g}/handover/accept", api(handover_accept), methods=["POST"]),
    Route("/api/{g}/handover/issue", api(handover_issue), methods=["POST"]),
    Route("/api/{g}/ask", api(ask), methods=["POST"]),
    Route("/api/{g}/evidence", api(evidence), methods=["GET", "POST"]),
    Route("/api/{g}/knowers", api(knowers), methods=["GET", "POST"]),
    Mount("/static", StaticFiles(directory=WEB), name="static"),
]

app = Starlette(routes=routes)


def main():
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    if os.getenv("LASTDAY_AUTOSEED", "1") == "1":
        try:
            if "demo" not in get_db().list_graphs():
                print("Seeding the Acme Pay demo graph ...")
                print(S.seed_demo())
        except Exception as e:
            print(f"Could not reach FalkorDB ({e}). Start it with: docker compose up -d")
    print(f"Last Day running on http://localhost:{port}")
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=port, log_level="warning")


if __name__ == "__main__":
    main()
