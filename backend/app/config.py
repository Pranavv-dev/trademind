from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://trademind:trademind_dev@db:5432/trademind"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Zerodha Kite Connect
    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""
    kite_redirect_url: str = "http://localhost:8000/api/auth/kite/callback"

    # Zerodha auto-login (UNATTENDED daily auth via TOTP).
    # Kite Connect tokens expire daily and there's no refresh token, so a fresh
    # login is required each morning. When you can't be awake to click the login
    # URL, these credentials let a Celery task log in automatically at 8:00 AM IST.
    #
    # SECURITY: this stores your Zerodha password + TOTP secret in .env. Keep .env
    # gitignored and the host locked down. Leave blank to disable (falls back to
    # manual login). To get kite_totp_secret: Zerodha → Settings → set up an
    # external authenticator (Google Authenticator/Authy) — the base32 secret shown
    # under the QR code is what goes here.
    kite_auto_auth_enabled: bool = False
    kite_user_id: str = ""  # e.g. "AB1234"
    kite_password: str = ""  # your kite.zerodha.com login password
    kite_totp_secret: str = ""  # base32 TOTP secret (NOT the 6-digit code)
    # Internal URL the auto-auth task pings to wake the backend's WS ticker
    # after storing a fresh token. Default works inside docker-compose.
    backend_internal_url: str = "http://backend:5000"

    # Gemini API
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash-lite"

    # Notifications
    # Telegram (unavailable in some regions — Discord is the supported alternative).
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # Discord webhook: create one in a channel via Edit Channel → Integrations →
    # Webhooks → New Webhook → Copy URL. Paste it here. Send-only (no commands).
    discord_webhook_url: str = ""

    # App Config
    trading_mode: str = "paper"
    default_capital: float = 50_000.0
    log_level: str = "INFO"
    timezone: str = "Asia/Kolkata"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
