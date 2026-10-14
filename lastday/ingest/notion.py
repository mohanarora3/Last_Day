"""Notion connector: who wrote which docs and decisions.

Uses an internal integration token (NOTION_TOKEN, starts with ntn_ or secret_):
  1. Create an integration at https://www.notion.so/profile/integrations with the capabilities
     "Read content" and "Read user information including email addresses".
  2. In Notion, open each page or teamspace you want Last Day to read: ... menu > Connections > add the integration.
     Sub-pages are included automatically.

raw = {"source": "api", "workspace": str, "fetched": ts,
       "users": [{"id", "name", "email", "type"}],
       "pages": [{"id", "title", "url", "created_time", "last_edited_time", "created_by", "last_edited_by",
                  "parent_title", "text", "in_trash"}]}

`evidence()` turns each page into evidence for the person who wrote it: `decision` for ADRs / RFCs / pages in a
decisions database, `doc` for everything else. Both are "persistent" kinds (config.PERSISTENT_KINDS), so a page
keeps covering its area after its author leaves. If someone else edited the page last, they get a smaller `doc`
piece of evidence too. Notion's API only says who created a page and who edited it last, not everyone in between.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable

from ..config import EVIDENCE_WEIGHTS

API = os.getenv("NOTION_API_BASE", "https://api.notion.com/v1").rstrip("/")
VERSION = os.getenv("NOTION_VERSION", "2026-03-11")
MIN_INTERVAL = float(os.getenv("NOTION_MIN_INTERVAL", "0.34"))  # Notion allows ~3 requests per second
TEXT_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3", "bulleted_list_item", "numbered_list_item",
              "to_do", "toggle", "quote", "callout", "code")
DECISION_RX = re.compile(r"^\s*(adr|rfc|decision|design decision|daci)\b|\b(adr|rfc)[\s-]*\d+", re.I)

_last_call = [0.0]


class NotionError(RuntimeError):
    pass


def _call(method: str, path: str, token: str, body: dict | None = None, params: dict | None = None,
          retries: int = 6) -> dict:
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(retries):
        wait = MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "authorization": f"Bearer {token}", "notion-version": VERSION, "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After") or 2))
                continue
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(2 + attempt * 2)
                continue
            try:
                msg = json.loads(e.read()).get("message", "")
            except Exception:
                msg = ""
            if e.code == 401:
                raise NotionError("Notion rejected the token (401). Check that you copied the integration secret.")
            raise NotionError(f"Notion API {path} failed with HTTP {e.code} {msg}".strip())
    raise NotionError(f"Notion API {path} kept failing")


def _plain(rich: list | None) -> str:
    return "".join(t.get("plain_text", "") for t in (rich or []))


def _title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return _plain(prop.get("title")).strip()
    return ""


def _page_text(token: str, block_id: str, budget: list[int], depth: int = 0) -> list[str]:
    """Plain text of a page's blocks, depth-first, until the character budget runs out."""
    out, cursor = [], None
    while budget[0] > 0:
        data = _call("GET", f"/blocks/{block_id}/children", token, params={"page_size": 100, "start_cursor": cursor})
        for b in data.get("results", []):
            t = b.get("type")
            if t in TEXT_TYPES:
                line = _plain((b.get(t) or {}).get("rich_text")).strip()
                if line:
                    out.append(line)
                    budget[0] -= len(line)
            if b.get("has_children") and t not in ("child_page", "child_database") and depth < 2 and budget[0] > 0:
                out += _page_text(token, b["id"], budget, depth + 1)
            if budget[0] <= 0:
                break
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            break
    return out


def _parent_title(token: str, parent: dict, titles: dict[str, str]) -> str:
    kind = (parent or {}).get("type")
    pid = (parent or {}).get(kind) if kind else None
    if not pid or kind == "workspace":
        return ""
    if pid in titles:
        return titles[pid]
    title = ""
    try:
        if kind == "database_id":
            title = _plain(_call("GET", f"/databases/{pid}", token).get("title"))
        elif kind == "data_source_id":
            title = _plain(_call("GET", f"/data_sources/{pid}", token).get("title"))
    except NotionError:
        title = ""
    titles[pid] = title
    return title


