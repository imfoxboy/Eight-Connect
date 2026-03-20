from typing import Dict, Any, Optional
import httpx
import time
from ...settings import settings
from ...utils.http import client, retry_policy
from ...db import upsert_mapping, get_mapping_by_token_any


class FortaNSPKAdapter:
    """
    Forta NSPK (SBP_ECOM):
      - POST /merchantApic2c/invoice           (создать инвойс)
      - GET  /merchantApic2c/invoice?id={guid} (статус инвойса)
    Возвращаем внешний формат, совместимый с RP UI:
      status="OK", gateway_token, result, requisites, redirectRequest, with_external_format, provider_response_data, logs[]
    """
    name = "Forta_NSPK"

    def __init__(self):
        self.base_url = (settings.FORTA_BASE_URL or "https://pt.wallet-expert.com").rstrip("/")

    def _api_token(self, payload: Dict[str, Any]) -> str:
        # приоритет: settings.authorization_token из RP-запроса -> ENV
        override = payload.get("_provider_auth")
        return override or settings.FORTA_API_TOKEN

    def _headers(self, token: str) -> Dict[str, str]:
        tok = token.strip()
        # у forta обычно просто значение, без "Bearer "
        return {"Authorization": tok, "Content-Type": "application/json"}

    # ---- status map ----
    def _status_map(self, s: Optional[str]) -> str:
        """Map provider status to RP status (approved/declined/pending/refunded)"""
        sl = (s or "").upper()
        if sl in {"PAID", "SUCCESS", "CONFIRMED", "APPROVED"}:
            return "approved"
        if sl in {"CANCELED", "CANCELLED", "FAILED", "DECLINED", "ERROR"}:
            return "declined"
        if sl in {"PENDING"}:
            return "pending"
        return "pending"  # INIT, INPROGRESS, CREATED, ...

    @retry_policy()
    async def _post(self, path: str, json_payload: Dict[str, Any], token: str) -> httpx.Response:
        async with client() as c:
            return await c.post(f"{self.base_url}{path}", json=json_payload, headers=self._headers(token))

    @retry_policy()
    async def _get(self, path: str, token: str) -> httpx.Response:
        async with client() as c:
            return await c.get(f"{self.base_url}{path}", headers=self._headers(token))

    # ---- build requisites & provider_response_data ----
    def _build_output(self, data_block: Dict[str, Any], payload: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Forta в ответе присылает:
          data.guid, data.qrCodeLink, data.status, data.receiverName, data.receiverBank, data.receiverPhone ...
        Формируем:
          - requisites: LINK (если есть qrCodeLink) + holder/bank_name
          - provider_response_data: для gateway_details.provider_response_data
        """
        link = data_block.get("qrCodeLink") or data_block.get("link") or None
        holder = data_block.get("receiverName") or ""
        bank_name = data_block.get("receiverBank") or ""
        phone = str(data_block.get("receiverPhone") or "")

        # Проверяем флаг wrapped_to_json для H2H формата
        wrapped_to_json = payload and payload.get("wrapped_to_json") == True

        if link:
            # RP ожидает формат LINK с вложенным объектом:
            # "link": {"url": "https://..."} для extraReturnParam: "link"
            # Формат должен совпадать с Royal Finance
            requisites = {
                "link": {"url": link},
                "holder": holder,
                "bank_name": bank_name
            }
            if phone:
                requisites["phone"] = phone
        else:
            # запасной вариант: если нет ссылки — хотя бы реквизиты SBP по телефону
            requisites = {
                "pan": phone,
                "holder": holder,
                "bank_name": bank_name
            } if phone else {}

        provider_response_data = {
            "guid": data_block.get("guid"),
            "orderId": data_block.get("orderId"),
            "amount": data_block.get("amount"),
            "bank": data_block.get("bank"),
            "status": data_block.get("status"),
            # qrCodeLink removed - RP cannot process this field
            # QR link is already in requisites.link.url and requisites.url
            "receiverName": holder,
            "receiverBank": bank_name,
            "receiverPhone": phone,
            "wrapped_to_json": wrapped_to_json
        }
        return {"requisites": requisites, "provider_response_data": provider_response_data}

    # ---- Adapter API ----
    async def pay(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        token = self._api_token(payload)

        # Конвертируем сумму из копеек в рубли (RP отправляет 20000 для 200 руб)
        amount_in_kopecks = int(payload["amount"])
        amount_in_rubles = amount_in_kopecks / 100

        # Готовим тело запроса в Forta
        # orderId должен быть уникальным - используем payment.token (rp_token)
        order_id = payload.get("rp_token")

        body = {
            "orderId": order_id,
            "amount": amount_in_rubles,
            "bank": "SBP_ECOM",
            "payerHash": (payload.get("customer") or {}).get("client_id") or payload.get("rp_token"),
            # На Forta должен указывать вебхук вашего коннектора, а не RP:
            "callbackUrl": settings.FORTA_WEBHOOK_URL or f"{settings.PUBLIC_BASE_URL.rstrip('/')}/provider/forta/webhook",
            # returnUrl должен быть processing_url (не redirect_success_url!)
            "returnUrl": payload.get("processing_url") or settings.PUBLIC_BASE_URL
        }

        logs = [{
            "gateway": "forta",
            "request": {"url": "/merchantApic2c/invoice", "params": body},
            "status": None,
            "response": None,
            "kind": "pay",
        }]

        try:
            resp = await self._post("/merchantApic2c/invoice", json_payload=body, token=token)
            try:
                js = resp.json()
            except Exception as json_err:
                js = {"raw_text": resp.text or "", "json_parse_error": str(json_err)}
            logs[-1]["status"] = resp.status_code
            logs[-1]["response"] = js

            # Если статус не успешный, возвращаем decline
            if resp.status_code >= 400:
                duration = time.time() - start_time
                error_msg = js.get("message") or js.get("error") or f"HTTP {resp.status_code}"

                # Save mapping even on error so RP can query status later
                mpk = payload.get("merchant_private_key")
                await upsert_mapping(
                    rp_token=payload["rp_token"],
                    order_number=payload.get("order_number"),
                    provider=self.name,
                    callback_url=payload["callback_url"],
                    provider_operation_id=None,  # No gateway token on error
                    status="declined",  # Mark as declined
                    merchant_private_key=mpk,
                )

                return {
                    "status": "OK",
                    "gateway_token": None,
                    "result": "declined",  # Transaction creation failed
                    "reason": error_msg,  # RP requires reason for declined
                    "requisites": {},
                    "redirectRequest": {"url": None, "type": "post_iframes", "iframes": []},
                    "with_external_format": True,
                    "provider_response_data": {"error": error_msg, "status_code": resp.status_code},
                    "logs": logs,
                    "duration": duration,
                    "gateway_details": {
                        "status": "declined"
                    }
                }
        except Exception as e:
            logs[-1]["status"] = 599
            logs[-1]["response"] = {"error": str(e), "error_type": type(e).__name__}
            duration = time.time() - start_time

            # Save mapping even on exception so RP can query status later
            mpk = payload.get("merchant_private_key")
            await upsert_mapping(
                rp_token=payload["rp_token"],
                order_number=payload.get("order_number"),
                provider=self.name,
                callback_url=payload["callback_url"],
                provider_operation_id=None,  # No gateway token on exception
                status="declined",  # Mark as declined
                merchant_private_key=mpk,
            )

            return {
                "status": "OK",
                "gateway_token": None,
                "result": "declined",  # Transaction creation failed
                "reason": str(e),  # RP requires reason for declined
                "requisites": {},
                "redirectRequest": {"url": None, "type": "post_iframes", "iframes": []},
                "with_external_format": True,
                "provider_response_data": {"error": str(e)},
                "logs": logs,
                "duration": duration,
                "gateway_details": {
                    "status": "declined"
                }
            }

        data_block = js.get("data") or {}
        provider_status = data_block.get("status") or (js.get("result") or {}).get("status")
        status_value = self._status_map(provider_status)  # approved/declined/pending
        gateway_token = str(data_block.get("guid") or "")

        # Проверяем что получили guid от провайдера
        if not gateway_token:
            duration = time.time() - start_time
            error_msg = js.get("message") or js.get("error") or "No guid in provider response"

            # Save mapping even without gateway_token so RP can query status later
            mpk = payload.get("merchant_private_key")
            await upsert_mapping(
                rp_token=payload["rp_token"],
                order_number=payload.get("order_number"),
                provider=self.name,
                callback_url=payload["callback_url"],
                provider_operation_id=None,  # No gateway token
                status="declined",  # Mark as declined
                merchant_private_key=mpk,
            )

            return {
                "status": "OK",
                "gateway_token": None,
                "result": "declined",  # Transaction creation failed (no guid)
                "reason": error_msg,  # RP requires reason for declined
                "requisites": {},
                "redirectRequest": {"url": None, "type": "post_iframes", "iframes": []},
                "with_external_format": True,
                "provider_response_data": {"error": error_msg, "raw_response": js},
                "logs": logs,
                "duration": duration,
                "gateway_details": {
                    "status": "declined"
                }
            }

        # Сохраняем маппинг для статусов/вебхуков
        mpk = payload.get("merchant_private_key")
        print(f"[FORTA] About to save mapping with merchant_private_key: {mpk}")
        await upsert_mapping(
            rp_token=payload["rp_token"],
            order_number=payload.get("order_number"),
            provider=self.name,
            callback_url=payload["callback_url"],
            provider_operation_id=gateway_token,
            status=provider_status,
            merchant_private_key=mpk,
        )

        built = self._build_output(data_block, payload)
        requisites = built["requisites"]
        provider_response_data = built["provider_response_data"]

        # Определяем тип редиректа
        # Forta NSPK поддерживает H2H режим (wrapped_to_json_response: true)
        # В H2H режиме merchant получает QR ссылку через processing_url JSON response
        # и передает ее в свое приложение (аналогично Royal Finance с card details)
        is_wrapped = payload.get("wrapped_to_json_response")
        processing_url = payload.get("processing_url")
        charge_page_url = payload.get("charge_page_url")
        qr_link = provider_response_data.get("qrCodeLink")

        # Логика редиректа аналогично Royal Finance
        if is_wrapped:
            # H2H режим - merchant получит requisites через processing_url
            # RP вернет JSON с requisites.link.url для передачи в merchant app
            redirect_request = {
                "url": processing_url,
                "type": "get_with_processing"
            }
        elif charge_page_url and qr_link:
            # Обычный режим - показать QR на charge_page RP
            redirect_request = {
                "url": charge_page_url,
                "type": "post"
            }
        elif qr_link:
            # Fallback - прямой редирект на QR ссылку Forta
            redirect_request = {
                "url": qr_link,
                "type": "redirect",
                "iframes": []
            }
        else:
            # Нет QR ссылки - редирект на processing_url
            redirect_request = {
                "url": processing_url or charge_page_url,
                "type": "post_iframes",
                "iframes": []
            }

        duration = time.time() - start_time

        return {
            "status": "OK",
            "gateway_token": gateway_token or None,
            "result": "success",  # Transaction created successfully (even if pending/declined by provider)
            "requisites": requisites,
            "redirectRequest": redirect_request,
            "with_external_format": True,
            # Return bank_account with requisite_type so RP knows the format
            # This is the mapping Ilya mentioned - takes priority over extra_return_param
            "bank_account": {
                "requisite_type": "link"  # Forta NSPK always returns QR links
            },
            # Return extra_return_param so RP knows which format requisites are in
            "extra_return_param": payload.get("extra_return_param"),
            # provider_response_data removed per Dmitry's request
            # "не отправляйте provider_response_data"
            "logs": logs,
            "duration": duration,
            # Add gateway_details with status for RP
            "gateway_details": {
                "status": status_value  # approved/declined/pending (transaction state)
            }
        }

    async def status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        token = self._api_token(payload)
        # ищем guid: сначала из входа, иначе по маппингу
        key = payload.get("gateway_token") or payload.get("rp_token") or payload.get("order_number")
        mapping = await get_mapping_by_token_any(key) if key else None
        if not mapping or not mapping.get("provider_operation_id"):
            return {
                "result": "OK",
                "status": "pending",
                "details": "no guid in mapping",
                "amount": None,
                "currency": None,
                "logs": [],
                "with_external_format": True,
                "provider_response_data": {},
                "requisites": {}
            }

        guid = mapping["provider_operation_id"]

        logs = [{
            "gateway": "forta",
            "request": {"url": "/merchantApic2c/invoice", "params": {"id": guid}},
            "status": None,
            "response": None,
            "kind": "status",
        }]

        try:
            resp = await self._get(f"/merchantApic2c/invoice?id={guid}", token=token)
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
                "with_external_format": True,
                "provider_response_data": {},
                "requisites": {}
            }

        data_block = js.get("data") or {}
        provider_status = data_block.get("status") or (js.get("result") or {}).get("status")
        status_value = self._status_map(provider_status)  # approved/declined/pending

        built = self._build_output(data_block, payload)

        # по возможности отдадим сумму/валюту
        amount = data_block.get("amount")
        currency = data_block.get("currency") or "RUB"

        duration = time.time() - start_time

        return {
            "result": "OK",
            "status": status_value,  # approved/declined/pending
            "details": f"Transaction status: {status_value}",
            "amount": amount,
            "currency": currency,
            "logs": logs,
            "with_external_format": True,
            # provider_response_data removed per Dmitry's request
            # "не отправляйте provider_response_data"
            "requisites": built["requisites"],
            "duration": duration,
        }

    async def refund(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "result": "ERROR",
            "status": "declined",
            "details": "Refund not supported by Forta SBP_ECOM",
            "amount": None,
            "currency": None,
            "logs": [],
        }

    async def payout(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "result": "ERROR",
            "status": "declined",
            "details": "Payout not implemented for Forta SBP_ECOM",
            "amount": None,
            "currency": None,
            "logs": [],
        }
