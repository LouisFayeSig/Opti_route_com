from __future__ import annotations

import pandas as pd
import pytest

from opti_route.provisioning import (
    EntraMember,
    ProvisioningError,
    build_provisioning_plan,
    entra_export_sheets,
    merge_entra_member_exports,
    parse_dr_regions,
    parse_entra_members_csv,
    parse_entra_members_table,
    parse_users_entra_members,
    parse_workbook_entra_exports,
    reconcile_atc_portfolios,
    users_entra_id_count,
    users_sheet_name,
)


def _clients() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "salesperson_email": ["atc@example.test", "other@example.test"],
            "salesperson_code": ["20-01-47", "20-01-48"],
            "salesperson": ["Victorien Prouteau", "Other ATC"],
            "rag_email": ["rag@example.test", "rag@example.test"],
            "ug": ["20", "24"],
        }
    )


def test_automatic_provisioning_matches_atc_rag_and_dr() -> None:
    members = parse_entra_members_csv(
        b"app_role,object_id,display_name,user_principal_name,email\n"
        b"admin,oid-admin,Admin,admin@example.test,\n"
        b"atc,oid-atc,ATC,atc@example.test,\n"
        b"rag,oid-rag,RAG,rag@example.test,\n"
        b"dr,oid-dr,Cedric,c.cheve@example.test,\n"
    )
    users = pd.DataFrame(
        {
            "Role Entra": ["D-FR-APP-OPTIROUTE-DR"],
            "UPN ou e-mail": ["c.cheve@example.test"],
            "Code Region": ["20-24-31-34"],
        }
    )

    plan = build_provisioning_plan(_clients(), members, parse_dr_regions(users))
    profiles = {candidate.profile.principal_id: candidate.profile for candidate in plan.candidates}

    assert plan.admin_count == 1
    assert profiles["oid-atc"].atc_email == "atc@example.test"
    assert profiles["oid-atc"].atc_code == "20-01-47"
    assert profiles["oid-rag"].rag_email == "rag@example.test"
    assert profiles["oid-dr"].ugs == ("20", "24")
    assert len(plan.issues) == 1
    assert "31, 34" in plan.issues[0].message


def test_unmatched_atc_is_not_provisioned() -> None:
    members = parse_entra_members_csv(
        b"app_role,object_id,display_name,email\natc,oid-atc,ATC,missing@example.test\n"
    )

    plan = build_provisioning_plan(_clients(), members, {})

    assert not plan.candidates
    assert plan.issues[0].principal_id == "oid-atc"


def test_duplicate_entra_roles_are_refused() -> None:
    payload = (
        b"app_role,object_id,display_name,email\n"
        b"atc,oid-same,First,first@example.test\n"
        b"rag,oid-same,First,first@example.test\n"
    )

    with pytest.raises(ProvisioningError, match="plusieurs groupes"):
        parse_entra_members_csv(payload)


def test_native_entra_group_export_uses_the_selected_group_role() -> None:
    members = parse_entra_members_csv(
        b"Object ID [objectId],User principal name [userPrincipalName],Display name [displayName],Member type\n"
        b"oid-atc,atc@example.test,ATC,User\n",
        default_role="D-FR-APP-OPTIROUTE-ATC",
    )

    assert members == (
        EntraMember(
            role="atc",
            principal_id="oid-atc",
            display_name="ATC",
            email="atc@example.test",
        ),
    )


def test_native_entra_portal_technical_headers_are_supported() -> None:
    members = parse_entra_members_csv(
        b"displayName,objectType,mail,userType,id,deviceId,userPrincipalName,isUser,isGroup,isGuest\n"
        b"ATC,User,atc@example.test,Member,oid-atc,,atc@example.test,true,false,false\n",
        default_role="atc",
    )

    assert members[0].principal_id == "oid-atc"
    assert members[0].display_name == "ATC"
    assert members[0].email == "atc@example.test"


def test_native_group_exports_reject_a_member_with_two_roles() -> None:
    atc = parse_entra_members_csv(
        b"Object ID,User principal name,Display name,Member type\n"
        b"oid-same,same@example.test,Same,User\n",
        default_role="atc",
    )
    rag = parse_entra_members_csv(
        b"Object ID,User principal name,Display name,Member type\n"
        b"oid-same,same@example.test,Same,User\n",
        default_role="rag",
    )

    with pytest.raises(ProvisioningError, match="plusieurs groupes"):
        merge_entra_member_exports(atc, rag)


def test_users_sheet_is_detected() -> None:
    assert users_sheet_name(["Listing client", "Utilisateurs", "AA"]) == "Utilisateurs"


