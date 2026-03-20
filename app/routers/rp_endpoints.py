from fastapi import APIRouter, HTTPException, Request
from typing import Optional, Dict, Any
from ..settings import settings
from ..db import init_db
from ..providers.registry import get_provider_by_name, resolve_provider_by_payment_method

router = APIRouter()


@router.on_event("startup")
async def _startup():
    await init_db()


def _normalize_provider_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    n = name.strip().lower()
    if n in {"brusnika", "brusnika_sbp", "brusnika-sbp", "sbp-brusnika"}:
        return "Brusnika_SBP"
    return name


def _select_provider(provider_name: Optional[str], payment_method: Optional[str]):
    prov = get_provider_by_name(_normalize_provider_name(provider_name)) if provider_name else None
    if not prov and payment_method:
        prov = resolve_provider_by_payment_method(payment_method)
    if not prov:
        prov = get_provider_by_name(settings.DEFAULT_PROVIDER)
    if not prov:
        raise HTTPException(status_code=400, detail="Provider not found")
    return prov


def _normalize_nested_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Принимаем RP-вложенный формат и приводим к единому виду для адаптера.
    Ожидаем: settings / params.customer / payment / callback_url / processing_url
    """
    settings_in = (body.get("params", {}).get("settings") or body.get("settings") or {}) or {}
    customer_in = (body.get("params", {}).get("customer") or body.get("customer") or {}) or {}
    payment_in = (body.get("params", {}).get("payment") or body.get("payment") or {}) or {}

    callback_url = body.get("callback_url")
    processing_url = body.get("processing_url")
    charge_page_url = body.get("charge_page_url")
    method_name = body.get("method_name")

    # Обязательные поля
    amount_value = payment_in.get("amount", payment_in.get("gateway_amount"))
    currency_value = payment_in.get("currency", payment_in.get("gateway_currency"))
    if amount_value is None:
        raise HTTPException(status_code=400, detail="payment.amount or payment.gateway_amount is required")
    if currency_value is None:
        raise HTTPException(status_code=400, detail="payment.currency or payment.gateway_currency is required")
    if not callback_url:
        raise HTTPException(status_code=400, detail="callback_url is required")
    if "token" not in payment_in:
        raise HTTPException(status_code=400, detail="payment.token is required")

    # Use token as fallback for order_number if not provided
    order_number = payment_in.get("order_number") or payment_in["token"]

    return {
        # ключи RP
        "rp_token": payment_in["token"],
        "order_number": order_number,
        "amount": int(amount_value),
        "currency": str(currency_value),
        "product": payment_in.get("product"),  # Добавлено: используем как orderId
        "callback_url": callback_url,
        "redirect_success_url": payment_in.get("redirect_success_url"),
        "redirect_fail_url": payment_in.get("redirect_fail_url"),
        # merchant_private_key для шифрования в коллбеках
        "merchant_private_key": payment_in.get("merchant_private_key"),
        # extra_return_param для указания типа реквизитов (card/sbp/link/etc)
        "extra_return_param": payment_in.get("extra_return_param"),
        # провайдерские настройки
        "_provider_auth": settings_in.get("auth_token") or settings_in.get("authorization_token") or settings_in.get("token"),
        "_provider_method": (settings_in.get("payment_method") or settings_in.get("method")),
        "_iqono_payment_token": (body.get("params", {}) or {}).get("payment_token") or payment_in.get("payment_token"),
        "_iqono_digital_wallet": ((settings_in.get("method") or payment_in.get("paymentMethod") or payment_in.get("payment_method") or "").strip().lower()),
        # доп. инфо
        "customer": customer_in or {},
        "processing_url": processing_url,
        "charge_page_url": charge_page_url,
        "method_name": method_name,
        # флаги для QR обработки
        "wrapped_to_json_response": (
            settings_in.get("wrapped_to_json") or
            settings_in.get("wrapped_to_json_response") or
            body.get("wrapped_to_json") or
            body.get("wrapped_to_json_response")
        ),
        "wrapped_to_json": (
            settings_in.get("wrapped_to_json") or
            settings_in.get("wrapped_to_json_response") or
            body.get("wrapped_to_json") or
            body.get("wrapped_to_json_response")
        ),
        "show_qr_on_form": settings_in.get("show_qr_on_form") or body.get("show_qr_on_form"),
        "_raw": body,  # для логов
    }


@router.post("/pay")
async def pay(body: Dict[str, Any]):
    """
    Вход — строго «вложенный» JSON, как ты прислал.
    Выход — внешний формат, понятный RP UI:
    {
      "status": "OK",
      "gateway_token": "...",
      "result": "pending|approved|declined",
      "requisites": {...},
      "redirectRequest": {"url": null|..., "type": "post_iframes"|"redirect", "iframes": []},
      "with_external_format": true,
      "provider_response_data": {...},
      "logs": [...]
    }
    """
    print(f"[PAY] Received request from RP")
    print(f"[PAY] Full body: {body}")
    print(f"[PAY] Payment token: {body.get('payment', {}).get('token')}")
    print(f"[PAY] Product: {body.get('payment', {}).get('product')}")
    print(f"[PAY] Amount: {body.get('payment', {}).get('amount')}")
    print(f"[PAY] Currency: {body.get('payment', {}).get('currency')}")
    print(f"[PAY] merchant_private_key: {body.get('payment', {}).get('merchant_private_key')}")
    print(f"[PAY] settings: {body.get('settings')}")
    print(f"[PAY] wrapped_to_json in settings: {body.get('settings', {}).get('wrapped_to_json')}")
    print(f"[PAY] wrapped_to_json_response in settings: {body.get('settings', {}).get('wrapped_to_json_response')}")

    # Extract provider and payment_method
    settings_provider = (body.get("settings") or {}).get("provider")
    payment_method = (body.get("settings") or {}).get("method") or (body.get("payment") or {}).get("paymentMethod") or (body.get("payment") or {}).get("payment_method")
    print(f"[PAY] Provider from settings: {settings_provider}")
    print(f"[PAY] Payment method: {payment_method}")

    provider = _select_provider(settings_provider, payment_method)
    print(f"[PAY] Selected provider: {provider.name if hasattr(provider, 'name') else provider}")

    payload = _normalize_nested_payload(body)
    print(f"[PAY] orderId will be: {payload.get('rp_token')}")
    print(f"[PAY] Normalized payload merchant_private_key: {payload.get('merchant_private_key')}")

    # Выполняем платёж у провайдера
    result = await provider.pay(payload)
    print(f"[PAY] Provider result: {result}")

    # Адаптер уже возвращает внешний формат — просто прокидываем
    return result


@router.post("/status")
async def status(body: Dict[str, Any]):
    """
    Поддерживаем nested-форму статуса от RP:
    { "payment": { "gateway_token": "...", "token": "...", "order_number": "..." } }
    Приоритет: gateway_token -> token (rp_token) -> order_number
    """
    print(f"[STATUS] Received status request from RP")
    print(f"[STATUS] Full body: {body}")

    payment = (body.get("params", {}).get("payment") or body.get("payment") or {}) or {}
    gw = payment.get("gateway_token")
    rp_token = payment.get("token")
    order_number = payment.get("order_number")

    print(f"[STATUS] Extracted - gateway_token: {gw}, rp_token: {rp_token}, order_number: {order_number}")

    if not (gw or rp_token or order_number):
        return {
            "result": "ERROR",
            "status": "declined",
            "details": "gateway_token or payment.token or payment.order_number is required",
            "amount": None,
            "currency": None,
            "logs": [],
        }

    # Достаём провайдера по маппингу в адаптере
    from ..db import get_mapping_by_token_any
    mapping_key = gw or rp_token or order_number
    print(f"[STATUS] Looking up mapping for key: {mapping_key}")
    mapping = await get_mapping_by_token_any(mapping_key)
    print(f"[STATUS] Mapping found: {mapping}")
    if not mapping:
        print(f"[STATUS] No mapping found for key: {mapping_key} - returning 404")
        raise HTTPException(status_code=404, detail="Unknown token")

    provider = get_provider_by_name(mapping["provider"])
    if not provider:
        raise HTTPException(status_code=400, detail="Provider missing for token")

    # Extract settings from request to pass to provider
    settings_in = (body.get("params", {}).get("settings") or body.get("settings") or {}) or {}

    result = await provider.status({
        "rp_token": rp_token,
        "order_number": order_number,
        "gateway_token": gw,
        # Pass auth token for provider API calls
        "_provider_auth": settings_in.get("auth_token") or settings_in.get("authorization_token") or settings_in.get("token"),
        # Pass settings for wrapped_to_json_response flag
        "wrapped_to_json_response": (
            settings_in.get("wrapped_to_json") or
            settings_in.get("wrapped_to_json_response") or
            body.get("wrapped_to_json") or
            body.get("wrapped_to_json_response")
        ),
        "wrapped_to_json": (
            settings_in.get("wrapped_to_json") or
            settings_in.get("wrapped_to_json_response") or
            body.get("wrapped_to_json") or
            body.get("wrapped_to_json_response")
        ),
    })
    print(f"[STATUS] Provider result: {result}")
    return result


@router.post("/refund")
async def refund(body: Dict[str, Any]):
    from ..db import get_mapping_by_token_any
    payment = (body.get("params", {}).get("payment") or body.get("payment") or {}) or {}
    gw = payment.get("gateway_token")
    rp_token = payment.get("token")
    order_number = payment.get("order_number")

    key = gw or rp_token or order_number
    if not key:
        raise HTTPException(status_code=400, detail="gateway_token or payment.token or payment.order_number required")

    mapping = await get_mapping_by_token_any(key)
    if not mapping:
        raise HTTPException(status_code=404, detail="Unknown token")

    provider = get_provider_by_name(mapping["provider"])
    if not provider:
        raise HTTPException(status_code=400, detail="Provider missing for token")

    return await provider.refund(body)


def _normalize_payout_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize RP payout request format to internal format
    RP format: params.card / params.customer / payment.token / payment.gateway_amount
    OR flat format: requisite / card at top level
    """
    settings_in = body.get("settings") or {}
    params_in = body.get("params") or {}
    payment_in = body.get("payment") or {}

    callback_url = body.get("callback_url")

    # Extract card data - check both nested (params.card) and flat (body.card) formats
    card = params_in.get("card") or body.get("card")
    customer = params_in.get("customer") or {}

    # Extract requisite from multiple possible locations
    requisite = None
    if card and isinstance(card, dict):
        requisite = card.get("pan") or card.get("card_number")
    if not requisite:
        requisite = params_in.get("requisite") or body.get("requisite")

    print(f"[NORMALIZE_PAYOUT] Card extracted: {card}")
    print(f"[NORMALIZE_PAYOUT] Requisite extracted: {requisite[:4] + '****' + requisite[-4:] if requisite else 'NONE'}")

    return {
        # RP keys
        "rp_token": payment_in.get("token"),
        "order_number": params_in.get("order_number") or body.get("order_number"),
        "amount": int(payment_in.get("gateway_amount", 0)),
        "currency": str(payment_in.get("gateway_currency", "EUR")),
        "callback_url": callback_url,
        # Provider settings (check multiple field names for auth)
        # For payouts, prefer payout_authorization_token if available (but not if it's null)
        "_provider_auth": (
            (settings_in.get("payout_authorization_token") if settings_in.get("payout_authorization_token") else None) or
            (settings_in.get("authorization_token") if not ("payout_authorization_token" in settings_in) else None) or
            settings_in.get("bearer_token") or
            settings_in.get("login") or
            settings_in.get("api_token")
        ),
        "merchant_private_key": payment_in.get("merchant_private_key"),
        "gateway_token": payment_in.get("gateway_token"),
        # Card/requisite data (pass full card object for Royal Finance payout adapter)
        "card": card,
        "requisite": requisite,
        "card_expires": card.get("expires") if card and isinstance(card, dict) else None,
        "method": params_in.get("method") or body.get("method") or "INTERBANK",
        "bank": params_in.get("bank") or body.get("bank"),
        # Customer data
        "customer": customer,
        "extra_return_param": params_in.get("extra_return_param"),
        # Processing URL for form-based payouts
        "processingUrl": payment_in.get("processingUrl") or body.get("processingUrl"),
        "_raw": body,
    }


