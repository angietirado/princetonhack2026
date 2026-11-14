import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from spending_api.config import Settings, load_settings
from spending_api.main import create_app


def test_public_file_pins_customer_and_overrides_old_env_options(tmp_path, monkeypatch):
    options = tmp_path / "settings.toml"
    options.write_text('data_source = "nessie"\nnessie_customer_id = "cus_pinned"\n')
    credentials = tmp_path / ".env"
    credentials.write_text("NESSIE_API_KEY=fake-key\nAPP_API_TOKEN=test-app-token\n")
    monkeypatch.setenv("NESSIE_CUSTOMER_ID", "cus_old")
    monkeypatch.setenv("DATA_SOURCE", "fixture")
    monkeypatch.delenv("NESSIE_API_KEY", raising=False)
    monkeypatch.delenv("APP_API_TOKEN", raising=False)

    for _ in range(2):
        settings = load_settings(options, env_file=credentials)
        assert settings.data_source == "nessie"
        assert settings.nessie_customer_id == "cus_pinned"
        assert settings.app_api_token.get_secret_value() == "test-app-token"
        assert settings.nessie_api_key.get_secret_value() == "fake-key"
        assert settings.photon_enabled is False


def test_settings_file_rejects_secrets_without_echoing_values(tmp_path):
    options = tmp_path / "settings.toml"
    options.write_text('app_api_token = "must-never-appear-in-error"\n')
    with pytest.raises(ValidationError) as error:
        load_settings(options, env_file=None)
    assert "must-never-appear-in-error" not in str(error.value)


def test_live_settings_still_require_pinned_customer_and_credentials(tmp_path, monkeypatch):
    options = tmp_path / "settings.toml"
    options.write_text('data_source = "nessie"\n')
    monkeypatch.setenv("NESSIE_API_KEY", "test-key")
    monkeypatch.setenv("APP_API_TOKEN", "test-token")
    monkeypatch.setenv("NESSIE_CUSTOMER_ID", "must-not-replace-file")
    with pytest.raises(ValidationError, match="nessie_customer_id"):
        load_settings(options, env_file=None)


def test_photon_remains_disabled_even_with_credentials(tmp_path):
    settings = Settings(
        _env_file=None,
        photon_enabled=False,
        photon_api_key="unused",
        photon_webhook_secret="unused",
        photon_agent_user_id="unused",
        photon_allowed_sender_id="unused",
        photon_inbox_path=tmp_path / "inbox.sqlite3",
    )
    with TestClient(create_app(settings)) as client:
        assert client.post("/webhooks/photon", json={}).status_code == 503
    assert not settings.photon_inbox_path.exists()


@pytest.mark.parametrize(
    "provider,key_name", [("gemini", "gemini_api_key"), ("nemotron", "nvidia_api_key")]
)
def test_ai_requires_selected_provider_key_and_app_token(provider, key_name):
    base = dict(
        _env_file=None,
        data_source="fixture",
        llm_enabled=True,
        llm_provider=provider,
        gemini_api_key="",
        nvidia_api_key="",
        app_api_token="",
    )
    with pytest.raises(ValidationError, match="Enabled AI"):
        Settings(**base)
    base[key_name] = "secret-must-not-be-echoed"
    with pytest.raises(ValidationError) as error:
        Settings(**base)
    assert "secret-must-not-be-echoed" not in str(error.value)
    base["app_api_token"] = "fake-app-token"
    assert Settings(**base).llm_provider == provider


@pytest.mark.parametrize("name", ["gemini_api_key", "nvidia_api_key", "matterport_api_token"])
def test_public_ai_settings_reject_credentials(tmp_path, name):
    options = tmp_path / "settings.toml"
    options.write_text(f'{name} = "never-echo-this"\n')
    with pytest.raises(ValidationError) as error:
        load_settings(options, env_file=None)
    assert "never-echo-this" not in str(error.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("llm_max_model_turns", 100),
        ("llm_max_tool_calls", 0),
        ("llm_timeout_seconds", 1000),
        ("llm_max_output_tokens", 999999),
        ("llm_provider", "unconfigured-provider"),
    ],
)
def test_ai_settings_bound_work(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, data_source="fixture", llm_enabled=False, **{field: value})


@pytest.mark.parametrize(
    "missing", ["costar_enabled", "matterport_api_key", "matterport_api_token", "app_api_token"]
)
def test_matterport_requires_catalog_and_backend_credentials(missing):
    options = dict(
        _env_file=None,
        data_source="fixture",
        llm_enabled=False,
        costar_enabled=True,
        matterport_enabled=True,
        matterport_api_key="fake-id",
        matterport_api_token="never-echo-secret",
        app_api_token="fake-app-token",
    )
    options[missing] = False if missing == "costar_enabled" else ""
    with pytest.raises(ValidationError) as error:
        Settings(**options)
    assert "never-echo-secret" not in str(error.value)
