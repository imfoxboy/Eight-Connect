from pydantic import BaseModel
from typing import Optional


class IqonoCheckoutCallback(BaseModel):
    """IQONO Checkout sends callbacks as application/x-www-form-urlencoded.

    Field names match the Checkout documentation:
    - ``id`` is the IQONO payment public ID
    - ``order_number`` is the merchant's order ID (our rp_token)
    - ``order_status`` is the payment-level status (settled, decline, pending, …)
    - ``type`` is the operation type (sale, 3ds, redirect, capture, refund, …)
    - ``status`` is the transaction-level status (success, fail, waiting, undefined)
    """
    id: Optional[str] = None                    # IQONO payment public ID
    order_number: Optional[str] = None          # our rp_token / order number
    order_amount: Optional[str] = None          # e.g. "100.99"
    order_currency: Optional[str] = None        # e.g. "USD"
    order_description: Optional[str] = None     # product description
    order_status: Optional[str] = None          # settled, decline, pending, 3ds, redirect, …
    type: Optional[str] = None                  # sale, 3ds, redirect, capture, refund, void, …
    status: Optional[str] = None                # success, fail, waiting, undefined
    reason: Optional[str] = None                # decline/error reason

    card: Optional[str] = None                  # masked card number
    card_expiration_date: Optional[str] = None
    card_token: Optional[str] = None            # if req_token was enabled

    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_country: Optional[str] = None
    customer_state: Optional[str] = None
    customer_city: Optional[str] = None
    customer_address: Optional[str] = None
    customer_ip: Optional[str] = None

    date: Optional[str] = None                  # transaction date
    digital_wallet: Optional[str] = None        # googlepay, applepay
    pan_type: Optional[str] = None              # dpan, fpan

    recurring_init_trans_id: Optional[str] = None
    recurring_token: Optional[str] = None
    schedule_id: Optional[str] = None

    hash: Optional[str] = None                  # signature for validation
