"""Filtres d'affichage appliqués exclusivement au portefeuille déjà autorisé."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from .auth import ROLE_ADMIN, ROLE_ATC, ROLE_DR, ROLE_RAG
from .storage import UserAccessProfile

SCOPE_COMMERCIAL = "Commercial sélectionné"
SCOPE_PORTFOLIOS = "Portefeuilles"
SCOPE_AGENCY = "Agences"
SCOPE_REGION = "Régions commerciales"
SCOPE_ALL = "Tout mon périmètre"


def _text_column(clients: pd.DataFrame, column: str) -> pd.Series:
    return clients.reindex(columns=[column])[column].fillna("").astype(str).str.strip()


def salesperson_keys(clients: pd.DataFrame) -> pd.Series:
    """Distingue les commerciaux même lorsque plusieurs partagent le même code ATC."""
    email = _text_column(clients, "salesperson_email").str.casefold()
    fallback = (
        "code:"
        + _text_column(clients, "salesperson_code").str.casefold()
        + "|name:"
        + _text_column(clients, "salesperson").str.casefold()
    )
    return email.where(email.ne(""), fallback)


@dataclass(frozen=True)
class CommercialRegion:
    label: str
    ugs: tuple[str, ...]


def commercial_regions(
    clients: pd.DataFrame, profiles: Iterable[UserAccessProfile]
) -> dict[str, CommercialRegion]:
    """Reprend les groupes d'UG des habilitations DR, sans attribuer de droits."""
    all_ugs = set(_text_column(clients, "ug").str.casefold())
    available = all_ugs - {""}
    groups: dict[tuple[str, ...], set[str]] = {}
    for profile in profiles:
        if profile.role != ROLE_DR:
            continue
        ugs = tuple(sorted({value.strip().casefold() for value in profile.ugs if value.strip()}))
        if not available.intersection(ugs):
            continue
        names = groups.setdefault(ugs, set())
        if profile.display_name.strip():
            names.add(profile.display_name.strip())

    regions: dict[str, CommercialRegion] = {}
    covered: set[str] = set()
    for ugs, names in sorted(groups.items()):
        visible_ugs = tuple(ug for ug in ugs if ug in available)
        covered.update(visible_ugs)
        label = " / ".join(sorted(names, key=str.casefold)) or "Région commerciale"
        regions["-".join(ugs)] = CommercialRegion(
            f"{label} · UG {', '.join(visible_ugs)}", visible_ugs
        )
    unassigned = all_ugs - covered
    if regions and unassigned:
        regions["unassigned"] = CommercialRegion(
            "Sans région commerciale configurée", tuple(sorted(unassigned))
        )
    return regions


def map_scope_options(
    role: str, clients: pd.DataFrame, regions: Mapping[str, CommercialRegion]
) -> tuple[str, ...]:
    if role == ROLE_ATC:
        return (SCOPE_COMMERCIAL,)
    if role not in {ROLE_RAG, ROLE_DR, ROLE_ADMIN}:
        return ()
    options = [SCOPE_COMMERCIAL, SCOPE_PORTFOLIOS]
    if _text_column(clients, "agency").ne("").any():
        options.append(SCOPE_AGENCY)
    if role in {ROLE_DR, ROLE_ADMIN} and regions:
        options.append(SCOPE_REGION)
    options.append(SCOPE_ALL)
    return tuple(options)


def default_map_scope(role: str, options: Sequence[str]) -> str:
    if role in {ROLE_DR, ROLE_ADMIN} and SCOPE_REGION in options:
        return SCOPE_REGION
    if role == ROLE_RAG and SCOPE_PORTFOLIOS in options:
        return SCOPE_PORTFOLIOS
    return SCOPE_ALL if SCOPE_ALL in options else SCOPE_COMMERCIAL


def filter_map_clients(
    clients: pd.DataFrame,
    *,
    role: str,
    scope: str,
    active_salesperson: str,
    portfolios: Sequence[str] = (),
    agencies: Sequence[str] = (),
    region_keys: Sequence[str] = (),
    regions: Mapping[str, CommercialRegion] | None = None,
) -> pd.DataFrame:
    """Réduit le périmètre autorisé ; une sélection vide ou inconnue reste vide."""
    regions = regions or {}
    if scope not in map_scope_options(role, clients, regions):
        return clients.iloc[:0].copy()
    if scope == SCOPE_ALL:
        return clients.copy()
    if scope == SCOPE_COMMERCIAL:
        mask = salesperson_keys(clients).eq(active_salesperson)
    elif scope == SCOPE_PORTFOLIOS:
        mask = salesperson_keys(clients).isin(portfolios)
    elif scope == SCOPE_AGENCY:
        mask = (
            _text_column(clients, "agency")
            .str.casefold()
            .isin(value.strip().casefold() for value in agencies)
        )
    else:
        ugs = {ug for key in region_keys if key in regions for ug in regions[key].ugs}
        mask = _text_column(clients, "ug").str.casefold().isin(ugs)
    return clients.loc[mask].copy()
