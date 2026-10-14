"""FalkorDB access layer.

Uses the official `falkordb` Python client when it is installed. If it is not,
it falls back to a tiny built-in RESP client that speaks GRAPH.QUERY --compact,
so the app only needs a running FalkorDB server.

Every query run through `Graph.q()` is also recorded in a per-request log, so
the UI can show judges the exact Cypher behind each answer.
"""
from __future__ import annotations

import contextvars
import json
import os
import socket
import time
from typing import Any

GRAPH_PREFIX = "lastday__"

_query_log: contextvars.ContextVar[list | None] = contextvars.ContextVar("query_log", default=None)


def start_query_log() -> list:
    log: list = []
    _query_log.set(log)
    return log


def _record(query: str, params: dict | None, ms: float, rows: int) -> None:
    log = _query_log.get()
    if log is not None and len(log) < 60:
        log.append({"cypher": " ".join(query.split()), "params": _short(params), "ms": round(ms, 2), "rows": rows})


def _short(params: dict | None) -> dict:
    out = {}
    for k, v in (params or {}).items():
        if isinstance(v, list) and len(v) > 6:
            out[k] = f"<list of {len(v)}>"
        elif isinstance(v, str) and len(v) > 80:
            out[k] = v[:77] + "..."
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------
# Minimal RESP client (fallback when the falkordb package is not installed)
# --------------------------------------------------------------------------

def _cypher_literal(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v)) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return json.dumps(v)  # double-quoted, escaped
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_cypher_literal(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"`{k}`: {_cypher_literal(x)}" for k, x in v.items()) + "}"
    raise TypeError(f"unsupported param type {type(v)}")


class _RespConn:
    def __init__(self, host: str, port: int, password: str | None = None, username: str | None = None):
        import threading
        self.lock = threading.Lock()
        self.sock = socket.create_connection((host, port), timeout=120)
        self.buf = b""
        if password:
            args = ["AUTH", username, password] if username else ["AUTH", password]
            self.command(*args)

    def command(self, *args: str):
        out = [f"*{len(args)}\r\n".encode()]
        for a in args:
            b = a.encode() if isinstance(a, str) else a
            out.append(f"${len(b)}\r\n".encode() + b + b"\r\n")
        with self.lock:
            self.sock.sendall(b"".join(out))
            return self._read()

    def _line(self) -> bytes:
        while b"\r\n" not in self.buf:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise ConnectionError("connection closed")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\r\n", 1)
        return line

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n + 2:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise ConnectionError("connection closed")
            self.buf += chunk
        data, self.buf = self.buf[:n], self.buf[n + 2:]
        return data

    def _read(self):
        line = self._line()
        t, rest = line[:1], line[1:]
        if t == b"+":
            return rest.decode()
        if t == b"-":
            raise RuntimeError(rest.decode())
        if t == b":":
            return int(rest)
        if t == b"$":
            n = int(rest)
            return None if n < 0 else self._exact(n).decode("utf-8", "replace")
        if t == b"*":
            n = int(rest)
            return None if n < 0 else [self._read() for _ in range(n)]
        if t == b",":
            return float(rest)
        if t == b"_":
            return None
        if t == b"#":
            return rest == b"t"
        raise RuntimeError(f"unknown RESP type {t!r}")


def _compact_value(v):
    typ, val = v[0], v[1]
    if typ == 1:
        return None
    if typ == 2:
        return val
    if typ == 3:
        return int(val)
    if typ == 4:
        return val in ("true", True, 1)
    if typ == 5:
        return float(val)
    if typ == 6:
        return [_compact_value(x) for x in val]
    if typ == 10:
        return {val[i]: _compact_value(val[i + 1]) for i in range(0, len(val), 2)}
    # nodes / edges / paths: return the raw structure, queries in this app return scalars
    return val


class _MiniGraph:
    def __init__(self, conn: _RespConn, name: str):
        self.conn, self.name = conn, name

    def query(self, q: str, params: dict | None = None):
        if params:
            q = "CYPHER " + " ".join(f"{k}={_cypher_literal(v)}" for k, v in params.items()) + " " + q
        res = self.conn.command("GRAPH.QUERY", self.name, q, "--compact")
        if len(res) == 1:  # stats only
            return _Result([], [])
        header = [h[1] if isinstance(h, list) else h for h in res[0]]
        rows = [[_compact_value(c) for c in row] for row in res[1]]
        return _Result(header, rows)

    def delete(self):
        self.conn.command("GRAPH.DELETE", self.name)


class _Result:
    def __init__(self, header, rows):
        self.header, self.result_set = header, rows


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

class Graph:
    """A thin wrapper around one FalkorDB graph (one graph per company / repo)."""

    def __init__(self, db: "DB", name: str):
        self.db, self.name = db, name
        self._g = db._select(name)

    def q(self, query: str, params: dict | None = None) -> list[dict]:
        t0 = time.perf_counter()
        res = self._g.query(query, params or {})
        ms = (time.perf_counter() - t0) * 1000
        header = getattr(res, "header", None)
        cols = [h[1] if isinstance(h, (list, tuple)) else h for h in (header or [])]
        rows = [dict(zip(cols, r)) for r in (res.result_set or [])]
        _record(query, params, ms, len(rows))
        return rows

    def q1(self, query: str, params: dict | None = None) -> dict | None:
        rows = self.q(query, params)
        return rows[0] if rows else None

    def delete(self) -> None:
        try:
            self._g.delete()
        except Exception:
            pass


class DB:
    def __init__(self, host: str | None = None, port: int | None = None, password: str | None = None,
                 username: str | None = None):
        self.host = host or os.getenv("FALKORDB_HOST", "localhost")
        self.port = int(port or os.getenv("FALKORDB_PORT", "6379"))
        self.password = password or os.getenv("FALKORDB_PASSWORD") or None
        self.username = username or os.getenv("FALKORDB_USERNAME") or None
        self.backend = "falkordb-py"
        try:
            from falkordb import FalkorDB  # type: ignore
            self._client = FalkorDB(host=self.host, port=self.port, password=self.password,
                                    username=self.username)
        except ImportError:
            self.backend = "resp-mini"
            self._client = None
            self._conn = _RespConn(self.host, self.port, self.password, self.username)

    def _select(self, name: str):
        if self._client is not None:
            return self._client.select_graph(name)
        return _MiniGraph(self._conn, name)

    def graph(self, name: str) -> Graph:
        if not name.startswith(GRAPH_PREFIX):
            name = GRAPH_PREFIX + name
        return Graph(self, name)

    def list_graphs(self) -> list[str]:
        if self._client is not None:
            names = self._client.list_graphs()
        else:
            names = self._conn.command("GRAPH.LIST")
        return sorted(n[len(GRAPH_PREFIX):] for n in names if n.startswith(GRAPH_PREFIX))

    def ping(self) -> bool:
        try:
            self.list_graphs()
            return True
        except Exception:
            return False


_db: DB | None = None


def get_db() -> DB:
    global _db
    if _db is None:
        _db = DB()
    return _db
