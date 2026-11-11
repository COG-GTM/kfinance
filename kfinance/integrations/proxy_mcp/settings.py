from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseModel):
    refresh_token: str | None = None
    client_id: str | None = None
    private_key: str | None = None
    okta_host: str = "https://kensho.okta.com"
    refresh_url: str = "https://kfinance.kensho.com/oauth2/refresh"


class InboundSettings(BaseModel):
    """Controls which callers may use the proxy (and therefore the operator's credentials)."""

    token: SecretStr | None = None
    allowed_origins: list[str] = []
    allowed_hosts: list[str] = []

    @field_validator("allowed_origins")
    @classmethod
    def reject_wildcard_origin(cls, origins: list[str]) -> list[str]:
        """Disallow "*" so that browser access is always limited to named origins."""
        if "*" in origins:
            raise ValueError("INBOUND_ALLOWED_ORIGINS must list explicit origins, not '*'")
        return origins

    @field_validator("allowed_hosts")
    @classmethod
    def reject_wildcard_host(cls, hosts: list[str]) -> list[str]:
        """Disallow "*" so that the Host check cannot be silently disabled."""
        if "*" in hosts:
            raise ValueError("INBOUND_ALLOWED_HOSTS must list explicit host names, not '*'")
        return hosts


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_nested_delimiter="_", env_nested_max_split=1)

    backend_url: str = "https://kfinance.kensho.com/integrations/mcp"
    auth: AuthSettings = AuthSettings()
    inbound: InboundSettings = InboundSettings()


settings = Settings()
