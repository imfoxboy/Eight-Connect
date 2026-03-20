import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IQONO_ROOT = ROOT / 'iqono_integration'
if str(IQONO_ROOT) not in sys.path:
    sys.path.insert(0, str(IQONO_ROOT))

from gateway import builder, handler  # type: ignore
from schemas.payment import PayRequest  # type: ignore
from schemas.status import StatusRequest  # type: ignore


# ---------------------------------------------------------------------------
# Hash helpers — verify against IQONO Checkout docs
# ---------------------------------------------------------------------------

def _doc_checkout_hash(order_number: str, order_amount: str, order_currency: str,
                       order_description: str, password: str) -> str:
    """SHA1(MD5(UPPER(number + amount + currency + description + password)))"""
    raw = (order_number + order_amount + order_currency + order_description + password).upper()
    md5_hex = hashlib.md5(raw.encode()).hexdigest()
    return hashlib.sha1(md5_hex.encode()).hexdigest()


def _doc_callback_hash(payment_id: str, order_number: str, order_amount: str,
                       order_currency: str, order_description: str, password: str) -> str:
    """SHA1(MD5(UPPER(payment_id + number + amount + currency + description + password)))"""
    raw = (payment_id + order_number + order_amount + order_currency + order_description + password).upper()
    md5_hex = hashlib.md5(raw.encode()).hexdigest()
    return hashlib.sha1(md5_hex.encode()).hexdigest()


def _doc_status_hash(payment_id: str, password: str) -> str:
    """SHA1(MD5(UPPER(payment_id + password)))"""
    raw = (payment_id + password).upper()
    md5_hex = hashlib.md5(raw.encode()).hexdigest()
    return hashlib.sha1(md5_hex.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Test data factory
# ---------------------------------------------------------------------------

def make_pay_request(**overrides):
    payload = {
        'settings': {
            'auth_token': 'merchant-key-123:secret-pass',
            'method': 'card',
            'wrapped_to_json_response': False,
        },
        'payment': {
            'token': 'rp-123',
            'gateway_amount': 10099,
            'gateway_currency': 'USD',
            'ip': '1.2.3.4',
            'merchant_private_key': 'merchant-secret',
            'redirect_success_url': 'https://merchant/success',
            'redirect_fail_url': 'https://merchant/fail',
            'order_number': 'ORD-1',
            'product': 'Test product',
        },
        'params': {
            'customer': {
                'email': 'user@example.com',
                'ip': '1.2.3.4',
                'first_name': 'John',
                'last_name': 'Doe',
                'address': 'Main st',
                'country': 'US',
                'city': 'NY',
                'zip': '10001',
                'phone': '12345678',
            },
        },
        'processing_url': 'https://merchant/processing',
        'callback_url': 'https://webhooksite.net/14043116-4abe-47f6-b51e-02a8930a275a',
    }
    for key, value in overrides.items():
        payload[key] = value
    return PayRequest.model_validate(payload)


# ---------------------------------------------------------------------------
# Amount formatting (unchanged behavior)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ('amount', 'currency', 'expected'),
    [
        (100.99, 'USD', '100.99'),
        (100, 'BHD', '100.000'),
        (100, 'KRW', '100'),
        (100, 'JPY', '100.00'),
        (100, 'CLP', '100'),
    ],
)
def test_format_amount_matches_documented_currency_rules(amount, currency, expected):
    assert builder.format_amount(amount, currency) == expected


# ---------------------------------------------------------------------------
# Checkout session hash
# ---------------------------------------------------------------------------

def test_checkout_session_hash_matches_iqono_documentation():
    """Verify hash formula: SHA1(MD5(UPPER(number + amount + currency + description + password)))"""
    h = builder.hash_session(
        order_number="test-10984",
        order_amount="10.00",
        order_currency="USD",
        order_description="test",
        password="2fc46f1fgh657106ae916a579bd45th",
    )
    expected = _doc_checkout_hash("test-10984", "10.00", "USD", "test", "2fc46f1fgh657106ae916a579bd45th")
    assert h == expected


def test_build_checkout_session_uses_correct_hash():
    payload = builder.build_checkout_session_payload(
        merchant_key='merchant-key-123',
        password='secret-pass',
        order_number='rp-123',
        order_amount=100.99,
        order_currency='USD',
        order_description='Test product',
        success_url='https://merchant/success',
    )
    expected = _doc_checkout_hash('rp-123', '100.99', 'USD', 'Test product', 'secret-pass')
    assert payload['hash'] == expected
    assert payload['merchant_key'] == 'merchant-key-123'
    assert payload['operation'] == 'purchase'
    assert payload['order']['number'] == 'rp-123'
    assert payload['order']['amount'] == '100.99'
    assert payload['order']['currency'] == 'USD'
    assert payload['success_url'] == 'https://merchant/success'


def test_build_checkout_session_includes_optional_fields():
    payload = builder.build_checkout_session_payload(
        merchant_key='mk',
        password='pw',
        order_number='ord-1',
        order_amount=50.00,
        order_currency='EUR',
        order_description='Gift',
        success_url='https://ok.com',
        cancel_url='https://cancel.com',
        methods=['card', 'googlepay'],
        customer_name='John Doe',
        customer_email='john@example.com',
        billing_country='US',
        billing_city='NY',
        billing_address='123 Main',
        billing_zip='10001',
    )
    assert payload['cancel_url'] == 'https://cancel.com'
    assert payload['methods'] == ['card', 'googlepay']
    assert payload['customer'] == {'name': 'John Doe', 'email': 'john@example.com'}
    assert payload['billing_address']['country'] == 'US'
    assert payload['billing_address']['city'] == 'NY'


