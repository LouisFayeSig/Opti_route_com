from __future__ import annotations

import pandas as pd
import pytest

from opti_route.provisioning import (
    ProvisioningError,
    build_provisioning_plan,
    parse_dr_regions,
    parse_entra_members_csv,
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
        b"app_role,object_id,display_name,email\n"
        b"atc,oid-atc,ATC,missing@example.test\n"
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


def test_users_sheet_is_detected() -> None:
    assert users_sheet_name(["Listing client", "Utilisateurs", "AA"]) == "Utilisateurs"
