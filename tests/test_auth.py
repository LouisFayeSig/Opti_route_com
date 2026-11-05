from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from opti_route import auth
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
        auth_mode="entra",
    )


class _AuthenticationStopped(Exception):
    pass


def _entra_ui(monkeypatch, expires_at: object) -> Mock:
    ui = Mock()
    ui.user.is_logged_in = True
    ui.user.to_dict.return_value = {
        "oid": "object-123",
        "tid": "tenant-1",
        "roles": ["D-FR-APP-OPTIROUTE-ATC"],
        "exp": expires_at,
    }
    ui.stop.side_effect = _AuthenticationStopped
    monkeypatch.setattr(auth, "st", ui)
    return ui


def test_expired_entra_session_renews_with_sso_without_provider_logout(monkeypatch) -> None:
    ui = _entra_ui(monkeypatch, 1)

    with pytest.raises(_AuthenticationStopped):
        auth.require_authentication(_settings())

    ui.login.assert_called_once_with("microsoft")
    ui.logout.assert_not_called()


@pytest.mark.parametrize("expiration", [None, "invalide", "nan", "inf", "-inf"])
def test_entra_session_rejects_invalid_expiration(monkeypatch, expiration) -> None:
    ui = _entra_ui(monkeypatch, expiration)

    with pytest.raises(_AuthenticationStopped):
        auth.require_authentication(_settings())

    ui.error.assert_called_once()
    ui.login.assert_not_called()


def test_valid_entra_session_is_reused(monkeypatch) -> None:
    ui = _entra_ui(monkeypatch, 10**12)

    user = auth.require_authentication(_settings())

    assert user.principal_id == "object-123"
    ui.login.assert_not_called()
    ui.logout.assert_not_called()


@pytest.mark.parametrize("method", ["password", "entra"])
def test_logout_clears_all_session_data(monkeypatch, method) -> None:
    ui = Mock()
    ui.session_state = {
        "opti_route_authenticated": True,
        "route_plan": object(),
        "portfolio_layer_visible": False,
        "admin_portfolio_upload": b"private data",
    }
    monkeypatch.setattr(auth, "st", ui)

    auth._logout(method)

    assert ui.session_state == {}
    if method == "entra":
        ui.logout.assert_called_once()
    else:
        ui.logout.assert_not_called()


def test_password_lock_is_enforced_even_if_a_form_is_submitted(monkeypatch) -> None:
    ui = MagicMock()
    ui.session_state = {"opti_route_login_locked_until": 100.6}
    ui.form_submit_button.return_value = True
    ui.text_input.side_effect = ["user", "secret"]
    ui.stop.side_effect = _AuthenticationStopped
    monkeypatch.setattr(auth, "st", ui)
    monkeypatch.setattr(auth.time, "monotonic", lambda: 100.2)
    credential_check = Mock()
    monkeypatch.setattr(auth, "password_user_from_credentials", credential_check)
    settings = replace(_settings(), auth_mode="password", auth_username="user", auth_password="secret")

    with pytest.raises(_AuthenticationStopped):
        auth.require_authentication(settings)

    credential_check.assert_not_called()
    assert ui.form_submit_button.call_args.kwargs["disabled"] is True


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
