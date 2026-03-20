from typing import Dict, Any, Optional
import httpx
import time
from ...settings import settings
from ...utils.http import client, retry_policy
from ...db import upsert_mapping, get_mapping_by_token_any


class RyleCodeAdapter:
    """
    RyleCode (Fluxs Gateway) E-com HPP Integration:
      - POST /api/v1/payments (Create payment - HPP redirect)
      - GET  /api/v1/payments/{token} (Get payment status)
      - POST /api/v1/refunds (Create refund)
      - POST /api/v1/payouts (Create payout)

    API Documentation: https://docs.fluxsgate.com

    Method: Classic E-com with HPP (Hosted Payment Page)
    """

    name = "RyleCode_Ecom"

    def __init__(self):
        # Production: https://business.fluxsgate.com
        # Sandbox: https://business.fluxsgate.com (same URL, different credentials)
        self.base_url = (settings.RYLECODE_BASE_URL or "https://business.fluxsgate.com").rstrip("/")

    def _api_token(self, payload: Dict[str, Any]) -> str:
        """Get API token with override support"""
        override = payload.get("_provider_auth")
        return override or settings.RYLECODE_API_TOKEN

    def _headers(self, token: str) -> Dict[str, str]:
        """Build authorization headers (Bearer token format)"""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    # ---- Status Mapping ----
    def _status_map(self, s: Optional[str]) -> str:
        """
        Map RyleCode status to RP status (approved/declined/pending/refunded)

        RyleCode statuses: init, pending, approved, declined
        """
        sl = (s or "").lower()
        if sl in {"approved", "success", "completed"}:
            return "approved"
        if sl in {"declined", "failed", "canceled"}:
            return "declined"
        # init, pending
        return "pending"

    @retry_policy()
    async def _post(self, path: str, json_payload: Dict[str, Any], token: str) -> httpx.Response:
        """Make POST request to RyleCode API"""
        async with client(timeout_sec=30) as c:
            return await c.post(
                f"{self.base_url}{path}",
                json=json_payload,
                headers=self._headers(token)
            )

    @retry_policy()
    async def _get(self, path: str, token: str) -> httpx.Response:
        """Make GET request to RyleCode API"""
        async with client(timeout_sec=30) as c:
            return await c.get(
                f"{self.base_url}{path}",
                headers=self._headers(token)
            )

    # ---- Adapter API: PAY ----
    async def pay(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create payment via RyleCode E-com HPP

        POST /api/v1/payments
        """
        start_time = time.time()
        token = self._api_token(payload)

        # Amount conversion: RP sends in kopecks, RyleCode expects cents (same for CNY/EUR/USD)
        amount_in_kopecks = int(payload["amount"])

        # Currency (default RUB for e-com HPP)
        currency = payload.get("currency", "RUB")

        # Product description (required, min 5 chars)
        product = payload.get("product") or f"Payment #{payload.get('order_number', payload['rp_token'])}"
        if len(product) < 5:
            product = f"Order {product}"

        # Build RyleCode request
        body = {
            "product": product,
            "amount": amount_in_kopecks,  # in cents
            "currency": currency,
            "orderNumber": payload.get("order_number") or payload["rp_token"],
            "callbackUrl": f"{settings.PUBLIC_BASE_URL.rstrip('/')}/provider/rylecode/webhook",
            "redirectSuccessUrl": payload.get("redirect_success_url") or "https://success.foxew.com/",
            "redirectFailUrl": payload.get("redirect_fail_url") or "https://declined.foxew.com/",
            "extraReturnParam": payload.get("extra_return_param") or payload["rp_token"],
            "locale": "en"  # en, zh, jp
        }

        # Add optional customer data if provided
        customer = payload.get("customer") or {}
        if customer.get("client_id"):
            body["customer"] = {
                "id": customer.get("client_id"),
                "email": customer.get("email"),
                "ip": customer.get("ip")
            }

        logs = [{
            "gateway": "rylecode",
            "request": {"url": f"{self.base_url}/api/v1/payments", "params": body},
            "status": None,
            "response": None,
            "kind": "pay",
        }]

        try:
            resp = await self._post("/api/v1/payments", json_payload=body, token=token)
            try:
                js = resp.json()
            except Exception as json_err:
                js = {"raw_text": resp.text or "", "json_parse_error": str(json_err)}

            logs[-1]["status"] = resp.status_code
            logs[-1]["response"] = js

            # Handle errors
            if resp.status_code >= 400 or not js.get("success"):
                duration = time.time() - start_time
                errors = js.get("errors", [])
                error_msg = str(errors[0]) if errors else f"HTTP {resp.status_code}"

                # Save mapping even on error so RP can query status later
                mpk = payload.get("merchant_private_key")
                await upsert_mapping(
                    rp_token=payload["rp_token"],
                    order_number=payload.get("order_number"),
                    provider=self.name,
                    callback_url=payload["callback_url"],
                    provider_operation_id=None,
                    status="declined",
                    merchant_private_key=mpk,
                )

                return {
                    "status": "OK",
                    "gateway_token": None,
                    "result": "declined",
                    "reason": error_msg,
                    "requisites": {},
                    "redirectRequest": {"url": None, "type": "post_iframes", "iframes": []},
                    "duration": round(duration, 3),
                    "logs": logs,
                    "gateway_details": {
                        "status": "declined"
                    }
                }
        except Exception as e:
            duration = time.time() - start_time
            logs[-1]["status"] = 599
            logs[-1]["response"] = {"error": str(e), "error_type": type(e).__name__}

            # Save mapping even on exception
            mpk = payload.get("merchant_private_key")
            await upsert_mapping(
                rp_token=payload["rp_token"],
                order_number=payload.get("order_number"),
                provider=self.name,
                callback_url=payload["callback_url"],
                provider_operation_id=None,
                status="declined",
                merchant_private_key=mpk,
            )

            return {
                "status": "OK",
                "gateway_token": None,
                "result": "declined",
                "reason": str(e),
                "requisites": {},
                "redirectRequest": {"url": None, "type": "post_iframes", "iframes": []},
                "duration": round(duration, 3),
                "logs": logs,
                "gateway_details": {
                    "status": "declined"
                }
            }

        # Parse successful response
        gateway_token = js.get("token") or ""
        processing_url = js.get("processingUrl") or ""
        payment_data = js.get("payment") or {}
        provider_status = payment_data.get("status") or "init"
        status_value = self._status_map(provider_status)

        if not gateway_token:
            duration = time.time() - start_time
            error_msg = "No payment token in response"

            mpk = payload.get("merchant_private_key")
            await upsert_mapping(
                rp_token=payload["rp_token"],
                order_number=payload.get("order_number"),
                provider=self.name,
                callback_url=payload["callback_url"],
                provider_operation_id=None,
                status="declined",
                merchant_private_key=mpk,
            )

            return {
                "status": "OK",
                "gateway_token": None,
                "result": "declined",
                "reason": error_msg,
                "requisites": {},
                "redirectRequest": {"url": None, "type": "post_iframes", "iframes": []},
                "duration": round(duration, 3),
                "logs": logs,
                "gateway_details": {
                    "status": "declined"
                }
            }

        # Save mapping
        mpk = payload.get("merchant_private_key")
        await upsert_mapping(
            rp_token=payload["rp_token"],
            order_number=payload.get("order_number"),
            provider=self.name,
            callback_url=payload["callback_url"],
            provider_operation_id=gateway_token,
            status=provider_status,
            merchant_private_key=mpk,
        )

        # RyleCode uses HPP (Hosted Payment Page) - redirect customer to processingUrl
        # Check if there's a redirectRequest for 3DS
        redirect_request_data = js.get("redirectRequest") or {}

        if redirect_request_data and redirect_request_data.get("url"):
            # 3DS flow - redirect to ACS URL with POST params
            redirect_request = {
                "url": redirect_request_data.get("url"),
                "type": redirect_request_data.get("type", "post"),
                "params": redirect_request_data.get("params", {}),
                "iframes": []
            }
        elif processing_url:
            # Standard HPP flow - redirect to RyleCode payment page
            redirect_request = {
                "url": processing_url,
                "type": "redirect",
                "iframes": []
            }
        else:
            # Fallback
            redirect_request = {
                "url": None,
                "type": "post_iframes",
                "iframes": []
            }

        duration = time.time() - start_time

        return {
            "status": "OK",
            "gateway_token": gateway_token or None,
            "result": "success",  # Transaction created successfully
            "requisites": {},  # HPP doesn't return requisites
            "redirectRequest": redirect_request,
            "duration": round(duration, 3),
            "logs": logs,
            "gateway_details": {
                "status": status_value,
                "amount": payment_data.get("amount"),
                "currency": payment_data.get("currency")
            }
        }

    # ---- Adapter API: STATUS ----
    async def status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check payment status

        GET /api/v1/payments/{token}
        """
        start_time = time.time()
        token = self._api_token(payload)

        # Get gateway token from payload or mapping
        key = payload.get("gateway_token") or payload.get("rp_token") or payload.get("order_number")
        mapping = await get_mapping_by_token_any(key) if key else None

        if not mapping or not mapping.get("provider_operation_id"):
            duration = time.time() - start_time
            return {
                "result": "OK",
                "status": "pending",
                "details": "Payment not found in mapping",
                "amount": None,
                "currency": None,
                "duration": round(duration, 3),
                "logs": [],
            }

        payment_token = mapping["provider_operation_id"]

        logs = [{
            "gateway": "rylecode",
            "request": {"url": f"/api/v1/payments/{payment_token}", "params": {}},
            "status": None,
            "response": None,
            "kind": "status",
        }]

        try:
            resp = await self._get(f"/api/v1/payments/{payment_token}", token=token)
            try:
                js = resp.json()
            except Exception:
                js = {"raw_text": resp.text or ""}

            logs[-1]["status"] = resp.status_code
            logs[-1]["response"] = js

            if resp.status_code >= 400:
                duration = time.time() - start_time
                return {
                    "result": "OK",
                    "status": "pending",
                    "details": "Failed to get payment status",
                    "amount": None,
                    "currency": None,
                    "duration": round(duration, 3),
                    "logs": logs,
                }
        except Exception as e:
            logs[-1]["status"] = 599
            logs[-1]["response"] = {"error": str(e)}
            duration = time.time() - start_time
            return {
                "result": "OK",
                "status": "pending",
                "details": f"Gateway unreachable: {e}",
                "amount": None,
                "currency": None,
                "duration": round(duration, 3),
                "logs": logs,
            }

        # Parse response
        payment_data = js.get("payment") or {}
        provider_status = payment_data.get("status") or "pending"
        status_value = self._status_map(provider_status)

        # Update mapping with latest status
        await upsert_mapping(
            rp_token=mapping["rp_token"],
            order_number=mapping.get("order_number"),
            provider=self.name,
            callback_url=mapping["callback_url"],
            provider_operation_id=payment_token,
            status=provider_status,
            merchant_private_key=mapping.get("merchant_private_key"),
        )

        duration = time.time() - start_time

        return {
            "result": "OK",
            "status": status_value,
            "details": f"Payment status: {status_value}",
            "amount": payment_data.get("amount"),
            "currency": payment_data.get("currency"),
            "duration": round(duration, 3),
            "logs": logs,
        }

    # ---- Adapter API: REFUND ----
    async def refund(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create refund

        POST /api/v1/refunds
        """
        start_time = time.time()
        token = self._api_token(payload)

        # Get payment token from mapping
        key = payload.get("gateway_token") or payload.get("rp_token") or payload.get("order_number")
        mapping = await get_mapping_by_token_any(key) if key else None

        if not mapping or not mapping.get("provider_operation_id"):
            return {
                "result": "ERROR",
                "status": "declined",
                "details": "Payment not found",
                "amount": None,
                "currency": None,
                "logs": [],
            }

        payment_token = mapping["provider_operation_id"]

        # Refund amount (optional - if not provided, full refund)
        refund_amount = payload.get("amount")  # in cents

        body = {
            "token": payment_token
        }

        if refund_amount:
            body["amount"] = int(refund_amount)

        logs = [{
            "gateway": "rylecode",
            "request": {"url": "/api/v1/refunds", "params": body},
            "status": None,
            "response": None,
            "kind": "refund",
        }]

        try:
            resp = await self._post("/api/v1/refunds", json_payload=body, token=token)
            try:
                js = resp.json()
            except Exception:
                js = {"raw_text": resp.text or ""}

            logs[-1]["status"] = resp.status_code
            logs[-1]["response"] = js

            if resp.status_code >= 400 or not js.get("success"):
                duration = time.time() - start_time
                errors = js.get("errors", [])
                error_msg = str(errors[0]) if errors else f"HTTP {resp.status_code}"

                return {
                    "result": "ERROR",
                    "status": "declined",
                    "details": error_msg,
                    "amount": refund_amount,
                    "currency": None,
                    "duration": round(duration, 3),
                    "logs": logs,
                }
        except Exception as e:
            logs[-1]["status"] = 599
            logs[-1]["response"] = {"error": str(e)}
            duration = time.time() - start_time

            return {
                "result": "ERROR",
                "status": "declined",
                "details": str(e),
                "amount": refund_amount,
                "currency": None,
                "duration": round(duration, 3),
                "logs": logs,
            }

        # Parse successful response
        refund_data = js.get("refund") or {}
        refund_status = refund_data.get("status") or "approved"
        status_value = self._status_map(refund_status)

        duration = time.time() - start_time

        return {
            "result": "OK",
            "status": status_value,
            "details": f"Refund {status_value}",
            "amount": refund_data.get("amount"),
            "currency": refund_data.get("currency"),
            "duration": round(duration, 3),
            "logs": logs,
        }

    # ---- Adapter API: PAYOUT ----
    async def payout(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create payout

        POST /api/v1/payouts
        """
        start_time = time.time()
        token = self._api_token(payload)

        # Amount conversion
        amount_in_kopecks = int(payload.get("amount", 0))

        # Currency
        currency = payload.get("currency", "RUB")

        # Extract card details
        card_obj = payload.get("card")
        if not card_obj or not isinstance(card_obj, dict):
            return {
                "result": "ERROR",
                "status": "declined",
                "details": "Card details required for payout",
                "amount": amount_in_kopecks,
                "currency": currency,
                "logs": [],
            }

        pan = card_obj.get("pan") or card_obj.get("card_number")
        expires = card_obj.get("expires") or card_obj.get("expire_date")

        if not pan or not expires:
            return {
                "result": "ERROR",
                "status": "declined",
                "details": "Card PAN and expiry date required",
                "amount": amount_in_kopecks,
                "currency": currency,
                "logs": [],
            }

        # Format expiry date to mm/yyyy if needed
        if "/" not in expires:
            # Assume format is MMYY or MMYYYY
            if len(expires) == 4:
                expires = f"{expires[:2]}/{expires[2:]}"
            elif len(expires) == 6:
                expires = f"{expires[:2]}/20{expires[2:]}"

        # Build payout request
        body = {
            "amount": amount_in_kopecks,
            "currency": currency,
            "orderNumber": payload.get("order_number") or payload.get("rp_token"),
            "card": {
                "pan": pan,
                "expires": expires
            },
            "customer": {
                "email": (payload.get("customer") or {}).get("email") or "noreply@example.com",
                "address": (payload.get("customer") or {}).get("address") or "N/A",
                "ip": (payload.get("customer") or {}).get("ip") or "127.0.0.1"
            }
        }

        logs = [{
            "gateway": "rylecode",
            "request": {"url": "/api/v1/payouts", "params": {**body, "card": {"pan": f"{pan[:6]}******{pan[-4:]}", "expires": expires}}},
            "status": None,
            "response": None,
            "kind": "payout",
        }]

        try:
            resp = await self._post("/api/v1/payouts", json_payload=body, token=token)
            try:
                js = resp.json()
            except Exception:
                js = {"raw_text": resp.text or ""}

            logs[-1]["status"] = resp.status_code
            logs[-1]["response"] = js

            if resp.status_code >= 400 or not js.get("success"):
                duration = time.time() - start_time
                errors = js.get("errors", [])
                error_msg = str(errors[0]) if errors else f"HTTP {resp.status_code}"

                return {
                    "result": "ERROR",
                    "status": "declined",
                    "details": error_msg,
                    "amount": amount_in_kopecks,
                    "currency": currency,
                    "duration": round(duration, 3),
                    "logs": logs,
                }
        except Exception as e:
            logs[-1]["status"] = 599
            logs[-1]["response"] = {"error": str(e)}
            duration = time.time() - start_time

            return {
                "result": "ERROR",
                "status": "declined",
                "details": str(e),
                "amount": amount_in_kopecks,
                "currency": currency,
                "duration": round(duration, 3),
                "logs": logs,
            }

        # Parse successful response
        payout_data = js.get("payout") or {}
        payout_status = payout_data.get("status") or "pending"
        status_value = self._status_map(payout_status)
        payout_token = payout_data.get("token")

        # Save payout mapping
        if payout_token:
            await upsert_mapping(
                rp_token=payload.get("rp_token") or f"PAYOUT_{payout_token}",
                order_number=payload.get("order_number"),
                provider=self.name,
                callback_url=payload.get("callback_url", ""),
                provider_operation_id=payout_token,
                status=payout_status,
            )

        duration = time.time() - start_time

        return {
            "result": "OK",
            "status": status_value,
            "details": f"Payout {status_value}",
            "amount": amount_in_kopecks,
            "currency": currency,
            "duration": round(duration, 3),
            "logs": logs,
        }