def _native_columns() -> list[str]:
    return [
        "displayName",
        "objectType",
        "mail",
        "userType",
        "id",
        "deviceId",
        "userPrincipalName",
        "isUser",
        "isGroup",
        "isGuest",
    ]


def _embedded_sheets() -> dict[str, pd.DataFrame]:
    result = {}
    for role in ("admin", "atc", "dr", "rag"):
        email = f"{role}@example.test"
        values = [
            role.upper(),
            "user",
            email,
            "Member",
            f"oid-{role}",
            "",
            email,
            "True",
            "False",
            "False",
        ]
        if role in {"dr", "rag"}:
            result[role.upper()] = pd.DataFrame({",".join(_native_columns()): [",".join(values)]})
        else:
            result[role.upper()] = pd.DataFrame([values], columns=_native_columns())
    return result


def test_workbook_exports_accept_both_excel_columns_and_single_column_csv() -> None:
    members = parse_workbook_entra_exports(_embedded_sheets())
    assert len(members) == 4
    assert {member.role for member in members} == {"admin", "atc", "dr", "rag"}
    assert next(member for member in members if member.role == "rag").email == "rag@example.test"


def test_incomplete_or_ambiguous_workbook_exports_are_rejected() -> None:
    sheets = _embedded_sheets()
    del sheets["DR"]
    with pytest.raises(ProvisioningError, match="manquantes"):
        parse_workbook_entra_exports(sheets)
    with pytest.raises(ProvisioningError, match="Plusieurs feuilles"):
        entra_export_sheets(["ATC", "D-FR-APP-OPTIROUTE-ATC"])


@pytest.mark.parametrize("flat", [True, False])
def test_unquoted_comma_in_a_native_display_name_does_not_shift_the_object_id(flat) -> None:
    object_id = "b6a05ea4-1f5d-45ec-af76-38293a39709a"
    values = [
        "Faye",
        " Louis",
        "user",
        "l.faye@example.test",
        "Member",
        object_id,
        "",
        "l.faye@example.test",
        "True",
        "False",
        "False",
    ]
    if flat:
        frame = pd.DataFrame({",".join(_native_columns()): [",".join(values)]})
    else:
        frame = pd.DataFrame([values], columns=[*_native_columns(), "Unnamed: 10"])
    members = parse_entra_members_table(frame, default_role="admin")
    assert members[0].principal_id == object_id
    assert members[0].display_name == "Faye, Louis"
    assert members[0].email == "l.faye@example.test"


def test_csv_native_name_with_quotes_keeps_the_full_name() -> None:
    payload = (
        ",".join(_native_columns())
        + '\n"Doe, Jane",user,jane@example.test,Member,oid-jane,,jane@example.test,True,False,False\n'
    )
    member = parse_entra_members_csv(payload.encode(), default_role="rag")[0]
    assert member.display_name == "Doe, Jane"
    assert member.principal_id == "oid-jane"


def test_shifted_columns_with_an_unidentifiable_object_id_are_rejected() -> None:
    values = [
        "Doe",
        "Jane",
        "user",
        "jane@example.test",
        "Member",
        "not-an-object-id",
        "",
        "jane@example.test",
        "True",
        "False",
        "False",
    ]
    frame = pd.DataFrame([values], columns=[*_native_columns(), "Unnamed: 10"])
    with pytest.raises(ProvisioningError, match="décalé"):
        parse_entra_members_table(frame, default_role="atc")


def test_embedded_role_cannot_conflict_with_its_sheet_role() -> None:
    frame = pd.DataFrame({"object_id": ["oid"], "app_role": ["admin"], "email": ["x@example.test"]})
    with pytest.raises(ProvisioningError, match="rôle différent"):
        parse_entra_members_table(frame, default_role="atc")


def test_users_object_ids_are_used_and_repeated_account_rows_are_deduplicated() -> None:
    users = pd.DataFrame(
        {
            "Role Entra": ["D-FR-APP-OPTIROUTE-ATC"] * 3,
            "UPN ou e-mail": ["atc@example.test", "ATC@example.test", "other@example.test"],
            "Nom affiche": ["ATC Rennes", "ATC Saint-Brieuc", "Sans identifiant"],
            "Object ID Entra a restituer": ["oid-atc", "oid-atc", None],
        }
    )
    assert users_entra_id_count(users) == 2
    members = parse_users_entra_members(users)
    assert len(members) == 1
    plan = build_provisioning_plan(_clients(), members, {})
    assert plan.candidates[0].profile.principal_id == "oid-atc"
    assert plan.candidates[0].profile.atc_email == "atc@example.test"


