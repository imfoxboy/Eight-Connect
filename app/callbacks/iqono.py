import logging
from decimal import Decimal, ROUND_HALF_UP

from fastapi import Request
from fastapi.responses import PlainTextResponse

from app.db import get_mapping_by_token_any, upsert_mapping
from app.providers.iqono.common import map_iqono_to_rp, hash_callback
from app.settings import settings
from app.utils.callbacks import send_rp_callback

logger = logging.getLogger("app.callbacks.iqono")


async def handle_iqono_webhook(request: Request) -> PlainTextResponse:
    """
    Receive IQONO status callback (application/x-www-form-urlencoded).
    Validates MD5 hash, updates DB, forwards to RP callback URL.
    """
    form_data = dict(await request.form())

    order_id = form_data.get("order_id", "")
    trans_id = form_data.get("trans_id", "")
    result = form_data.get("result", "")
    status = form_data.get("status", "")
    amount_str = form_data.get("amount", "0")
    currency = form_data.get("currency", "")
    received_hash = form_data.get("hash", "")

    logger.info(
        f"IQONO webhook: order_id={order_id} trans_id={trans_id} "
        f"result={result} status={status} amount={amount_str} currency={currency}"
    )

    # Look up by order_id (=rp_token) first, fall back to trans_id
    mapping = await get_mapping_by_token_any(order_id) or await get_mapping_by_token_any(trans_id)
    if not mapping:
        logger.warning(f"IQONO webhook: no mapping found for order_id={order_id} trans_id={trans_id}")
        return PlainTextResponse("ERROR")

    # Validate callback hash
    auth_password = mapping.get("auth_password")
    if auth_password and received_hash:
        expected_hash = hash_callback(auth_password, status, trans_id, amount_str, order_id)
        if received_hash != expected_hash:
            logger.warning(
                f"IQONO webhook hash mismatch: order_id={order_id} trans_id={trans_id} "
                f"received={received_hash} expected={expected_hash}"
            )
            return PlainTextResponse("ERROR")
    elif not received_hash:
        logger.warning(f"IQONO webhook missing hash: order_id={order_id} trans_id={trans_id}")

    rp_token = mapping["rp_token"]
    callback_url = mapping["callback_url"]
    merchant_private_key = mapping["merchant_private_key"]

    # Update DB with latest trans_id and status
    await upsert_mapping(
        rp_token=rp_token,
        provider="iqono",
        callback_url=callback_url,
        provider_operation_id=trans_id or None,
        status=status,
    )

    rp_status = map_iqono_to_rp(result, status)

    try:
        amount_minor = int((Decimal(amount_str) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    except Exception:
        amount_minor = 0

    await send_rp_callback(
        callback_url=callback_url,
        status=rp_status,
        currency=currency,
        amount_minor=amount_minor,
        sign_key=settings.RP_CALLBACK_SIGNING_SECRET_IQONO or settings.RP_CALLBACK_SIGNING_SECRET,
        merchant_private_key=merchant_private_key,
    )

    return PlainTextResponse("OK")
