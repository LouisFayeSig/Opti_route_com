from __future__ import annotations

import hmac
import time
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


@dataclass(frozen=True)
class AuthenticatedUser:
    display_name: str
    method: str
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


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
        return AuthenticatedUser(
            str(st.session_state.get(_AUTHENTICATED_NAME_KEY) or settings.auth_username),
            "password",
            str(st.session_state.get(_AUTHENTICATED_ROLE_KEY) or "user"),
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
            st.session_state[_AUTHENTICATED_ROLE_KEY] = "admin" if admin_matches else "user"
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
            is_expired = expires_at is not None and time.time() >= float(str(expires_at))
        except ValueError:
            is_expired = False
        if is_expired:
            st.logout()
        display_name = str(
            user_data.get("name")
            or user_data.get("preferred_username")
            or user_data.get("email")
            or "Collaborateur"
        )
        principal = (
            str(
                user_data.get("preferred_username")
                or user_data.get("email")
                or user_data.get("upn")
                or ""
            )
            .strip()
            .casefold()
        )
        role = "admin" if principal and principal in settings.admin_emails else "user"
        return AuthenticatedUser(display_name, "entra", role)

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
        return AuthenticatedUser("Accès non protégé", "none")
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

    role_label = "Administrateur" if user.is_admin else "Utilisateur"
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
