from pathlib import Path

import pytest

from opti_route.auth import (
    AuthenticatedUser,
    AuthenticationError,
    authenticated_user_from_entra_claims,
    credentials_match,
    password_user_from_credentials,
)
from opti_route.config import LocalTestAccount, Settings


def test_credentials_match_requires_both_exact_values() -> None:
    assert credentials_match("collaborateur", "secret", "collaborateur", "secret")
    assert not credentials_match("autre", "secret", "collaborateur", "secret")
    assert not credentials_match("collaborateur", "autre", "collaborateur", "secret")


def test_password_authentication_recognizes_a_scoped_local_test_account() -> None:
    settings = Settings(
        azure_maps_endpoint="https://atlas.microsoft.com",
        azure_maps_key=None,
        geocode_cache_path=Path("cache.sqlite3"),
        local_test_accounts=(
            LocalTestAccount(
                username="test-rag",
                password="test-secret",
                display_name="Test RAG",
                role="rag",
                rag_email="c.vernay@litt.fr",
            ),
        ),
    )

    user = password_user_from_credentials(settings, "test-rag", "test-secret")

    assert user is not None
    assert user.is_rag
    assert user.principal_id == "local:test-rag"
    assert password_user_from_credentials(settings, "test-rag", "incorrect") is None


def test_authenticated_user_exposes_admin_role() -> None:
    assert AuthenticatedUser("Admin", "password", "admin").is_admin
    assert not AuthenticatedUser("Utilisateur", "entra").is_admin


def _settings() -> Settings:
    return Settings(
        azure_maps_endpoint="https://atlas.microsoft.com",
        azure_maps_key=None,
        geocode_cache_path=Path("cache.sqlite3"),
        entra_tenant_id="tenant-1",
    )


def test_entra_claims_use_application_role_and_stable_object_id() -> None:
    user = authenticated_user_from_entra_claims(
        {
            "oid": "object-123",
            "tid": "tenant-1",
            "name": "RAG",
            "preferred_username": "rag@example.com",
            "roles": ["D-FR-APP-OPTIROUTE-RAG"],
        },
        _settings(),
    )

    assert user.is_rag
    assert user.principal_id == "object-123"
    assert user.principal_name == "rag@example.com"


def test_entra_claims_recognize_dr_role() -> None:
    user = authenticated_user_from_entra_claims(
        {
            "oid": "object-dr",
            "tid": "tenant-1",
            "roles": ["D-FR-APP-OPTIROUTE-DR"],
        },
        _settings(),
    )

    assert user.is_dr


def test_entra_claims_fail_closed_without_a_known_role() -> None:
    with pytest.raises(AuthenticationError, match="Aucun role"):
        authenticated_user_from_entra_claims(
            {"oid": "object-123", "tid": "tenant-1", "roles": ["Another.App"]},
            _settings(),
        )


def test_entra_claims_reject_another_tenant() -> None:
    with pytest.raises(AuthenticationError, match="tenant"):
        authenticated_user_from_entra_claims(
            {"oid": "object-123", "tid": "tenant-2", "roles": ["D-FR-APP-OPTIROUTE-ATC"]},
            _settings(),
        )


def test_entra_claims_fail_closed_with_multiple_application_roles() -> None:
    with pytest.raises(AuthenticationError, match="Plusieurs roles"):
        authenticated_user_from_entra_claims(
            {
                "oid": "object-123",
                "tid": "tenant-1",
                "roles": ["D-FR-APP-OPTIROUTE-ATC", "D-FR-APP-OPTIROUTE-ADMIN"],
            },
            _settings(),
        )
