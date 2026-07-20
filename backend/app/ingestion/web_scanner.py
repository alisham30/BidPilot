"""Tender-portal scanner — no per-site parsers.

Fetches each configured URL, reduces the page to visible text plus labeled
links, and lets Claude extract listings against the PortalListings schema.
Adding a portal is one line of YAML in config/sources.yaml.
"""
from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from .. import llm
from ..config import sources
from ..dataset.registry import parse_iso_date, upsert_rfp
from ..schemas import PortalListings
from ..tracking.escalations import escalate
from .docparse import clean_text

log = logging.getLogger("bidpilot.web")

LISTING_SYSTEM = """You extract tender/RFP listings from a procurement portal page for a
wires & cables OEM. Only include listings plausibly related to these product
categories: {categories}.
Use only information visible in the page text. Resolve relative links against
the page URL when obvious; otherwise leave url empty. Never invent reference
numbers or dates; dates must be ISO YYYY-MM-DD or null."""


def _page_to_text(html: str, base_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    links = []
    for a in soup.find_all("a", href=True):
        label = a.get_text(" ", strip=True)
        if label:
            links.append(f"[{label}]({a['href']})")
    text = soup.get_text("\n", strip=True)
    return clean_text(f"PAGE URL: {base_url}\n\n{text}\n\nLINKS:\n" + "\n".join(links))


def scan_portals(session: Session) -> dict:
    stats = {"portals": 0, "listings": 0, "ingested": 0}
    urls = sources.web.urls
    if not urls:
        log.info("no portal URLs configured (config/sources.yaml → web.urls)")
        return stats

    for url in urls:
        stats["portals"] += 1
        try:
            resp = httpx.get(url, timeout=sources.web.request_timeout, follow_redirects=True,
                             headers={"User-Agent": "BidPilot/1.0 (tender monitoring)"})
            resp.raise_for_status()
        except Exception as e:
            escalate(session, "sales_agent", f"portal fetch failed for {url}: {e}")
            continue

        try:
            extracted = llm.extract(
                PortalListings,
                LISTING_SYSTEM.format(categories=", ".join(sources.filters.product_categories)),
                _page_to_text(resp.text, url),
            )
        except llm.LLMError as e:
            escalate(session, "sales_agent", f"listing extraction failed for {url}: {e}")
            continue

        for listing in extracted.listings:
            stats["listings"] += 1
            _, created = upsert_rfp(
                session,
                title=listing.title, issuer=listing.issuer, reference_no=listing.reference_no,
                due_date=parse_iso_date(listing.due_date), source="web", source_detail=url,
            )
            if created:
                stats["ingested"] += 1

    log.info("portal scan: %s", stats)
    return stats
