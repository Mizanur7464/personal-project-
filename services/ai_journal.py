from __future__ import annotations

import os
from typing import Optional

import httpx


def ai_enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


async def generate_trade_feedback(*, notes: str, symbol: Optional[str] = None, side: Optional[str] = None) -> Optional[str]:
    """
    Optional AI helper. If OPENAI_API_KEY is not set, returns None.
    Uses OpenAI Responses API (HTTP) to generate short, practical feedback.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    prompt = (
        "You are a trading journal coach. Provide concise, practical feedback on the trade notes.\n"
        "Focus on: setup clarity, risk, entry/exit plan, mistakes, and one actionable improvement.\n"
        "Avoid financial advice and predictions. Keep it under 10 bullet lines.\n\n"
        f"Symbol: {symbol or '-'}\n"
        f"Side: {side or '-'}\n"
        f"Notes:\n{notes}\n"
    )

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "input": prompt,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post("https://api.openai.com/v1/responses", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

    # Try to extract text from response content
    try:
        output = data.get("output") or []
        for item in output:
            content = item.get("content") or []
            for c in content:
                if c.get("type") == "output_text" and c.get("text"):
                    return str(c["text"]).strip()
    except Exception:
        return None
    return None

