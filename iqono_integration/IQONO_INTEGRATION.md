# IQONO Integration (Gateway Connector) — Hosted Checkout / HPP

Methods:
Payin: Hosted Checkout (Apple Pay / Google Pay) → (checkout session creation + redirect + callback)

Payout: NOT SUPPORTED

Docs: https://docs.iqono.com/docs/guides/checkout_integration

Hosted Checkout Page (HPP):
https://pay.iqono.com/

For API access, merchant credentials are required.

API Credentials

Merchant credentials are passed from RP via:

settings.auth_token = MERCHANT_KEY:PASSWORD

Where:
- MERCHANT_KEY — merchant key from IQONO admin panel
- PASSWORD — merchant password / secret used for hash generation

Webhook / Callback Security

Connector → RP callback uses:
- callback_token — for Authorization header when calling RP callback endpoint
- SIGN_KEY — for JWT signing (HS512)

Important:
- callback_token is NOT equal to SIGN_KEY
- callback_token is used in HTTP Authorization header
- SIGN_KEY is used to sign JWT payload
- secure block contains encrypted merchant_private_key (AES-256-CBC)
- secure block is included only in JWT payload, not in plain HTTP body

Overview

This is a standalone gateway connector that translates RP payment requests into IQONO Hosted Payment Page (HPP) format.

This integration is NOT direct S2S card acquiring.

The customer is redirected to IQONO hosted checkout page, where the payment is completed on provider side using:
- Apple Pay
- Google Pay

Settings

