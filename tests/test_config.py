from __future__ import annotations

from pathlib import Path

import pytest

from opti_route.config import load_settings

CONFIG_VARIABLES = (
    "AUTH_MODE",
    "AUTH_USERNAME",
    "AUTH_PASSWORD",
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "LOCAL_TEST_ACCOUNTS_JSON",
    "ADMIN_EMAILS",
    "ENTRA_TENANT_ID",
    "ENTRA_ROLE_ATC",
    "ENTRA_ROLE_RAG",
    "ENTRA_ROLE_DR",
    "ENTRA_ROLE_DIRECTOR",
    "ENTRA_ROLE_ADMIN",
    "APP_STORAGE_BACKEND",
    "AZURE_STORAGE_ACCOUNT_URL",
    "AZURE_STORAGE_CONTAINER",
    "AZURE_STORAGE_CONNECTION_STRING",
    "AZURE_MAPS_SUBSCRIPTION_KEY",
    "AZURE_MAPS_KEY",
    "IGN_GEOCODING_ENDPOINT",
    "IGN_GEOCODING_MAX_REQUESTS_PER_SECOND",
    "GEOCODING_AZURE_FALLBACK",
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
                "ADMIN_USERNAME": "administrateur-cloud",
                "ADMIN_PASSWORD": "secret-admin-cloud",
                "LOCAL_TEST_ACCOUNTS_JSON": (
                    '[{"username":"test-atc","password":"test-secret",'
                    '"display_name":"Test ATC","role":"atc","atc_code":"31-02-004"}]'
                ),
                "ADMIN_EMAILS": "Admin@Example.com, autre@example.com",
                "ENTRA_TENANT_ID": "tenant-id",
                "ENTRA_ROLE_ATC": "Route.ATC",
                "ENTRA_ROLE_RAG": "Route.RAG",
                "ENTRA_ROLE_DR": "Route.DR",
                "ENTRA_ROLE_ADMIN": "Route.Admin",
                "APP_STORAGE_BACKEND": "azure_blob",
                "AZURE_STORAGE_ACCOUNT_URL": "https://storage.blob.core.windows.net",
                "AZURE_STORAGE_CONTAINER": "private-data",
                "AZURE_MAPS_SUBSCRIPTION_KEY": "azure-cloud",
                "IGN_GEOCODING_ENDPOINT": "https://geocoder.example.test/search",
                "IGN_GEOCODING_MAX_REQUESTS_PER_SECOND": "35",
                "GEOCODING_AZURE_FALLBACK": "true",
            }
        },
    )

    assert settings.auth_mode == "password"
    assert settings.auth_username == "collaborateur-cloud"
    assert settings.auth_password == "secret-cloud"
    assert settings.admin_username == "administrateur-cloud"
    assert settings.admin_password == "secret-admin-cloud"
    assert settings.local_test_accounts[0].username == "test-atc"
    assert settings.local_test_accounts[0].atc_code == "31-02-004"
    assert settings.admin_emails == ("admin@example.com", "autre@example.com")
    assert settings.entra_tenant_id == "tenant-id"
    assert settings.entra_atc_role == "Route.ATC"
    assert settings.entra_rag_role == "Route.RAG"
    assert settings.entra_dr_role == "Route.DR"
    assert settings.entra_admin_role == "Route.Admin"
    assert settings.app_storage_backend == "azure_blob"
    assert settings.azure_storage_account_url == "https://storage.blob.core.windows.net"
    assert settings.azure_storage_container == "private-data"
    assert settings.azure_maps_key == "azure-cloud"
    assert settings.ign_geocoding_endpoint == "https://geocoder.example.test/search"
    assert settings.ign_geocoding_max_requests_per_second == 35
    assert settings.geocoding_azure_fallback is True


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


def test_azure_geocoding_fallback_is_enabled_by_default(monkeypatch) -> None:
    for name in CONFIG_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    assert load_settings(EMPTY_PROJECT_ROOT).geocoding_azure_fallback is True


def test_entra_application_roles_must_be_distinct(monkeypatch) -> None:
    for name in CONFIG_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="distincts"):
        load_settings(
            EMPTY_PROJECT_ROOT,
            secrets={
                "app": {
                    "ENTRA_ROLE_ATC": "OptiRoute.Same",
                    "ENTRA_ROLE_RAG": "OptiRoute.Same",
                    "ENTRA_ROLE_DR": "OptiRoute.DR",
                    "ENTRA_ROLE_ADMIN": "D-FR-APP-OPTIROUTE-ADMIN",
                }
            },
        )


def test_local_test_accounts_require_a_scope(monkeypatch) -> None:
    for name in CONFIG_VARIABLES:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="atc_code"):
        load_settings(
            EMPTY_PROJECT_ROOT,
            secrets={
                "app": {
                    "LOCAL_TEST_ACCOUNTS_JSON": (
                        '[{"username":"test-atc","password":"secret",'
                        '"role":"atc"}]'
                    )
                }
            },
        )
