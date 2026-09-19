from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseModel):
    refresh_token: str | None = None
    client_id: str | None = None
    private_key: str | None = None
    okta_host: str = "https://kensho.okta.com"
    refresh_url: str = "https://kfinance.kensho.com/oauth2/refresh"


class InboundSettings(BaseModel):
    """Settings controlling which callers may use the proxy."""

    token: str | None = None
    allowed_origins: list[str] = []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="_", env_nested_max_split=1)

    backend_url: str = "https://kfinance.kensho.com/integrations/mcp"
    auth: AuthSettings = AuthSettings()
    inbound: InboundSettings = InboundSettings()


settings = Settings()
