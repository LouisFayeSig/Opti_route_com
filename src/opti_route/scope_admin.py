"""Administration des périmètres ; les rôles sont gérés uniquement par les demandes AD."""

import pandas as pd
import streamlit as st

from .portfolio_operations import portfolios, profile_value
from .scope_catalog import business_scope, scope_clients
from .storage import StorageError


def render_habilitations(service, user):
    clients, metadata = service.store.load_clients()
    profiles = service.store.list_access_profiles()
    st.caption(
        "Les rôles se gèrent dans Demandes de groupe AD. Ce volet modifie les codes ATC, les rattachements RAG et les UG des DR, avec écriture dans le listing client."
    )
    rows = []
    for profile in profiles:
        matched = scope_clients(
            clients, profile.role.upper(), business_scope(profile.role.upper(), profile)
        )
        managers = matched.reindex(columns=["rag_name", "rag_email"]).fillna("").drop_duplicates()
        rows.append(
            {
                "Collaborateur": profile.display_name,
                "Object ID Entra": profile.principal_id,
                "Rôle attendu": profile.role.upper(),
                "Code ATC": profile.atc_code or "",
                "E-mail ATC": profile.atc_email or "",
                "Commercial ATC": profile.atc_name or "",
                "Rattachement RAG": " / ".join(
                    f"{r.rag_name} ({r.rag_email})"
                    for r in managers.itertuples()
                    if r.rag_name or r.rag_email
                )
                if profile.role == "atc"
                else "",
                "E-mail RAG": profile.rag_email or "",
                "Entreprises": len(matched),
                "UG DR": ", ".join(profile.ugs),
                "Accès actif": not service.blocked(profile.principal_id),
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    from .access_admin import render_scope_catalog

    render_scope_catalog(service, user)
    editable = {p.principal_id: p for p in profiles if not service.blocked(p.principal_id)}
    if not editable or clients is None:
        st.info("Les comptes et les rôles sont à ajouter depuis Demandes de groupe AD.")
        return
    selected = st.selectbox(
        "Compte dont le périmètre doit être modifié",
        list(editable),
        format_func=lambda v: f"{editable[v].display_name} · {editable[v].role.upper()}",
        key="access_profile_selection",
    )
    profile = editable[selected]
    baseline_key = "scope_editor_baseline"
    if st.button("Actualiser le formulaire", key="scope_editor_refresh"):
        st.session_state.pop(baseline_key, None)
        for key in list(st.session_state):
            if key.startswith("scope_edit_"):
                del st.session_state[key]
        st.rerun()
    baseline = st.session_state.get(baseline_key)
    if not baseline or baseline["id"] != selected:
        baseline = {"id": selected, "digest": metadata.digest, "profile": profile_value(profile)}
        st.session_state[baseline_key] = baseline
    prefix = f"scope_edit_{selected}"
    st.caption(f"Rôle : {profile.role.upper()} · Object ID : {selected}")
    arguments = {}
    if profile.role == "atc":
        arguments["atc_code"] = st.text_input(
            "Code ATC", value=profile.atc_code or "", key=prefix + "_code"
        )
        matched = scope_clients(clients, "ATC", business_scope("ATC", profile))
        emails = sorted(set(matched["rag_email"].dropna().str.casefold()) - {""})
        managers = {
            p.rag_email: p.display_name
            for p in profiles
            if p.role == "rag" and p.rag_email and not service.blocked(p.principal_id)
        }
        for row in (
            clients[["rag_email", "rag_name"]].fillna("").drop_duplicates().itertuples(index=False)
        ):
            if row.rag_email:
                managers.setdefault(row.rag_email.casefold(), row.rag_name)
        # Une sélection explicite est nécessaire si le portefeuille a plusieurs RAG.
        options = [None, "", *sorted(managers)] if len(emails) > 1 else ["", *sorted(managers)]
        default = emails[0] if len(emails) == 1 else None if len(emails) > 1 else ""
        arguments["rag_email"] = st.selectbox(
            "Rattachement RAG",
            options,
            index=options.index(default),
            format_func=lambda v: (
                "Conserver les rattachements actuels"
                if v is None
                else f"{managers[v]} ({v})"
                if v
                else "Aucun RAG"
            ),
            key=prefix + "_rag",
        )
    elif profile.role == "rag":
        choices = portfolios(clients)
        assigned = [
            k
            for k, v in choices.items()
            if v["clients"]["rag_email"].fillna("").str.casefold().eq(profile.rag_email).any()
        ]
        arguments["rag_portfolios"] = st.multiselect(
            "Portefeuilles ATC rattachés au RAG",
            list(choices),
            default=assigned,
            format_func=lambda v: choices[v]["label"],
            key=prefix + "_portfolios",
        )
        st.caption(
            "Ajouter un portefeuille le rattache à ce RAG ; le retirer efface son rattachement à ce RAG. Son éventuel portefeuille personnel reste accessible."
        )
    else:
        options = sorted(set(clients["ug"].dropna().astype(str)) | set(profile.ugs))
        arguments["ugs"] = st.multiselect(
            "UG de contrôle du DR", options, default=list(profile.ugs), key=prefix + "_ugs"
        )
    if st.button("Enregistrer le périmètre", key="save_access_profile", type="primary"):
        try:
            operation = service.operations.update(
                user,
                selected,
                expected_digest=baseline["digest"],
                expected_profile=baseline["profile"],
                **arguments,
            )
        except (StorageError, ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.session_state.pop(baseline_key, None)
            st.session_state["scope_update_notice"] = (
                f"Périmètre enregistré dans le listing client ({(operation['plan'] or {}).get('client_count', 0)} clients)."
            )
            st.cache_data.clear()
            st.rerun()
    if message := st.session_state.pop("scope_update_notice", None):
        st.success(message)
