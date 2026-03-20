from pydantic import BaseModel
from typing import Optional, Any


class PaymentSettings(BaseModel):
    auth_token: str
    callback_token: Optional[str] = None
    method: Optional[str] = None
    wrapped_to_json_response: Optional[bool] = False


class PaymentInfo(BaseModel):
    token: str
    gateway_amount: float
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
    city: Optional[str] = None
    zip: Optional[str] = None
    phone: Optional[str] = None


class PayParams(BaseModel):
    customer: Optional[CustomerInfo] = None
    extra_return_param: Optional[str] = None
    payment_token: Optional[str] = None   # Apple Pay / Google Pay wallet token
    order_number: Optional[str] = None


class PayRequest(BaseModel):
    settings: PaymentSettings
    payment: PaymentInfo
    params: Optional[PayParams] = None
    processing_url: Optional[str] = None
    charge_page_url: Optional[str] = None
    callback_url: str


class StatusSettings(BaseModel):
    auth_token: str


class StatusPayment(BaseModel):
    gateway_token: str


class StatusRequest(BaseModel):
    settings: StatusSettings
    payment: StatusPayment
