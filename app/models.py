"""Pydantic request/response schemas for the crawl API."""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl


class WaitUntil(str, Enum):
    load = "load"
    domcontentloaded = "domcontentloaded"
    networkidle = "networkidle"
    commit = "commit"


class CrawlRequest(BaseModel):
    url: HttpUrl = Field(..., description="The page to crawl.")
    wait_until: WaitUntil = Field(
        WaitUntil.domcontentloaded,
        description="Playwright load state to wait for before processing.",
    )
    wait_for_selector: Optional[str] = Field(
        None, description="Optional CSS selector to wait for after navigation."
    )
    wait_for_timeout_ms: int = Field(
        0, ge=0, le=60000, description="Extra fixed wait after load (ms)."
    )
    timeout_ms: Optional[int] = Field(
        None, ge=1000, le=120000, description="Navigation timeout override (ms)."
    )
    screenshot: bool = Field(False, description="Capture a full-page PNG (base64).")
    extract_links: bool = Field(True, description="Return all anchor links on the page.")
    extract_text: bool = Field(True, description="Return cleaned visible text.")
    return_html: bool = Field(True, description="Return the rendered HTML.")
    user_actions: List["UserAction"] = Field(
        default_factory=list,
        description="Optional sequence of human-like actions before extraction.",
    )


class ActionType(str, Enum):
    click = "click"
    type = "type"
    scroll = "scroll"
    wait = "wait"
    hover = "hover"


class UserAction(BaseModel):
    type: ActionType
    selector: Optional[str] = None
    text: Optional[str] = None
    value: Optional[int] = Field(
        None, description="Pixels for scroll, or milliseconds for wait."
    )


class LinkItem(BaseModel):
    text: str
    href: str


class CrawlResponse(BaseModel):
    url: str
    final_url: str
    status: Optional[int] = None
    title: Optional[str] = None
    meta: Dict[str, str] = Field(default_factory=dict)
    text: Optional[str] = None
    html: Optional[str] = None
    links: List[LinkItem] = Field(default_factory=list)
    screenshot_base64: Optional[str] = None
    elapsed_ms: int = 0


CrawlRequest.model_rebuild()
