"""Crawl API endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..crawler import crawl
from ..models import CrawlRequest, CrawlResponse

logger = logging.getLogger("app.routers.crawl")

router = APIRouter(prefix="/api/v1", tags=["crawl"])


@router.post("/crawl", response_model=CrawlResponse)
async def crawl_endpoint(req: CrawlRequest) -> CrawlResponse:
    try:
        return await crawl(req)
    except PlaywrightTimeout as exc:
        raise HTTPException(status_code=504, detail=f"Navigation timed out: {exc}") from exc
    except PlaywrightError as exc:
        raise HTTPException(status_code=502, detail=f"Browser error: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected crawl failure")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
