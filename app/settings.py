import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "GatewayConnect"
    APP_ENV: str = "dev"
    LOG_LEVEL: str = "INFO"
    PORT: int = 8080

    # RP callback security
    # Default sign key (used for Royal Finance and as fallback)
    RP_CALLBACK_SIGNING_SECRET: str = os.getenv("RP_CALLBACK_SIGNING_SECRET", "dev-secret-key")
    # Forta-specific sign key
    RP_CALLBACK_SIGNING_SECRET_FORTA: str = os.getenv("RP_CALLBACK_SIGNING_SECRET_FORTA", "")
    # RyleCode-specific sign key
    RP_CALLBACK_SIGNING_SECRET_RYLECODE: str = os.getenv("RP_CALLBACK_SIGNING_SECRET_RYLECODE", "")
    # IQONO-specific sign key
    RP_CALLBACK_SIGNING_SECRET_IQONO: str = os.getenv("RP_CALLBACK_SIGNING_SECRET_IQONO", "")

    RP_CALLBACK_RETRY_MAX: int = 6
    RP_CALLBACK_BASE_TIMEOUT_SEC: int = 2

    # Default provider selection
    DEFAULT_PROVIDER: str = "Brusnika_SBP"

    # Provider: Brusnika
    BRUSNIKA_BASE_URL: str = "https://api.brusnikapay.top"
    BRUSNIKA_WEBHOOK_URL: str = "shad-mighty-bluegill.ngrok-free.app/provider/brusnika/webhook"
    # BRUSNIKA_API_KEY: str = "REPLACE"
    # BRUSNIKA_WEBHOOK_SIGNING_SECRET: str = "REPLACE"

    # --- Forta ---
    FORTA_BASE_URL: str = "https://pt.wallet-expert.com"
    FORTA_API_TOKEN: str = os.getenv("FORTA_API_TOKEN", "")
    FORTA_WEBHOOK_URL: str = ""  # Uses PUBLIC_BASE_URL instead

    # --- Royal Finance ---
    # Payin (payments)
    ROYAL_PAYIN_BASE_URL: str = "https://page.royal-pay.cc"
    ROYAL_PAYIN_API_TOKEN: str = os.getenv("ROYAL_PAYIN_API_TOKEN", "")

    # Payout (disbursements)
    ROYAL_PAYOUT_BASE_URL: str = "https://royal-pay.org"
    ROYAL_PAYOUT_API_TOKEN: str = os.getenv("ROYAL_PAYOUT_API_TOKEN", "")

    # Shared
    ROYAL_FORM_BASE_URL: str = "https://page.royal-pay.cc"
    ROYAL_WEBHOOK_URL: str = os.getenv("ROYAL_WEBHOOK_URL", "")

    # --- RyleCode (Fluxs Gateway) ---
    RYLECODE_BASE_URL: str = "https://business.fluxsgate.com"  # Production & Sandbox use same URL
    RYLECODE_API_TOKEN: str = os.getenv("RYLECODE_API_TOKEN", "670e05331f85e84c9064")  # Merchant Private Key

    # --- IQONO (Apple Pay / Google Pay) ---
    IQONO_GATEWAY_URL: str = os.getenv("IQONO_GATEWAY_URL", "https://api.iqono.com/post")
    IQONO_AUTH_TOKEN: str = os.getenv("IQONO_AUTH_TOKEN", "")

    # Public base URL
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "https://semimonopolistic-cladocarpous-deb.ngrok-free.dev")

    # DB
    DB_URL: str = "sqlite+aiosqlite:///./data/mappings.sqlite3"

settings = Settings()
