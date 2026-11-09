"""Provisionnement des périmètres Opti Route depuis Entra et le portefeuille.

Les groupes Entra restent la source d'autorité des rôles. Ce module ne crée que
les profils de périmètre stockés par l'application, après une prévisualisation
administrateur explicite.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from uuid import UUID

import pandas as pd

from .access import rag_portfolio_overview
from .data import normalize_column_name
from .storage import UserAccessProfile

_ROLE_VALUES = {
    "admin": "admin",
    "atc": "atc",
    "rag": "rag",
    "dr": "dr",
    "d_fr_app_optiroute_admin": "admin",
    "d_fr_app_optiroute_atc": "atc",
    "d_fr_app_optiroute_rag": "rag",
    "d_fr_app_optiroute_dr": "dr",
}
_ENTRA_ROLE_COLUMNS = ("app_role", "role_entra", "entra_role", "role", "groupe_entra")
_OBJECT_ID_COLUMNS = (
    "object_id",
    "objectid",
    "id_objet",
    "id_d_objet",
    "entra_object_id",
    "id",
)
_DISPLAY_NAME_COLUMNS = (
    "display_name",
    "displayname",
    "nom_affiche",
    "nom_d_affichage",
    "nom",
    "name",
)
_EMAIL_COLUMNS = (
    "email",
    "e_mail",
    "mail",
    "user_principal_name",
    "userprincipalname",
    "nom_d_utilisateur_principal",
    "upn",
    "upn_ou_e_mail",
    "upn_ou_email",
    "adresse_email",
)
_DR_UG_COLUMNS = (
    "region",
    "regions",
    "code_region",
    "code_regions",
    "ug",
    "ugs",
    "code_ug",
    "codes_ug",
    "perimetre",
)


class ProvisioningError(ValueError):
    """Le fichier de provisionnement ne permet pas une attribution sûre."""


@dataclass(frozen=True)
class EntraMember:
    role: str
    principal_id: str
    display_name: str
    email: str


@dataclass(frozen=True)
class ProvisioningIssue:
    severity: str
    message: str
    principal_id: str = ""


@dataclass(frozen=True)
class ProvisioningCandidate:
    profile: UserAccessProfile
    scope_description: str


@dataclass(frozen=True)
class ProvisioningPlan:
    candidates: tuple[ProvisioningCandidate, ...]
    issues: tuple[ProvisioningIssue, ...]
    admin_count: int = 0

    def preview_rows(self, existing_profile_ids: set[str] | None = None) -> list[dict[str, str]]:
        existing_profile_ids = {value.casefold() for value in existing_profile_ids or set()}
        rows = []
        for candidate in self.candidates:
            profile = candidate.profile
            action = (
                "Mettre à jour"
                if profile.principal_id.casefold() in existing_profile_ids
                else "Créer"
            )
            rows.append(
                {
                    "Action": action,
                    "Rôle": profile.role.upper(),
                    "Collaborateur": profile.display_name,
                    "Object ID Entra": profile.principal_id,
                    "Périmètre": candidate.scope_description,
                }
            )
        return rows


def _text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _email(value: object) -> str:
    return _text(value).casefold()


def _column_name(frame: pd.DataFrame, aliases: tuple[str, ...], label: str) -> str:
    match = _optional_column_name(frame, aliases)
    if match is not None:
        return match
    available = ", ".join(map(str, frame.columns))
    raise ProvisioningError(f"Colonne {label} introuvable. Colonnes détectées : {available}")


def _optional_column_name(frame: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    normalized = {normalize_column_name(column): str(column) for column in frame.columns}
    exact = next((normalized[alias] for alias in aliases if alias in normalized), None)
    if exact is not None:
        return exact
    for alias in aliases:
        decorated = next(
            (
                column
                for normalized_name, column in normalized.items()
                if normalized_name.startswith(f"{alias}_") or normalized_name.endswith(f"_{alias}")
            ),
            None,
        )
        if decorated is not None:
            return decorated
    return None


def _role(value: object) -> str | None:
    return _ROLE_VALUES.get(normalize_column_name(_text(value)))


def _validate_unique_entra_roles(members: tuple[EntraMember, ...]) -> tuple[EntraMember, ...]:
    unique: dict[str, EntraMember] = {}
    for member in members:
        previous = unique.get(member.principal_id)
        if previous is not None:
            if previous.role != member.role or previous.email != member.email:
                raise ProvisioningError(
                    "Un ou plusieurs comptes appartiennent à plusieurs groupes Opti Route "
                    "ou présentent des e-mails contradictoires : " + member.principal_id
                )
            # Un compte peut figurer sur plusieurs lignes Utilisateurs (plusieurs portefeuilles).
            continue
        unique[member.principal_id] = member
    return tuple(unique.values())


def merge_entra_member_exports(*exports: tuple[EntraMember, ...]) -> tuple[EntraMember, ...]:
    """Réunit les exports des groupes et rejette un rôle Entra multiple."""

    return _validate_unique_entra_roles(tuple(member for export in exports for member in export))


_NATIVE_ENTRA_COLUMNS = (
    "displayname",
    "objecttype",
    "mail",
    "usertype",
    "id",
    "deviceid",
    "userprincipalname",
    "isuser",
    "isgroup",
    "isguest",
)


def _entra_table_from_rows(headers: list[str], rows: list[list[object]]) -> pd.DataFrame:
    """Réaligne uniquement le cas identifiable d'une virgule non protégée dans le nom."""
    native = tuple(normalize_column_name(value) for value in headers[:10]) == _NATIVE_ENTRA_COLUMNS
    if native and all(
        not _text(value) or str(value).startswith("Unnamed:") for value in headers[10:]
    ):
        headers = headers[:10]
    normalized: list[list[object]] = []
    for row in rows:
        if not any(_text(value) for value in row):
            continue
        values = list(row)
        while len(values) > len(headers) and not _text(values[-1]):
            values.pop()
        if native and len(headers) == 10 and len(values) > 10:
            name_parts = len(values) - 9
            suffix = values[name_parts:]
            try:
                UUID(_text(suffix[3]))
            except (ValueError, AttributeError) as exc:
                raise ProvisioningError(
                    "Export Entra décalé : l'Object ID ne peut pas être identifié."
                ) from exc
            if (
                _text(suffix[0]).casefold() != "user"
                or _text(suffix[6]).casefold() != "true"
                or any(_text(value).casefold() not in {"true", "false"} for value in suffix[7:])
            ):
                raise ProvisioningError(
                    "Export Entra décalé : les colonnes utilisateur sont incohérentes."
                )
            values = [", ".join(_text(value) for value in values[:name_parts]), *suffix]
        if len(values) > len(headers):
            raise ProvisioningError(
                "Le nombre de colonnes d'une ligne Entra ne correspond pas aux en-têtes."
            )
        normalized.append([*values, *([None] * (len(headers) - len(values)))])
    return pd.DataFrame(normalized, columns=headers)


