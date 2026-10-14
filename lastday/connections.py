"""Slack / Notion connection settings, tokens and synced data, kept on disk in data/connections/.

That folder is private (it is in .gitignore): it holds tokens and copies of Slack messages and Notion pages.

  settings.json     company name, GitHub repo, Slack mode, channel -> area map, people links. Safe to show in the UI.
  secrets.json      slack_token, notion_token. Never sent to the browser.
  slack_export.zip  the uploaded Slack export
  slack_raw.json    Slack data in slack.py's raw format (from the export or the API)
  notion_raw.json   Notion data in notion.py's raw format

Tokens can also come from the environment (.env): SLACK_BOT_TOKEN and NOTION_TOKEN. A token saved from the UI wins.
"""
from __future__ import annotations

import copy
import json
import os
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = Path(os.getenv("LASTDAY_CONNECTIONS", ROOT / "data" / "connections"))
ENV_TOKENS = {"slack_token": "SLACK_BOT_TOKEN", "notion_token": "NOTION_TOKEN"}

DEFAULTS = {
    "name": "",
    "github_repo": "",
    "slack": {"mode": "", "workspace_url": "", "synced": None, "summary": None},
    "notion": {"synced": None, "summary": None},
    "channel_map": {},   # slack channel name -> [asset ids]
    "links": {},         # account key -> account key it belongs with, e.g. "slack:U03" -> "github:carol"
    "separate": [],      # account keys that must not be matched automatically
    "graph": "",
    "built": None,
}


def path(name: str) -> Path:
    return DIR / name


def _read(name: str, default):
    p = path(name)
    if not p.exists():
        return copy.deepcopy(default)
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return copy.deepcopy(default)


def _write(name: str, data) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    p = path(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def load_settings() -> dict:
    s = _read("settings.json", {})
    out = copy.deepcopy(DEFAULTS)
    for k, v in s.items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def save_settings(s: dict) -> dict:
    _write("settings.json", s)
    return s


def update_settings(**patch) -> dict:
    s = load_settings()
    for k, v in patch.items():
        if isinstance(s.get(k), dict) and isinstance(v, dict):
            s[k].update(v)
        else:
            s[k] = v
    return save_settings(s)


def secret(name: str) -> str:
    return _read("secrets.json", {}).get(name) or os.getenv(ENV_TOKENS.get(name, ""), "") or ""


def set_secret(name: str, value: str | None) -> None:
    sec = _read("secrets.json", {})
    if value:
        sec[name] = value
    else:
        sec.pop(name, None)
    _write("secrets.json", sec)


def read_raw(source: str) -> dict | None:
    p = path(f"{source}_raw.json")
    if not p.exists():
        return None
    return json.loads(p.read_text())


def write_raw(source: str, raw: dict) -> None:
    _write(f"{source}_raw.json", raw)


def clear(source: str) -> None:
    for name in (f"{source}_raw.json", "slack_export.zip" if source == "slack" else ""):
        if name and path(name).exists():
            path(name).unlink()
    set_secret(f"{source}_token", None)
    update_settings(**{source: copy.deepcopy(DEFAULTS[source])})


def slack_domain(workspace_url: str) -> str:
    """'https://acme.slack.com' or 'acme.slack.com' or 'acme' -> 'acme'."""
    url = (workspace_url or "").strip()
    if not url:
        return ""
    host = urllib.parse.urlparse(url if "//" in url else "https://" + url).hostname or ""
    return host.split(".")[0] if host else ""


def public_state() -> dict:
    """Everything the Connections panel shows. Tokens are reported only as set / not set."""
    s = load_settings()
    s["slack"]["token_set"] = bool(secret("slack_token"))
    s["slack"]["export_uploaded"] = path("slack_export.zip").exists()
    s["slack"]["connected"] = path("slack_raw.json").exists()
    s["notion"]["token_set"] = bool(secret("notion_token"))
    s["notion"]["connected"] = path("notion_raw.json").exists()
    s["github_token"] = bool(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN"))
    return s
