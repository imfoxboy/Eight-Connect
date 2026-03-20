import hashlib
from typing import Optional

IQONO_STATUS_TO_RP = {
    "SETTLED": "approved",
    "DECLINED": "declined",
    "VOID": "declined",
    "REVERSAL": "declined",
    "REFUND": "declined",
    "CHARGEBACK": "declined",
    "PENDING": "pending",
    "PREPARE": "pending",
    "3DS": "pending",
    "REDIRECT": "pending",
}

IQONO_RESULT_TO_RP = {
    "SUCCESS": "approved",
    "DECLINED": "declined",
    "REDIRECT": "pending",
    "ACCEPTED": "pending",
    "UNDEFINED": "pending",
    "ERROR": "declined",
}

METHOD_LIST = {
    "applepay": "applepay",
    "googlepay": "googlepay",
}


def map_iqono_to_rp(result: str, status: str) -> str:
    if status:
        rp = IQONO_STATUS_TO_RP.get(status.upper())
        if rp:
            return rp
    return IQONO_RESULT_TO_RP.get(result.upper() if result else "", "pending")


def parse_credentials(auth_token: str) -> tuple[str, str]:
    if ":" not in auth_token:
        raise ValueError("IQONO auth_token must be 'CLIENT_KEY:PASSWORD'")
    client_key, password = auth_token.split(":", 1)
    return client_key.strip(), password.strip()


def hash_sale(password: str, payer_email: str) -> str:
    """Published Appendix A Formula 8 for digital wallets."""
    raw = (payer_email[::-1] + password).upper()
    return hashlib.md5(raw.encode()).hexdigest()


def hash_trans(client_key: str, password: str, trans_id: str) -> str:
    raw = (password[::-1] + client_key + trans_id).upper()
    return hashlib.md5(raw.encode()).hexdigest()


def hash_callback(password: str, status: str, trans_id: str, amount: str, order_id: str) -> str:
    raw = (password[::-1] + status + trans_id + amount + order_id).upper()
    return hashlib.md5(raw.encode()).hexdigest()


_ZERO_EXPONENT = {"BIF", "CLP", "DJF", "GNF", "ISK", "KMF", "KRW", "PYG",
                  "RWF", "VND", "VUV", "XAF", "XOF", "XPF"}
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


def build_sale_payload(
    client_key: str,
    password: str,
    order_id: str,
    order_amount: float,
    order_currency: str,
    order_description: str,
    digital_wallet: str,
    payment_token: str,
    payer_ip: str,
    term_url_3ds: str,
    payer_email: Optional[str] = None,
    payer_first_name: Optional[str] = None,
    payer_last_name: Optional[str] = None,
    payer_address: Optional[str] = None,
    payer_country: Optional[str] = None,
    payer_city: Optional[str] = None,
    payer_zip: Optional[str] = None,
    payer_phone: Optional[str] = None,
) -> dict:
    amount_str = format_amount(order_amount, order_currency)
    payload = {
        "action": "SALE",
        "client_key": client_key,
        "order_id": order_id,
        "order_amount": amount_str,
        "order_currency": order_currency,
        "order_description": order_description,
        "digital_wallet": digital_wallet,
        "payment_token": payment_token,
        "payer_ip": payer_ip,
        "term_url_3ds": term_url_3ds,
        "hash": hash_sale(password=password, payer_email=payer_email or ""),
    }
    for key, val in [
        ("payer_email", payer_email),
        ("payer_first_name", payer_first_name),
        ("payer_last_name", payer_last_name),
        ("payer_address", payer_address),
        ("payer_country", payer_country),
        ("payer_city", payer_city),
        ("payer_zip", payer_zip),
        ("payer_phone", payer_phone),
    ]:
        if val:
            payload[key] = val
    return payload


def build_status_payload(client_key: str, password: str, trans_id: str) -> dict:
    return {
        "action": "GET_TRANS_STATUS",
        "client_key": client_key,
        "trans_id": trans_id,
        "hash": hash_trans(client_key, password, trans_id),
    }
