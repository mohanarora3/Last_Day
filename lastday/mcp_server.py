"""Last Day as an MCP server (stdio), so Claude / Cursor / any MCP client can ask the company brain.

Add to your MCP client config:
  {"mcpServers": {"last-day": {"command": "python", "args": ["-m", "lastday.mcp_server"],
                               "cwd": "/path/to/company_brain", "env": {"LASTDAY_GRAPH": "demo"}}}}
"""
from __future__ import annotations

import json
import os
import sys

from . import service as S

GRAPH = os.getenv("LASTDAY_GRAPH", "demo")

TOOLS = [
    {"name": "who_knows", "description": "Who knows a system/customer/module best, with evidence counts.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string",
                     "description": "e.g. 'who knows ledger reconciliation'"}}, "required": ["question"]}},
    {"name": "simulate_departure",
     "description": "What breaks if these people leave: orphaned/fragile areas, dependency cascade, lost team links.",
     "inputSchema": {"type": "object", "properties": {"people": {"type": "array", "items": {"type": "string"},
                     "description": "person ids"}}, "required": ["people"]}},
    {"name": "who_covers", "description": "If these people are on leave, who covers these assets?",
     "inputSchema": {"type": "object", "properties": {
         "absent": {"type": "array", "items": {"type": "string"}},
         "assets": {"type": "array", "items": {"type": "string"}}}, "required": ["absent", "assets"]}},
    {"name": "exit_interview", "description": "Generate a targeted, cited exit interview for a person.",
     "inputSchema": {"type": "object", "properties": {"person": {"type": "string"}}, "required": ["person"]}},
    {"name": "ask", "description": "Ask the company brain anything; answers cite sources, including people who left.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "list_people", "description": "List people and teams in the graph.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def call(name: str, args: dict):
    g = S.graph(GRAPH)
    if name == "who_knows":
        return S.ask_mod.ask(g, args["question"] if "who" in args["question"].lower()
                             else "who knows " + args["question"])
    if name == "simulate_departure":
        r = S.simulate(g, args["people"])
        return {"summary": r["summary"], "top_risks": r["assets"][:8], "team_links": r["team_links"][:5]}
    if name == "who_covers":
        return S.who_covers(g, args["absent"], args["assets"])
    if name == "exit_interview":
        return S.interview_mod.build_questions(g, args["person"])
    if name == "ask":
        return S.ask_mod.ask(g, args["question"])
    if name == "list_people":
        return g.q("MATCH (p:Person) RETURN p.id AS id, p.name AS name, p.team AS team, p.active AS active")
    raise ValueError(f"unknown tool {name}")


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:  # notification
            continue
        try:
            if method == "initialize":
                result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "last-day", "version": "1.0.0"}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                p = msg["params"]
                out = call(p["name"], p.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(out, default=str)[:60000]}]}
            elif method == "ping":
                result = {}
            else:
                raise ValueError(f"method not found: {method}")
            resp = {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(e)}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
