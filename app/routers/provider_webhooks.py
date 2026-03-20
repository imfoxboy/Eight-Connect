from fastapi import APIRouter, Request, Header, HTTPException
from ..db import get_mapping_by_token_any, update_status_by_token_any
from ..callbacks.rp_client import RPCallbackClient
from ..settings import settings
from ..callbacks.iqono import handle_iqono_webhook
import hashlib

router = APIRouter()


def _to_rp_result(provider_status: str | None) -> str:
    s = (provider_status or "").lower()
    if s in {"paid", "success", "confirmed", "completed"}:
        return "approved"
    if s in {"cancelled", "canceled", "declined", "failed", "expired", "failed_to_send_payout"}:
        return "declined"
    if s in {"refund"}:
        return "refunded"
    return "pending"


@router.post("/provider/brusnika/webhook")
async def brusnika_webhook(request: Request, x_signature: str | None = Header(default=None)):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    order_number = payload.get("merchantOrderId") or payload.get("orderId")
    provider_status = payload.get("status")
    platform_id = payload.get("idPlatform") or payload.get("platformOperationId")

    if not order_number:
        raise HTTPException(status_code=400, detail="merchantOrderId is required in webhook")

    mapping = await get_mapping_by_token_any(order_number)
    if not mapping:
        return {"ok": True}

    await update_status_by_token_any(mapping["rp_token"], provider_status or "unknown")

    callback_payload = {
        "result": _to_rp_result(provider_status),
        "gateway_token": str(platform_id) if platform_id else mapping.get("provider_operation_id"),
        "logs": [],
        "requisites": None
    }

    client = RPCallbackClient()
    try:
        await client.send_callback(mapping["callback_url"], callback_payload)
    except Exception:
        pass

    return {"ok": True}


