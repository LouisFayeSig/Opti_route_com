"""Interface ADMIN des demandes ; aucun appel AD pour les autres utilisateurs."""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from .contact import account_request_mailto
from .flow_client import AdUnavailable, RequeteFluxInvalide
from .storage import StorageError

_ACTIONS = {"Ajouter": "AJOUTER", "Modifier": "MODIFIER", "Supprimer": "SUPPRIMER"}


def _check_created(service, user, request):
    if request["action"] != "MODIFIER_CODE" and not service.blocked(user.principal_id):
        try:
            with st.spinner("Vérification AD…"):
                service.check(user, request["id"])
        except (AdUnavailable, RequeteFluxInvalide) as exc:
            st.session_state["access_request_notice"] = str(exc)
    st.session_state["access_request_last"] = request["id"]


def _summary(request):
    control = request.get("dernierControle") or {}
    return {
        "Personne": " ".join((request["nom"], request["prenom"])).strip(),
        "Action": request["action"], "Groupe": request["groupe"],
        "Ancien groupe": request.get("ancienGroupe") or "", "Périmètre": request.get("code", ""),
        "Statut": request["statut"], "Créée le": request["created_at"],
        "Dernier contrôle": control.get("date", ""), "Résultat": control.get("motif", ""),
    }


def render_access_requests(service, user, recipient):
    if not user.is_admin:
        raise PermissionError("Interface réservée aux administrateurs.")
    try:
        results = service.check_pending(user, automatic=True)
        errors = [error for _, _, error in results if error]
        if errors:
            st.warning(errors[-1])
        profiles = {profile["principal_id"]: profile for profile in service.profiles()}
    except (StorageError, ValueError, PermissionError) as exc:
        st.error(str(exc))
        return
    st.caption(f"Helpdesk : {recipient} · L'application prépare un brouillon, envoyé par l'administrateur.")
    if service.flow is None:
        st.info("La vérification AD est indisponible. Les demandes restent enregistrées en attente.")
    st.markdown("##### Nouvelle demande")
    action = _ACTIONS[st.selectbox("Action", list(_ACTIONS), key="request_action")]
    profile = None
    principal_id = ""
    if action != "AJOUTER":
        selected = st.selectbox(
            "Compte concerné", [None, *profiles],
            format_func=lambda value: "— Saisir un Object ID —" if value is None else f"{profiles[value].get('display_name', value)} · {profiles[value]['role'].upper()}",
            key="request_profile",
        )
        profile = profiles.get(selected)
        if profile:
            principal_id = selected
            st.caption(f"Object ID : {principal_id}")
        else:
            principal_id = st.text_input("Object ID Entra", key="request_object_id").strip()
    nom = st.text_input("Nom", value=(profile or {}).get("display_name", ""), key=f"request_nom_{principal_id or 'new'}")
    prenom = st.text_input("Prénom", key=f"request_prenom_{principal_id or 'new'}")
    old_group = profile["role"].upper() if profile else None
    if action == "MODIFIER" and old_group is None:
        old_group = st.selectbox("Groupe actuel", ["ATC", "RAG", "DR", "ADMIN"], key="request_old_group")
    groups = ["ATC", "RAG", "DR", "ADMIN"]
    group = st.selectbox(
        "Groupe concerné" if action == "SUPPRIMER" else "Groupe visé", groups,
        index=groups.index(old_group) if old_group else 0,
        disabled=action == "SUPPRIMER" and profile is not None,
        key=f"request_group_{action}_{principal_id}",
    )
    scope = {}
    prefix = f"request_scope_{principal_id}_{group}"
    if action != "SUPPRIMER":
        if group == "ATC":
            scope["atc_code"] = st.text_input("Code ATC", value=(profile or {}).get("atc_code") or "", key=prefix + "_code")
            scope["atc_email"] = st.text_input("E-mail du commercial (facultatif)", value=(profile or {}).get("atc_email") or "", key=prefix + "_email")
            scope["atc_name"] = st.text_input("Nom du commercial dans le portefeuille (facultatif)", value=(profile or {}).get("atc_name") or "", key=prefix + "_name")
        elif group == "RAG":
            scope["rag_email"] = st.text_input("E-mail N+1 / RAG", value=(profile or {}).get("rag_email") or "", key=prefix + "_email")
        elif group == "DR":
            scope["ugs"] = st.text_input("Codes UG / région", value="-".join((profile or {}).get("ugs") or ()), placeholder="40-45-71-37", key=prefix + "_ugs")
        if action == "MODIFIER" and group == old_group:
            st.info("Le groupe reste identique : seul le périmètre sera modifié directement, sans mail ni appel AD.")
    if st.button("Enregistrer la demande", key="create_access_request", type="primary"):
        try:
            request = service.create(user, action=action, group=group, nom=nom, prenom=prenom,
                                     principal_id=principal_id or None, old_group=old_group, scope=scope)
            _check_created(service, user, request)
        except (StorageError, ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.rerun()

    notice = st.session_state.pop("access_request_notice", None)
    if notice:
        st.warning(notice)
    try:
        pending, recent = service.pending(), service.recent()
    except StorageError as exc:
        st.error(str(exc))
        return
    by_id = {request["id"]: request for request in [*pending, *recent]}
    last = by_id.get(st.session_state.get("access_request_last"))
    if last:
        st.success(f"Demande enregistrée : {last['statut']}.")
        if last["action"] != "MODIFIER_CODE" and last["statut"] != "ANNULEE":
            name = " ".join((last["nom"], last["prenom"])).strip() or last.get("objectId", "")
            st.link_button("Ouvrir le brouillon Helpdesk", account_request_mailto(name, last["action"].lower(), last["groupe"], recipient=recipient))
    st.markdown("##### Demandes en cours")
    if pending:
        st.dataframe(pd.DataFrame([_summary(request) for request in pending]), hide_index=True, use_container_width=True)
        old = [request for request in pending if service.clock() - datetime.fromisoformat(request["created_at"]) > timedelta(days=7)]
        if old:
            st.warning(f"{len(old)} demande(s) attendent depuis plus de sept jours.")
        if st.button("Vérifier toutes les demandes", key="check_all_access_requests"):
            try:
                with st.spinner("Vérification des demandes…"):
                    result = service.check_pending(user)
                errors = [error for _, _, error in result if error]
                if errors:
                    st.session_state["access_request_notice"] = errors[-1]
            except (StorageError, ValueError, PermissionError) as exc:
                st.session_state["access_request_notice"] = str(exc)
            st.rerun()
        selected_id = st.selectbox("Demande à examiner", [request["id"] for request in pending],
                                   format_func=lambda value: f"{by_id[value]['nom']} {by_id[value]['prenom']} · {by_id[value]['action']}",
                                   key="access_request_selection")
        request = by_id[selected_id]
        if not last or last["id"] != selected_id:
            name = " ".join((request["nom"], request["prenom"])).strip() or request.get("objectId", "")
            st.link_button("Ouvrir le brouillon Helpdesk", account_request_mailto(name, request["action"].lower(), request["groupe"], recipient=recipient))
        st.caption(f"Object ID : {request.get('objectId') or 'à identifier'} · Demande : {selected_id}")
        if request.get("motif"):
            st.info(request["motif"])
        verify, cancel = st.columns(2)
        if verify.button("Vérifier maintenant", key="check_access_request", disabled=request["statut"] == "A_PRECISER"):
            try:
                with st.spinner("Vérification AD…"):
                    service.check(user, selected_id)
            except (StorageError, ValueError, PermissionError, AdUnavailable) as exc:
                st.session_state["access_request_notice"] = str(exc)
            st.rerun()
        if cancel.button("Annuler la demande", key="cancel_access_request"):
            try:
                service.cancel(user, selected_id)
            except (StorageError, ValueError, PermissionError) as exc:
                st.session_state["access_request_notice"] = str(exc)
            st.rerun()
        if request["statut"] == "A_PRECISER" and request["action"] == "AJOUTER":
            candidates = request["candidats"]
            if candidates:
                candidate = st.selectbox("Compte identifié dans l'annuaire", [None, *range(len(candidates))],
                                         format_func=lambda index: "— Choisir un compte —" if index is None else f"{candidates[index].get('displayName', '')} · {candidates[index].get('upn') or candidates[index].get('mail', '')}",
                                         key=f"candidate_{selected_id}")
                if st.button("Choisir ce compte", disabled=candidate is None, key="choose_access_candidate"):
                    try:
                        service.clarify(user, selected_id, candidate=candidates[candidate])
                    except (StorageError, ValueError, PermissionError, AdUnavailable) as exc:
                        st.session_state["access_request_notice"] = str(exc)
                    st.rerun()
            if not request.get("objectId"):
                corrected_nom = st.text_input("Nom corrigé", value=request["nom"], key=f"correct_nom_{selected_id}")
                corrected_prenom = st.text_input("Prénom corrigé", value=request["prenom"], key=f"correct_prenom_{selected_id}")
                if st.button("Corriger le nom", key="correct_access_name"):
                    try:
                        service.clarify(user, selected_id, nom=corrected_nom, prenom=corrected_prenom)
                    except (StorageError, ValueError, PermissionError, AdUnavailable) as exc:
                        st.session_state["access_request_notice"] = str(exc)
                    st.rerun()
    else:
        st.caption("Aucune demande en cours.")
    with st.expander("Historique des trois derniers mois"):
        if recent:
            st.dataframe(pd.DataFrame([{**_summary(request), "Preuve": request["resolved_by"], "Clôturée le": request["resolved_at"]} for request in recent]), hide_index=True, use_container_width=True)
        else:
            st.caption("Aucune demande traitée.")
