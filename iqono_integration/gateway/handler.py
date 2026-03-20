"""
IQONO Checkout — request handlers.

Flow:
  1. /pay      → POST /api/v1/session        → get redirect_url → return to RP
  2. /callback → IQONO webhook               → validate hash    → forward to RP via JWT
  3. /status   → POST /api/v1/payment/status → return mapped status
"""

import base64
import os
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import httpx
import jwt
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

try:
    from ..client.http import post_checkout_session, post_payment_status
    from ..config import BASE_URL, CHECKOUT_URL, MERCHANT_KEY, MERCHANT_PASS, SIGN_KEY
    from .builder import (
        build_checkout_session_payload,
        build_status_payload,
        hash_callback,
        map_to_rp,
        parse_credentials,
    )
    from ..schemas.payment import PayRequest
    from ..schemas.status import StatusRequest
    from ..utils.db import get_mapping, upsert_mapping
    from ..utils.logger import logger
except ImportError:  # pragma: no cover
    from client.http import post_checkout_session, post_payment_status
    from config import BASE_URL, CHECKOUT_URL, MERCHANT_KEY, MERCHANT_PASS, SIGN_KEY
    from gateway.builder import (
        build_checkout_session_payload,
        build_status_payload,
        hash_callback,
        map_to_rp,
        parse_credentials,
    )
    from schemas.payment import PayRequest
    from schemas.status import StatusRequest
    from utils.db import get_mapping, upsert_mapping
    from utils.logger import logger


# ---------------------------------------------------------------------------
# RP callback helpers
# ---------------------------------------------------------------------------

def _encrypt_aes256cbc(data: str, key: str) -> tuple[str, str]:
    key_bytes = key.encode("utf-8").ljust(32, b"\0")[:32]
    iv = os.urandom(16)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(pad(data.encode("utf-8"), AES.block_size))
    return base64.b64encode(encrypted).decode(), base64.b64encode(iv).decode()


def _build_rp_jwt(
    status: str,
    currency: str,
    amount_minor: int,
    sign_key: str,
    merchant_private_key: Optional[str] = None,
) -> str:
    payload: dict = {
        "status": status,
        "currency": currency,
        "amount": amount_minor,
    }

    if merchant_private_key:
        enc, iv = _encrypt_aes256cbc(merchant_private_key, sign_key)
        payload["secure"] = {
            "encrypted_data": enc,
            "iv_value": iv,
        }

    return jwt.encode(payload, sign_key, algorithm="HS512")


