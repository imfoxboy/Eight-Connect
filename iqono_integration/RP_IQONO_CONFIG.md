# RP IQONO Configuration

This file describes RP-side configuration for IQONO Hosted Checkout integration.

RP Configuration Example

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

Field Description

- auth_token
  - format: MERCHANT_KEY:PASSWORD
  - used for IQONO API authentication and hash generation

- callback_token
  - used by Connector when calling RP callback endpoint
  - passed in Authorization header

- class
  - provider class name in RP

- provider
  - provider identifier

- payment_method
  - should be "card"
  - actual Apple Pay / Google Pay selection happens on IQONO HPP side

- masked_provider
  - true
  - provider is masked on RP side

- not_internal_page
  - false
  - customer is redirected to external hosted checkout page

- enable_change_final_status
  - true
  - allows RP to accept final callback status updates

- enable_update_amount
  - false
  - amount should not be changed after payment creation

- wrapped_to_json_response
  - false
  - response is redirect-based, not requisites-based

Important

- callback_token is NOT the JWT signing key
- JWT signing key is configured separately in connector env via SIGN_KEY
- IQONO integration is redirect / hosted checkout based
- no payout flow is available in this connector