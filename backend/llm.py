"""Thin Groq client (OpenAI-compatible endpoint) using httpx. No extra SDK needed.

Returns None on any failure (no key, network, bad JSON) so callers fall back to the offline path.
"""
from __future__ import annotations

import json
import re

import httpx

from .config import GROQ_API_KEY, GROQ_MODEL
from .privacy import get_logger, mask_text

log = get_logger("llm")
URL = "https://api.groq.com/openai/v1/chat/completions"


def enabled() -> bool:
    return bool(GROQ_API_KEY)


def chat(prompt: str, *, json_mode: bool = False, max_tokens: int = 300) -> str | None:
    if not GROQ_API_KEY:
        return None
    body = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(URL, json=body, headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, timeout=12)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.warning("Groq call failed, using offline fallback: %s", mask_text(str(e)))
        return None


def chat_json(prompt: str) -> dict | None:
    raw = chat(prompt, json_mode=True)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else None
