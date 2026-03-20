from .brusnika.adapter import BrusnikaAdapter
from .forta.adapter import FortaNSPKAdapter
from .royal_finance.adapter import RoyalFinanceAdapter
from .rylecode.adapter import RyleCodeAdapter
from .iqono.adapter import IqonoAdapter

# Инициализируем адаптеры
_registry = {
    "Brusnika_SBP": BrusnikaAdapter(),
    "Forta_NSPK": FortaNSPKAdapter(),
    "RoyalFinance_AZN_P2P": RoyalFinanceAdapter(),
    "RyleCode_Ecom": RyleCodeAdapter(),
    "Iqono_ApplePay_GooglePay": IqonoAdapter(),
}

# Алиасы имён провайдеров → канонические ключи реестра
_aliases = {
    # Brusnika
    "brusnika": "Brusnika_SBP",
    "brusnika_sbp": "Brusnika_SBP",
    "sbp-brusnika": "Brusnika_SBP",

    # Forta NSPK (SBP_ECOM)
    "fortanspk": "Forta_NSPK",
    "forta_nspk": "Forta_NSPK",
    "fortaqr": "Forta_NSPK",
    "forta_qr": "Forta_NSPK",
    "forta_sbp_ecom": "Forta_NSPK",
    "sbp_ecom": "Forta_NSPK",
    "nspk": "Forta_NSPK",

    # Royal Finance (AZN P2P)
    "royal": "RoyalFinance_AZN_P2P",
    "royalfinance": "RoyalFinance_AZN_P2P",
    "royal_finance": "RoyalFinance_AZN_P2P",
    "royal_finance_azn_p2p": "RoyalFinance_AZN_P2P",
    "royal_azn": "RoyalFinance_AZN_P2P",
    "azn_p2p": "RoyalFinance_AZN_P2P",
    "azn": "RoyalFinance_AZN_P2P",

    # RyleCode (E-com HPP)
    "rylecode": "RyleCode_Ecom",
    "ryle": "RyleCode_Ecom",
    "rylecode_ecom": "RyleCode_Ecom",
    "fluxs": "RyleCode_Ecom",
    "fluxsgate": "RyleCode_Ecom",

    # IQONO (Apple Pay / Google Pay)
    "iqono": "Iqono_ApplePay_GooglePay",
    "iqono_applepay": "Iqono_ApplePay_GooglePay",
    "iqono_googlepay": "Iqono_ApplePay_GooglePay",
    "iqono_applepay_googlepay": "Iqono_ApplePay_GooglePay",
    "applepay": "Iqono_ApplePay_GooglePay",
    "googlepay": "Iqono_ApplePay_GooglePay",
    "apple_pay": "Iqono_ApplePay_GooglePay",
    "google_pay": "Iqono_ApplePay_GooglePay",
}

def get_provider_by_name(name: str | None):
    if not name:
        return None
    key = _aliases.get(name.strip().lower(), name)
    return _registry.get(key)

def resolve_provider_by_payment_method(payment_method: str | None):
    if not payment_method:
        return None
    pm = payment_method.strip().upper()
    if pm in {"APPLEPAY", "APPLE_PAY", "GOOGLEPAY", "GOOGLE_PAY"}:
        return _registry.get("Iqono_ApplePay_GooglePay")
    if pm == "SBP_ECOM" or pm == "NSPK":
        return _registry.get("Forta_NSPK")
    if "ECOM" in pm or "NSPK" in pm:
        return _registry.get("Forta_NSPK")
    if "SBP" in pm:
        return _registry.get("Brusnika_SBP")
    if "AZN" in pm or "P2P" in pm:
        return _registry.get("RoyalFinance_AZN_P2P")
    return None
