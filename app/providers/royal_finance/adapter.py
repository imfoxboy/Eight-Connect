from typing import Dict, Any, Optional
import httpx
import time
from ...settings import settings
from ...utils.http import client, retry_policy
from ...db import upsert_mapping, get_mapping_by_token_any


class RoyalFinanceAdapter:
    """
    Royal Finance AZN P2P Integration:
      - POST /api/v1/payments/ (Payin - прием платежей, direct to payment page)
      - POST /api/v3/payouts/ (Payout - выплаты, callback-first)
      - GET /api/v2/accounts/me (Account info)

    Метод: AZN P2P (geo="Азербайджан")
    Типы: to_card_number, to_sbp_number, to_account_number
    Payment Form: page.royal-pay.cc/api/v1/payments/{id}
    """

    name = "RoyalFinance_AZN_P2P"

    def __init__(self):
        # Payin uses royal-finance.org
        self.payin_base_url = (settings.ROYAL_PAYIN_BASE_URL or "https://royal-finance.org").rstrip("/")
        # Payout uses royal-pay.org
        self.payout_base_url = (settings.ROYAL_PAYOUT_BASE_URL or "https://royal-pay.org").rstrip("/")
        # Form URL
        self.form_base_url = (settings.ROYAL_FORM_BASE_URL or "https://front.royal-finance.org").rstrip("/")

    def _api_token(self, payload: Dict[str, Any], operation: str = "payin") -> str:
        """Get API token with override support"""
        override = payload.get("_provider_auth")
        if override:
            print(f"[TOKEN_DEBUG] Using token from RP: {override[:10]}...{override[-10:]}")
            return override

        # Use different tokens for payin vs payout from .env
        if operation == "payout":
            print(f"[TOKEN_DEBUG] No token from RP, using .env payout token: {settings.ROYAL_PAYOUT_API_TOKEN[:10]}...{settings.ROYAL_PAYOUT_API_TOKEN[-10:]}")
            return settings.ROYAL_PAYOUT_API_TOKEN

        print(f"[TOKEN_DEBUG] No token from RP, using .env payin token: {settings.ROYAL_PAYIN_API_TOKEN[:10]}...{settings.ROYAL_PAYIN_API_TOKEN[-10:]}")
        return settings.ROYAL_PAYIN_API_TOKEN

    def _headers(self, token: str) -> Dict[str, str]:
        """Build authorization headers (Static Token format)"""
        return {
            "Authorization": f"Token {token}",
            "Content-Type": "application/json"
        }

    # ---- Status Mapping ----
    def _status_map(self, s: Optional[str]) -> str:
        """Map Royal Finance status to RP status (approved/declined/pending/refunded)"""
        sl = (s or "").lower()
        if sl in {"completed", "success", "approved"}:
            return "approved"
        if sl in {"canceled", "cancelled", "refund", "failed", "declined"}:
            return "declined"
        return "pending"  # created

    def _mask_card_number(self, card: str) -> str:
        """Mask card number for logs (show first 6 and last 4 digits)"""
        if not card or len(card) < 10:
            return "************"
        return f"{card[:6]}******{card[-4:]}"

    def _mask_sensitive_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively mask sensitive data in response for logs"""
        import copy
        masked = copy.deepcopy(data)

        # Fields to mask
        sensitive_fields = ["card_number", "sbp_phone_number", "account_number"]

        for field in sensitive_fields:
            if field in masked and masked[field]:
                if field == "card_number":
                    masked[field] = self._mask_card_number(str(masked[field]))
                elif field == "sbp_phone_number":
                    phone = str(masked[field])
                    if len(phone) > 4:
                        masked[field] = f"{phone[:2]}***{phone[-2:]}"
                elif field == "account_number":
                    account = str(masked[field])
                    if len(account) > 4:
                        masked[field] = f"***{account[-4:]}"

        return masked

    @retry_policy()
    async def _post(self, path: str, json_payload: Dict[str, Any], token: str, base_url: str = None) -> httpx.Response:
        """Make POST request to Royal Finance API"""
        url = base_url or self.payin_base_url
        async with client(timeout_sec=30) as c:
            return await c.post(
                f"{url}{path}",
                json=json_payload,
                headers=self._headers(token)
            )

    @retry_policy()
    async def _get(self, path: str, token: str, base_url: str = None) -> httpx.Response:
        """Make GET request to Royal Finance API"""
        url = base_url or self.payin_base_url
        async with client(timeout_sec=30) as c:
            return await c.get(
                f"{url}{path}",
                headers=self._headers(token)
            )

    # ---- Webhook Verification Methods ----
    async def _verify_payin_status(self, operation_id: str, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify Payin status via API
        GET /api/v2/payments/{provider_order_id}

        Returns payment data if successful, None if error
        """
        try:
            print(f"[ROYAL_FINANCE] Verifying Payin status for operation_id: {operation_id}")
            resp = await self._get(f"/api/v2/payments/{operation_id}", token=token)

            if resp.status_code != 200:
                print(f"[ROYAL_FINANCE] Payin status check failed: HTTP {resp.status_code}")
                return None

            data = resp.json()
            print(f"[ROYAL_FINANCE] Payin status from API: {data}")
            return data
        except Exception as e:
            print(f"[ROYAL_FINANCE] Exception during Payin status check: {e}")
            return None

    async def _verify_payout_status(self, outter_id: str, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify Payout status via API
        GET /api/v3/payouts/?outter_id={merchant_order_id}

        Returns payout data if successful, None if error
        """
        try:
            print(f"[ROYAL_FINANCE] Verifying Payout status for outter_id: {outter_id}")
            resp = await self._get(f"/api/v3/payouts/?outter_id={outter_id}", token=token, base_url=self.payout_base_url)

            if resp.status_code != 200:
                print(f"[ROYAL_FINANCE] Payout status check failed: HTTP {resp.status_code}")
                return None

            data = resp.json()
            print(f"[ROYAL_FINANCE] Payout status from API: {data}")

            # API returns array of payouts, get first match
            if isinstance(data, list) and len(data) > 0:
                return data[0]
            elif isinstance(data, dict):
                return data

            return None
        except Exception as e:
            print(f"[ROYAL_FINANCE] Exception during Payout status check: {e}")
            return None

    # ---- Build Requisites ----
    def _build_requisites_and_provider_data(
        self, payment_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build requisites and provider_response_data from Royal Finance payment response

        Royal Finance returns:
        - card_number: for to_card_number
        - sbp_phone_number: for to_sbp_number
        - account_number: for to_account_number
        - qr_code: QR code image URL
        - full_name: Recipient name
        """
        requisites: Optional[Dict[str, Any]] = None
        provider_response: Dict[str, Any] = {}

        transfer_method = payment_data.get("type") or payment_data.get("transfer_method") or ""
        # v1 API returns "bank_card", v2 API returns "card_number"
        card_number = payment_data.get("bank_card") or payment_data.get("card_number")
        # Ignore masked card numbers (e.g., "777744******7414")
        if card_number and '*' in str(card_number):
            card_number = None

        sbp_phone = payment_data.get("sbp_phone_number")
        account_number = payment_data.get("account_number")
        full_name = payment_data.get("full_name") or ""
        bank_name = payment_data.get("bank") or payment_data.get("russified_name") or ""
        qr_code = payment_data.get("qr_code")
        qr_code_link = payment_data.get("qr_code_link")
        nspk_url = payment_data.get("nspk_url")
        deeplink_url = payment_data.get("deeplink_url")

        # Determine requisite type based on method
        if card_number:
            # CARD type - flat structure matching Metricengine format
            requisites = {
                "pan": card_number,
                "holder": full_name,
                "bank_name": bank_name
            }
            provider_response = {
                "card_number": card_number,
                "holder": full_name,
                "bank": bank_name,
                "qr_code": qr_code or qr_code_link,
                "transfer_method": transfer_method
            }
        elif sbp_phone:
            # SBP type
            requisites = {
                "pan": sbp_phone,
                "holder": full_name,
                "bank_name": bank_name
            }
            provider_response = {
                "sbp_phone_number": sbp_phone,
                "holder": full_name,
                "bank": bank_name,
                "qr_code": qr_code or qr_code_link,
                "nspk_url": nspk_url,
                "transfer_method": transfer_method
            }
        elif account_number:
            # ACCOUNT type
            requisites = {
                "account": account_number,
                "holder": full_name,
                "bank_name": bank_name
            }
            provider_response = {
                "account_number": account_number,
                "holder": full_name,
                "bank": bank_name,
                "qr_code": qr_code or qr_code_link,
                "transfer_method": transfer_method
            }
        elif qr_code or qr_code_link or nspk_url or deeplink_url:
            # LINK type (QR/NSPK/Deeplink)
            link_url = deeplink_url or nspk_url or qr_code_link or qr_code
            requisites = {
                "link": {"url": link_url},
                "holder": full_name,
                "bank_name": bank_name
            }
            provider_response = {
                "qr_code": qr_code,
                "qr_code_link": qr_code_link,
                "nspk_url": nspk_url,
                "deeplink_url": deeplink_url,
                "holder": full_name,
                "bank": bank_name,
                "transfer_method": transfer_method
            }
        else:
            # Fallback
            requisites = {}
            provider_response = {
                "holder": full_name,
                "bank": bank_name,
                "transfer_method": transfer_method
            }

        # Add all payment details to provider_response_data
        provider_response.update({
            "id": payment_data.get("id"),
            "payment_gateway_id": payment_data.get("payment_gateway_id"),
            "status": payment_data.get("status"),
            "sum": payment_data.get("sum"),
            "currency": payment_data.get("currency"),
            "bik": payment_data.get("bik"),
            "work_country": payment_data.get("work_country"),
            "link": payment_data.get("link"),
            "merchant_payment_detail": payment_data.get("merchant_payment_detail")
        })

        return {
            "requisites": requisites,
            "provider_response_data": provider_response
        }

    # ---- Adapter API: PAY ----
    async def pay(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create Payin (прием платежей) via Royal Finance

        POST /api/v2/payments/
        """
        start_time = time.time()
        token = self._api_token(payload)

        # Amount conversion: RP sends in kopecks, Royal Finance expects whole units
        amount_in_kopecks = int(payload["amount"])
        amount = amount_in_kopecks / 100

        # Determine payment type (default to_card_number for AZN)
        # Valid types: to_card_number, to_sbp_number, to_account_number
        payment_type = payload.get("_provider_method") or "to_card_number"

        # Map RP payment_method to Royal Finance type if needed
        if payment_type in ["AZN_P2P", "azn_p2p", "P2P", "p2p"]:
            payment_type = "to_card_number"  # Default to card for P2P payments

        # Get bank if specified
        bank = payload.get("bank")

        # Get redirect URLs from payload
        success_url = payload.get("redirect_success_url") or payload.get("success_url")
        fail_url = payload.get("redirect_fail_url") or payload.get("fail_url")

        # Build Royal Finance request (v1 API uses "amount" not "sum")
        body = {
            "amount": amount,
            "transfer_method": payment_type,  # v1 API uses "transfer_method" not "type"
            "geo": "Азербайджан",  # AZN P2P
            "client_id": (payload.get("customer") or {}).get("client_id") or payload.get("rp_token"),
            "outter_id": payload.get("order_number"),
            "callback_url": f"{settings.PUBLIC_BASE_URL.rstrip('/')}/provider/royal_finance/webhook",
            # Required fields for form display
            "redirect_url": success_url or "https://success.foxew.com/",
            "success_redirect_url": success_url or "https://success.foxew.com/",
            "fail_redirect_url": fail_url or "https://declined.foxew.com/",
        }

        # Add optional fields
        if bank:
            body["bank"] = bank

        # is_allow_another_amount support
        if payload.get("is_allow_another_amount"):
            body["is_allow_another_amount"] = True

        # Full URL for logs
        full_url = f"{self.payin_base_url}/api/v1/payments/"

        logs = [{
            "gateway": "royal_finance",
            "request": {"url": full_url, "params": body},
            "status": None,
            "response": None,
            "kind": "pay",
        }]

        try:
            resp = await self._post("/api/v1/payments/", json_payload=body, token=token)
            try:
                js = resp.json()
            except Exception as json_err:
                js = {"raw_text": resp.text or "", "json_parse_error": str(json_err)}

            logs[-1]["status"] = resp.status_code
            logs[-1]["response"] = self._mask_sensitive_data(js)  # Mask sensitive data in logs

            # Handle errors
            if resp.status_code >= 400:
                duration = time.time() - start_time
                error_msg = js.get("error") or js.get("message") or f"HTTP {resp.status_code}"

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
                    "result": "declined",
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
                "result": "declined",
                "requisites": {},
                "redirectRequest": {"url": None, "type": "post_iframes", "iframes": []},
                "duration": round(duration, 3),
                "logs": logs,
                "gateway_details": {
                    "status": "declined"
                }
            }

        # Parse response
        # page.royal-pay.cc returns "payment_gateway_id", royal-finance.org returns "id"
        gateway_token = str(js.get("payment_gateway_id") or js.get("id") or "")
        provider_status = js.get("status") or "created"
        status_value = self._status_map(provider_status)  # approved/declined/pending
        result_value = status_value  # result = status in RP format

        if not gateway_token:
            duration = time.time() - start_time

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
                "result": "declined",
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
        print(f"[ROYAL FINANCE] About to save mapping with merchant_private_key: {mpk}")
        await upsert_mapping(
            rp_token=payload["rp_token"],
            order_number=payload.get("order_number"),
            provider=self.name,
            callback_url=payload["callback_url"],
            provider_operation_id=gateway_token,
            status=provider_status,
            merchant_private_key=mpk,
        )

        # Build requisites and provider data
        built = self._build_requisites_and_provider_data(js)
        requisites = built["requisites"]
        provider_response_data = built["provider_response_data"]

        # Build redirect request following mengine pattern:
        # if wrapped_to_json_response: redirect to processing_url with type "get_with_processing"
        # else: redirect to charge_page_url with type "post"

        is_wrapped = payload.get("wrapped_to_json_response")
        processing_url = payload.get("processing_url")
        charge_page_url = payload.get("charge_page_url")

        # Check if we have card requisites (P2P mode)
        # Note: We now use "pan" for card numbers to match RP's expected field name
        has_card_requisites = requisites and ("card" in requisites or "pan" in requisites)

        print(f"[ROYAL FINANCE] wrapped_to_json_response: {is_wrapped}")
        print(f"[ROYAL FINANCE] processing_url: {processing_url}")
        print(f"[ROYAL FINANCE] charge_page_url: {charge_page_url}")
        print(f"[ROYAL FINANCE] Has card requisites: {has_card_requisites}")
        print(f"[ROYAL FINANCE] Requisites: {requisites}")

        # Check if Royal Finance returned a payment form link in response
        # v1 API returns "link" field: https://page.royal-pay.cc/payment/{uuid}
        payment_link = js.get("link") or js.get("form_url") or js.get("deeplink_url")

        print(f"[ROYAL FINANCE] Payment link from response: {payment_link}")

        # Determine redirect based on available data
        if is_wrapped:
            # Wrapped mode (wrapped_to_json_response: true) - show requisites on processing page
            redirect_request = {
                "url": processing_url,
                "type": "get_with_processing"
            }
            print(f"[ROYAL FINANCE] Wrapped mode - redirect to processing_url: {processing_url}")
        elif payment_link:
            # Royal Finance returned a payment form link - use it!
            # Fix old domain if needed
            if "front.royal-finance.org" in payment_link:
                payment_link = payment_link.replace("https://front.royal-finance.org", self.form_base_url)

            redirect_request = {
                "url": payment_link,
                "type": "redirect"
            }
            print(f"[ROYAL FINANCE] Using payment link from Royal Finance response: {payment_link}")
        elif has_card_requisites:
            # Have requisites but no link - redirect to RP's processing page
            redirect_request = {
                "url": processing_url,
                "type": "post"
            }
            print(f"[ROYAL FINANCE] No payment link - redirect to processing_url with requisites: {processing_url}")
        else:
            # No link and no requisites - fallback
            redirect_url = processing_url or charge_page_url
            redirect_type = "get_with_processing" if processing_url else "post"
            redirect_request = {"url": redirect_url, "type": redirect_type}
            print(f"[ROYAL FINANCE] Fallback redirect: {redirect_url}")

        # Return response in RP format
        duration = time.time() - start_time
        return {
            "status": "OK",
            "gateway_token": gateway_token or None,
            "result": result_value,  # success/declined
            "requisites": requisites,
            "redirectRequest": redirect_request,
            "duration": round(duration, 3),
            "logs": logs,
            # Add gateway_details with status for RP
            "gateway_details": {
                "status": status_value  # approved/declined/pending
            }
        }

    # ---- Adapter API: STATUS ----
    async def status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check payment status

        Royal Finance doesn't have a direct status endpoint in the docs,
        so we'll rely on webhook updates and DB status
        """
        start_time = time.time()
        token = self._api_token(payload)

        key = payload.get("gateway_token") or payload.get("rp_token") or payload.get("order_number")
        mapping = await get_mapping_by_token_any(key) if key else None

        if not mapping:
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

        # Get status from DB (updated by webhooks)
        db_status = mapping.get("status") or "created"
        status_value = self._status_map(db_status)  # approved/declined/pending

        duration = time.time() - start_time

        # Match mengine format: result, status, details, amount, currency, logs
        return {
            "result": "OK",
            "status": status_value,  # approved/declined/pending
            "details": f"Transaction is {status_value}",
            "amount": None,  # Not stored in mapping
            "currency": "AZN",
            "duration": round(duration, 3),
            "logs": [{
                "gateway": "royal_finance",
                "request": {"url": "status_check_from_db", "params": {"key": key}},
                "status": 200,
                "response": {"status": db_status, "source": "database"},
                "kind": "status",
            }],
        }

    # ---- Adapter API: REFUND ----
    async def refund(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Refund not supported in Royal Finance API docs
        Would need to create appeal manually
        """
        return {
            "result": "ERROR",
            "status": "declined",
            "details": "Refund not supported by Royal Finance API. Please create appeal manually.",
            "amount": None,
            "currency": None,
            "logs": [],
        }

    # ---- Adapter API: PAYOUT ----
    async def payout(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create Payout via Royal Finance v3 (callback-first)

        POST /api/v3/payouts/

        This uses callback confirmation flow:
        1. We send payout request
        2. Royal Finance sends callback to our webhook
        3. We confirm with 200 OK
        4. They activate payout
        """
        start_time = time.time()
        token = self._api_token(payload, operation="payout")

        # DEBUG: Log incoming payout payload
        print(f"\n{'='*80}")
        print(f"[ROYAL_FINANCE_PAYOUT] Using token: {token[:10]}...{token[-10:] if len(token) > 20 else ''}")
        print(f"[ROYAL_FINANCE_PAYOUT] _provider_auth from payload: {payload.get('_provider_auth')}")
        print(f"{'='*80}")
        print(f"[ROYAL_FINANCE_PAYOUT] Incoming payload:")
        print(f"  - order_number: {payload.get('order_number')}")
        print(f"  - rp_token: {payload.get('rp_token')}")
        print(f"  - amount: {payload.get('amount')}")
        print(f"  - currency: {payload.get('currency')}")
        print(f"  - card: {payload.get('card')}")
        print(f"  - requisite: {payload.get('requisite')}")
        print(f"  - method: {payload.get('method')}")
        print(f"  - processingUrl: {payload.get('processingUrl')}")
        print(f"{'='*80}\n")

        # Extract payout details
        amount_in_kopecks = int(payload.get("amount", 0))
        amount = amount_in_kopecks / 100

        # Get requisite (card number, phone, account)
        # RP sends card details as object: {"pan": "...", "expire_date": "...", "cvv": "..."}
        card_obj = payload.get("card")
        if card_obj and isinstance(card_obj, dict):
            requisite = card_obj.get("pan") or card_obj.get("card_number")
        else:
            requisite = payload.get("requisite") or payload.get("card_number") or payload.get("phone") or payload.get("account")

        print(f"[ROYAL_FINANCE_PAYOUT] Extracted requisite: {(requisite[:4] + '****' + requisite[-4:]) if requisite else 'NONE'}")

        # Royal Finance requires card details upfront - cannot proceed without requisite
        if not requisite:
            print(f"[ROYAL_FINANCE_PAYOUT] ERROR - no requisite provided")
            print(f"  Royal Finance requires card details to create payout")
            print(f"{'='*80}\n")

            # Return error - RP must provide card details
            return {
                "result": "ERROR",
                "status": "declined",
                "details": "Card details required for payout. Please provide card information.",
                "amount": amount_in_kopecks,
                "currency": "AZN",
                "logs": [{
                    "gateway": "royal_finance",
                    "request": {"note": "Payout rejected - missing card details"},
                    "status": 400,
                    "response": {"error": "requisite_required"},
                    "kind": "payout",
                }],
            }

        # Determine method (default to INTERBANK for card numbers)
        method = payload.get("method") or "INTERBANK"

        # Get bank (required for SBP)
        bank = payload.get("bank")

        # Build Royal Finance payout request
        body = {
            "method": method,
            "requisite": requisite,
            "amount": int(amount),  # Royal Finance expects integer for payouts
            "outter_id": payload.get("order_number") or payload.get("rp_token"),
            "geo": "Азербайджан",  # Azerbaijan geo for AZN P2P
        }

        if bank:
            body["bank"] = bank

        # Full URL for logs
        full_payout_url = f"{self.payout_base_url}/api/v3/payouts/"

        # Mask requisite in logs (card/phone/account)
        body_for_logs = body.copy()
        if requisite:
            if len(requisite) >= 10:  # Likely a card number
                body_for_logs["requisite"] = self._mask_card_number(requisite)
            elif len(requisite) > 4:  # Phone or account
                body_for_logs["requisite"] = f"{requisite[:2]}***{requisite[-2:]}"

        logs = [{
            "gateway": "royal_finance",
            "request": {"url": full_payout_url, "params": body_for_logs},
            "status": None,
            "response": None,
            "kind": "payout",
        }]

        print(f"[ROYAL_FINANCE_PAYOUT] Sending request to Royal Finance:")
        print(f"  URL: {full_payout_url}")
        print(f"  Body: {body_for_logs}")

        try:
            resp = await self._post("/api/v3/payouts/", json_payload=body, token=token, base_url=self.payout_base_url)
            try:
                js = resp.json()
            except Exception:
                js = {"raw_text": resp.text or ""}

            logs[-1]["status"] = resp.status_code
            logs[-1]["response"] = self._mask_sensitive_data(js)  # Mask sensitive data in logs

            print(f"[ROYAL_FINANCE_PAYOUT] Royal Finance response:")
            print(f"  Status: {resp.status_code}")
            print(f"  Response: {js}")

            # Handle errors
            if resp.status_code >= 400:
                duration = time.time() - start_time
                error_msg = js.get("error") or js.get("message") or js.get("amount") or f"HTTP {resp.status_code}"
                print(f"[ROYAL_FINANCE_PAYOUT] ERROR: {error_msg}")
                return {
                    "result": "ERROR",
                    "status": "declined",
                    "details": str(error_msg),
                    "amount": amount_in_kopecks,
                    "currency": "AZN",
                    "duration": round(duration, 3),
                    "logs": logs,
                }
        except Exception as e:
            duration = time.time() - start_time
            logs[-1]["status"] = 599
            logs[-1]["response"] = {"error": str(e)}
            print(f"[ROYAL_FINANCE_PAYOUT] EXCEPTION: {str(e)}")
            return {
                "result": "ERROR",
                "status": "declined",
                "details": f"Payout request failed: {str(e)}",
                "amount": amount_in_kopecks,
                "currency": "AZN",
                "duration": round(duration, 3),
                "logs": logs,
            }

        # Parse response
        payout_id = str(js.get("id") or "")
        payout_status = js.get("status") or "wait_confirm"
        status_value = self._status_map(payout_status)  # approved/declined/pending

        print(f"[ROYAL_FINANCE_PAYOUT] Parsed response:")
        print(f"  Payout ID: {payout_id}")
        print(f"  Payout Status: {payout_status}")
        print(f"  Normalized Status: {status_value}")

        # Save payout mapping
        await upsert_mapping(
            rp_token=payload.get("rp_token") or f"PAYOUT_{payout_id}",
            order_number=payload.get("order_number"),
            provider=self.name,
            callback_url=payload.get("callback_url", ""),
            provider_operation_id=payout_id,
            status=payout_status,
        )

        duration = time.time() - start_time

        final_response = {
            "result": "OK",
            "status": status_value,  # approved/declined/pending
            "details": f"Payout created: {payout_status}",
            "amount": amount_in_kopecks,
            "currency": "AZN",
            "duration": round(duration, 3),  # Processing time in seconds
            "logs": logs,
        }

        print(f"[ROYAL_FINANCE_PAYOUT] Returning final response:")
        print(f"  {final_response}")
        print(f"{'='*80}\n")

        return final_response
