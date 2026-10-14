"""Slack connector: who answers whose questions, from thread replies.

Two ways in, both producing the same "raw" dict:

  * A Slack workspace export: the ZIP a workspace admin downloads from
    Settings & administration > Workspace settings > Import/Export Data (or the unzipped folder).
    It has users.json, channels.json and one folder per public channel with one JSON file per day.
    No app, no token, no rate limits. Only public channels are read.

  * The Web API with a bot token (SLACK_BOT_TOKEN, starts with xoxb-) of an app installed in the workspace,
    with the scopes channels:read, channels:history, users:read and users:read.email
    (add channels:join and the bot joins public channels by itself; otherwise invite it to each channel).
    Create it as an internal app for your own workspace: since May 2025, apps distributed to other workspaces
    without Slack Marketplace approval may only read 15 messages per minute.

raw = {"source": "export" | "api", "team": {"id", "name", "domain"}, "fetched": ts,
       "users":    [{"id", "name", "real_name", "display_name", "email", "deleted", "is_bot", "title", "avatar"}],
       "channels": [{"id", "name", "purpose", "is_archived", "skipped",
                     "messages": [{"user", "text", "ts", "thread_ts", "subtype", "bot_id"}]}]}

`evidence()` turns raw into normalized evidence: every reply inside a thread, written by someone other than the
person who started the thread, is an answer (kind `slack_answer`) with the thread starter as the counterpart.
"""
from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

API = os.getenv("SLACK_API_BASE", "https://slack.com/api").rstrip("/")
DAY = 86400.0

SKIP_SUBTYPES = {"channel_join", "channel_leave", "channel_topic", "channel_purpose", "channel_name",
                 "channel_archive", "channel_unarchive", "bot_message", "pinned_item", "unpinned_item",
                 "group_join", "group_leave", "reminder_add", "tombstone", "joiner_notification_for_inviter"}
# Replies that carry no knowledge.
NOISE = re.compile(r"^\W*(thanks?( you)?|thx|ty|tysm|\+1|lgtm|ok(ay)?|nice|cool|great|done|same|yes|no|yep|nope|"
                   r"sure|got it|on it|agreed?|looking|will do|np|:[\w+-]+:)\W*$", re.I)


class SlackError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------------
# Export (ZIP or folder)
# --------------------------------------------------------------------------------------------------

