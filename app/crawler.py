"""Core crawl + process pipeline: navigate, optionally act, then extract."""
from __future__ import annotations

import base64
import logging
import time
from typing import Dict, List

from bs4 import BeautifulSoup

from .browser import browser_manager
from .config import settings
from .models import (
    ActionType,
    CrawlRequest,
    CrawlResponse,
    LinkItem,
    UserAction,
)

logger = logging.getLogger("app.crawler")


async def _perform_actions(page, actions: List[UserAction]) -> None:
    """Run a sequence of human-like interactions before extraction."""
    for action in actions:
        if action.type == ActionType.click and action.selector:
            await page.click(action.selector)
        elif action.type == ActionType.type and action.selector:
            # type() emits per-key events; Camoufox humanizes the cursor too.
            await page.fill(action.selector, action.text or "")
        elif action.type == ActionType.hover and action.selector:
            await page.hover(action.selector)
        elif action.type == ActionType.scroll:
            await page.mouse.wheel(0, action.value or 800)
        elif action.type == ActionType.wait:
            await page.wait_for_timeout(action.value or 1000)


def _extract_meta(soup: BeautifulSoup) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = tag.get("name") or tag.get("property")
        content = tag.get("content")
        if key and content:
            meta[key] = content
    return meta


def _extract_text(soup: BeautifulSoup) -> str:
    for bad in soup(["script", "style", "noscript", "template"]):
        bad.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _extract_links(soup: BeautifulSoup, base_url: str) -> List[LinkItem]:
    from urllib.parse import urljoin

    links: List[LinkItem] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        if href in seen:
            continue
        seen.add(href)
        links.append(LinkItem(text=a.get_text(strip=True), href=href))
    return links


async def crawl(req: CrawlRequest) -> CrawlResponse:
    started = time.monotonic()
    url = str(req.url)
    timeout = req.timeout_ms or settings.browser_nav_timeout_ms

    context = await browser_manager.new_context()
    try:
        page = await context.new_page()
        page.set_default_timeout(timeout)

        response = await page.goto(url, wait_until=req.wait_until.value, timeout=timeout)

        if req.wait_for_selector:
            await page.wait_for_selector(req.wait_for_selector, timeout=timeout)
        if req.wait_for_timeout_ms:
            await page.wait_for_timeout(req.wait_for_timeout_ms)
        if req.user_actions:
            await _perform_actions(page, req.user_actions)

        html = await page.content()
        final_url = page.url
        title = await page.title()

        soup = BeautifulSoup(html, "lxml")

        result = CrawlResponse(
            url=url,
            final_url=final_url,
            status=response.status if response else None,
            title=title,
            meta=_extract_meta(soup),
            text=_extract_text(soup) if req.extract_text else None,
            html=html if req.return_html else None,
            links=_extract_links(soup, final_url) if req.extract_links else [],
        )

        if req.screenshot:
            png = await page.screenshot(full_page=True, type="png")
            result.screenshot_base64 = base64.b64encode(png).decode("ascii")

        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        return result
    finally:
        await context.close()
        browser_manager.release()
