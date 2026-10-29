from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///.runtime/core.db"
    mock_database_url: str = "sqlite+aiosqlite:///.runtime/mock.db"
    log_level: str = "INFO"
    local_api_token: str | None = None
    mock_platform_seed: int = 7
    auto_approve_enabled: bool = False
    external_side_effects_enabled: bool = False
    postiz_enabled: bool = False
    website_remote_enabled: bool = False

    @model_validator(mode="after")
    def local_only(self) -> "Settings":
        if self.app_env not in {"development", "test"}:
            raise ValueError("Phase 2 supports development/test only")
        if self.auto_approve_enabled:
            raise ValueError("Automatic approval is unavailable at LEVEL 2")
        if self.database_url == self.mock_database_url:
            raise ValueError("Mock external system requires a separate database")
        return self