class _Reader:
    """Reads files from a ZIP or a folder, whichever the export is, wherever users.json sits inside it."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.is_dir():
            hits = sorted(self.path.rglob("users.json"), key=lambda p: len(p.parts))
            if not hits:
                raise SlackError("This folder is not a Slack export: users.json is missing.")
            self.root, self.zip = hits[0].parent, None
            self.names = [str(p.relative_to(self.root)).replace(os.sep, "/") for p in self.root.rglob("*.json")]
        else:
            try:
                self.zip = zipfile.ZipFile(self.path)
            except zipfile.BadZipFile:
                raise SlackError("This file is not a ZIP. Upload the .zip Slack gives you when you export.")
            hits = sorted((n for n in self.zip.namelist() if n.endswith("users.json")), key=lambda n: n.count("/"))
            if not hits:
                raise SlackError("This ZIP is not a Slack export: users.json is missing.")
            self.prefix = hits[0][: -len("users.json")]
            self.names = [n[len(self.prefix):] for n in self.zip.namelist()
                          if n.startswith(self.prefix) and n.endswith(".json")]

    def read(self, name: str):
        try:
            data = (self.root / name).read_bytes() if self.zip is None else self.zip.read(self.prefix + name)
        except (KeyError, FileNotFoundError):
            return None
        return json.loads(data.decode("utf-8"))


def _user(u: dict) -> dict:
    p = u.get("profile") or {}
    return {"id": u["id"], "name": u.get("name") or "",
            "real_name": u.get("real_name") or p.get("real_name") or "",
            "display_name": p.get("display_name") or "", "email": (p.get("email") or "").lower(),
            "deleted": bool(u.get("deleted")),
            "is_bot": bool(u.get("is_bot") or u.get("is_app_user")) or u["id"] == "USLACKBOT",
            "title": p.get("title") or "", "avatar": p.get("image_72") or ""}


def _msg(m: dict) -> dict:
    return {"user": m.get("user") or "", "text": m.get("text") or "", "ts": m.get("ts") or "",
            "thread_ts": m.get("thread_ts") or "", "subtype": m.get("subtype") or "", "bot_id": m.get("bot_id") or ""}


def load_export(path: str | Path, team_name: str = "", domain: str = "") -> dict:
    """Read a Slack export (ZIP or folder) into the raw format."""
    r = _Reader(path)
    users = [_user(u) for u in (r.read("users.json") or [])]
    channels = []
    for ch in r.read("channels.json") or []:
        name = ch.get("name") or ""
        days = sorted(n for n in r.names if n.startswith(name + "/") and n.count("/") == 1)
        msgs = []
        for day in days:
            for m in r.read(day) or []:
                if m.get("ts"):
                    msgs.append(_msg(m))
        channels.append({"id": ch.get("id") or name, "name": name,
                         "purpose": ((ch.get("purpose") or {}).get("value") or "")[:200],
                         "is_archived": bool(ch.get("is_archived")), "skipped": "", "messages": msgs})
    if not users or not channels:
        raise SlackError("The export has no users or no public channels.")
    return {"source": "export", "team": {"id": "", "name": team_name or Path(path).stem, "domain": domain},
            "fetched": time.time(), "users": users, "channels": channels}


# --------------------------------------------------------------------------------------------------
# Web API
# --------------------------------------------------------------------------------------------------

def _call(method: str, token: str, params: dict | None = None, retries: int = 6) -> dict:
    url = f"{API}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"authorization": f"Bearer {token}",
                                                   "user-agent": "last-day-hackathon"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(float(e.headers.get("Retry-After") or 5))
                continue
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(2 + attempt * 3)
                continue
            raise SlackError(f"Slack API {method} failed with HTTP {e.code}")
        if not data.get("ok"):
            if data.get("error") == "ratelimited" and attempt < retries - 1:
                time.sleep(5)
                continue
            raise SlackError(data.get("error") or "unknown_error")
        return data
    raise SlackError(f"Slack API {method} kept failing")


def _pages(method: str, token: str, key: str, params: dict, limit_items: int = 100000):
    cursor = None
    got = 0
    while True:
        data = _call(method, token, {**params, "cursor": cursor})
        items = data.get(key) or []
        got += len(items)
        yield from items
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor or got >= limit_items:
            return


def fetch_api(token: str, since_days: int = 730, max_messages_per_channel: int = 5000,
              progress: Callable[[str], None] | None = None) -> dict:
    """Read users, public channels, their history and every thread's replies with a bot token."""
    progress = progress or (lambda m: None)
    me = _call("auth.test", token)
    domain = urllib.parse.urlparse(me.get("url") or "").hostname or ""
    domain = domain.split(".")[0] if domain.endswith(".slack.com") else ""
    progress(f"connected to Slack workspace {me.get('team')}")
    users = [_user(u) for u in _pages("users.list", token, "members", {"limit": 200})]
    progress(f"{len(users)} Slack users")
    oldest = time.time() - since_days * DAY
    channels = []
    chans = list(_pages("conversations.list", token, "channels",
                        {"limit": 200, "types": "public_channel", "exclude_archived": "false"}))
    for i, ch in enumerate(chans):
        entry = {"id": ch["id"], "name": ch.get("name") or ch["id"],
                 "purpose": ((ch.get("purpose") or {}).get("value") or "")[:200],
                 "is_archived": bool(ch.get("is_archived")), "skipped": "", "messages": []}
        channels.append(entry)
        if not ch.get("is_member"):
            try:
                _call("conversations.join", token, {"channel": ch["id"]})
            except SlackError as e:
                entry["skipped"] = f"bot is not in this channel ({e}); invite it with /invite"
                progress(f"#{entry['name']}: skipped, the bot is not a member")
                continue
        try:
            top = list(_pages("conversations.history", token, "messages",
                              {"channel": ch["id"], "limit": 200, "oldest": f"{oldest:.6f}"},
                              limit_items=max_messages_per_channel))
        except SlackError as e:
            entry["skipped"] = str(e)
            progress(f"#{entry['name']}: skipped ({e})")
            continue
        msgs = [_msg(m) for m in top]
        for m in top:
            if m.get("reply_count") and m.get("thread_ts"):
                try:
                    for r in _pages("conversations.replies", token, "messages",
                                    {"channel": ch["id"], "ts": m["thread_ts"], "limit": 200}):
                        if r.get("ts") != r.get("thread_ts"):  # the parent is already in history
                            msgs.append(_msg(r))
                except SlackError:
                    pass
        entry["messages"] = msgs
        progress(f"#{entry['name']}: {len(msgs)} messages ({i + 1}/{len(chans)} channels)")
    return {"source": "api", "team": {"id": me.get("team_id") or "", "name": me.get("team") or "",
                                      "domain": domain},
            "fetched": time.time(), "users": users, "channels": channels}


# --------------------------------------------------------------------------------------------------
# Raw -> identities and evidence
# --------------------------------------------------------------------------------------------------

def identities(raw: dict) -> list[dict]:
    """One entry per human Slack account, for matching people across tools."""
    out = []
    for u in raw.get("users", []):
        if u.get("is_bot"):
            continue
        out.append({"key": "slack:" + u["id"], "source": "slack", "id": u["id"],
                    "name": u.get("real_name") or u.get("display_name") or u.get("name") or u["id"],
                    "handles": list(dict.fromkeys(h.lower() for h in (u.get("name"), u.get("display_name")) if h)),
                    "email": u.get("email") or "", "deleted": bool(u.get("deleted")),
                    "avatar": u.get("avatar") or "", "title": u.get("title") or ""})
    return out


