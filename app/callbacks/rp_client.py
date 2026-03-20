import json
import httpx
import jwt
import os
from Crypto.Cipher import AES
from base64 import b64encode
from typing import Dict, Any
from app.settings import settings
from ..utils.http import client, retry_policy
from ..settings import settings
from ..utils.security import hmac_sha256_b64


def encrypt_merchant_key(merchant_key: str, sign_key: str) -> Dict[str, str]:
    """
    Encrypts merchant_private_key using AES-256-CBC as per RP documentation and mengine example.
    Returns dict with 'encrypted_data' and 'iv_value' (both base64 encoded).

    This is the CORRECT format - encrypt the merchant_private_key string, NOT a JSON object!
    """
    # Prepare 256-bit key (32 bytes)
    key_bytes = sign_key.encode("utf-8")
    if len(key_bytes) < 32:
        key_bytes = key_bytes.ljust(32, b"\0")
    else:
        key_bytes = key_bytes[:32]

    # Generate random IV (16 bytes for AES)
    iv = os.urandom(16)

    # Create cipher and encrypt
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv)

    # PKCS7 padding on the merchant_key string
    raw = merchant_key.encode("utf-8")
    pad = 16 - len(raw) % 16
    raw += bytes([pad]) * pad

    encrypted = cipher.encrypt(raw)

    # Return dict with both encrypted data and IV (as per RP docs)
    return {
        "encrypted_data": b64encode(encrypted).decode(),
        "iv_value": b64encode(iv).decode()
    }


def make_jwt(payload: Dict[str, Any], secret: str) -> str:
    return jwt.encode(payload, secret, algorithm="HS512")


async def send_callback_to_rp(tx: dict):
    """
    Отправляет callback в RP по URL из транзакции в формате из документации RP:
    POST /callbacks/v2/gateway_callbacks/:token
    Authorization: Bearer <JWT_TOKEN>
    Body: { status, currency, amount, secure: {encrypted_data, iv_value} }

    ВАЖНО: secure block должен содержать зашифрованный merchant_private_key!
    """
    print("\n" + "=" * 80)
    print("[RP CALLBACK] ===== PREPARING CALLBACK TO RP (JWT + AES-256-CBC) =====")
    print(f"[RP CALLBACK] Input transaction object: {tx}")

    callback_url = tx.get("callback_url")
    merchant_private_key = tx.get("merchant_private_key")
    provider = tx.get("provider", "")  # Get provider name

    if not callback_url:
        print("[RP CALLBACK] No callback_url provided - skipping callback")
        print("=" * 80)
        return

    # Select provider-specific sign key
    if provider == "forta" and settings.RP_CALLBACK_SIGNING_SECRET_FORTA:
        sign_key = settings.RP_CALLBACK_SIGNING_SECRET_FORTA
        print(f"[RP CALLBACK] Using Forta-specific sign key")
    elif provider == "rylecode" and settings.RP_CALLBACK_SIGNING_SECRET_RYLECODE:
        sign_key = settings.RP_CALLBACK_SIGNING_SECRET_RYLECODE
        print(f"[RP CALLBACK] Using RyleCode-specific sign key")
    elif provider == "iqono" and settings.RP_CALLBACK_SIGNING_SECRET_IQONO:
        sign_key = settings.RP_CALLBACK_SIGNING_SECRET_IQONO
        print(f"[RP CALLBACK] Using IQONO-specific sign key")
    else:
        sign_key = settings.RP_CALLBACK_SIGNING_SECRET
        print(f"[RP CALLBACK] Using default sign key (Royal Finance) for provider: {provider}")

    print(f"[RP CALLBACK] Target callback URL: {callback_url}")
    print(f"[RP CALLBACK] Using signing secret: {sign_key}")
    print(f"[RP CALLBACK] Merchant private key present: {bool(merchant_private_key)}")

    # Construct callback body (what goes in HTTP body)
    callback_body = {
        "status": tx.get("status"),
        "currency": tx.get("currency"),
        "amount": tx.get("amount"),
    }

    # Construct JWT payload (includes secure block for JWT signature)
    # Per mengine: JWT payload = callback_body + secure, but HTTP body = callback_body only!
    jwt_payload = {**callback_body}

    if merchant_private_key:
        # Encrypt the merchant_private_key string
        encrypted_secure = encrypt_merchant_key(merchant_private_key, sign_key)
        jwt_payload["secure"] = encrypted_secure  # Add secure ONLY to JWT payload
        print(f"[RP CALLBACK] Encrypted merchant_private_key (added to JWT only):")
        print(f"  - encrypted_data: {encrypted_secure['encrypted_data'][:50]}...")
        print(f"  - iv_value: {encrypted_secure['iv_value']}")
    else:
        print(f"[RP CALLBACK] No merchant_private_key - no secure block in JWT")

    print(f"[RP CALLBACK] JWT payload: {jwt_payload}")
    print(f"[RP CALLBACK] HTTP body: {callback_body}")

    # Create JWT token with HS512 using provider-specific sign key
    jwt_token = make_jwt(jwt_payload, sign_key)
    print(f"[RP CALLBACK] Generated JWT token (HS512): {jwt_token[:50]}...{jwt_token[-30:]}" if len(jwt_token) > 80 else jwt_token)

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json"
    }
    print(f"[RP CALLBACK] Request headers: {headers}")

    print(f"\n[RP CALLBACK] ===== SENDING HTTP POST TO RP =====")
    print(f"[RP CALLBACK] URL: {callback_url}")
    print(f"[RP CALLBACK] Method: POST")
    print(f"[RP CALLBACK] Body (JSON): {json.dumps(callback_body, indent=2)}")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(callback_url, json=callback_body, headers=headers)
            print(f"\n[RP CALLBACK] ===== RESPONSE FROM RP =====")
            print(f"[RP CALLBACK] Status code: {resp.status_code}")
            print(f"[RP CALLBACK] Response headers: {dict(resp.headers)}")
            print(f"[RP CALLBACK] Response body: {resp.text}")

            if resp.status_code >= 400:
                print(f"[RP CALLBACK] ❌ ERROR: Received error status {resp.status_code}")
            else:
                print(f"[RP CALLBACK] ✅ SUCCESS: Callback delivered successfully")

            print("=" * 80 + "\n")
    except Exception as e:
        print(f"\n[RP CALLBACK] ===== HTTP REQUEST FAILED =====")
        print(f"[RP CALLBACK] ❌ Exception: {e}")
        import traceback
        print(f"[RP CALLBACK] Traceback: {traceback.format_exc()}")
        print("=" * 80 + "\n")
        raise


class RPCallbackClient:
    """
    Отправляет коллбеки в RP в формате:
    {
      "result": "approved|declined|pending",
      "gateway_token": "<id>",
      "logs": [],
      "requisites": null
    }
    HMAC-подпись (опционально): заголовок X-RP-Signature (base64(HMAC-SHA256)).
    """

    @retry_policy(max_attempts=settings.RP_CALLBACK_RETRY_MAX)
    async def send_callback(self, url: str, payload: Dict[str, Any]) -> int:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        if settings.RP_CALLBACK_SIGNING_SECRET and settings.RP_CALLBACK_SIGNING_SECRET != "replace_me":
            signature = hmac_sha256_b64(settings.RP_CALLBACK_SIGNING_SECRET, body)
            headers["X-RP-Signature"] = signature

        async with client(timeout_sec=15) as c:
            resp = await c.post(url, content=body, headers=headers)
            resp.raise_for_status()
            return resp.status_code
