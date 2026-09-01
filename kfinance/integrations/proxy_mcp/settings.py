from typing import Any

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseModel):
    refresh_token: str | None = None
    client_id: str | None = None
    private_key: str | None = None
    okta_host: str = "https://kensho.okta.com"
    refresh_url: str = "https://kfinance.kensho.com/oauth2/refresh"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="_", env_nested_max_split=1)

    backend_url: str = "https://kfinance.kensho.com/integrations/mcp"
    # Browser origins allowed to call the proxy. Empty by default because the proxy
    # injects the operator's credentials into every backend request, so any allowed
    # origin can act as that user. Never set this to "*".
    allowed_origins: list[str] = []
    # Host header values the proxy answers to. Restricting them blocks DNS rebinding,
    # where an attacker resolves their own domain to the loopback address.
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]
    auth: AuthSettings = AuthSettings()

    @field_validator("allowed_origins", "allowed_hosts", mode="before")
    @classmethod
    def split_comma_separated(cls, value: Any) -> Any:
        """Allow comma-separated env var values in addition to JSON lists."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


settings = Settings()
