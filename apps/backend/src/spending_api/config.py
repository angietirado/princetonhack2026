from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_source: Literal["fixture", "nessie"] = "fixture"
    fixture_path: Path = Path(".data/demo.json")
    nessie_base_url: str = "https://v2-prod-api.nessieisreal.com"
    nessie_api_key: SecretStr = SecretStr("")
    nessie_customer_id: str = ""
    app_api_token: SecretStr = SecretStr("")
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    photon_api_base_url: str = "https://api.photon.codes/v1"
    photon_api_key: SecretStr = SecretStr("")
    photon_project_id: str = ""
    photon_agent_user_id: str = ""
    photon_webhook_secret: SecretStr = SecretStr("")
    photon_allowed_sender_id: str = ""
    photon_inbox_path: Path = Path(".data/photon-inbox.sqlite3")

    @model_validator(mode="after")
    def require_live_credentials(self):
        if self.data_source == "nessie" and not all(
            [
                self.nessie_api_key.get_secret_value(),
                self.nessie_customer_id,
                self.app_api_token.get_secret_value(),
            ]
        ):
            raise ValueError("Nessie mode requires API key, customer ID, and APP_API_TOKEN")
        return self
