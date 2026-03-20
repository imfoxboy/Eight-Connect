import hashlib
from typing import Dict, Any, Optional

import httpx

from ...settings import settings
from ...utils.http import client, retry_policy
from ...db import upsert_mapping, get_mapping_by_token_any
from ...callbacks.rp_client import send_callback_to_rp
from ...providers.iqono import format_amount


# ─── Credential helper ────────────────────────────────────────────────────────

def parse_credentials(auth_token: str):
    """Split 'CLIENT_KEY:PASSWORD' into (client_key, password)."""
    if ":" not in auth_token:
        raise ValueError("auth_token must be 'CLIENT_KEY:PASSWORD'")
    client_key, password = auth_token.split(":", 1)
    return client_key.strip(), password.strip()


# ─── Hash formulas ────────────────────────────────────────────────────────────

def _hash_sale(client_key: str, password: str, order_id: str, amount_str: str, currency: str) -> str:
    """
    Formula 8 — digital wallet SALE:
    MD5(upper(strrev(password) + client_key + order_id + amount + currency))
    """
    raw = (password[::-1] + client_key + order_id + amount_str + currency).upper()
    return hashlib.md5(raw.encode()).hexdigest()


def _hash_trans(client_key: str, password: str, trans_id: str) -> str:
    """
    Formula 2 — GET_TRANS_STATUS / callback validation:
    MD5(upper(strrev(password) + client_key + trans_id))
    """
    raw = (password[::-1] + client_key + trans_id).upper()
    return hashlib.md5(raw.encode()).hexdigest()


def _hash_callback(password: str, status: str, trans_id: str, amount: str, order_id: str) -> str:
    """
    Formula 2 variant — incoming callback validation:
    MD5(upper(strrev(password) + status + trans_id + amount + order_id))
    """
    raw = (password[::-1] + status + trans_id + amount + order_id).upper()
    return hashlib.md5(raw.encode()).hexdigest()


# ─── Status mapping ───────────────────────────────────────────────────────────

def _map_status(result: str, status: str) -> str:
    _by_status = {
        "SETTLED":    "approved",
        "DECLINED":   "declined",
        "VOID":       "declined",
        "REVERSAL":   "declined",
        "REFUND":     "declined",
        "CHARGEBACK": "declined",
        "PENDING":    "pending",
        "PREPARE":    "pending",
        "3DS":        "pending",
        "REDIRECT":   "pending",
    }
    _by_result = {
        "SUCCESS":   "approved",
        "DECLINED":  "declined",
        "REDIRECT":  "pending",
        "ACCEPTED":  "pending",
        "UNDEFINED": "pending",
        "ERROR":     "declined",
    }
    if status:
        rp = _by_status.get(status.upper())
        if rp:
            return rp
    return _by_result.get((result or "").upper(), "pending")


# ─── Adapter ─────────────────────────────────────────────────────────────────

class IqonoAdapter:
    """
    IQONO S2S CARD — Apple Pay / Google Pay
    Docs: https://docs.iqono.com/docs/guides/s2s_card/

    settings.auth_token  = "CLIENT_KEY:PASSWORD"   (passed per-request by RP)
    settings.method      = "applepay" | "googlepay"
    params.payment_token = wallet token from Apple/Google Pay
    """

    name = "Iqono_ApplePay_GooglePay"

    def _gateway_url(self) -> str:
        return settings.IQONO_GATEWAY_URL.rstrip("/")

    def _auth(self, payload: Dict[str, Any]) -> tuple[str, str]:
        token = (
            payload.get("_provider_auth")
            or payload.get("auth_token")
            or settings.IQONO_AUTH_TOKEN
        )
        return parse_credentials(token)

    @retry_policy()
    async def _post_form(self, form_data: Dict[str, Any]) -> Dict[str, Any]:
        async with client(timeout_sec=30) as c:
            resp = await c.post(
                f"{self._gateway_url()}",
                data=form_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            return resp.json()

    # ─── pay ──────────────────────────────────────────────────────────────────

    async def pay(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Expects normalized payload from rp_endpoints._normalize_nested_payload()
        plus extra IQONO-specific fields extracted in the router:
          payload["_iqono_payment_token"]  — Apple/Google Pay wallet token
          payload["_iqono_digital_wallet"] — "applepay" | "googlepay"
        """
        try:
            client_key, password = self._auth(payload)
        except ValueError as e:
            return {"result": "declined", "gateway_token": None, "logs": [str(e)]}

        rp_token       = payload["rp_token"]
        order_id       = rp_token
        # RP sends gateway_amount in minor units (e.g. 10099 = $100.99)
        gateway_amount = payload.get("amount") or payload.get("gateway_amount") or 0
        order_amount   = gateway_amount / 100
        currency       = payload.get("currency", "USD")
        description    = payload.get("product") or f"Payment {rp_token}"
        callback_url   = payload["callback_url"]
        merchant_key   = payload.get("merchant_private_key")
        digital_wallet = payload.get("_iqono_digital_wallet", "applepay")
        payment_token  = payload.get("_iqono_payment_token", "")
        customer       = payload.get("customer") or {}
        payer_ip       = customer.get("ip") or payload.get("ip") or "0.0.0.0"
        term_url_3ds   = (
            payload.get("processing_url")
            or payload.get("redirect_success_url")
            or f"{settings.PUBLIC_BASE_URL}/3ds_return"
        )

        if not payment_token:
            return {
                "result":        "declined",
                "gateway_token": None,
                "logs":          ["payment_token is required for Apple Pay / Google Pay"],
            }

        amount_str = format_amount(order_amount, currency)
        form_data: Dict[str, Any] = {
            "action":            "SALE",
            "client_key":        client_key,
            "order_id":          order_id,
            "order_amount":      amount_str,
            "order_currency":    currency,
            "order_description": description,
            "digital_wallet":    digital_wallet,
            "payment_token":     payment_token,
            "payer_ip":          payer_ip,
            "term_url_3ds":      term_url_3ds,
            "hash": _hash_sale(client_key, password, order_id, amount_str, currency),
        }

        # Optional payer fields
        for field, value in [
            ("payer_email",      customer.get("email")),
            ("payer_first_name", customer.get("first_name") or customer.get("client_first_name")),
            ("payer_last_name",  customer.get("last_name")  or customer.get("client_last_name")),
            ("payer_address",    customer.get("address")),
            ("payer_country",    customer.get("country")),
            ("payer_city",       customer.get("city")),
            ("payer_zip",        customer.get("zip")),
            ("payer_phone",      customer.get("phone")),
        ]:
            if value:
                form_data[field] = value

        # Persist mapping BEFORE calling IQONO (webhook can arrive immediately)
        await upsert_mapping(
            rp_token=rp_token,
            provider=self.name,
            callback_url=callback_url,
            order_number=payload.get("order_number"),
            merchant_private_key=merchant_key,
            auth_password=password,
        )

        print(f"[IQONO] SALE → order_id={order_id} wallet={digital_wallet} amount={amount_str} {currency}")

        try:
            resp = await self._post_form(form_data)
        except Exception as exc:
            print(f"[IQONO] SALE error: {exc}")
            return {"result": "declined", "gateway_token": None, "logs": [str(exc)]}

        print(f"[IQONO] SALE ← {resp}")

        result   = resp.get("result", "")
        iqono_st = resp.get("status", "")
        trans_id = resp.get("trans_id", "")
        rp_st    = _map_status(result, iqono_st)

        if trans_id:
            await upsert_mapping(
                rp_token=rp_token,
                provider=self.name,
                callback_url=callback_url,
                provider_operation_id=trans_id,
                status=iqono_st,
            )

        # Immediately settled or declined → fire RP callback now
        if rp_st in ("approved", "declined"):
            tx = {
                "callback_url":        callback_url,
                "rp_token":            rp_token,
                "provider_operation_id": trans_id,
                "status":              rp_st,
                "amount":              int(gateway_amount),
                "currency":            currency,
                "merchant_private_key": merchant_key,
                "provider":            "iqono",
            }
            try:
                await send_callback_to_rp(tx)
            except Exception as e:
                print(f"[IQONO] RP callback failed: {e}")

        response: Dict[str, Any] = {
            "result":        rp_st,
            "gateway_token": trans_id or None,
            "logs":          [],
        }

        # 3DS redirect
        if result == "REDIRECT":
            response["redirect_request"] = {
                "url":    resp.get("redirect_url"),
                "method": resp.get("redirect_method", "POST"),
                "body":   resp.get("redirect_params", {}),
            }

        return response

    # ─── status ───────────────────────────────────────────────────────────────

    async def status(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        trans_id = payload.get("gateway_token") or ""
        if not trans_id:
            return {"result": "declined", "gateway_token": None, "logs": ["gateway_token required"]}

        try:
            client_key, password = self._auth(payload)
        except ValueError as e:
            return {"result": "declined", "gateway_token": trans_id, "logs": [str(e)]}

        form_data = {
            "action":     "GET_TRANS_STATUS",
            "client_key": client_key,
            "trans_id":   trans_id,
            "hash":       _hash_trans(client_key, password, trans_id),
        }

        print(f"[IQONO] GET_TRANS_STATUS → trans_id={trans_id}")

        try:
            resp = await self._post_form(form_data)
        except Exception as exc:
            print(f"[IQONO] GET_TRANS_STATUS error: {exc}")
            return {"result": "pending", "gateway_token": trans_id, "logs": [str(exc)]}

        print(f"[IQONO] GET_TRANS_STATUS ← {resp}")

        result   = resp.get("result", "")
        iqono_st = resp.get("status", "")

        return {
            "result":        _map_status(result, iqono_st),
            "gateway_token": trans_id,
            "logs":          [],
        }

    # ─── refund / payout (not supported) ─────────────────────────────────────

    async def refund(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"result": "declined", "logs": ["refund not supported for IQONO"]}

    async def payout(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"result": "declined", "logs": ["payout not supported for IQONO"]}

    async def confirm_secure_code(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"result": "not_applicable"}

    async def resend_otp(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"result": "not_applicable"}

    async def next_payment_step(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"result": "not_applicable"}
