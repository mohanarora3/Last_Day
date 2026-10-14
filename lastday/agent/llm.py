"""Optional LLM. Last Day works fully without one (deterministic, cited output);
with a key it writes friendlier answers and handover summaries.

ANTHROPIC_API_KEY            -> Anthropic Messages API (LLM_MODEL, default below)
OPENAI_API_KEY (+ OPENAI_BASE_URL) -> any OpenAI-compatible API (OpenAI, Groq, Together, Ollama ...)
"""
from __future__ import annotations

import json
import os
import urllib.request


def provider() -> str | None:
    if os.getenv("LASTDAY_NO_LLM"):
        return None
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return None


def complete(system: str, user: str, max_tokens: int = 700) -> str | None:
    p = provider()
    if not p:
        return None
    try:
        if p == "anthropic":
            body = {"model": os.getenv("LLM_MODEL", "claude-sonnet-4-5"), "max_tokens": max_tokens,
                    "system": system, "messages": [{"role": "user", "content": user}]}
            req = urllib.request.Request(
                os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/") + "/v1/messages",
                data=json.dumps(body).encode(),
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            return "".join(b.get("text", "") for b in data.get("content", [])).strip()
        body = {"model": os.getenv("LLM_MODEL", "gpt-4o-mini"), "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        req = urllib.request.Request(
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode(),
            headers={"authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                     "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:  # never let the LLM break the product
        print(f"[llm] {p} call failed: {e}")
        return None