Base URL: Your deployed connector URL (for example: https://iqono-connector.example.com)

RP Configuration Example:

{
    "USD": {
        "gateways": {
            "pay": {
                "providers": [
                    {
                        "iqono": "iqono_checkout"
                    }
                ]
            }
        }
    },
    "gateways": {
        "iqono_checkout": {
            "auth_token": "MERCHANT_KEY:PASSWORD",
            "callback_token": "rp_callback_token",
            "class": "iqono_checkout",
            "enable_change_final_status": true,
            "enable_update_amount": false,
            "masked_provider": true,
            "not_internal_page": false,
            "payment_method": "card",
            "provider": "iqono_checkout",
            "wrapped_to_json_response": false
        }
    }
}

Tokens Explanation

- auth_token
  - Format: MERCHANT_KEY:PASSWORD
  - Used by connector to authenticate and sign requests to IQONO API

- callback_token
  - Used by connector when sending callback to RP
  - Passed in Authorization header:
    Authorization: Bearer <callback_token>

- SIGN_KEY
  - Used to sign JWT callback payload
  - HS512 algorithm
  - NOT equal to callback_token

Method List

PAYIN_METHOD_LIST = {
    "card": "card"
}

Important:
- RP may pass method = "card"
- actual payment completion happens on IQONO Hosted Checkout Page
- Apple Pay / Google Pay selection is handled on provider side on HPP
- there is no payout method in this connector

Payin

Пример запроса от RP к Connector

{
    "settings": {
        "auth_token": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:yourpassword",
        "callback_token": "rp_callback_token",
        "method": "card",
        "wrapped_to_json_response": false
    },
    "payment": {
        "token": "abc123",
        "gateway_amount": 10099,
        "gateway_currency": "USD",
        "merchant_private_key": "c0baec85ab554cc61fba",
        "redirect_success_url": "https://success.example.com/",
        "redirect_fail_url": "https://declined.example.com/",
        "product": "Important gift"
    },
    "params": {
        "customer": {
            "email": "user@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "country": "US",
            "city": "New York",
            "zip": "10001",
            "address": "123 Main St"
        }
    },
    "processing_url": "https://business.example.com/checkout_results/abc123/processing",
    "charge_page_url": "https://business.example.com/payments/charge_pages?id=abc123",
    "callback_url": "https://business.example.com/callbacks/v2/gateway_callbacks/abc123"
}

Connector → IQONO API Request

POST https://pay.iqono.com/api/v1/session
Content-Type: application/json

{
    "merchant_key": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "operation": "purchase",
    "methods": ["card"],
    "order": {
        "number": "abc123",
        "amount": "100.99",
        "currency": "USD",
        "description": "Important gift"
    },
    "success_url": "https://success.example.com/",
    "cancel_url": "https://declined.example.com/",
    "customer": {
        "name": "John Doe",
        "email": "user@example.com"
    },
    "billing_address": {
        "country": "US",
        "city": "New York",
        "zip": "10001",
        "address": "123 Main St"
    },
    "hash": "<SHA1(MD5(UPPER(order_number + order_amount + order_currency + order_description + password)))>"
}

IQONO Response

{
    "redirect_url": "https://pay.iqono.com/auth/ZXlKMGVY..."
}

Connector → RP Response

{
    "status": "OK",
    "gateway_token": null,
    "result": "redirect",
    "requisites": {},
    "redirectRequest": {
        "url": "https://pay.iqono.com/auth/ZXlKMGVY...",
        "type": "redirect",
        "iframes": []
    },
    "provider_response_data": {
        "redirect_url": "https://pay.iqono.com/auth/ZXlKMGVY..."
    },
    "logs": []
}

Flow Payin

RP вызывает Connector endpoint /pay

Connector вызывает IQONO API:
POST https://pay.iqono.com/api/v1/session

IQONO возвращает:
- redirect_url — URL hosted checkout page

Connector возвращает redirect response в RP

Пользователь редиректится на IQONO HPP:
https://pay.iqono.com/

Пользователь завершает оплату через Apple Pay / Google Pay на стороне IQONO

После завершения операции IQONO отправляет webhook на Connector

Connector валидирует callback hash

Connector пересылает callback в RP с JWT + AES-256-CBC encryption

Status Flow

Этот коннектор в первую очередь callback-driven.

Основной финальный статус приходит через webhook.

Polling через /status используется только если:
- RP вызывает status endpoint явно
- уже известен IQONO payment_id
- этот payment_id сохранен как gateway_token

Важно:
- на момент /pay gateway_token обычно отсутствует
- payment_id появляется только после callback от IQONO
- поэтому callback-only flow для IQONO является допустимым и ожидаемым поведением

API Endpoints

Payin

Endpoint:
POST https://pay.iqono.com/api/v1/session

Авторизация:
merchant_key + hash in request body

Ответ:
Возвращает данные checkout session, включая:
- redirect_url — URL hosted payment page

Status

Endpoint:
POST https://pay.iqono.com/api/v1/payment/status

Используется для проверки статуса платежа

Авторизация:
merchant_key + hash in request body

Пример запроса статуса:

{
    "merchant_key": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "payment_id": "dc66cdd8-d702-11ea-9a2f-0242c0a87002",
    "hash": "<SHA1(MD5(UPPER(payment_id + password)))>"
}

Ответ:
Возвращает текущий статус платежа, если payment_id известен

Webhook Payload

IQONO → Connector

POST https://iqono-connector.example.com/callback
Content-Type: application/x-www-form-urlencoded

id=dc66cdd8-d702-11ea-9a2f-0242c0a87002
order_number=abc123
order_amount=100.99
order_currency=USD
order_description=Important gift
order_status=settled
type=sale
status=success
card=411111****1111
customer_name=John Doe
customer_email=user@example.com
customer_ip=1.2.3.4
date=2024-01-15 10:30:00
hash=<SHA1(MD5(UPPER(id + order_number + order_amount + order_currency + order_description + password)))>

Connector → RP Callback

POST https://business.example.com/callbacks/v2/gateway_callbacks/{token}
Authorization: Bearer <callback_token>
Content-Type: application/json

{
    "status": "approved",
    "currency": "USD",
    "amount": 10099
}

JWT Payload (signed with SIGN_KEY, HS512)

{
    "status": "approved",
    "currency": "USD",
    "amount": 10099,
    "secure": {
        "encrypted_data": "base64_encrypted_data",
        "iv_value": "base64_iv"
    }
}

Важно:

- secure block содержит зашифрованный merchant_private_key (AES-256-CBC)
- secure block присутствует только в JWT payload, но не в HTTP body
- JWT подписан с помощью SIGN_KEY (HS512 algorithm)

Hash Formulas

Session (Checkout Session Creation) Hash

SHA1( MD5( UPPER( order_number + order_amount + order_currency + order_description + password ) ) )

Где:
- order_number = payment.token / order.number
- order_amount = amount in major units, for example 100.99
- order_currency = USD
- order_description = product / description
- password = merchant secret

Callback Validation Hash

SHA1( MD5( UPPER( payment_id + order_number + order_amount + order_currency + order_description + password ) ) )

Где:
- payment_id = callback field id
- order_number = callback field order_number
- order_amount = callback field order_amount
- order_currency = callback field order_currency
- order_description = callback field order_description
- password = merchant secret

Status Request Hash

SHA1( MD5( UPPER( payment_id + password ) ) )

Status Mapping

IQONO order_status → RP status:

settled → approved

decline / declined → declined

refund / reversal / void / chargeback → declined

pending / prepare / 3ds / redirect → pending

IQONO tx status (fallback) → RP status:

success → approved

fail / failed → declined

waiting / undefined → pending

Environment Variables

# Base URL of this connector
BASE_URL=https://iqono-connector.example.com

# RP Business URL
BUSINESS_URL=https://business.reactivepay.com

# IQONO Hosted Payment Page / Checkout API URL
CHECKOUT_URL=https://pay.iqono.com

# JWT signing key for callback payload
SIGN_KEY=your_rp_sign_key_here

# Optional defaults (usually overridden by settings.auth_token)
MERCHANT_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MERCHANT_PASS=yourpassword

Deployment

Install dependencies:

pip install -r requirements.txt

Run the application:

uvicorn main:app --host 0.0.0.0 --port 8000

Testing

Test Payin:

curl -X POST https://iqono-connector.example.com/pay \
  -H "Content-Type: application/json" \
  -d '{
    "settings": {
      "auth_token": "MERCHANT_KEY:PASSWORD",
      "callback_token": "rp_callback_token",
      "method": "card",
      "wrapped_to_json_response": false
    },
    "payment": {
      "token": "test123",
      "gateway_amount": 10099,
      "gateway_currency": "USD",
      "merchant_private_key": "test_key",
      "redirect_success_url": "https://success.com",
      "redirect_fail_url": "https://fail.com",
      "product": "Test payment"
    },
    "params": {
      "customer": {
        "email": "test@example.com",
        "first_name": "Test",
        "last_name": "User"
      }
    },
    "callback_url": "https://business.example.com/callbacks/v2/gateway_callbacks/test123"
  }'

