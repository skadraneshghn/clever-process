"""FastAPI application: a stealth web-crawling API backed by Camoufox."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .browser import browser_manager
from .config import settings
from .routers import crawl, flows

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Launch the shared Camoufox browser once, before serving traffic.
    await browser_manager.start()
    try:
        yield
    finally:
        await browser_manager.stop()


app = FastAPI(
    title="Clever Process",
    description="Stealth web crawling & processing API powered by Camoufox.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(crawl.router)
app.include_router(flows.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {
        "status": "ok",
        "browser_ready": browser_manager.is_ready,
        "headless": settings.browser_headless,
    }


@app.get("/", tags=["health"])
async def root() -> dict:
    return {"service": "clever-process", "docs": "/docs"}
