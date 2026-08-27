from __future__ import annotations

from pathlib import Path

from opti_route.config import load_settings

CONFIG_VARIABLES = (
    "AUTH_MODE",
    "AUTH_USERNAME",
    "AUTH_PASSWORD",
    "AZURE_MAPS_SUBSCRIPTION_KEY",
    "AZURE_MAPS_KEY",
)
EMPTY_PROJECT_ROOT = Path("tests/.config-test-no-env")


def test_settings_read_streamlit_app_secrets(monkeypatch) -> None:
    for name in CONFIG_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    settings = load_settings(
        EMPTY_PROJECT_ROOT,
        secrets={
            "app": {
                "AUTH_MODE": "password",
                "AUTH_USERNAME": "collaborateur-cloud",
                "AUTH_PASSWORD": "secret-cloud",
                "AZURE_MAPS_SUBSCRIPTION_KEY": "azure-cloud",
            }
        },
    )

    assert settings.auth_mode == "password"
    assert settings.auth_username == "collaborateur-cloud"
    assert settings.auth_password == "secret-cloud"
    assert settings.azure_maps_key == "azure-cloud"


def test_environment_values_take_priority_over_streamlit_secrets(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.setenv("AUTH_USERNAME", "local")
    monkeypatch.setenv("AUTH_PASSWORD", "local-secret")

    settings = load_settings(
        EMPTY_PROJECT_ROOT,
        secrets={
            "app": {
                "AUTH_MODE": "password",
                "AUTH_USERNAME": "cloud",
                "AUTH_PASSWORD": "cloud-secret",
            }
        },
    )

    assert settings.auth_mode == "none"
    assert settings.auth_username == "local"
    assert settings.auth_password == "local-secret"