@router.post("/payout")
async def payout(body: Dict[str, Any]):
    # DEBUG: Log full raw body from RP
    print(f"\n{'='*80}")
    print(f"[RP_PAYOUT_ENDPOINT] Raw body from RP:")
    import json
    print(json.dumps(body, indent=2, ensure_ascii=False))
    print(f"{'='*80}\n")

    provider = _select_provider((body.get("settings") or {}).get("provider"), None)
    payload = _normalize_payout_payload(body)
    return await provider.payout(payload)


@router.get("/qr_form/{gateway_token}")
async def qr_form(gateway_token: str):
    """
    Простая QR форма для отображения QR кода на нашей странице
    Используется когда show_qr_on_form = true
    """
    from ..db import get_mapping_by_token_any

    mapping = await get_mapping_by_token_any(gateway_token)
    if not mapping:
        raise HTTPException(status_code=404, detail="QR form not found")

    # Простая HTML форма с QR кодом
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>SBP Payment - QR Code</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial, sans-serif; text-align: center; padding: 20px; }}
            .qr-container {{ max-width: 400px; margin: 0 auto; }}
            .qr-code {{ width: 300px; height: 300px; margin: 20px auto; }}
            .info {{ margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="qr-container">
            <h2>SBP Payment</h2>
            <div class="info">
                <p><strong>Order:</strong> {mapping.get('order_number', 'N/A')}</p>
                <p><strong>Status:</strong> {mapping.get('status', 'pending')}</p>
            </div>
            <div class="qr-code">
                <p>Scan QR code to pay:</p>
                <div id="qr-placeholder">
                    <p>Loading QR code...</p>
                </div>
            </div>
        </div>
        <script>
            // Здесь можно добавить логику для отображения QR кода
            // или периодической проверки статуса платежа
            setTimeout(function() {{
                document.getElementById('qr-placeholder').innerHTML = '<p>QR code would be displayed here</p>';
            }}, 1000);
        </script>
    </body>
    </html>
    """

    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html_content, status_code=200)