def fetch_api(token: str, max_pages: int = 500, text_chars: int = 3000,
              progress: Callable[[str], None] | None = None) -> dict:
    """Read every page shared with the integration, its text, and the workspace's people."""
    progress = progress or (lambda m: None)
    me = _call("GET", "/users/me", token)
    workspace = ((me.get("bot") or {}).get("workspace_name")) or "Notion"
    progress(f"connected to Notion workspace {workspace}")
    users, cursor = [], None
    while True:
        data = _call("GET", "/users", token, params={"page_size": 100, "start_cursor": cursor})
        for u in data.get("results", []):
            users.append({"id": u["id"], "name": u.get("name") or "", "type": u.get("type") or "",
                          "email": ((u.get("person") or {}).get("email") or "").lower()})
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            break
    progress(f"{len([u for u in users if u['type'] == 'person'])} Notion people")

    raw_pages, cursor = [], None
    while len(raw_pages) < max_pages:
        data = _call("POST", "/search", token, body={
            "filter": {"property": "object", "value": "page"}, "page_size": 100,
            **({"start_cursor": cursor} if cursor else {})})
        raw_pages += data.get("results", [])
        cursor = data.get("next_cursor")
        if not data.get("has_more") or not cursor:
            break
    progress(f"{len(raw_pages)} pages shared with the integration")

    titles = {p["id"]: _title(p) for p in raw_pages}
    pages = []
    for i, p in enumerate(raw_pages[:max_pages]):
        if p.get("in_trash") or p.get("archived"):
            continue
        try:
            text = "\n".join(_page_text(token, p["id"], [text_chars]))
        except NotionError as e:
            text = ""
            progress(f"could not read the text of '{titles.get(p['id'])}': {e}")
        pages.append({
            "id": p["id"], "title": titles.get(p["id"]) or "Untitled", "url": p.get("url") or "",
            "created_time": p.get("created_time") or "", "last_edited_time": p.get("last_edited_time") or "",
            "created_by": (p.get("created_by") or {}).get("id") or "",
            "last_edited_by": (p.get("last_edited_by") or {}).get("id") or "",
            "parent_title": _parent_title(token, p.get("parent") or {}, titles),
            "text": text[:text_chars], "in_trash": False,
        })
        if (i + 1) % 20 == 0:
            progress(f"read {i + 1}/{len(raw_pages)} pages")
    return {"source": "api", "workspace": workspace, "fetched": time.time(), "users": users, "pages": pages}


# --------------------------------------------------------------------------------------------------
# Raw -> identities and evidence
# --------------------------------------------------------------------------------------------------

def _ts(iso: str) -> float:
    if not iso:
        return 0.0
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def identities(raw: dict) -> list[dict]:
    return [{"key": "notion:" + u["id"], "source": "notion", "id": u["id"], "name": u.get("name") or u["id"],
             "handles": [], "email": u.get("email") or "", "deleted": False, "avatar": "", "title": ""}
            for u in raw.get("users", []) if u.get("type") == "person"]


def is_decision(page: dict) -> bool:
    parent = (page.get("parent_title") or "").lower()
    return bool(DECISION_RX.search(page.get("title") or "")) or "decision" in parent or bool(
        re.search(r"\b(adr|adrs|rfcs?)\b", parent))


def evidence(raw: dict, match: Callable[[str], list[str]], until: float | None = None) -> tuple[list[dict], dict]:
    """Pages -> doc / decision evidence. Returns (evidence, stats)."""
    until = until or 1e20
    people = {u["id"] for u in raw.get("users", []) if u.get("type") == "person"}
    out = []
    st = {"pages": 0, "linked": 0, "decisions": 0, "areas": {}, "unlinked": []}
    for p in raw.get("pages", []):
        if p.get("in_trash"):
            continue
        st["pages"] += 1
        title, text = p.get("title") or "", p.get("text") or ""
        assets = match(title) or match(text[:3000])
        if not assets:
            if len(st["unlinked"]) < 20:
                st["unlinked"].append(title)
            continue
        st["linked"] += 1
        for a in assets:
            st["areas"][a] = st["areas"].get(a, 0) + 1
        kind = "decision" if is_decision(p) else "doc"
        st["decisions"] += kind == "decision"
        share = [[a, round(1.0 / len(assets), 3)] for a in assets]
        creator, editor = p.get("created_by"), p.get("last_edited_by")
        same = creator == editor
        t_created, t_edited = _ts(p.get("created_time")), _ts(p.get("last_edited_time"))
        if creator in people and (t_edited if same else t_created) < until:
            out.append({"id": f"no_{p['id']}", "kind": kind, "author": "notion:" + creator, "counterpart": None,
                        "assets": share, "title": title, "text": text[:2000], "url": p.get("url") or "",
                        "ts": t_edited if same else t_created, "channel": p.get("parent_title") or "Notion"})
        if editor in people and not same and t_edited < until:
            out.append({"id": f"no_{p['id']}_edit", "kind": "doc", "author": "notion:" + editor,
                        "counterpart": ("notion:" + creator) if creator in people else None,
                        "weight": EVIDENCE_WEIGHTS["doc"] * 0.5, "assets": share, "title": f"Edited: {title}",
                        "text": "", "url": p.get("url") or "", "ts": t_edited,
                        "channel": p.get("parent_title") or "Notion"})
    return out, st


def latest_ts(raw: dict) -> float:
    return max((_ts(p.get("last_edited_time")) for p in raw.get("pages", [])), default=0.0)


def summary(raw: dict) -> dict:
    return {"source": raw.get("source"), "workspace": raw.get("workspace", ""),
            "users": len([u for u in raw.get("users", []) if u.get("type") == "person"]),
            "pages": len(raw.get("pages", [])), "fetched": raw.get("fetched")}