async def _send_rp_callback(
    callback_url: str,
    status: str,
    currency: str,
    amount_minor: int,
    sign_key: str,
    merchant_private_key: Optional[str] = None,
) -> None:
    if not callback_url:
        logger.warning("RP callback skipped: callback_url is empty")
        return

    token = _build_rp_jwt(
        status=status,
        currency=currency,
        amount_minor=amount_minor,
        sign_key=sign_key,
        merchant_private_key=merchant_private_key,
    )

    body = {
        "status": status,
        "currency": currency,
        "amount": amount_minor,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(
                callback_url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            logger.info("RP callback → %s HTTP %s", callback_url, resp.status_code)
        except Exception as exc:
            logger.error("RP callback failed [%s]: %s", callback_url, exc)


# ---------------------------------------------------------------------------
# /pay helpers
# ---------------------------------------------------------------------------

def _error_pay_response(logs: list[str]) -> dict:
    return {
        "status": "ERROR",
        "gateway_token": None,
        "result": "declined",
        "requisites": {},
        "redirectRequest": {
            "url": None,
            "type": "redirect",
            "iframes": [],
        },
        "provider_response_data": {},
        "logs": logs,
    }


def _safe_amount_major(amount_minor: int) -> Decimal:
    return (Decimal(amount_minor) / Decimal("100")).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# /pay — Checkout session creation
# ---------------------------------------------------------------------------

async def handle_pay(req: PayRequest) -> dict:
    try:
        auth_token = req.settings.auth_token or f"{MERCHANT_KEY}:{MERCHANT_PASS}"
        merchant_key, password = parse_credentials(auth_token)
    except ValueError as exc:
        return _error_pay_response([str(exc)])

    rp_token = req.payment.token
    order_number = rp_token
    order_amount = _safe_amount_major(req.payment.gateway_amount)
    order_currency = req.payment.gateway_currency
    order_description = req.payment.product or f"Payment {rp_token}"

    success_url = req.payment.redirect_success_url or f"{BASE_URL}/payment/success"
    cancel_url = req.payment.redirect_fail_url or f"{BASE_URL}/payment/fail"

    customer = req.params.customer if req.params else None

    customer_name = None
    customer_email = None

    if customer:
        name_parts = [customer.first_name or "", customer.last_name or ""]
        customer_name = " ".join(part for part in name_parts if part).strip() or None
        customer_email = customer.email or None

    # Для generic Checkout / HPP method лучше не форсить.
    methods = None
    if req.settings.method and req.settings.method.strip():
        method_value = req.settings.method.strip().lower()
        if method_value not in {"checkout", "hpp", "auto", "any"}:
            methods = [method_value]

    # ВАЖНО:
    # По умолчанию отправляем минимальный payload.
    # Дополнительные поля возвращаем только если точно известно,
    # что они нужны и не ломают session creation.
    payload = build_checkout_session_payload(
        merchant_key=merchant_key,
        password=password,
        order_number=order_number,
        order_amount=order_amount,
        order_currency=order_currency,
        order_description=order_description,
        success_url=success_url,
        cancel_url=cancel_url,
        methods=methods,
        customer_name=customer_name,
        customer_email=customer_email,
        billing_country=None,
        billing_state=None,
        billing_city=None,
        billing_address=None,
        billing_zip=None,
        billing_phone=None,
        card_token=None,
        channel_id=None,
        custom_data=None,
    )

    logger.info("IQONO Checkout request payload: %s", payload)

    # Сначала сохраняем базовую связку до вызова IQONO
    await upsert_mapping(
        rp_token=rp_token,
        callback_url=req.callback_url,
        order_number=req.payment.order_number or order_number,
        merchant_private_key=req.payment.merchant_private_key,
        auth_password=password,
        order_description=order_description,
        status="created",
    )

    try:
        iqono_resp = await post_checkout_session(CHECKOUT_URL, payload)
    except Exception as exc:
        logger.error("IQONO Checkout session error: %s", exc)
        return _error_pay_response([str(exc)])

    if "error_message" in iqono_resp or "errors" in iqono_resp:
        error_msg = iqono_resp.get("error_message", "Unknown error")
        errors = iqono_resp.get("errors", [])

        detail_msgs = []
        for item in errors:
            if isinstance(item, dict):
                detail = item.get("error_message")
                if detail:
                    detail_msgs.append(detail)

        all_msgs = [msg for msg in [error_msg, *detail_msgs] if msg]
        logger.warning("IQONO Checkout session error: %s", all_msgs)
        return _error_pay_response(all_msgs)

    redirect_url = iqono_resp.get("redirect_url")
    if not redirect_url:
        return _error_pay_response(["No redirect_url in IQONO response"])

    # gateway_token по контракту RP оставляем None,
    # но provider_operation_id обязательно сохраняем
    provider_operation_id = (
        iqono_resp.get("id")
        or iqono_resp.get("payment_id")
        or iqono_resp.get("session_id")
    )

    logger.info(
        "IQONO session created: provider_operation_id=%s redirect=%s",
        provider_operation_id,
        redirect_url,
    )

    await upsert_mapping(
        rp_token=rp_token,
        callback_url=req.callback_url,
        provider_operation_id=provider_operation_id,
        status="session_created",
    )

    return {
        "status": "OK",
        "gateway_token": None,
        "result": "redirect",
        "requisites": {},
        "redirectRequest": {
            "url": redirect_url,
            "type": "redirect",
            "iframes": [],
        },
        "with_external_format": bool(req.settings.wrapped_to_json_response),
        "provider_response_data": iqono_resp,
        "logs": [],
    }


# ---------------------------------------------------------------------------
# /status — Payment status query
# ---------------------------------------------------------------------------

async def handle_status(req: StatusRequest) -> dict:
    try:
        auth_token = req.settings.auth_token or f"{MERCHANT_KEY}:{MERCHANT_PASS}"
        merchant_key, password = parse_credentials(auth_token)
    except ValueError as exc:
        return {
            "result": "ERROR",
            "status": "declined",
            "details": {},
            "logs": [str(exc)],
        }

    payment_id = req.payment.gateway_token
    if not payment_id:
        return {
            "result": "ERROR",
            "status": "pending",
            "details": {},
            "logs": ["gateway_token is empty; payment is not yet linked to IQONO payment id"],
        }

    payload = build_status_payload(merchant_key, password, payment_id)

    try:
        iqono_resp = await post_payment_status(CHECKOUT_URL, payload)
    except Exception as exc:
        logger.error("IQONO payment status error: %s", exc)
        return {
            "result": "ERROR",
            "status": "pending",
            "details": {
                "gateway_token": payment_id,
            },
            "logs": [str(exc)],
        }

    iqono_status = iqono_resp.get("status", "")

    return {
        "result": "OK",
        "status": map_to_rp(iqono_status, ""),
        "details": {
            "gateway_token": payment_id,
            "raw_status": iqono_status,
            "recurring_token": iqono_resp.get("recurring_token"),
            "digital_wallet": iqono_resp.get("digital_wallet"),
            "provider_response_data": iqono_resp,
        },
        "logs": [],
    }


# ---------------------------------------------------------------------------
# /callback — IQONO webhook handler
# ---------------------------------------------------------------------------

async def handle_callback(form_data: dict) -> str:
    """
    Process IQONO Checkout callback (application/x-www-form-urlencoded).

    Expected IQONO fields:
      - id
      - order_number
      - order_amount
      - order_currency
      - order_description
      - order_status
      - type
      - status
      - hash
    """
    payment_id = form_data.get("id", "") or ""
    order_number = form_data.get("order_number", "") or ""
    order_amount = form_data.get("order_amount", "0") or "0"
    order_currency = form_data.get("order_currency", "") or ""
    order_description = form_data.get("order_description", "") or ""
    order_status = form_data.get("order_status", "") or ""
    tx_type = form_data.get("type", "") or ""
    tx_status = form_data.get("status", "") or ""
    received_hash = form_data.get("hash", "") or ""

    logger.info(
        "IQONO webhook: id=%s order_number=%s order_status=%s type=%s status=%s",
        payment_id,
        order_number,
        order_status,
        tx_type,
        tx_status,
    )

    if not order_number and not payment_id:
        logger.warning("IQONO webhook rejected: missing both order_number and id")
        return "ERROR"

    mapping = await get_mapping(order_number) or await get_mapping(payment_id)
    if not mapping:
        logger.warning("No mapping found: order_number=%s id=%s", order_number, payment_id)
        return "ERROR"

    auth_password = mapping.get("auth_password")
    stored_description = mapping.get("order_description") or order_description

    if not received_hash:
        logger.warning("IQONO webhook missing hash: order_number=%s", order_number)
        return "ERROR"

    if auth_password:
        expected_hash = hash_callback(
            payment_id=payment_id,
            order_number=order_number,
            order_amount=order_amount,
            order_currency=order_currency,
            order_description=stored_description,
            password=auth_password,
        )

        if received_hash != expected_hash:
            logger.warning(
                "IQONO webhook hash mismatch: order_number=%s received=%s expected=%s",
                order_number,
                received_hash,
                expected_hash,
            )
            return "ERROR"

    rp_token = mapping["rp_token"]
    callback_url = mapping.get("callback_url")
    merchant_private_key = mapping.get("merchant_private_key")

    await upsert_mapping(
        rp_token=rp_token,
        callback_url=callback_url,
        provider_operation_id=payment_id or None,
        status=order_status or tx_status or "unknown",
    )

    rp_status = map_to_rp(order_status, tx_status)

    try:
        amount_minor = int(
            (Decimal(order_amount) * 100).to_integral_value(rounding=ROUND_HALF_UP)
        )
    except Exception:
        amount_minor = 0

    await _send_rp_callback(
        callback_url=callback_url,
        status=rp_status,
        currency=order_currency,
        amount_minor=amount_minor,
        sign_key=SIGN_KEY,
        merchant_private_key=merchant_private_key,
    )

    return "OK"