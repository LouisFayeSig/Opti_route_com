from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .auth import ROLE_ATC, ROLE_DIRECTOR, AuthenticatedUser
from .storage import UserAccessProfile


class AccessDeniedError(PermissionError):
    """L'utilisateur authentifie ne dispose pas du perimetre demande."""


@dataclass(frozen=True)
class AuthorizedPortfolio:
    clients: pd.DataFrame
    profile: UserAccessProfile | None

    @property
    def signature(self) -> str:
        if self.profile is None:
            return "admin:all"
        agencies = ",".join(sorted(self.profile.agencies, key=str.casefold))
        return (
            f"{self.profile.principal_id}:{self.profile.role}:"
            f"{self.profile.atc_code or ''}:{self.profile.atc_email or ''}:"
            f"{self.profile.atc_name or ''}:"
            f"{self.profile.director_email or ''}:{agencies}:{self.profile.updated_at}"
        )


def _normalized_text(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.casefold()


def authorize_portfolio(
    clients: pd.DataFrame,
    user: AuthenticatedUser,
    profile: UserAccessProfile | None,
) -> AuthorizedPortfolio:
    """Applique le perimetre d'acces avant toute utilisation des donnees client.

    Le role vient du jeton Entra signe. Le profil stocke ne peut que restreindre ce
    role a un code ATC ou a des agences ; il ne peut jamais elever les privileges.
    """

    if user.is_admin:
        return AuthorizedPortfolio(clients.copy(), None)

    if profile is None:
        raise AccessDeniedError(
            "Votre compte est authentifie mais aucune habilitation Opti Route ne lui est "
            "associee. Contactez un administrateur."
        )
    if profile.role != user.role:
        raise AccessDeniedError(
            "Le role Entra et l'habilitation Opti Route de ce compte ne correspondent pas. "
            "L'acces aux donnees est bloque par securite."
        )

    if user.role == ROLE_ATC:
        if profile.atc_email:
            if "salesperson_email" not in clients.columns:
                raise AccessDeniedError("Le perimetre ATC par e-mail ne peut pas etre applique.")
            mask = _normalized_text(clients["salesperson_email"]).eq(
                profile.atc_email.casefold()
            )
        elif "salesperson_code" in clients.columns and profile.atc_code:
            mask = _normalized_text(clients["salesperson_code"]).eq(
                profile.atc_code.strip().casefold()
            )
            if profile.atc_name:
                if "salesperson" not in clients.columns:
                    raise AccessDeniedError("Le nom du commercial est absent du portefeuille.")
                mask &= _normalized_text(clients["salesperson"]).eq(
                    profile.atc_name.casefold()
                )
            elif "salesperson" in clients.columns:
                matching_salespeople = _normalized_text(clients.loc[mask, "salesperson"])
                if matching_salespeople.nunique() > 1:
                    raise AccessDeniedError(
                        "Le code ATC est ambigu dans le portefeuille. "
                        "Configurez le commercial ou son e-mail dans l'habilitation."
                    )
        else:
            raise AccessDeniedError("Le perimetre ATC ne peut pas etre applique.")
    elif user.role == ROLE_DIRECTOR:
        if profile.director_email:
            if "director_email" not in clients.columns:
                raise AccessDeniedError("Le perimetre directeur par e-mail ne peut pas etre applique.")
            mask = _normalized_text(clients["director_email"]).eq(
                profile.director_email.casefold()
            )
            if "salesperson_email" in clients.columns:
                mask |= _normalized_text(clients["salesperson_email"]).eq(
                    profile.director_email.casefold()
                )
        elif "agency" in clients.columns and profile.agencies:
            allowed_agencies = {value.strip().casefold() for value in profile.agencies}
            mask = _normalized_text(clients["agency"]).isin(allowed_agencies)
        else:
            raise AccessDeniedError("Le perimetre agence ne peut pas etre applique.")
    else:
        raise AccessDeniedError("Ce role applicatif n'est pas autorise.")

    scoped_clients = clients.loc[mask].copy()
    if scoped_clients.empty:
        raise AccessDeniedError(
            "Aucun client du portefeuille actif ne correspond a votre habilitation. "
            "Contactez un administrateur."
        )
    return AuthorizedPortfolio(scoped_clients, profile)
