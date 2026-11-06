"""Thin Groq client (OpenAI-compatible endpoint) using httpx. No extra SDK needed.

Returns None on any failure (no key, network, empty answer, bad JSON) so callers fall back to the offline path.

Reasoning models (e.g. openai/gpt-oss-*) spend part of `max_tokens` on hidden reasoning. With a small budget the
visible answer can come back EMPTY, so for those models we ask for low reasoning effort and a larger budget, and we
fall back to GROQ_FALLBACK_MODEL if the answer is still empty.
"""
from __future__ import annotations

import json
import re

import httpx

from . import config
from .privacy import get_logger, mask_text

log = get_logger("llm")
URL = "https://api.groq.com/openai/v1/chat/completions"


def enabled() -> bool:
    return bool(config.GROQ_API_KEY)


def _call(model: str, prompt: str, json_mode: bool, max_tokens: int) -> str | None:
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if "gpt-oss" in model:                       # reasoning model: keep thinking short, leave room for the answer
        body["reasoning_effort"] = "low"
        body["max_tokens"] = max(max_tokens, 1500)
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        r = httpx.post(URL, json=body, headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}, timeout=15)
        r.raise_for_status()
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
        if not text:
            log.warning("Groq model %s returned an empty answer", model)
        return text or None
    except Exception as e:
        log.warning("Groq call to %s failed: %s", model, mask_text(str(e)))
        return None


def chat(prompt: str, *, json_mode: bool = False, max_tokens: int = 300) -> str | None:
    if not config.GROQ_API_KEY:
        return None
    models = [config.GROQ_MODEL]
    if config.GROQ_FALLBACK_MODEL and config.GROQ_FALLBACK_MODEL != config.GROQ_MODEL:
        models.append(config.GROQ_FALLBACK_MODEL)
    for m in models:
        out = _call(m, prompt, json_mode, max_tokens)
        if out:
            return out
    log.warning("All Groq models failed; using offline fallback")
    return None


def chat_json(prompt: str) -> dict | None:
    raw = chat(prompt, json_mode=True)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            return json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            return None