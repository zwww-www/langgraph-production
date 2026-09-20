from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql://safeops:safeops@localhost:5432/safeops"
    environment: Literal["development", "test", "production"] = "development"
    llm_provider: Literal["offline", "openai-compatible", "aliyun"] = "offline"
    llm_model: str = "configured-model"
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_timeout_seconds: float = Field(default=60, gt=0, le=300)
    llm_max_tokens: int = Field(default=2048, ge=128, le=8192)
    min_confidence: float = Field(default=0.7, ge=0, le=1)
    refund_approval_cents: int = Field(default=1000, ge=0)
    refund_deny_cents: int = Field(default=100000, gt=0)
    policy_version: str = "2026-09-01"
    log_level: str = "INFO"
    api_tokens: dict[str, dict[str, str]] = Field(
        default_factory=lambda: {
            "demo-operator": {"name": "demo.operator", "role": "operator"},
            "demo-admin": {"name": "demo.admin", "role": "admin"},
        }
    )
    mcp_servers: dict[str, str] = Field(default_factory=dict)
    tool_bindings: dict[str, dict[str, str]] = Field(default_factory=dict)
    worker_poll_seconds: float = Field(default=0.5, gt=0)

    @model_validator(mode="after")
    def validate_settings(self) -> "Settings":
        if not self.database_url.startswith("postgresql://"):
            raise ValueError("DATABASE_URL must use postgresql://")
        if self.refund_deny_cents <= self.refund_approval_cents:
            raise ValueError("deny threshold must exceed approval threshold")
        if self.environment == "production" and (
            not self.api_tokens or any(k.startswith("demo-") for k in self.api_tokens)
        ):
            raise ValueError("production requires private API_TOKENS")
        return self

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
