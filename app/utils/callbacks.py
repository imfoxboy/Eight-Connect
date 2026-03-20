from __future__ import annotations

from ..callbacks.rp_client import send_callback_to_rp


async def send_rp_callback(
    callback_url: str,
    status: str,
    currency: str,
    amount_minor: int,
    sign_key: str | None = None,
    merchant_private_key: str | None = None,
) -> None:
    """
    Backward-compatible wrapper used by provider-specific webhook handlers.

    The underlying RP callback client already selects the provider-specific
    signing secret from app settings based on provider name. The ``sign_key``
    argument is accepted for compatibility with older call sites but is not
    required here.
    """
    tx = {
        "callback_url": callback_url,
        "status": status,
        "currency": currency,
        "amount": amount_minor,
        "merchant_private_key": merchant_private_key,
        "provider": "iqono",
    }
    await send_callback_to_rp(tx)
