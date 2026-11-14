import os
import tomllib
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PublicSettings(BaseModel):
    """Readable application options; credentials are deliberately not accepted here."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    data_source: Literal["fixture", "nessie"] = "fixture"
    fixture_path: Path = Path(".data/demo.json")
    nessie_base_url: str = "https://v2-prod-api.nessieisreal.com"
    nessie_customer_id: str = ""
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    photon_enabled: bool = False
    photon_api_base_url: str = "https://api.photon.codes/v1"
    photon_project_id: str = ""
    photon_agent_user_id: str = ""
    photon_allowed_sender_id: str = ""
    photon_inbox_path: Path = Path(".data/photon-inbox.sqlite3")
    llm_enabled: bool = False
    llm_provider: Literal["gemini", "nemotron"] = "gemini"
    gemini_model: str = Field(default="gemini-3.8-flash", min_length=1, max_length=160)
    nemotron_model: str = Field(
        default="nvidia/nemotron-3-super-120b-a12b", min_length=1, max_length=160
    )
    llm_max_model_turns: int = Field(default=4, ge=1, le=8)
    llm_max_tool_calls: int = Field(default=6, ge=1, le=12)
    llm_timeout_seconds: float = Field(default=45, ge=1, le=120)
    llm_max_output_tokens: int = Field(default=1024, ge=128, le=4096)
    costar_enabled: bool = False
    costar_data_dir: Path = Path("costardata")
    costar_snapshot_date: date | None = None


class Settings(PublicSettings, BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    nessie_api_key: SecretStr = SecretStr("")
    app_api_token: SecretStr = SecretStr("")
    photon_api_key: SecretStr = SecretStr("")
    photon_webhook_secret: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    nvidia_api_key: SecretStr = SecretStr("")
    matterport_sdk_key: SecretStr = SecretStr("")
    matterport_api_key: SecretStr = SecretStr("")
    matterport_api_token: SecretStr = SecretStr("")
    vexcel_api_key: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def require_live_credentials(self):
        if self.llm_enabled:
            key = self.gemini_api_key if self.llm_provider == "gemini" else self.nvidia_api_key
            if not key.get_secret_value() or not self.app_api_token.get_secret_value():
                raise ValueError(
                    "Enabled AI requires APP_API_TOKEN and the selected provider key "
                    "(GEMINI_API_KEY or NVIDIA_API_KEY) in the environment"
                )
        if self.data_source == "nessie" and not all(
            [
                self.nessie_api_key.get_secret_value(),
                self.nessie_customer_id,
                self.app_api_token.get_secret_value(),
            ]
        ):
            raise ValueError(
                "Nessie mode requires nessie_customer_id in settings.toml and "
                "NESSIE_API_KEY / APP_API_TOKEN in the environment"
            )
        return self


def load_settings(path: Path | None = None, *, env_file: Path | None = Path(".env")) -> Settings:
    """File options take priority over legacy env options; secrets stay in the environment."""
    path = path or Path(os.getenv("APP_SETTINGS_FILE", "settings.toml"))
    try:
        with path.open("rb") as source:
            options = tomllib.load(source)
    except FileNotFoundError:
        raise ValueError(
            "Settings file is missing; copy settings.example.toml to settings.toml"
        ) from None
    except tomllib.TOMLDecodeError:
        raise ValueError("Settings file is not valid TOML") from None
    public = PublicSettings.model_validate(options)
    return Settings(_env_file=env_file, **public.model_dump())
