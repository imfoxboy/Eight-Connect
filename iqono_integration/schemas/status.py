from pydantic import BaseModel


class StatusSettings(BaseModel):
    auth_token: str   # "MERCHANT_KEY:PASSWORD"


class StatusPayment(BaseModel):
    gateway_token: str   # IQONO payment_id (returned in callback as "id")


class StatusRequest(BaseModel):
    settings: StatusSettings
    payment: StatusPayment
