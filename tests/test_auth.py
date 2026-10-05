from pathlib import Path

import pytest

from opti_route.auth import (
    AuthenticatedUser,
    AuthenticationError,
    authenticated_user_from_entra_claims,
    credentials_match,
)
from opti_route.config import Settings


def test_credentials_match_requires_both_exact_values() -> None:
    assert credentials_match("collaborateur", "secret", "collaborateur", "secret")
    assert not credentials_match("autre", "secret", "collaborateur", "secret")
    assert not credentials_match("collaborateur", "autre", "collaborateur", "secret")


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
            "name": "Directrice",
            "preferred_username": "direction@example.com",
            "roles": ["OptiRoute.Director"],
        },
        _settings(),
    )

    assert user.is_director
    assert user.principal_id == "object-123"
    assert user.principal_name == "direction@example.com"


def test_entra_claims_fail_closed_without_a_known_role() -> None:
    with pytest.raises(AuthenticationError, match="Aucun role"):
        authenticated_user_from_entra_claims(
            {"oid": "object-123", "tid": "tenant-1", "roles": ["Another.App"]},
            _settings(),
        )


def test_entra_claims_reject_another_tenant() -> None:
    with pytest.raises(AuthenticationError, match="tenant"):
        authenticated_user_from_entra_claims(
            {"oid": "object-123", "tid": "tenant-2", "roles": ["OptiRoute.ATC"]},
            _settings(),
        )


def test_entra_claims_fail_closed_with_multiple_application_roles() -> None:
    with pytest.raises(AuthenticationError, match="Plusieurs roles"):
        authenticated_user_from_entra_claims(
            {
                "oid": "object-123",
                "tid": "tenant-1",
                "roles": ["OptiRoute.ATC", "OptiRoute.Admin"],
            },
            _settings(),
        )
