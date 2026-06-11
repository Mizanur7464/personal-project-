"""Crypto news from RSS feeds."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

COINDESK_RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/"


async def fetch_crypto_news(limit: int = 5) -> str:
    """Fetch latest headlines from CoinDesk RSS."""
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(COINDESK_RSS)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
    except Exception:
        return "Could not fetch news right now. Try again later."

    items = root.findall(".//item")[:limit]
    if not items:
        return "No news items found."

    lines = ["📰 Latest Crypto News (CoinDesk)\n"]
    for item in items:
        title_el = item.find("title")
        link_el = item.find("link")
        title = (title_el.text or "").strip() if title_el is not None else "?"
        link = (link_el.text or "").strip() if link_el is not None else ""
        lines.append(f"• {title}")
        if link:
            lines.append(f"  {link}")
        lines.append("")

    lines.append("⚠️ News is for information only — not trading advice.")
    return "\n".join(lines)
