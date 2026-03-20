import os

BASE_URL      = os.getenv("BASE_URL")          # This connector's public URL
BUSINESS_URL  = os.getenv("BUSINESS_URL")       # RP Business URL
CHECKOUT_URL  = os.getenv("CHECKOUT_URL",       # IQONO Checkout URL (e.g. https://checkout.iqono.com)
                          os.getenv("GATEWAY_URL"))  # backward compat with old env var
SIGN_KEY      = os.getenv("SIGN_KEY")           # RP Sign Key for JWT callbacks
MERCHANT_KEY  = os.getenv("MERCHANT_KEY", "")   # IQONO merchant_key from admin panel
MERCHANT_PASS = os.getenv("MERCHANT_PASS", "")  # IQONO password from admin panel
