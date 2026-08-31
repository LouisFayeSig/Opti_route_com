from opti_route.auth import AuthenticatedUser, credentials_match


def test_credentials_match_requires_both_exact_values() -> None:
    assert credentials_match("collaborateur", "secret", "collaborateur", "secret")
    assert not credentials_match("autre", "secret", "collaborateur", "secret")
    assert not credentials_match("collaborateur", "autre", "collaborateur", "secret")


def test_authenticated_user_exposes_admin_role() -> None:
    assert AuthenticatedUser("Admin", "password", "admin").is_admin
    assert not AuthenticatedUser("Utilisateur", "entra").is_admin
