# -*- coding: utf-8 -*-
"""Optional GPT-5.6 Sol narrative on top of structured analysis JSON."""
from __future__ import annotations

import json
import os
import time
from typing import Any

from config import OPENAI_MODEL


def model_name() -> str:
    return os.environ.get("OPENAI_MODEL", OPENAI_MODEL)


def is_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _extract_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None) or ""
    if not text and getattr(resp, "output", None):
        parts: list[str] = []
        for item in resp.output:
            for c in getattr(item, "content", []) or []:
                t = getattr(c, "text", None)
                if t:
                    parts.append(t)
        text = "\n".join(parts)
    return text.strip()


def _usage(resp: Any) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    keys = ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")
    out: dict = {}
    for k in keys:
        v = getattr(u, k, None)
        if v is None and isinstance(u, dict):
            v = u.get(k)
        if v is not None:
            out[k] = v
    return out


def narrate(project_id: str, analysis: dict[str, Any]) -> dict:
    """Generate a narrative using the server-side key only."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = model_name()
    if not key:
        return {
            "ok": False,
            "error": "OPENAI_API_KEY is not configured on the server.",
            "model": model,
        }

    try:
        from openai import OpenAI
    except ImportError:
        return {
            "ok": False,
            "error": "openai package not installed. pip install openai",
            "model": model,
        }

    # Keep prompt compact — structured metrics already computed locally
    compact = analysis
    if len(json.dumps(analysis, ensure_ascii=False)) > 12000:
        compact = {
            k: analysis[k]
            for k in analysis
            if k
            in (
                "summary",
                "bots",
                "one_liner",
                "trade_notes",
                "n_fills",
                "n_buys",
                "n_sells",
                "positions",
            )
        }

    system = (
        "You are a concise Korean trading-ops assistant. "
        "Given structured live-bot log analysis JSON, write a short status briefing. "
        "Use 4-8 bullet points max, then one-line 한줄 결론. "
        "Do not invent numbers; only use values present in the JSON. "
        "Flag risks (errors, heat skips, MDD, ghost adjustments) clearly."
    )
    user = (
        f"project={project_id}\n"
        f"analysis_json=\n{json.dumps(compact, ensure_ascii=False, indent=2)}"
    )

    client = OpenAI(api_key=key)
    t0 = time.perf_counter()
    try:
        resp = client.responses.create(
            model=model,
            reasoning={"effort": "low"},
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        duration_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": True,
            "model": model,
            "narrative": _extract_text(resp),
            "duration_ms": duration_ms,
            "usage": _usage(resp),
            "response_id": getattr(resp, "id", None),
        }
    except Exception as e:  # noqa: BLE001 — surface API errors to UI
        return {
            "ok": False,
            "model": model,
            "error": str(e),
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }
