"""
IQONO Checkout — payload builders, hash functions, status mapping.

Checkout hash formula (from IQONO docs):
    SHA1( MD5( UPPER( order_number + order_amount + order_currency + order_description + password ) ) )

Callback hash formula (same):
    SHA1( MD5( UPPER( payment_public_id + order_number + order_amount + order_currency + order_description + password ) ) )

Status request hash:
    SHA1( MD5( UPPER( payment_id + password ) ) )
"""

import hashlib
from typing import Optional


# ---------------------------------------------------------------------------
# Payment method list (kept for RP compatibility)
# ---------------------------------------------------------------------------

METHOD_LIST = {
    "card": "card",
    "applepay": "applepay",
    "googlepay": "googlepay",
}

# ---------------------------------------------------------------------------
# IQONO → RP status mapping
# ---------------------------------------------------------------------------

_IQONO_ORDER_STATUS_TO_RP = {
    "SETTLED":    "approved",
    "DECLINE":    "declined",
    "DECLINED":   "declined",
    "REFUND":     "declined",
    "REVERSAL":   "declined",
    "VOID":       "declined",
    "CHARGEBACK": "declined",
    "PENDING":    "pending",
    "PREPARE":    "pending",
    "3DS":        "pending",
    "REDIRECT":   "pending",
}

_IQONO_TX_STATUS_TO_RP = {
    "SUCCESS":   "approved",
    "FAIL":      "declined",
    "WAITING":   "pending",
    "UNDEFINED": "pending",
}


def map_to_rp(order_status: str, tx_status: str) -> str:
    """Map IQONO Checkout callback statuses to RP status.

    In the Checkout callback:
      - ``order_status`` is the payment-level status (settled, decline, pending, …)
      - ``status`` (here ``tx_status``) is the transaction-level status (success, fail, waiting, undefined)

    We prefer order_status when available; fall back to tx_status.
    """
    if order_status:
        rp = _IQONO_ORDER_STATUS_TO_RP.get(order_status.upper())
        if rp:
            return rp
    if tx_status:
        rp = _IQONO_TX_STATUS_TO_RP.get(tx_status.upper())
        if rp:
            return rp
    return "pending"


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def parse_credentials(auth_token: str) -> tuple[str, str]:
    """Parse 'MERCHANT_KEY:PASSWORD' into (merchant_key, password)."""
    if ":" not in auth_token:
        raise ValueError("auth_token must be 'MERCHANT_KEY:PASSWORD'")
    merchant_key, password = auth_token.split(":", 1)
    return merchant_key.strip(), password.strip()


# ---------------------------------------------------------------------------
# Hash functions — IQONO Checkout
# ---------------------------------------------------------------------------

def _sha1_of_md5(raw: str) -> str:
    """SHA1(MD5(raw)) — hex digests, as required by IQONO Checkout."""
    md5_hex = hashlib.md5(raw.encode("utf-8")).hexdigest()
    sha1_hex = hashlib.sha1(md5_hex.encode("utf-8")).hexdigest()
    return sha1_hex


def hash_session(
    order_number: str,
    order_amount: str,
    order_currency: str,
    order_description: str,
    password: str,
) -> str:
    """Hash for the /api/v1/session (Authentication) request.

    Formula: SHA1( MD5( UPPER( number + amount + currency + description + password ) ) )
    """
    raw = (order_number + order_amount + order_currency + order_description + password).upper()
    return _sha1_of_md5(raw)


def hash_callback(
    payment_id: str,
    order_number: str,
    order_amount: str,
    order_currency: str,
    order_description: str,
    password: str,
) -> str:
    """Hash for callback verification.

    Formula: SHA1( MD5( UPPER( payment_public_id + order.number + order.amount
                               + order.currency + order.description + merchant.pass ) ) )
    """
    raw = (payment_id + order_number + order_amount + order_currency + order_description + password).upper()
    return _sha1_of_md5(raw)


def hash_status(payment_id: str, password: str) -> str:
    """Hash for /api/v1/payment/status request.

    Formula: SHA1( MD5( UPPER( payment_id + password ) ) )
    """
    raw = (payment_id + password).upper()
    return _sha1_of_md5(raw)


# ---------------------------------------------------------------------------
# Amount formatting (unchanged — shared across both protocols)
# ---------------------------------------------------------------------------

_ZERO_EXPONENT = {
    "BIF", "CLP", "DJF", "GNF", "ISK", "KMF", "KRW", "PYG",
    "RWF", "VND", "VUV", "XAF", "XOF", "XPF",
}
_THREE_EXPONENT = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}
_SPECIAL_TWO_EXPONENT = {"UGX", "JPY"}


def format_amount(amount: float, currency: str) -> str:
    cur = currency.upper()
    if cur in _THREE_EXPONENT:
        return f"{amount:.3f}"
    if cur in _SPECIAL_TWO_EXPONENT:
        return f"{amount:.2f}"
    if cur in _ZERO_EXPONENT:
        return str(int(amount))
    return f"{amount:.2f}"


# ---------------------------------------------------------------------------
# Checkout session payload builder
# ---------------------------------------------------------------------------

def build_checkout_session_payload(
    merchant_key: str,
    password: str,
    order_number: str,
    order_amount: float,
    order_currency: str,
    order_description: str,
    success_url: str,
    cancel_url: Optional[str] = None,
    methods: Optional[list[str]] = None,
    customer_name: Optional[str] = None,
    customer_email: Optional[str] = None,
    billing_country: Optional[str] = None,
    billing_state: Optional[str] = None,
    billing_city: Optional[str] = None,
    billing_address: Optional[str] = None,
    billing_zip: Optional[str] = None,
    billing_phone: Optional[str] = None,
    req_token: bool = False,
    card_token: Optional[list[str]] = None,
    recurring_init: bool = False,
    session_expiry: int = 60,
    channel_id: Optional[str] = None,
    custom_data: Optional[dict] = None,
) -> dict:
    """Build the JSON body for POST /api/v1/session (Checkout Authentication).

    Returns a dict ready to be serialized as JSON.
    """
    amount_str = format_amount(order_amount, order_currency)

    payload: dict = {
        "merchant_key": merchant_key,
        "operation": "purchase",
        "order": {
            "number": order_number,
            "amount": amount_str,
            "currency": order_currency,
            "description": order_description,
        },
        "success_url": success_url,
        "hash": hash_session(
            order_number=order_number,
            order_amount=amount_str,
            order_currency=order_currency,
            order_description=order_description,
            password=password,
        ),
    }

    if cancel_url:
        payload["cancel_url"] = cancel_url
    if methods:
        payload["methods"] = methods
    if session_expiry and session_expiry != 60:
        payload["session_expiry"] = session_expiry
    if channel_id:
        payload["channel_id"] = channel_id
    if req_token:
        payload["req_token"] = True
    if card_token:
        payload["card_token"] = card_token
    if recurring_init:
        payload["recurring_init"] = True

    # Customer object
    customer: dict = {}
    if customer_name:
        customer["name"] = customer_name
    if customer_email:
        customer["email"] = customer_email
    if customer:
        payload["customer"] = customer

    # Billing address object
    billing: dict = {}
    if billing_country:
        billing["country"] = billing_country
    if billing_state:
        billing["state"] = billing_state
    if billing_city:
        billing["city"] = billing_city
    if billing_address:
        billing["address"] = billing_address
    if billing_zip:
        billing["zip"] = billing_zip
    if billing_phone:
        billing["phone"] = billing_phone
    if billing:
        payload["billing_address"] = billing

    if custom_data:
        payload["custom_data"] = custom_data

    return payload


# ---------------------------------------------------------------------------
# Status request payload builder
# ---------------------------------------------------------------------------

def build_status_payload(merchant_key: str, password: str, payment_id: str) -> dict:
    """Build JSON body for POST /api/v1/payment/status."""
    return {
        "merchant_key": merchant_key,
        "payment_id": payment_id,
        "hash": hash_status(payment_id, password),
    }
