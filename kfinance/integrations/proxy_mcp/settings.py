from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseModel):
    refresh_token: str | None = None
    client_id: str | None = None
    private_key: str | None = None
    okta_host: str = "https://kensho.okta.com"
    refresh_url: str = "https://kfinance.kensho.com/oauth2/refresh"


class ClientSettings(BaseModel):
    """Settings for authenticating the MCP clients that call this proxy."""

    tokens: list[str] = []
    auth_disabled: bool = False
    cors_origins: list[str] = []

    @field_validator("tokens", "cors_origins", mode="before")
    @classmethod
    def _allow_comma_separated(cls, value: object) -> object:
        """Accept a comma separated environment variable in place of a JSON list."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="_", env_nested_max_split=1)

    backend_url: str = "https://kfinance.kensho.com/integrations/mcp"
    auth: AuthSettings = AuthSettings()
    client: ClientSettings = ClientSettings()


settings = Settings()