def test_users_conflicting_identity_is_not_silently_deduplicated() -> None:
    users = pd.DataFrame(
        {
            "Role Entra": ["atc", "atc"],
            "Object ID": ["oid", "oid"],
            "UPN ou e-mail": ["a@example.test", "b@example.test"],
        }
    )
    with pytest.raises(ProvisioningError, match="contradictoires"):
        parse_users_entra_members(users)
    assert parse_users_entra_members(pd.DataFrame({"Object ID": [None]})) == ()


def test_rag_own_portfolio_is_included_in_provisioning() -> None:
    members = (EntraMember("rag", "oid-rag-own", "RAG", "atc@example.test"),)
    plan = build_provisioning_plan(_clients(), members, {})
    assert plan.candidates[0].profile.rag_email == "atc@example.test"
    assert "1 portefeuille" in plan.candidates[0].scope_description


def test_user_identity_can_link_two_atc_portfolios_with_different_email_aliases() -> None:
    clients = pd.DataFrame(
        {
            "salesperson": ["Alex Rennes", "Alex Saint-Brieuc", "Other ATC"],
            "salesperson_code": ["Alex Rennes", "Alex Saint-Brieuc", "40-01-01"],
            "salesperson_email": [
                "rennes@example.test",
                "stbrieuc@example.test",
                "other@example.test",
            ],
            "rag_email": ["rag@example.test"] * 3,
            "ug": ["22", "22", "40"],
        }
    )
    users = pd.DataFrame(
        {
            "Object ID": ["oid-alex", "oid-alex"],
            "Role Entra": ["atc", "atc"],
            "Nom affiche": ["Alex Rennes", "Alex Saint-Brieuc"],
            "UPN ou e-mail": ["alex@example.test"] * 2,
            "Code ATC": ["22-04-35", "22-02-22"],
        }
    )
    members = (EntraMember("atc", "oid-alex", "Alex", "alex@example.test"),)
    reconciled, report = reconcile_atc_portfolios(clients, users, members)
    assert len(report) == 2
    assert reconciled["salesperson_code"].tolist() == ["22-04-35", "22-02-22", "40-01-01"]
    assert reconciled["salesperson_email"].tolist() == [
        "alex@example.test",
        "alex@example.test",
        "other@example.test",
    ]
    assert clients.iloc[0]["salesperson_email"] == "rennes@example.test"
    plan = build_provisioning_plan(reconciled, members, {})
    assert not plan.issues
    assert len(plan.candidates) == 1
    assert plan.candidates[0].profile.atc_email == "alex@example.test"
    assert "2 portefeuille(s)" in plan.candidates[0].scope_description


def test_identity_reconciliation_requires_an_entra_match_and_an_unambiguous_portfolio() -> None:
    clients = _clients()
    users = pd.DataFrame(
        {
            "Object ID": ["oid-alex"],
            "Role Entra": ["atc"],
            "Nom affiche": ["Victorien Prouteau"],
            "UPN ou e-mail": ["new@example.test"],
            "Code ATC": ["different-code"],
        }
    )
    members = (EntraMember("atc", "oid-alex", "Alex", "new@example.test"),)
    reconciled, report = reconcile_atc_portfolios(clients, users, members)
    assert report.empty  # Un code existant différent ne peut pas être réattribué par le seul nom.
    assert reconciled["salesperson_email"].tolist() == clients["salesperson_email"].tolist()
    reconciled, report = reconcile_atc_portfolios(clients, users, ())
    assert report.empty
    users.loc[0, "UPN ou e-mail"] = "wrong@example.test"
    with pytest.raises(ProvisioningError, match="diffère"):
        reconcile_atc_portfolios(clients, users, members)


def test_two_identities_cannot_reclaim_the_same_portfolio() -> None:
    clients = _clients()
    users = pd.DataFrame(
        {
            "Object ID": ["oid-a", "oid-b"],
            "Role Entra": ["atc", "atc"],
            "Nom affiche": ["Victorien Prouteau"] * 2,
            "Code ATC": ["20-01-47"] * 2,
            "UPN ou e-mail": ["a@example.test", "b@example.test"],
        }
    )
    members = (
        EntraMember("atc", "oid-a", "A", "a@example.test"),
        EntraMember("atc", "oid-b", "B", "b@example.test"),
    )
    with pytest.raises(ProvisioningError, match="Plusieurs identités"):
        reconcile_atc_portfolios(clients, users, members)