def _read_entra_csv(payload: str) -> pd.DataFrame:
    header_line = next((line for line in payload.splitlines() if line.strip()), "")
    delimiter = ";" if header_line.count(";") > header_line.count(",") else ","
    try:
        rows = list(
            csv.reader(io.StringIO(payload.lstrip("\ufeff")), delimiter=delimiter, strict=True)
        )
    except csv.Error as exc:
        raise ProvisioningError("Lecture de l'export Entra impossible.") from exc
    rows = [row for row in rows if any(value.strip() for value in row)]
    if not rows:
        raise ProvisioningError("L'export Entra ne contient aucun utilisateur.")
    return _entra_table_from_rows(rows[0], rows[1:])


def parse_entra_members_csv(
    payload: bytes, *, default_role: str | None = None
) -> tuple[EntraMember, ...]:
    """Lit un CSV consolidé ou le téléchargement natif des membres d'un groupe Entra.

    Le fichier peut être séparé par une virgule ou un point-virgule. Un export
    consolidé doit comporter l'Object ID et le rôle Entra. L'export natif du
    portail Entra n'a pas de colonne de rôle : ``default_role`` est alors fourni
    par l'écran qui associe explicitement le fichier à son groupe.
    """

    try:
        frame = _read_entra_csv(payload.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise ProvisioningError("Lecture de l'export Entra impossible.") from exc
    return parse_entra_members_table(frame, default_role=default_role)


def parse_entra_members_table(
    frame: pd.DataFrame, *, default_role: str | None = None
) -> tuple[EntraMember, ...]:
    """Lit les colonnes d'un export ou son CSV collé dans une seule colonne Excel."""
    frame = frame.dropna(how="all")
    if len(frame.columns) == 1 and any(
        separator in str(frame.columns[0]) for separator in (",", ";")
    ):
        payload = "\n".join([str(frame.columns[0]), *frame.iloc[:, 0].dropna().astype(str)])
        frame = _read_entra_csv(payload)
    else:
        frame = _entra_table_from_rows(list(map(str, frame.columns)), frame.values.tolist())
    if frame.empty:
        raise ProvisioningError("L'export Entra ne contient aucun utilisateur.")

    role_column = _optional_column_name(frame, _ENTRA_ROLE_COLUMNS)
    if role_column is None and _role(default_role) is None:
        raise ProvisioningError(
            "Colonne du rôle Entra introuvable. Importez le CSV consolidé ou associez "
            "l'export natif au groupe Entra correspondant."
        )
    id_column = _column_name(frame, _OBJECT_ID_COLUMNS, "de l'Object ID Entra")
    display_name_column = _optional_column_name(frame, _DISPLAY_NAME_COLUMNS)
    email_column = _optional_column_name(frame, ("email", "e_mail", "mail"))
    upn_column = _optional_column_name(
        frame, ("user_principal_name", "userprincipalname", "nom_d_utilisateur_principal", "upn")
    )
    if email_column is None and upn_column is None:
        raise ProvisioningError("Colonne e-mail ou UPN introuvable dans l'export Entra.")

    members: list[EntraMember] = []
    issues: list[str] = []
    for _, row in frame.iterrows():
        principal_id = _text(row[id_column]).casefold()
        role = _role(row[role_column]) if role_column else _role(default_role)
        if default_role is not None and role != _role(default_role):
            issues.append(
                f"le compte {principal_id} a un rôle différent de celui de sa feuille ou de son groupe"
            )
            continue
        email = _email(row[email_column]) if email_column else ""
        email = email or (_email(row[upn_column]) if upn_column else "")
        display_name = _text(row[display_name_column]) if display_name_column else email
        if not principal_id:
            issues.append("une ligne sans Object ID Entra")
            continue
        if role is None:
            issues.append(f"le compte {principal_id} a un rôle Entra non reconnu")
            continue
        if role != "admin" and not email:
            issues.append(f"le compte {principal_id} n'a ni e-mail ni UPN")
            continue
        members.append(
            EntraMember(
                role=role,
                principal_id=principal_id,
                display_name=display_name or principal_id,
                email=email,
            )
        )

    if issues:
        raise ProvisioningError("Export Entra invalide : " + "; ".join(issues[:3]))
    return _validate_unique_entra_roles(tuple(members))


def entra_export_sheets(sheet_names: list[str]) -> dict[str, str]:
    """Associe les feuilles ADMIN/ATC/DR/RAG à leur rôle métier attendu."""
    exports: dict[str, str] = {}
    for name in sheet_names:
        role = _role(name)
        if role is None:
            continue
        if role in exports:
            raise ProvisioningError(f"Plusieurs feuilles correspondent au groupe {role.upper()}.")
        exports[role] = name
    return exports


def parse_workbook_entra_exports(
    sheets: dict[str, pd.DataFrame],
) -> tuple[EntraMember, ...]:
    exports = entra_export_sheets(list(sheets))
    missing = {"admin", "atc", "dr", "rag"} - set(exports)
    if missing:
        raise ProvisioningError(
            "Feuilles d'export Entra manquantes : " + ", ".join(sorted(missing)).upper()
        )
    return merge_entra_member_exports(
        *(
            parse_entra_members_table(sheets[name], default_role=role)
            for role, name in exports.items()
        )
    )


def parse_users_entra_members(users: pd.DataFrame) -> tuple[EntraMember, ...]:
    """Utilise les Object ID déjà renseignés dans la feuille Utilisateurs."""
    id_column = _optional_column_name(users, _OBJECT_ID_COLUMNS)
    if id_column is None:
        return ()
    identified = users.loc[users[id_column].map(_text).ne("")]
    if identified.empty:
        return ()
    role_column = _column_name(identified, _ENTRA_ROLE_COLUMNS, "du rôle Entra")
    email_column = _column_name(identified, _EMAIL_COLUMNS, "de l'e-mail utilisateur")
    name_column = _optional_column_name(identified, _DISPLAY_NAME_COLUMNS)
    frame = pd.DataFrame(
        {
            "object_id": identified[id_column],
            "app_role": identified[role_column],
            "email": identified[email_column],
            "display_name": identified[name_column] if name_column else identified[email_column],
        }
    )
    return parse_entra_members_table(frame)


def users_entra_id_count(users: pd.DataFrame) -> int:
    column = _optional_column_name(users, _OBJECT_ID_COLUMNS)
    return int(users[column].map(_text).ne("").sum()) if column else 0


def reconcile_atc_portfolios(
    clients: pd.DataFrame, users: pd.DataFrame, members: tuple[EntraMember, ...]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rattache les alias de portefeuille aux identités explicites de Utilisateurs/Entra.

    Le nom doit correspondre exactement (hors casse/espaces), avec le code ATC
    renseigné ou encore absent du portefeuille. Aucun rapprochement approximatif.
    """
    result = clients.copy()
    columns = [
        "Commercial",
        "Code ATC",
        "Ancien e-mail portefeuille",
        "E-mail de connexion",
        "Clients",
    ]
    report: list[dict[str, object]] = []
    id_column = _optional_column_name(users, _OBJECT_ID_COLUMNS)
    code_column = _optional_column_name(users, ("code_atc", "atc_code", "code_commercial"))
    name_column = _optional_column_name(users, _DISPLAY_NAME_COLUMNS)
    if id_column is None or code_column is None or name_column is None:
        return result, pd.DataFrame(columns=columns)
    role_column = _column_name(users, _ENTRA_ROLE_COLUMNS, "du rôle Entra")
    email_column = _column_name(users, _EMAIL_COLUMNS, "de l'e-mail utilisateur")
    by_id = {member.principal_id: member for member in members}
    names = clients["salesperson"].map(_text).str.replace(r"\s+", " ", regex=True).str.casefold()
    codes = clients["salesperson_code"].map(_text).str.casefold()
    assignments: dict[object, tuple[str, str, str]] = {}
    for _, row in users.iterrows():
        member = by_id.get(_text(row[id_column]).casefold())
        if member is None or member.role != "atc" or _role(row[role_column]) != "atc":
            continue
        if _email(row[email_column]) != member.email:
            raise ProvisioningError(
                f"L'e-mail de {member.display_name} diffère entre Utilisateurs et l'export Entra."
            )
        code = _text(row[code_column])
        name = " ".join(_text(row[name_column]).split()).casefold()
        if not code or not name:
            continue
        # Le nom sert actuellement de code de repli lorsqu'une cellule `code` est vide.
        mask = names.eq(name) & (codes.eq(code.casefold()) | codes.eq(names) | codes.eq(""))
        if not mask.any():
            continue
        identity = (member.principal_id, member.email, code.casefold())
        for index in clients.index[mask]:
            if index in assignments and assignments[index] != identity:
                raise ProvisioningError(
                    f"Plusieurs identités Entra revendiquent le portefeuille {_text(row[name_column])}."
                )
            assignments[index] = identity
        changed = mask & (
            result["salesperson_email"].map(_email).ne(member.email)
            | result["salesperson_code"].map(_text).str.casefold().ne(code.casefold())
        )
        if not changed.any():
            continue
        old_emails = sorted(set(clients.loc[mask, "salesperson_email"].map(_email)) - {""})
        result.loc[mask, "salesperson_email"] = member.email
        result.loc[mask, "salesperson_code"] = code
        parts = re.fullmatch(r"(\d+)-(\d+)-\d+", code)
        if parts:
            for column, value in (("agency", f"{parts[1]}-{parts[2]}"), ("ug", parts[1])):
                if column not in result.columns:
                    result[column] = pd.NA
                missing = result[column].map(_text).eq("")
                result.loc[mask & missing, column] = value
        report.append(
            {
                "Commercial": _text(row[name_column]),
                "Code ATC": code,
                "Ancien e-mail portefeuille": ", ".join(old_emails),
                "E-mail de connexion": member.email,
                "Clients": int(mask.sum()),
            }
        )
    return result, pd.DataFrame(report, columns=columns)


def _split_ugs(value: object) -> tuple[str, ...]:
    raw = _text(value)
    if not raw:
        return ()
    values = (part.strip() for part in re.split(r"[-,;|/]", raw))
    return tuple(dict.fromkeys(value for value in values if value))


def parse_dr_regions(users_sheet: pd.DataFrame) -> dict[str, tuple[str, ...]]:
    """Extrait l'association e-mail DR -> codes UG de la feuille Utilisateurs."""

    if users_sheet.empty:
        raise ProvisioningError("La feuille Utilisateurs est vide.")
    role_column = _column_name(users_sheet, _ENTRA_ROLE_COLUMNS, "du rôle Entra")
    email_column = _column_name(users_sheet, _EMAIL_COLUMNS, "de l'e-mail utilisateur")
    ug_column = _column_name(users_sheet, _DR_UG_COLUMNS, "Région / UG")

    regions: dict[str, tuple[str, ...]] = {}
    for _, row in users_sheet.iterrows():
        if _role(row[role_column]) != "dr":
            continue
        email = _email(row[email_column])
        ugs = _split_ugs(row[ug_column])
        if not email or not ugs:
            continue
        if email in regions and regions[email] != ugs:
            raise ProvisioningError(
                f"Plusieurs périmètres DR différents sont renseignés pour {email}."
            )
        regions[email] = ugs
    return regions


def users_sheet_name(sheet_names: list[str]) -> str | None:
    """Retourne la feuille compagnon Utilisateurs lorsqu'elle existe."""

    return next(
        (
            sheet_name
            for sheet_name in sheet_names
            if normalize_column_name(sheet_name) in {"utilisateurs", "utilisateur", "users"}
        ),
        None,
    )


def build_provisioning_plan(
    clients: pd.DataFrame,
    members: tuple[EntraMember, ...],
    dr_regions: dict[str, tuple[str, ...]],
) -> ProvisioningPlan:
    """Construit les profils à créer sans effectuer d'écriture persistante."""

    issues: list[ProvisioningIssue] = []
    candidates: list[ProvisioningCandidate] = []
    admin_count = 0

    atc_emails = clients["salesperson_email"].fillna("").astype(str).str.strip().str.casefold()
    rag_emails = clients["rag_email"].fillna("").astype(str).str.strip().str.casefold()
    known_ugs = {value.strip() for value in clients["ug"].fillna("").astype(str) if value.strip()}

    for member in members:
        if member.role == "admin":
            admin_count += 1
            continue
        if member.role == "atc":
            matched = clients.loc[atc_emails.eq(member.email)]
            if matched.empty:
                issues.append(
                    ProvisioningIssue(
                        "warning",
                        "E-mail ATC absent du portefeuille : aucune habilitation créée "
                        f"pour {member.display_name} ({member.email}).",
                        member.principal_id,
                    )
                )
                continue
            codes = tuple(
                dict.fromkeys(
                    value.strip()
                    for value in matched["salesperson_code"].fillna("").astype(str)
                    if value.strip()
                )
            )
            names = tuple(
                dict.fromkeys(
                    value.strip()
                    for value in matched["salesperson"].fillna("").astype(str)
                    if value.strip() and value.strip() != "Tous"
                )
            )
            profile = UserAccessProfile(
                principal_id=member.principal_id,
                display_name=member.display_name,
                role="atc",
                atc_code=codes[0] if len(codes) == 1 else None,
                atc_email=member.email,
                atc_name=names[0] if len(names) == 1 else None,
            ).validated()
            candidates.append(
                ProvisioningCandidate(
                    profile,
                    f"ATC · {member.email} · {len(codes)} portefeuille(s) · {len(matched)} entreprise(s)",
                )
            )
            continue

        if member.role == "rag":
            if not (rag_emails.eq(member.email) | atc_emails.eq(member.email)).any():
                issues.append(
                    ProvisioningIssue(
                        "warning",
                        "E-mail RAG absent du portefeuille : aucune habilitation créée.",
                        member.principal_id,
                    )
                )
                continue
            profile = UserAccessProfile(
                principal_id=member.principal_id,
                display_name=member.display_name,
                role="rag",
                rag_email=member.email,
            ).validated()
            overview = rag_portfolio_overview(clients, member.email)
            candidates.append(
                ProvisioningCandidate(
                    profile,
                    f"RAG · {member.email} · {len(overview)} portefeuille(s) · "
                    f"{int(overview['Entreprises'].sum())} entreprise(s)",
                )
            )
            continue

        configured_ugs = dr_regions.get(member.email, ())
        valid_ugs = tuple(value for value in configured_ugs if value in known_ugs)
        missing_ugs = tuple(value for value in configured_ugs if value not in known_ugs)
        if missing_ugs:
            issues.append(
                ProvisioningIssue(
                    "warning",
                    "UG absente du portefeuille et ignorée : " + ", ".join(missing_ugs),
                    member.principal_id,
                )
            )
        if not valid_ugs:
            issues.append(
                ProvisioningIssue(
                    "warning",
                    "Périmètre DR absent de la feuille Utilisateurs : aucune habilitation créée.",
                    member.principal_id,
                )
            )
            continue
        profile = UserAccessProfile(
            principal_id=member.principal_id,
            display_name=member.display_name,
            role="dr",
            ugs=valid_ugs,
        ).validated()
        candidates.append(ProvisioningCandidate(profile, "DR · UG " + ", ".join(valid_ugs)))

    return ProvisioningPlan(tuple(candidates), tuple(issues), admin_count)
