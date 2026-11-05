"""Configurações centralizadas via pydantic-settings."""
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # APIs
    openai_api_key: str = Field(..., description="Chave da API OpenAI")
    gmail_credentials_path: Path = Field(
        default=Path("./credentials/credentials.json")
    )
    gmail_token_path: Path = Field(
        default=Path("./credentials/token.json")
    )

    # Banco de dados
    db_path: Path = Field(default=Path("./data/cache.db"))

    # Comportamento
    log_level: str = Field(default="INFO")
    safe_mode: bool = Field(default=True)
    max_batch_size: int = Field(default=30)
    confidence_threshold: float = Field(default=0.80)
    max_retries: int = Field(default=3)
    rate_limit_delay: float = Field(default=0.1)

    # Modelo OpenAI
    openai_model: str = Field(default="gpt-4o-mini")
    openai_max_tokens: int = Field(default=4096)

    # Gmail API
    gmail_scopes: list[str] = Field(
        # gmail.modify inclui as operações sobre labels; gmail.labels é redundante.
        default=["https://www.googleapis.com/auth/gmail.modify"]
    )


# Singleton
_settings: Settings | None = None


def get_settings() -> Settings:
    """Retorna instância singleton das configurações."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
