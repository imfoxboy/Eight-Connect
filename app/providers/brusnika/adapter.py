from typing import Dict, Any, Optional
import httpx
import re
from ...settings import settings
from ...utils.http import client, retry_policy
from ...db import upsert_mapping, get_mapping_by_token_any


class BrusnikaAdapter:
    """
    Brusnika H2H (SBP-first):
    - POST /host2host/payin
    - GET  /operation/operation/platform/{idPlatform}
    Возвращаем ВНЕШНИЙ формат, который RP UI ожидает:
      status="OK" | "ERROR", gateway_token, result, requisites, redirectRequest, with_external_format, provider_response_data, logs[]
    """

    name = "Brusnika_SBP"

    def __init__(self):
        self.base_url = settings.BRUSNIKA_BASE_URL.rstrip("/")

    def _api_key(self, payload: Dict[str, Any]) -> str:
        override = payload.get("_provider_auth")
        if override:
            return override
        # BRUSNIKA_API_KEY might not be configured if provider is not in use
        return getattr(settings, "BRUSNIKA_API_KEY", "")

    @retry_policy()
    async def _post(self, path: str, json_payload: Dict[str, Any], api_key: str) -> httpx.Response:
        async with client() as c:
            return await c.post(
                f"{self.base_url}{path}",
                json=json_payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            )

    @retry_policy()
    async def _get(self, path: str, api_key: str) -> httpx.Response:
        async with client() as c:
            return await c.get(
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            )

    # ---- Utils ----
    def _status_map(self, s: Optional[str]) -> str:
        sl = (s or "").lower()
        if sl in {"approved", "success", "succeeded", "completed", "paid", "confirmed"}:
            return "approved"
        if sl in {"declined", "failed", "error", "canceled", "cancelled", "expired"}:
            return "declined"
        if sl in {"refunded", "refund", "reversed"}:
            return "refunded"
        return "pending"

    def _digits(self, v: Any) -> str:
        s = str(v or "")
        return "".join(ch for ch in s if ch.isdigit())

    def _build_requisites_and_provider_data(
        self, payment_details: Dict[str, Any], deeplink: Optional[str]
    ) -> Dict[str, Any]:
        """
        Собираем:
          - requisites (строго по типам: SBP | CARD | ACCOUNT | LINK)
          - provider_response_data (с доп. полями: qr/phone и т.п.)
        """
        provider_response: Dict[str, Any] = {}
        requisites: Optional[Dict[str, Any]] = None

        if not isinstance(payment_details, dict):
            if deeplink:
                requisites = {"link": {"url": deeplink}, "holder": "", "bank_name": ""}
                provider_response = {"link": deeplink}
            return {"requisites": requisites, "provider_response_data": provider_response}

        method = (payment_details.get("paymentMethod") or "").lower()
        bank_name = payment_details.get("bankName") or ""
        holder = payment_details.get("nameMediator") or payment_details.get("holder") or ""
        number = payment_details.get("number") or ""
        number_add = payment_details.get("numberAdditional") or ""
        qr = payment_details.get("qRcode") or payment_details.get("qrCode") or ""

        digits = self._digits(number or number_add)
        is_card = bool(digits) and 13 <= len(digits) <= 19
        is_account = bool(digits) and len(digits) >= 20
        is_phone = bool(digits) and (len(digits) in (10, 11) or digits.startswith("7"))

        # Приоритет: метод → эвристика по номеру
        if method in {"sbp", "tophone", "to_phone"} or (qr and is_phone):
            # SBP
            requisites = {"pan": digits or number or number_add, "holder": holder, "bank_name": bank_name}
            provider_response = {
                "qr": qr or "",
                "pan": digits or number,
                "phone": digits or number,
                "holder": holder,
                "bank_name": bank_name,
            }
        elif method in {"tocard", "to_card"} or is_card:
            # CARD
            requisites = {"card": digits or number, "holder": holder, "bank_name": bank_name}
            provider_response = {"qr": qr or "", "card": digits or number, "holder": holder, "bank_name": bank_name}
        elif method in {"toaccount", "to_account"} or is_account:
            # ACCOUNT
            requisites = {"account": digits or number, "holder": holder, "bank_name": bank_name}
            provider_response = {"qr": qr or "", "account": digits or number, "holder": holder, "bank_name": bank_name}
        elif deeplink:
            # LINK
            requisites = {"link": {"url": deeplink}, "holder": holder, "bank_name": bank_name}
            provider_response = {"qr": qr or "", "link": deeplink, "holder": holder, "bank_name": bank_name}
        else:
            # ничего уверенного — provider_response остаётся как есть (без requisites)
            provider_response = {"qr": qr or "", "holder": holder, "bank_name": bank_name}

        return {"requisites": requisites, "provider_response_data": provider_response}

    # ---- Adapter API ----
    async def pay(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        api_key = self._api_key(payload)

        # Тело запроса к Brusnika (минимальное и валидное)
        body = {
            "clientID": (payload.get("customer") or {}).get("client_id") or "rp-client",
            "clientIP": (payload.get("customer") or {}).get("client_ip") or "127.0.0.1",
            "clientDateCreated": None,
            "paymentMethod": (payload.get("_provider_method") or "SBP"),
            "idTransactionMerchant": payload["order_number"],
            "amount": int(payload["amount"]),
            "integrationMerhcnatData": {
                "webHook": payload.get("callback_url") or settings.BRUSNIKA_CALLBACK_URL  # если используешь публичный /webhook/provider — подставь там
            }
        }

        logs = [{
            "gateway": "brusnika",
            "request": {"url": "/host2host/payin", "params": {**body, "integrationMerhcnatData": {"webHook": "***"}}},
            "status": None,
            "response": None,
            "kind": "pay",
        }]

        try:
            resp = await self._post("/host2host/payin", json_payload=body, api_key=api_key)
            try:
                js = resp.json()
            except Exception:
                js = {"raw_text": resp.text or ""}
            logs[-1]["status"] = resp.status_code
            logs[-1]["response"] = js
        except Exception as e:
            logs[-1]["status"] = 599
            logs[-1]["response"] = {"error": str(e)}
            return {
                "status": "OK",
                "gateway_token": None,
                "result": "declined",
                "requisites": {},
                "redirectRequest": {"url": None, "type": "post_iframes", "iframes": []},
                "with_external_format": True,
                "provider_response_data": {},
                "logs": logs,
            }

        data_block = js.get("data") or {}
        result_block = js.get("result") or {}

        gateway_token = str(data_block.get("id") or data_block.get("idPlatform") or "")
        provider_status = (data_block.get("status") or result_block.get("status") or "pending")
        result_norm = self._status_map(provider_status)

        payment_details = data_block.get("paymentDetailsData") or {}
        deeplink = data_block.get("deeplink") or None

        # Сохраняем маппинг для последующих вызовов
        await upsert_mapping(
            rp_token=payload["rp_token"],
            order_number=payload["order_number"],
            provider=self.name,
            callback_url=payload["callback_url"],
            provider_operation_id=gateway_token,
            status=provider_status,
        )

        built = self._build_requisites_and_provider_data(payment_details, deeplink)
        requisites = built.get("requisites") or {}
        provider_response_data = built.get("provider_response_data") or {}

        redirect_request = {"url": None, "type": "post_iframes", "iframes": []}
        if deeplink:
            redirect_request = {"url": deeplink, "type": "redirect", "iframes": []}

        return {
            "status": "OK",
            "gateway_token": gateway_token or None,
            "result": result_norm,
            "requisites": requisites,                       # <-- строго по типу (SBP/CARD/ACCOUNT/LINK)
            "redirectRequest": redirect_request,            # <-- camelCase + ожидаемые type
            "with_external_format": True,                   # <-- флаг, который RP использует
            "provider_response_data": provider_response_data,  # <-- НЕ null
            "logs": logs,
        }

    async def status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        api_key = self._api_key(payload)
        key = payload.get("gateway_token") or payload.get("rp_token") or payload.get("order_number")
        mapping = await get_mapping_by_token_any(key) if key else None
        if not mapping or not mapping.get("provider_operation_id"):
            return {
                "result": "OK",
                "status": "pending",
                "details": "no platform id in mapping",
                "amount": None,
                "currency": None,
                "logs": [],
            }

        platform_id = mapping["provider_operation_id"]
        logs = [{
            "gateway": "brusnika",
            "request": {"url": f"/operation/operation/platform/{platform_id}", "params": {"id": platform_id}},
            "status": None,
            "response": None,
            "kind": "status",
        }]

        try:
            resp = await self._get(f"/operation/operation/platform/{platform_id}", api_key=api_key)
            try:
                js = resp.json()
            except Exception:
                js = {"raw_text": resp.text or ""}
            logs[-1]["status"] = resp.status_code
            logs[-1]["response"] = js
        except Exception as e:
            logs[-1]["status"] = 599
            logs[-1]["response"] = {"error": str(e)}
            return {
                "result": "OK",
                "status": "pending",
                "details": f"Gateway unreachable: {e}",
                "amount": None,
                "currency": None,
                "logs": logs,
            }

        data_block = js.get("data") or js  # у некоторых ответов статус лежит прямо в data
        provider_status = data_block.get("status") or (js.get("result") or {}).get("status")
        status_norm = self._status_map(provider_status)

        payment_details = data_block.get("paymentDetailsData") or {}
        deeplink = data_block.get("deeplink") or None
        built = self._build_requisites_and_provider_data(payment_details, deeplink)

        # Статус-ответ для RP (status endpoint у RP использует другой «внешний» формат)
        amount = data_block.get("amount") or data_block.get("amountInitial")
        currency = data_block.get("currency")
        if isinstance(currency, str) and currency.upper() == "NOTSET":
            currency = None

        return {
            "result": "OK",
            "status": status_norm,
            "details": f"Transaction status: {status_norm}",
            "amount": amount,
            "currency": currency,
            "logs": logs,
            # на всякий добавим, чтобы RP мог обновить gateway_details при опросе
            "with_external_format": True,
            "provider_response_data": built.get("provider_response_data") or {},
            "requisites": built.get("requisites") or {},
        }

    async def refund(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "result": "ERROR",
            "status": "declined",
            "details": "Refund not supported by provider",
            "amount": None,
            "currency": None,
            "logs": [],
        }

    async def payout(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "result": "ERROR",
            "status": "declined",
            "details": "Payout not implemented for this provider",
            "amount": None,
            "currency": None,
            "logs": [],
        }
