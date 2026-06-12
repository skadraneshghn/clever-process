"""Site-specific scripted browser flows (human-like, multi-step interactions)."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any, Dict, List, Optional

from faker import Faker
from pydantic import BaseModel, Field

from .browser import browser_manager
from .config import settings

logger = logging.getLogger("app.flows")

XVPN_PRICING_URL = "https://xvpn.io/pricing"

# Single shared Faker instance for generating the cardholder's (female) name.
_fake = Faker()


class FlowStep(BaseModel):
    name: str
    ok: bool
    detail: str = ""
    elapsed_ms: int = 0


class XvpnCheckoutRequest(BaseModel):
    """Body for POST /api/v1/xvpn — the data filled into the checkout form.

    The first/last name are NOT taken from here: they are generated with Faker
    (a random woman's name) as requested.
    """

    email: str = Field(..., description="Email for the X-VPN account field.")
    cardnumber: str = Field(..., description="Card number (digits, spaces ok).")
    cardExpiry: str = Field(..., description="Expiry, e.g. '12 / 28' or '1228'.")
    cvc: str = Field(..., description="Card CVC / CVV.")


class ApiCall(BaseModel):
    """A single captured xvpn.io network call (request + response)."""

    method: str
    url: str
    status: Optional[int] = None
    response_body: Optional[Any] = None


class XvpnFlowResponse(BaseModel):
    url: str
    final_url: str
    title: Optional[str] = None
    generated_first_name: Optional[str] = None
    generated_last_name: Optional[str] = None
    steps: List[FlowStep] = []
    screenshot_base64: Optional[str] = None
    elapsed_ms: int = 0
    # All xvpn.io API calls captured after "Proceed to checkout" is clicked.
    xvpn_api_calls: List[ApiCall] = []


async def _click_first(page, candidates: List[str], timeout: int) -> str:
    """Click the first candidate locator that becomes visible.

    Tries each selector in order so the flow survives minor markup changes.
    `timeout` is the per-candidate budget — keep it short so a miss falls through
    to the next candidate quickly instead of stalling the whole flow.
    Returns the selector that matched, or raises if none did.
    """
    last_err: Optional[Exception] = None
    for selector in candidates:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=timeout)
            await locator.scroll_into_view_if_needed(timeout=timeout)
            await locator.click(timeout=timeout)
            return selector
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            last_err = exc
            logger.debug("Candidate %r failed: %s", selector, exc)
            continue
    raise RuntimeError(
        f"None of the candidate selectors were clickable: {candidates}"
    ) from last_err


async def _fill(locator, value: str, timeout: int) -> str:
    """Fill a plain (main-frame) input and report a non-sensitive detail."""
    await locator.wait_for(state="visible", timeout=timeout)
    await locator.scroll_into_view_if_needed(timeout=timeout)
    await locator.fill(value)
    return f"filled ({len(value)} chars)"


async def _type_secure(locator, value: str, timeout: int) -> str:
    """Type into a Stripe iframe input key-by-key so it validates/formats.

    Stripe's hosted fields ignore a bulk value set, so we click then type with a
    small per-key delay (also keeps it human-like). Card data is never logged.
    """
    await locator.wait_for(state="visible", timeout=timeout)
    await locator.click(timeout=timeout)
    await locator.press_sequentially(value, delay=60, timeout=timeout)
    return f"typed ({len(value)} chars)"


# How long (ms) after the last xvpn.io response finishes before we consider
# the network "idle" and stop collecting.
_API_IDLE_MS = 1500
# Hard ceiling (ms) we wait for the network idle after clicking Proceed.
_API_WAIT_CEILING_MS = 30_000


async def run_xvpn_flow(
    timeout_ms: Optional[int] = None,
    checkout: Optional[XvpnCheckoutRequest] = None,
) -> XvpnFlowResponse:
    """Open xvpn.io/pricing, click 'Get Premium' → 'Credit Card', fill checkout form.

    When `checkout` is provided (POST), also fill the checkout form, click
    'Proceed to checkout', then wait until ALL in-flight xvpn.io API calls
    have finished and return every captured call in `xvpn_api_calls`.
    """
    started = time.monotonic()
    timeout = timeout_ms or settings.browser_nav_timeout_ms
    # Per-candidate click budget: short so a wrong selector falls through fast
    # instead of stalling the whole flow on a 45s timeout.
    click_timeout = min(timeout, 12000)
    # xvpn keeps tracker/analytics sockets open, so 'networkidle' never truly
    # settles — cap the best-effort settle waits short instead of full timeout.
    settle_timeout = 6000
    steps: List[FlowStep] = []

    async def record(name: str, coro):
        t0 = time.monotonic()
        try:
            result = await coro
            steps.append(
                FlowStep(
                    name=name,
                    ok=True,
                    detail=str(result) if result is not None else "",
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )
            )
            return result
        except Exception as exc:  # noqa: BLE001
            steps.append(
                FlowStep(
                    name=name,
                    ok=False,
                    detail=f"{type(exc).__name__}: {exc}",
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )
            )
            raise

    # ── xvpn.io network tracker ──────────────────────────────────────────────
    # We record every request/response whose URL contains "xvpn.io".
    # _pending tracks in-flight requests so we know when the network is idle.
    _xvpn_calls: List[ApiCall] = []
    _pending: int = 0                   # in-flight xvpn.io requests
    _tracking_active: bool = False      # flip to True after Proceed is clicked
    _last_settled: float = 0.0          # monotonic time of last response
    _idle_event: asyncio.Event = asyncio.Event()

    context = await browser_manager.new_context()
    try:
        page = await context.new_page()
        page.set_default_timeout(timeout)

        # ── request listener ─────────────────────────────────────────────────
        def _on_request(request):
            nonlocal _pending, _last_settled
            if _tracking_active and "xvpn.io" in request.url:
                _pending += 1
                _idle_event.clear()
                logger.debug("[tracker] ++ %s %s  (pending=%d)", request.method, request.url, _pending)

        # ── response listener ─────────────────────────────────────────────────
        async def _on_response(response):
            nonlocal _pending, _last_settled
            # Only collect responses that happen AFTER "Proceed" is clicked.
            if not _tracking_active:
                return
            if "xvpn.io" not in response.url:
                return
            method = response.request.method
            url = response.url
            status = response.status
            try:
                body: Any = await response.json()
            except Exception:
                try:
                    body = await response.text()
                except Exception:
                    body = None
            call = ApiCall(method=method, url=url, status=status, response_body=body)
            _xvpn_calls.append(call)
            _pending = max(0, _pending - 1)
            _last_settled = time.monotonic()
            logger.debug("[tracker] -- %s %s  HTTP %s  (pending=%d)", method, url, status, _pending)
            if _pending == 0:
                _idle_event.set()

        page.on("request", _on_request)
        page.on("response", _on_response)

        # 1. Navigate to the pricing page.
        await record(
            "goto_pricing",
            page.goto(XVPN_PRICING_URL, wait_until="domcontentloaded", timeout=timeout),
        )
        # Let the SPA settle and look human before interacting.
        try:
            await page.wait_for_load_state("networkidle", timeout=settle_timeout)
        except Exception:  # networkidle can stall on ad/tracker pings; non-fatal
            pass
        await page.mouse.wheel(0, 400)
        await page.wait_for_timeout(800)

        # 2. Click the "Get Premium" button (real markup: div.premium-btn).
        await record(
            "click_get_premium",
            _click_first(
                page,
                [
                    "div.premium-btn",
                    ":text-is('Get Premium')",
                    "text=Get Premium",
                ],
                click_timeout,
            ),
        )
        await page.wait_for_timeout(1500)
        try:
            await page.wait_for_load_state("networkidle", timeout=settle_timeout)
        except Exception:
            pass

        # 3. Click the "Credit Card" payment row (appears after step 2 as
        #    div.row-bar > div.name, both reading "Credit Card").
        await record(
            "click_credit_card",
            _click_first(
                page,
                [
                    ".row-bar:has-text('Credit Card')",
                    ".name:text-is('Credit Card')",
                    ":text-is('Credit Card')",
                ],
                click_timeout,
            ),
        )

        # 4. (POST only) Fill the checkout form.
        generated_first: Optional[str] = None
        generated_last: Optional[str] = None
        if checkout is not None:
            generated_first = _fake.first_name_female()
            generated_last = _fake.last_name_female()

            # Email (main frame, identified by its placeholder).
            await record(
                "fill_email",
                _fill(
                    page.locator(
                        "input[placeholder='Enter the email for your X-VPN account']"
                    ).first,
                    checkout.email,
                    click_timeout,
                ),
            )
            # First / Last name — a random woman's name via Faker.
            await record(
                "fill_first_name",
                _fill(page.locator("input[name='FirstName']").first, generated_first, click_timeout),
            )
            await record(
                "fill_last_name",
                _fill(page.locator("input[name='LastName']").first, generated_last, click_timeout),
            )
            # Card fields live in three separate Stripe iframes (stable titles).
            await record(
                "fill_cardnumber",
                _type_secure(
                    page.frame_locator(
                        "iframe[title='Secure card number input frame']"
                    ).locator("input[name='cardnumber']"),
                    checkout.cardnumber,
                    click_timeout,
                ),
            )
            await record(
                "fill_cardExpiry",
                _type_secure(
                    page.frame_locator(
                        "iframe[title='Secure expiration date input frame']"
                    ).locator("input[name='exp-date']"),
                    checkout.cardExpiry,
                    click_timeout,
                ),
            )
            await record(
                "fill_cvc",
                _type_secure(
                    page.frame_locator(
                        "iframe[title='Secure CVC input frame']"
                    ).locator("input[name='cvc']"),
                    checkout.cvc,
                    click_timeout,
                ),
            )

        # 5. (POST only) Click "Proceed to secure checkout" then wait for ALL
        #    xvpn.io API calls to finish.
        if checkout is not None:
            # Small settle after filling all card fields before clicking submit.
            await page.wait_for_timeout(800)

            # Activate the network tracker right before we click so every
            # request triggered by the click is captured.
            # Reset _last_settled to NOW so the idle grace period is measured
            # from the click, not from time 0 (which would make it pass instantly).
            _tracking_active = True
            _last_settled = time.monotonic()
            _idle_event.clear()

            await record(
                "click_proceed_to_checkout",
                _click_first(
                    page,
                    [
                        # The actual xvpn checkout button is inside
                        # .expand-content > .btn-bar > .btn (desktop)
                        # or .btn-mobile (mobile) — not a semantic <button>.
                        ".expand-content .btn-bar .btn",
                        ".expand-content .btn-bar .btn-mobile",
                        ".btn-bar .btn",
                        ".btn-bar .btn-mobile",
                        # Text-based fallbacks in case markup changes:
                        ":text-is('Proceed to secure checkout')",
                        "text=Proceed to secure checkout",
                        ":text-is('Proceed to Secure Checkout')",
                        "text=Proceed to Secure Checkout",
                        "button:has-text('Proceed')",
                        "[type='submit']",
                    ],
                    click_timeout,
                ),
            )

            # Wait until the xvpn.io network goes idle:
            #   • idle = _pending == 0 AND at least _API_IDLE_MS ms have passed
            #     since the last response, OR the hard ceiling is reached.
            ceiling = _API_WAIT_CEILING_MS / 1000
            idle_grace = _API_IDLE_MS / 1000
            deadline = time.monotonic() + ceiling
            t0_wait = time.monotonic()

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.debug("[tracker] hard ceiling reached")
                    break
                if _pending == 0:
                    since_last = time.monotonic() - _last_settled
                    if since_last >= idle_grace:
                        logger.debug("[tracker] network idle (quiet for %.1fs)", since_last)
                        break
                    # Wait for the remainder of the grace period.
                    await asyncio.sleep(min(idle_grace - since_last, remaining))
                else:
                    # Wait for next response (or timeout).
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(_idle_event.wait()),
                            timeout=min(remaining, idle_grace),
                        )
                    except asyncio.TimeoutError:
                        pass

            api_wait_ms = int((time.monotonic() - t0_wait) * 1000)
            steps.append(
                FlowStep(
                    name="xvpn_api_calls_collected",
                    ok=True,
                    detail=(
                        f"Collected {len(_xvpn_calls)} xvpn.io call(s) "
                        f"in {api_wait_ms} ms"
                    ),
                    elapsed_ms=api_wait_ms,
                )
            )
        else:
            # GET-only flow: just wait 3 seconds as before.
            await record("wait_3s", page.wait_for_timeout(3000))

        title = await page.title()
        final_url = page.url
        png = await page.screenshot(full_page=False, type="png")

        return XvpnFlowResponse(
            url=XVPN_PRICING_URL,
            final_url=final_url,
            title=title,
            generated_first_name=generated_first,
            generated_last_name=generated_last,
            steps=steps,
            screenshot_base64=base64.b64encode(png).decode("ascii"),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            xvpn_api_calls=_xvpn_calls,
        )
    finally:
        await context.close()
        browser_manager.release()
