"""
HTTP client for IQONO Checkout API.

All Checkout endpoints use JSON (Content-Type: application/json).
"""

import httpx

try:
    from ..utils.logger import logger
except ImportError:  # pragma: no cover
    from utils.logger import logger


async def post_checkout_session(checkout_url: str, payload: dict) -> dict:
    """POST /api/v1/session — create a Checkout payment session."""
    url = f"{checkout_url.rstrip('/')}/api/v1/session"
    async with httpx.AsyncClient(timeout=30) as client:
        logger.info(f"IQONO Checkout session → {url}  order={payload.get('order', {}).get('number')}")
        resp = await client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        data = resp.json()
        if resp.status_code >= 400:
            logger.warning(f"IQONO Checkout session ← HTTP {resp.status_code}: {data}")
        else:
            logger.info(f"IQONO Checkout session ← redirect_url present={bool(data.get('redirect_url'))}")
        return data


async def post_payment_status(checkout_url: str, payload: dict) -> dict:
    """POST /api/v1/payment/status — query payment status."""
    url = f"{checkout_url.rstrip('/')}/api/v1/payment/status"
    async with httpx.AsyncClient(timeout=30) as client:
        logger.info(f"IQONO payment status → {url}  payment_id={payload.get('payment_id', payload.get('order_id'))}")
        resp = await client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        data = resp.json()
        logger.info(f"IQONO payment status ← status={data.get('status')}")
        return data
