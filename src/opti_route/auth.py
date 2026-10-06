from __future__ import annotations

import hmac
import time
from collections.abc import Mapping
from dataclasses import dataclass

import streamlit as st

from .config import Settings

_AUTHENTICATED_KEY = "opti_route_authenticated"
_AUTHENTICATED_NAME_KEY = "opti_route_authenticated_name"
_AUTHENTICATED_ROLE_KEY = "opti_route_authenticated_role"
_FAILED_ATTEMPTS_KEY = "opti_route_failed_login_attempts"
_LOCKED_UNTIL_KEY = "opti_route_login_locked_until"
_MAX_ATTEMPTS = 5
_LOCK_SECONDS = 30

ROLE_ATC = "atc"
ROLE_DIRECTOR = "director"
ROLE_ADMIN = "admin"
VALID_ROLES = (ROLE_ATC, ROLE_DIRECTOR, ROLE_ADMIN)


class AuthenticationError(RuntimeError):
    """Les claims d'identite ne permettent pas d'autoriser la session."""


@dataclass(frozen=True)
class AuthenticatedUser:
    display_name: str
    method: str
    role: str = ROLE_ATC
    principal_id: str = ""
    principal_name: str = ""
    tenant_id: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_director(self) -> bool:
        return self.role == ROLE_DIRECTOR

    @property
    def is_atc(self) -> bool:
        return self.role == ROLE_ATC


def _claim_values(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value)
    return ()


def authenticated_user_from_entra_claims(
    claims: Mapping[str, object], settings: Settings
) -> AuthenticatedUser:
    """Construit l'utilisateur depuis le jeton OIDC valide par Streamlit."""

    tenant_id = str(claims.get("tid") or "").strip()
    if settings.entra_tenant_id and tenant_id.casefold() != settings.entra_tenant_id.casefold():
        raise AuthenticationError("Ce compte n'appartient pas au tenant Entra autorise.")

    principal_id = str(claims.get("oid") or "").strip()
    if not principal_id:
        raise AuthenticationError("Le jeton Entra ne contient pas d'identifiant utilisateur stable.")

    role_claims = {value.strip().casefold() for value in _claim_values(claims.get("roles"))}
    configured_roles = (
        (ROLE_ADMIN, settings.entra_admin_role),
        (ROLE_DIRECTOR, settings.entra_director_role),
        (ROLE_ATC, settings.entra_atc_role),
    )
    matched_roles = [
        internal_role
        for internal_role, claim_value in configured_roles
        if claim_value.strip().casefold() in role_claims
    ]
    if not matched_roles:
        raise AuthenticationError(
            "Aucun role Opti Route reconnu n'est attribue a ce compte dans Microsoft Entra ID."
        )
    if len(matched_roles) > 1:
        raise AuthenticationError(
            "Plusieurs roles Opti Route sont attribues a ce compte. Un seul role est autorise."
        )
    role = matched_roles[0]

    principal_name = str(
        claims.get("preferred_username") or claims.get("email") or claims.get("upn") or ""
    ).strip()
    display_name = str(claims.get("name") or principal_name or "Collaborateur").strip()
    return AuthenticatedUser(
        display_name=display_name,
        method="entra",
        role=role,
        principal_id=principal_id,
        principal_name=principal_name,
        tenant_id=tenant_id,
    )


def credentials_match(
    supplied_username: str,
    supplied_password: str,
    expected_username: str,
    expected_password: str,
) -> bool:
    """Compare les deux secrets sans court-circuit dépendant de leur contenu."""
    username_matches = hmac.compare_digest(
        supplied_username.encode("utf-8"), expected_username.encode("utf-8")
    )
    password_matches = hmac.compare_digest(
        supplied_password.encode("utf-8"), expected_password.encode("utf-8")
    )
    return username_matches and password_matches


def _login_heading(description: str) -> None:
    st.title("🧭 Opti Route Com")
    st.markdown(
        '<div class="opti-subtitle">Accès réservé aux collaborateurs autorisés.</div>',
        unsafe_allow_html=True,
    )
    st.info(description, icon="🔐")


