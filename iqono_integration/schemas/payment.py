from pydantic import BaseModel
from typing import Optional


class PaymentSettings(BaseModel):
    auth_token: str                              # "MERCHANT_KEY:PASSWORD"
    callback_token: Optional[str] = None
    method: Optional[str] = None                 # optional: "card", "applepay", "googlepay"
    wrapped_to_json_response: Optional[bool] = False


class PaymentInfo(BaseModel):
    token: str                                   # RP payment token (becomes order.number)
    gateway_amount: float                        # minor units (e.g. 10099 = $100.99)
    gateway_currency: str
    ip: Optional[str] = None
    merchant_private_key: Optional[str] = None
    redirect_success_url: Optional[str] = None
    redirect_fail_url: Optional[str] = None
    order_number: Optional[str] = None
    product: Optional[str] = None


class CustomerInfo(BaseModel):
    email: Optional[str] = None
    ip: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    address: Optional[str] = None
    country: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    zip: Optional[str] = None
    phone: Optional[str] = None


class PayParams(BaseModel):
    customer: Optional[CustomerInfo] = None
    extra_return_param: Optional[str] = None
    # card_token for returning customers (Checkout tokenization)
    card_token: Optional[list[str]] = None
    # channel_id for routing to specific MID
    channel_id: Optional[str] = None
    # custom_data to pass through to callback
    custom_data: Optional[dict] = None


class PayRequest(BaseModel):
    settings: PaymentSettings
    payment: PaymentInfo
    params: Optional[PayParams] = None
    processing_url: Optional[str] = None
    charge_page_url: Optional[str] = None
    callback_url: str
