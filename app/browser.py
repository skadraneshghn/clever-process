"""Manages a single long-lived Camoufox browser shared across requests.

Camoufox is a stealth Firefox build (Playwright under the hood) tuned so the
browser presents itself as a real, human resident device: realistic fingerprint,
geolocation/timezone/locale derived from the outbound IP, and humanized cursor
movement. We launch ONE browser for the whole process and hand out fresh,
isolated browser *contexts* per crawl so cookies/state never leak between jobs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from camoufox.async_api import AsyncCamoufox

from .config import settings

logger = logging.getLogger("app.browser")


class BrowserManager:
    def __init__(self) -> None:
        self._cm: Optional[AsyncCamoufox] = None
        self._browser = None
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(settings.browser_max_concurrency)

    async def start(self) -> None:
        """Launch the shared Camoufox browser once at app startup."""
        async with self._lock:
            if self._browser is not None:
                return
            kwargs = settings.camoufox_kwargs()
            logger.info("Launching Camoufox with: %s", kwargs)
            # AsyncCamoufox is an async context manager; enter it manually so the
            # browser stays alive for the lifetime of the FastAPI app.
            self._cm = AsyncCamoufox(**kwargs)
            self._browser = await self._cm.__aenter__()
            logger.info("Camoufox browser ready.")

    async def stop(self) -> None:
        """Cleanly shut down the browser on app shutdown."""
        async with self._lock:
            if self._cm is not None:
                try:
                    await self._cm.__aexit__(None, None, None)
                finally:
                    self._cm = None
                    self._browser = None
                    logger.info("Camoufox browser stopped.")

    @property
    def is_ready(self) -> bool:
        return self._browser is not None

    async def new_context(self):
        """Acquire a concurrency slot and open a fresh, isolated context.

        Returns a tuple of (context, release_callable). The caller MUST close the
        context and call release() when finished (use the `lease` helper below).
        """
        if self._browser is None:
            raise RuntimeError("Browser is not started")
        await self._semaphore.acquire()
        try:
            context = await self._browser.new_context()
            return context
        except Exception:
            self._semaphore.release()
            raise

    def release(self) -> None:
        self._semaphore.release()


browser_manager = BrowserManager()