Test Status:

curl -X POST https://iqono-connector.example.com/status \
  -H "Content-Type: application/json" \
  -d '{
    "settings": {
      "auth_token": "MERCHANT_KEY:PASSWORD"
    },
    "payment": {
      "gateway_token": "dc66cdd8-d702-11ea-9a2f-0242c0a87002"
    }
  }'

Integration Status

✅ Payin: COMPLETE
- Checkout session creation via /pay endpoint
- Redirect flow to IQONO Hosted Payment Page
- Apple Pay / Google Pay support on provider side
- Webhook callbacks with JWT + AES encryption
- Optional status polling via /status endpoint if payment_id is known

❌ Payout: NOT SUPPORTED

Files Structure

iqono_integration/
├── main.py                  # FastAPI application entry point
├── config.py                # Environment configuration
├── gateway/
│   ├── handler.py           # Request handlers (pay, status, callback)
│   ├── builder.py           # Request/response builders
│   └── router.py            # FastAPI routes
├── client/
│   └── http.py              # HTTP client for IQONO API
├── schemas/
│   ├── payment.py           # Payin request schema
│   ├── status.py            # Status request schema
│   └── callback.py          # Callback request schema
├── utils/
│   ├── db.py                # Database operations / token mapping
│   └── logger.py            # Logging configuration
└── requirements.txt         # Python dependencies