def _require_password_auth(settings: Settings) -> AuthenticatedUser:
    if not settings.auth_username or not settings.auth_password:
        _login_heading("L’authentification locale doit être configurée par l’administrateur.")
        st.error(
            "Renseignez AUTH_USERNAME et AUTH_PASSWORD dans le fichier .env en local, "
            "ou dans la section [app] des secrets Streamlit en déploiement, puis redémarrez."
        )
        st.stop()

    if st.session_state.get(_AUTHENTICATED_KEY) is True:
        name = str(st.session_state.get(_AUTHENTICATED_NAME_KEY) or settings.auth_username)
        return AuthenticatedUser(
            name,
            "password",
            str(st.session_state.get(_AUTHENTICATED_ROLE_KEY) or ROLE_ATC),
            principal_id=f"local:{name.casefold()}",
            principal_name=name,
        )

    _login_heading("Saisissez l’identifiant partagé configuré pour cette application.")
    now = time.monotonic()
    locked_until = float(st.session_state.get(_LOCKED_UNTIL_KEY, 0.0))
    remaining = max(0, round(locked_until - now))
    if remaining:
        st.error(f"Trop de tentatives. Réessayez dans {remaining} secondes.")

    with st.form("opti_route_login", clear_on_submit=False):
        username = st.text_input("Nom d’utilisateur", autocomplete="username")
        password = st.text_input(
            "Mot de passe",
            type="password",
            autocomplete="current-password",
        )
        submitted = st.form_submit_button(
            "Se connecter",
            type="primary",
            use_container_width=True,
            disabled=remaining > 0,
        )

    if submitted:
        user_matches = credentials_match(
            username,
            password,
            settings.auth_username,
            settings.auth_password,
        )
        admin_matches = bool(
            settings.admin_username and settings.admin_password
        ) and credentials_match(
            username,
            password,
            settings.admin_username or "",
            settings.admin_password or "",
        )
        if user_matches or admin_matches:
            st.session_state[_AUTHENTICATED_KEY] = True
            st.session_state[_AUTHENTICATED_NAME_KEY] = username
            st.session_state[_AUTHENTICATED_ROLE_KEY] = (
                ROLE_ADMIN if admin_matches else ROLE_ATC
            )
            st.session_state.pop(_FAILED_ATTEMPTS_KEY, None)
            st.session_state.pop(_LOCKED_UNTIL_KEY, None)
            st.rerun()

        attempts = int(st.session_state.get(_FAILED_ATTEMPTS_KEY, 0)) + 1
        if attempts >= _MAX_ATTEMPTS:
            st.session_state[_FAILED_ATTEMPTS_KEY] = 0
            st.session_state[_LOCKED_UNTIL_KEY] = time.monotonic() + _LOCK_SECONDS
            st.error(f"Trop de tentatives. Accès bloqué pendant {_LOCK_SECONDS} secondes.")
        else:
            st.session_state[_FAILED_ATTEMPTS_KEY] = attempts
            st.error("Identifiant ou mot de passe incorrect.")
    st.stop()


def _require_entra_auth(settings: Settings) -> AuthenticatedUser:
    user_data: dict[str, object] = {}
    try:
        if getattr(st.user, "is_logged_in", False):
            user_data = st.user.to_dict()
    except Exception:
        user_data = {}

    if user_data:
        expires_at = user_data.get("exp")
        try:
            if expires_at is None:
                raise ValueError
            is_expired = time.time() >= float(str(expires_at))
        except (TypeError, ValueError):
            _login_heading("La session Microsoft reçue est invalide.")
            st.error("Le jeton Entra ne contient pas de date d'expiration valide.")
            st.button("Se déconnecter", on_click=st.logout, use_container_width=True)
            st.stop()
        if is_expired:
            st.logout()
            st.stop()
        try:
            return authenticated_user_from_entra_claims(user_data, settings)
        except AuthenticationError as exc:
            _login_heading("Votre identite Microsoft est valide, mais l'acces est refuse.")
            st.error(str(exc))
            principal_id = str(user_data.get("oid") or "").strip()
            if principal_id:
                st.code(principal_id, language=None)
                st.caption("Identifiant Entra a transmettre a l'administrateur si necessaire.")
            st.button("Se deconnecter", on_click=st.logout, use_container_width=True)
            st.stop()

    _login_heading("Connectez-vous avec votre compte Microsoft professionnel.")
    if st.button("Se connecter avec Microsoft", type="primary", use_container_width=True):
        try:
            st.login("microsoft")
        except Exception:
            st.error(
                "La configuration Entra ID est absente ou incomplète. "
                "Vérifiez .streamlit/secrets.toml."
            )
    st.stop()


def require_authentication(settings: Settings) -> AuthenticatedUser:
    if settings.auth_mode == "none":
        return AuthenticatedUser(
            "Accès non protégé",
            "none",
            principal_id="local:anonymous",
            principal_name="anonymous",
        )
    if settings.auth_mode == "password":
        return _require_password_auth(settings)
    if settings.auth_mode == "entra":
        return _require_entra_auth(settings)

    _login_heading("La configuration d’authentification est invalide.")
    st.error("AUTH_MODE doit valoir password, entra ou none.")
    st.stop()


def render_account_controls(user: AuthenticatedUser) -> None:
    if user.method == "none":
        st.markdown(
            '<span class="opti-badge opti-warn">⚠ Accès non protégé</span>',
            unsafe_allow_html=True,
        )
        return

    role_labels = {
        ROLE_ADMIN: "Administrateur",
        ROLE_DIRECTOR: "Directeur",
        ROLE_ATC: "ATC",
    }
    role_label = role_labels.get(user.role, "Accès refusé")
    st.caption(f"Connecté : {user.display_name} · {role_label}")
    if user.method == "entra":
        st.button("Se déconnecter", on_click=st.logout, use_container_width=True)
    elif st.button("Se déconnecter", use_container_width=True):
        st.session_state.pop(_AUTHENTICATED_KEY, None)
        st.session_state.pop(_AUTHENTICATED_NAME_KEY, None)
        st.session_state.pop(_AUTHENTICATED_ROLE_KEY, None)
        st.session_state.pop(_FAILED_ATTEMPTS_KEY, None)
        st.session_state.pop(_LOCKED_UNTIL_KEY, None)
        st.rerun()
