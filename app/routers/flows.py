"""Scripted site-flow endpoints (e.g. the X-VPN pricing walkthrough)."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from ..flows import XvpnCheckoutRequest, XvpnFlowResponse, run_xvpn_flow

logger = logging.getLogger("app.routers.flows")

router = APIRouter(prefix="/api/v1", tags=["flows"])


def _handle_error(exc: Exception):
    if isinstance(exc, PlaywrightTimeout):
        raise HTTPException(status_code=504, detail=f"Step timed out: {exc}") from exc
    if isinstance(exc, PlaywrightError):
        raise HTTPException(status_code=502, detail=f"Browser error: {exc}") from exc
    logger.exception("xvpn flow failed")
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/xvpn", response_model=XvpnFlowResponse)
async def xvpn_get(
    timeout_ms: int = Query(
        45000, ge=5000, le=120000, description="Per-step timeout in ms."
    )
) -> XvpnFlowResponse:
    """Open xvpn.io/pricing, click 'Get Premium' → 'Credit Card', wait 3s."""
    try:
        return await run_xvpn_flow(timeout_ms=timeout_ms)
    except Exception as exc:  # noqa: BLE001
        _handle_error(exc)


@router.post("/xvpn", response_model=XvpnFlowResponse)
async def xvpn_post(
    body: XvpnCheckoutRequest,
    timeout_ms: int = Query(
        45000, ge=5000, le=120000, description="Per-step timeout in ms."
    ),
) -> XvpnFlowResponse:
    """Run the flow, then fill the checkout form.

    Email + card details come from the JSON body; First/Last name are generated
    as a random woman's name via Faker.
    """
    try:
        return await run_xvpn_flow(timeout_ms=timeout_ms, checkout=body)
    except Exception as exc:  # noqa: BLE001
        _handle_error(exc)