# ---------- Forta webhook ----------
@router.post("/provider/forta/webhook")
async def forta_webhook(request: Request):
    """
    Ожидаемый callback от Forta:
    {
      "guid": "<gateway_token>",
      "orderId": "ORD123",
      "amount": 1000,
      "status": "PAID|INIT|INPROGRESS|CANCELED",
      "sign": "<md5(orderId + amount + PROVIDER_TOKEN)>"
    }
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    guid = str(payload.get("guid") or "")
    order_id = str(payload.get("orderId") or "")
    amount = str(payload.get("amount") or "")
    status = str(payload.get("status") or "")

    # Проверка подписи, если настроен токен
    prov_token = (settings.FORTA_API_TOKEN or "").strip()
    incoming_sign = str(payload.get("sign") or "")
    if prov_token and incoming_sign:
        check_str = f"{order_id}{amount}{prov_token}"
        calc = hashlib.md5(check_str.encode("utf-8")).hexdigest()
        if calc != incoming_sign:
            raise HTTPException(status_code=401, detail="invalid sign")

    # Ищем маппинг по guid или orderId
    mapping = None
    if guid:
        mapping = await get_mapping_by_token_any(guid)
    if not mapping and order_id:
        mapping = await get_mapping_by_token_any(order_id)
    if not mapping:
        return {"ok": True}

    await update_status_by_token_any(mapping["rp_token"], status or "unknown")

    # Формируем callback для RP с JWT+AES как в документации
    from ..callbacks.rp_client import send_callback_to_rp

    tx = {
        "callback_url": mapping["callback_url"],
        "rp_token": mapping["rp_token"],
        "provider_operation_id": guid or mapping.get("provider_operation_id"),
        "status": _to_rp_result(status),
        "amount": int(amount) if amount else None,
        "currency": "RUB",
        "merchant_private_key": mapping.get("merchant_private_key"),  # для шифрования в secure block
        "provider": "forta",  # Add provider name for sign key selection
    }

    try:
        await send_callback_to_rp(tx)
    except Exception as e:
        print(f"[FORTA WEBHOOK] Callback failed: {e}")

    return {"ok": True}


# ---------- Royal Finance webhook (unified for Payin & Payout) ----------
@router.post("/provider/royal_finance/webhook")
async def royal_finance_webhook(request: Request):
    """
    Royal Finance unified webhook for both Payin and Payout callbacks:

    Payin callback:
    {
      "id": 10440228,
      "status": "completed|created|canceled|refund",
      "amount": 35005,
      "method": "card_number",
      "outter_id": "ORD123",
      ...
    }

    Payout v3 callback:
    {
      "id": 2673,
      "status": "wait_confirm|failed_to_send_payout|completed|canceled",
      "amount": 1500,
      "outter_id": "ORD123",
      "receipts": ["https://..."]
    }
    """
    print("=" * 80)
    print("[ROYAL FINANCE WEBHOOK] ===== INCOMING WEBHOOK FROM ROYAL FINANCE =====")

    # Get all request headers
    headers_dict = dict(request.headers)
    print(f"[ROYAL FINANCE WEBHOOK] Headers received: {headers_dict}")

    try:
        payload = await request.json()
        print(f"[ROYAL FINANCE WEBHOOK] Full payload received: {payload}")
    except Exception as e:
        print(f"[ROYAL FINANCE WEBHOOK] Failed to parse JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    operation_id = str(payload.get("id") or "")
    status = str(payload.get("status") or "")
    outter_id = payload.get("outter_id") or payload.get("form_outter_id")
    rp_token = payload.get("token")  # For RP callbacks
    amount = payload.get("amount") or payload.get("sum")
    receipts = payload.get("receipts")

    print(f"[ROYAL FINANCE WEBHOOK] Extracted fields:")
    print(f"  - operation_id: {operation_id}")
    print(f"  - status: {status}")
    print(f"  - outter_id: {outter_id}")
    print(f"  - rp_token: {rp_token}")
    print(f"  - amount: {amount}")
    print(f"  - receipts: {receipts}")

    # Try to find mapping by operation_id, outter_id, or rp_token
    mapping = None
    if operation_id:
        print(f"[ROYAL FINANCE WEBHOOK] Looking up mapping by operation_id: {operation_id}")
        mapping = await get_mapping_by_token_any(operation_id)
    if not mapping and outter_id:
        print(f"[ROYAL FINANCE WEBHOOK] Looking up mapping by outter_id: {outter_id}")
        mapping = await get_mapping_by_token_any(outter_id)
    if not mapping and rp_token:
        print(f"[ROYAL FINANCE WEBHOOK] Looking up mapping by rp_token: {rp_token}")
        mapping = await get_mapping_by_token_any(rp_token)

    if not mapping:
        print(f"[ROYAL FINANCE WEBHOOK] No mapping found - returning OK (might be test webhook)")
        print("=" * 80)
        return {"ok": True}

    print(f"[ROYAL FINANCE WEBHOOK] Mapping found: {mapping}")

    # ===== WEBHOOK VERIFICATION VIA API =====
    # As requested by Royal Finance: verify webhook data against API to prevent fraud
    print(f"[ROYAL FINANCE WEBHOOK] ===== VERIFYING WEBHOOK DATA VIA API =====")

    from ..providers.royal_finance.adapter import RoyalFinanceAdapter
    adapter = RoyalFinanceAdapter()

    # Get token from mapping or use default
    # Note: We need to determine if this is payin or payout
    # If we have operation_id, it's likely payin (uses operation_id directly)
    # If we have outter_id without operation_id, it might be payout
    api_data = None
    is_payout = False

    # Try Payin verification first (if we have operation_id)
    if operation_id:
        print(f"[ROYAL FINANCE WEBHOOK] Attempting Payin verification for operation_id: {operation_id}")
        # Use token from settings - we don't have payload here
        token = settings.ROYAL_PAYIN_API_TOKEN
        api_data = await adapter._verify_payin_status(operation_id, token)

    # If Payin failed and we have outter_id, try Payout verification
    if not api_data and outter_id:
        print(f"[ROYAL FINANCE WEBHOOK] Attempting Payout verification for outter_id: {outter_id}")
        token = settings.ROYAL_PAYOUT_API_TOKEN
        api_data = await adapter._verify_payout_status(outter_id, token)
        is_payout = True

    # Verify webhook data against API response
    if api_data:
        api_status = str(api_data.get("status") or "")
        api_amount = api_data.get("amount") or api_data.get("sum")

        print(f"[ROYAL FINANCE WEBHOOK] API verification results:")
        print(f"  - API status: {api_status}")
        print(f"  - Webhook status: {status}")
        print(f"  - API amount: {api_amount}")
        print(f"  - Webhook amount: {amount}")

        # Check if status matches
        if api_status.lower() != status.lower():
            print(f"[ROYAL FINANCE WEBHOOK] ⚠️ WARNING: Status mismatch!")
            print(f"[ROYAL FINANCE WEBHOOK] Webhook status: {status}")
            print(f"[ROYAL FINANCE WEBHOOK] API status: {api_status}")
            print(f"[ROYAL FINANCE WEBHOOK] ❌ REJECTING WEBHOOK - Status does not match API")
            print("=" * 80)
            return {"ok": False, "error": "Status verification failed"}

        # Check if amount matches
        if api_amount is not None and amount is not None:
            # Convert to same format for comparison
            try:
                api_amount_float = float(api_amount)
                webhook_amount_float = float(amount)

                # Allow small difference due to float precision (0.01 units)
                if abs(api_amount_float - webhook_amount_float) > 0.01:
                    print(f"[ROYAL FINANCE WEBHOOK] ⚠️ WARNING: Amount mismatch!")
                    print(f"[ROYAL FINANCE WEBHOOK] Webhook amount: {webhook_amount_float}")
                    print(f"[ROYAL FINANCE WEBHOOK] API amount: {api_amount_float}")
                    print(f"[ROYAL FINANCE WEBHOOK] ❌ REJECTING WEBHOOK - Amount does not match API")
                    print("=" * 80)
                    return {"ok": False, "error": "Amount verification failed"}
            except (ValueError, TypeError) as e:
                print(f"[ROYAL FINANCE WEBHOOK] ⚠️ WARNING: Could not compare amounts: {e}")

        print(f"[ROYAL FINANCE WEBHOOK] ✅ Webhook verification PASSED")

        # Use API data as source of truth
        status = api_status
        amount = api_amount
    else:
        print(f"[ROYAL FINANCE WEBHOOK] ⚠️ WARNING: Could not verify webhook via API")
        print(f"[ROYAL FINANCE WEBHOOK] Proceeding with webhook data (not recommended)")

    # Update status in DB
    print(f"[ROYAL FINANCE WEBHOOK] Updating status in DB for rp_token: {mapping['rp_token']} -> {status}")
    await update_status_by_token_any(mapping["rp_token"], status or "unknown")

    # For payout wait_confirm - just return OK to activate payout
    if status == "wait_confirm":
        print(f"[ROYAL FINANCE WEBHOOK] Status is wait_confirm - confirming payout {operation_id}")
        print(f"[ROYAL FINANCE WEBHOOK] Responding to Royal Finance: {{'ok': True}}")
        print("=" * 80)
        return {"ok": True}

    # For completed/canceled/failed - send callback to RP
    if status in ["completed", "success", "canceled", "refund", "failed_to_send_payout"]:
        print(f"[ROYAL FINANCE WEBHOOK] Status requires callback to RP: {status}")
        from ..callbacks.rp_client import send_callback_to_rp

        # Convert amount to kopecks (RP expects kopecks, Royal Finance sends whole units)
        amount_in_kopecks = None
        if amount:
            try:
                # Royal Finance sends string like "500.00" or integer
                amount_in_kopecks = int(float(amount) * 100)
                print(f"[ROYAL FINANCE WEBHOOK] Converted amount: {amount} -> {amount_in_kopecks} kopecks")
            except (ValueError, TypeError) as e:
                print(f"[ROYAL FINANCE WEBHOOK] Failed to convert amount: {e}")
                amount_in_kopecks = None

        if amount_in_kopecks is None:
            print(f"[ROYAL FINANCE WEBHOOK] ⚠️ WARNING: No amount in webhook payload - using 0 as fallback")
            amount_in_kopecks = 0

        # Prepare transaction object for RP callback (JWT + AES-256-CBC format)
        tx = {
            "callback_url": mapping["callback_url"],
            "rp_token": mapping["rp_token"],
            "provider_operation_id": operation_id or mapping.get("provider_operation_id"),
            "status": _to_rp_result(status),
            "amount": amount_in_kopecks,
            "currency": "AZN",
            "merchant_private_key": mapping.get("merchant_private_key"),  # для шифрования в secure block
            "provider": "royal_finance",  # Add provider name for sign key selection
        }

        print(f"[ROYAL FINANCE WEBHOOK] Constructed callback transaction object:")
        print(f"  tx = {tx}")

        try:
            print(f"[ROYAL FINANCE WEBHOOK] Calling send_callback_to_rp()...")
            await send_callback_to_rp(tx)
            print(f"[ROYAL FINANCE WEBHOOK] Callback to RP completed successfully")
        except Exception as e:
            import traceback
            print(f"[ROYAL FINANCE WEBHOOK] ❌ Callback to RP FAILED: {e}")
            print(f"[ROYAL FINANCE WEBHOOK] Traceback: {traceback.format_exc()}")
    else:
        print(f"[ROYAL FINANCE WEBHOOK] Status '{status}' does not require callback to RP")

    print(f"[ROYAL FINANCE WEBHOOK] Responding to Royal Finance: {{'ok': True}}")
    print("=" * 80)
    return {"ok": True}


# ---------- RyleCode webhook ----------
@router.post("/provider/rylecode/webhook")
async def rylecode_webhook(request: Request):
    """
    RyleCode (Fluxs) webhook callback:
    {
      "token": "[payment token]",
      "type": "payment | payout",
      "status": "pending | approved | declined",
      "extraReturnParam": "extra params",
      "orderNumber": "merchant order number",
      "sanitizedMask": "payer's sanitized card",
      "amount": "payment amount in cents",
      "currency": "payment currency",
      "gatewayAmount": "exchanged amount in cents",
      "gatewayCurrency": "exchanged currency",
      "initAmount": "initial payment amount in cents (optional)"
    }
    """
    print("=" * 80)
    print("[RYLECODE WEBHOOK] ===== INCOMING WEBHOOK FROM RYLECODE =====")

    try:
        payload = await request.json()
        print(f"[RYLECODE WEBHOOK] Full payload received: {payload}")
    except Exception as e:
        print(f"[RYLECODE WEBHOOK] Failed to parse JSON: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    payment_token = payload.get("token") or ""
    operation_type = payload.get("type") or "payment"
    status = payload.get("status") or "pending"
    order_number = payload.get("orderNumber")
    extra_return_param = payload.get("extraReturnParam")
    amount = payload.get("amount")
    currency = payload.get("currency")

    print(f"[RYLECODE WEBHOOK] Extracted fields:")
    print(f"  - payment_token: {payment_token}")
    print(f"  - operation_type: {operation_type}")
    print(f"  - status: {status}")
    print(f"  - order_number: {order_number}")
    print(f"  - extra_return_param: {extra_return_param}")
    print(f"  - amount: {amount}")
    print(f"  - currency: {currency}")

    # Try to find mapping by payment_token, order_number, or extra_return_param
    mapping = None
    if payment_token:
        print(f"[RYLECODE WEBHOOK] Looking up mapping by payment_token: {payment_token}")
        mapping = await get_mapping_by_token_any(payment_token)
    if not mapping and order_number:
        print(f"[RYLECODE WEBHOOK] Looking up mapping by order_number: {order_number}")
        mapping = await get_mapping_by_token_any(order_number)
    if not mapping and extra_return_param:
        print(f"[RYLECODE WEBHOOK] Looking up mapping by extra_return_param: {extra_return_param}")
        mapping = await get_mapping_by_token_any(extra_return_param)

    if not mapping:
        print(f"[RYLECODE WEBHOOK] No mapping found - returning OK (might be test webhook)")
        print("=" * 80)
        return {"ok": True}

    print(f"[RYLECODE WEBHOOK] Mapping found: {mapping}")

    # Update status in DB
    print(f"[RYLECODE WEBHOOK] Updating status in DB for rp_token: {mapping['rp_token']} -> {status}")
    await update_status_by_token_any(mapping["rp_token"], status or "unknown")

    # Send callback to RP for final statuses
    if status in ["approved", "declined"]:
        print(f"[RYLECODE WEBHOOK] Status requires callback to RP: {status}")
        from ..callbacks.rp_client import send_callback_to_rp

        # Convert amount if provided
        amount_in_kopecks = None
        if amount:
            try:
                amount_in_kopecks = int(amount)  # RyleCode sends in cents already
                print(f"[RYLECODE WEBHOOK] Amount: {amount_in_kopecks} kopecks")
            except (ValueError, TypeError) as e:
                print(f"[RYLECODE WEBHOOK] Failed to convert amount: {e}")
                amount_in_kopecks = None

        # Prepare transaction object for RP callback
        tx = {
            "callback_url": mapping["callback_url"],
            "rp_token": mapping["rp_token"],
            "provider_operation_id": payment_token or mapping.get("provider_operation_id"),
            "status": _to_rp_result(status),
            "amount": amount_in_kopecks,
            "currency": currency or "RUB",
            "merchant_private_key": mapping.get("merchant_private_key"),
            "provider": "rylecode",
        }

        print(f"[RYLECODE WEBHOOK] Constructed callback transaction object:")
        print(f"  tx = {tx}")

        try:
            print(f"[RYLECODE WEBHOOK] Calling send_callback_to_rp()...")
            await send_callback_to_rp(tx)
            print(f"[RYLECODE WEBHOOK] Callback to RP completed successfully")
        except Exception as e:
            import traceback
            print(f"[RYLECODE WEBHOOK] ❌ Callback to RP FAILED: {e}")
            print(f"[RYLECODE WEBHOOK] Traceback: {traceback.format_exc()}")
    else:
        print(f"[RYLECODE WEBHOOK] Status '{status}' (pending) - no callback to RP yet")

    print(f"[RYLECODE WEBHOOK] Responding to RyleCode: {{'ok': True}}")
    print("=" * 80)
    return {"ok": True}


@router.post("/provider/iqono/webhook")
async def iqono_webhook(request: Request):
    return await handle_iqono_webhook(request)
