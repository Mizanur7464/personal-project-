"""Weekly AI summary from trade journal entries."""
from __future__ import annotations

import os
from typing import Optional

import httpx

from core.db import list_trade_journal_entries_since
from services.ai_journal import ai_enabled


async def generate_weekly_summary(telegram_id: int) -> str:
    entries = list_trade_journal_entries_since(telegram_id, days=7, limit=30)
    if not entries:
        return "No journal entries in the last 7 days. Use /journal_add to log trades."

    lines = ["Last 7 days — journal entries:\n"]
    for e in entries:
        sym = e.get("symbol") or "-"
        side = (e.get("side") or "-").upper()
        notes = (e.get("notes") or "")[:120]
        lines.append(f"#{e['entry_id']} {sym} {side}: {notes}")
    summary_input = "\n".join(lines)

    if not ai_enabled():
        return (
            "📊 Weekly Journal Summary (no AI)\n\n"
            + summary_input
            + "\n\nSet OPENAI_API_KEY for AI weekly insights."
        )

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    prompt = (
        "You are a trading coach. Summarize this trader's last 7 days of journal entries.\n"
        "Cover: patterns, recurring mistakes, risk discipline, and 3 actionable improvements.\n"
        "No financial advice or price predictions. Keep under 15 lines.\n\n"
        f"{summary_input}"
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "input": prompt}

    async with httpx.AsyncClient(timeout=45) as client:
        try:
            resp = await client.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return "Could not generate AI weekly summary. Try again later."

    ai_text = _extract_response_text(data)
    if not ai_text:
        return "AI returned empty summary. Try again later."

    return f"📊 Weekly AI Summary\n\n{ai_text}"


def _extract_response_text(data: dict) -> Optional[str]:
    try:
        for item in data.get("output") or []:
            for c in item.get("content") or []:
                if c.get("type") == "output_text" and c.get("text"):
                    return str(c["text"]).strip()
    except Exception:
        return None
    return None