# ---------------------------------------------------------------------------
# Callback hash
# ---------------------------------------------------------------------------

def test_callback_hash_matches_iqono_documentation():
    h = builder.hash_callback(
        payment_id='dc66cdd8-d702-11ea-9a2f-0242c0a87002',
        order_number='order-1234',
        order_amount='3.01',
        order_currency='USD',
        order_description='bloodline',
        password='secret-pass',
    )
    expected = _doc_callback_hash(
        'dc66cdd8-d702-11ea-9a2f-0242c0a87002',
        'order-1234', '3.01', 'USD', 'bloodline', 'secret-pass',
    )
    assert h == expected


# ---------------------------------------------------------------------------
# Status hash
# ---------------------------------------------------------------------------

def test_status_hash_matches_iqono_documentation():
    h = builder.hash_status(
        payment_id='dc66cdd8-d702-11ea-9a2f-0242c0a87002',
        password='secret-pass',
    )
    expected = _doc_status_hash('dc66cdd8-d702-11ea-9a2f-0242c0a87002', 'secret-pass')
    assert h == expected


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ('order_status', 'tx_status', 'expected_rp'),
    [
        ('settled', 'success', 'approved'),
        ('decline', 'fail', 'declined'),
        ('pending', 'waiting', 'pending'),
        ('3ds', 'success', 'pending'),
        ('redirect', 'success', 'pending'),
        ('', 'success', 'approved'),    # fallback to tx_status
        ('', 'fail', 'declined'),
        ('', '', 'pending'),
    ],
)
def test_map_to_rp_covers_checkout_statuses(order_status, tx_status, expected_rp):
    assert builder.map_to_rp(order_status, tx_status) == expected_rp


# ---------------------------------------------------------------------------
# /pay response contract
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pay_response_returns_redirect_url(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(handler, 'CHECKOUT_URL', 'https://checkout.iqono.example')
    monkeypatch.setattr(handler, 'BASE_URL', 'https://connector.example')
    monkeypatch.setattr(handler, 'SIGN_KEY', 'sign-key')

    async def fake_upsert_mapping(**kwargs):
        return None

    async def fake_post_checkout_session(checkout_url, payload):
        return {
            'redirect_url': 'https://checkout.iqono.example/auth/ABCDEF123456',
        }

    monkeypatch.setattr(handler, 'upsert_mapping', fake_upsert_mapping)
    monkeypatch.setattr(handler, 'post_checkout_session', fake_post_checkout_session)

    response = await handler.handle_pay(make_pay_request())

    assert response['status'] == 'OK'
    assert response['result'] == 'redirect'
    assert response['redirectRequest']['url'] == 'https://checkout.iqono.example/auth/ABCDEF123456'
    assert response['redirectRequest']['type'] == 'redirect'
    assert 'logs' in response
    assert response['logs'] == []


@pytest.mark.asyncio
async def test_pay_error_response_on_iqono_validation_error(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(handler, 'CHECKOUT_URL', 'https://checkout.iqono.example')
    monkeypatch.setattr(handler, 'BASE_URL', 'https://connector.example')
    monkeypatch.setattr(handler, 'SIGN_KEY', 'sign-key')

    async def fake_upsert_mapping(**kwargs):
        return None

    async def fake_post_checkout_session(checkout_url, payload):
        return {
            'error_code': 0,
            'error_message': 'Request data is invalid.',
            'errors': [
                {'error_code': 100000, 'error_message': 'hash: Hash is not valid.'},
            ],
        }

    monkeypatch.setattr(handler, 'upsert_mapping', fake_upsert_mapping)
    monkeypatch.setattr(handler, 'post_checkout_session', fake_post_checkout_session)

    response = await handler.handle_pay(make_pay_request())

    assert response['status'] == 'ERROR'
    assert response['result'] == 'declined'
    assert len(response['logs']) > 0


# ---------------------------------------------------------------------------
# /status response contract
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_response_contract_matches_existing_integrations(monkeypatch):
    monkeypatch.setattr(handler, 'CHECKOUT_URL', 'https://checkout.iqono.example')

    async def fake_post_payment_status(checkout_url, payload):
        return {
            'payment_id': 'dc66cdd8-d702-11ea-9a2f-0242c0a87002',
            'date': '2022-07-05 09:45:03',
            'status': 'settled',
            'order': {
                'number': 'rp-123',
                'amount': '100.99',
                'currency': 'USD',
                'description': 'Test product',
            },
        }

    monkeypatch.setattr(handler, 'post_payment_status', fake_post_payment_status)

    req = StatusRequest.model_validate(
        {
            'settings': {'auth_token': 'merchant-key:secret-pass'},
            'payment': {'gateway_token': 'dc66cdd8-d702-11ea-9a2f-0242c0a87002'},
        }
    )
    response = await handler.handle_status(req)

    assert response['result'] == 'OK'
    assert response['status'] == 'approved'
    assert 'details' in response
    assert 'logs' in response


# ---------------------------------------------------------------------------
# Build status payload
# ---------------------------------------------------------------------------

def test_build_status_payload():
    payload = builder.build_status_payload('merchant-key', 'secret-pass', 'pay-id-123')
    assert payload['merchant_key'] == 'merchant-key'
    assert payload['payment_id'] == 'pay-id-123'
    expected_hash = _doc_status_hash('pay-id-123', 'secret-pass')
    assert payload['hash'] == expected_hash