def clean_text(text: str, names: dict[str, str]) -> str:
    """Turn Slack markup into plain text: <@U1> -> @Priya, <#C1|payments> -> #payments, <url|label> -> label."""
    def link(m):
        body = m.group(1)
        if body.startswith("@"):
            uid = body[1:].split("|")[0]
            return "@" + names.get(uid, body.split("|")[-1] if "|" in body else uid)
        if body.startswith("#"):
            return "#" + (body.split("|")[1] if "|" in body else body[1:])
        if body.startswith("!"):
            return "@" + body[1:].split("|")[0].split("^")[0]
        return body.split("|")[1] if "|" in body else body
    text = re.sub(r"<([^<>]+)>", link, text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def permalink(team: dict, channel_id: str, ts: str, thread_ts: str = "") -> str:
    if not team.get("domain") or not channel_id or not ts:
        return ""
    url = f"https://{team['domain']}.slack.com/archives/{channel_id}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        url += f"?thread_ts={thread_ts}&cid={channel_id}"
    return url


def evidence(raw: dict, match: Callable[[str], list[str]], channel_assets: dict[str, list[str]] | None = None,
             until: float | None = None) -> tuple[list[dict], list[dict]]:
    """Thread replies -> slack_answer evidence. Returns (evidence, per-channel stats).

    `match(text)` returns the asset ids a text is about. `channel_assets` maps a channel name to asset ids that
    every answer in that channel is about (set in the Connections panel)."""
    channel_assets = channel_assets or {}
    until = until or 1e20
    humans = {u["id"]: u for u in raw.get("users", []) if not u.get("is_bot")}
    names = {u["id"]: (u.get("display_name") or u.get("real_name") or u.get("name") or u["id"])
             for u in raw.get("users", [])}
    out, stats = [], []
    for ch in raw.get("channels", []):
        msgs = [m for m in ch.get("messages", []) if m.get("ts")]
        by_ts = {m["ts"]: m for m in msgs}
        fixed = [a for a in channel_assets.get(ch["name"], []) if a]
        st = {"channel": ch["name"], "id": ch["id"], "purpose": ch.get("purpose", ""), "skipped": ch.get("skipped", ""),
              "messages": len(msgs), "threads": 0, "answers": 0, "linked": 0, "mapped": fixed, "areas": {}}
        threads: dict[tuple[str, str], list[dict]] = {}
        for m in msgs:
            if m["ts"] == m.get("thread_ts") and m["ts"] in by_ts:
                st["threads"] += 1
            t = m.get("thread_ts")
            if not t or t == m["ts"]:
                continue
            parent = by_ts.get(t)
            user = m.get("user")
            if (not parent or user not in humans or m.get("bot_id") or m.get("subtype") in SKIP_SUBTYPES
                    or user == parent.get("user") or float(m["ts"]) >= until):
                continue
            threads.setdefault((t, user), []).append(m)
        for (t, user), replies in threads.items():
            parent = by_ts[t]
            reply = " / ".join(clean_text(r["text"], names) for r in sorted(replies, key=lambda r: float(r["ts"])))
            if len(reply) < 12 or NOISE.match(reply):
                continue
            st["answers"] += 1
            question = clean_text(parent.get("text", ""), names)
            assets = list(dict.fromkeys(fixed + match(question + " " + reply)))
            if not assets:
                continue
            st["linked"] += 1
            for a in assets:
                st["areas"][a] = st["areas"].get(a, 0) + 1
            first = min(replies, key=lambda r: float(r["ts"]))
            asker = parent.get("user")
            out.append({
                "id": f"sl_{ch['id']}_{first['ts']}_{user}", "kind": "slack_answer", "author": "slack:" + user,
                "counterpart": ("slack:" + asker) if asker in humans else None,
                "assets": [[a, round(1.0 / len(assets), 3)] for a in assets],
                "title": f"#{ch['name']}: {question[:140] or 'thread'}", "text": reply[:600],
                "url": permalink(raw.get("team") or {}, ch["id"], first["ts"], t),
                "ts": float(first["ts"]), "channel": "#" + ch["name"],
            })
        stats.append(st)
    return out, stats


def latest_ts(raw: dict) -> float:
    return max((float(m["ts"]) for ch in raw.get("channels", []) for m in ch.get("messages", []) if m.get("ts")),
               default=0.0)


def summary(raw: dict) -> dict:
    chans = raw.get("channels", [])
    return {"source": raw.get("source"), "workspace": (raw.get("team") or {}).get("name", ""),
            "users": len([u for u in raw.get("users", []) if not u.get("is_bot")]),
            "channels": len(chans), "skipped": len([c for c in chans if c.get("skipped")]),
            "messages": sum(len(c.get("messages", [])) for c in chans), "fetched": raw.get("fetched")}
