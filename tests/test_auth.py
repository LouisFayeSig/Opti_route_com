from opti_route.auth import credentials_match


def test_credentials_match_requires_both_exact_values() -> None:
    assert credentials_match("collaborateur", "secret", "collaborateur", "secret")
    assert not credentials_match("autre", "secret", "collaborateur", "secret")
    assert not credentials_match("collaborateur", "autre", "collaborateur", "secret")
