"""Interface ADMIN des demandes ; aucun appel AD pour les autres utilisateurs."""

from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from .backup import backup_archive_bytes, backup_workbook_bytes, capture_backup
from .contact import account_request_mailto
from .data import list_sheet_names, read_tabular
from .flow_client import AdUnavailable, RequeteFluxInvalide
from .provisioning import users_sheet_name
from .scope_catalog import scope_clients
from .storage import StorageError

_ACTIONS = {"Ajouter": "AJOUTER", "Modifier": "MODIFIER", "Supprimer": "SUPPRIMER"}


def render_backup_panel(service, user):
    st.markdown("##### Sauvegarde à jour du portefeuille")
    st.caption("Préparez une nouvelle copie après vos modifications. L'Excel contient les données courantes de l'application ; le ZIP ajoute l'état complet des habilitations et des demandes, sans clés de connexion.")
    if st.button("Préparer la sauvegarde à jour", key="prepare_current_backup"):
        try:
            with st.spinner("Préparation de la sauvegarde…"):
                snapshot = capture_backup(service, user)
                workbook = backup_workbook_bytes(snapshot)
                archive = backup_archive_bytes(snapshot, workbook)
            st.session_state["admin_current_backup"] = {"actor": user.principal_id, "generated_at": snapshot["generated_at"],
                                                         "workbook": workbook, "archive": archive}
            st.cache_data.clear()
        except (StorageError, ValueError, PermissionError) as exc:
            st.error(str(exc))
    prepared = st.session_state.get("admin_current_backup")
    if prepared and prepared["actor"] == user.principal_id:
        date = datetime.fromisoformat(prepared["generated_at"])
        stamp = date.strftime("%Y%m%d_%H%M%S")
        st.caption(f"Copie préparée le {date:%d/%m/%Y à %H:%M:%S} (heure de Paris). Régénérez-la après toute nouvelle modification.")
        excel, archive = st.columns(2)
        excel.download_button("Télécharger le portefeuille à jour (.xlsx)", data=prepared["workbook"], file_name=f"opti_route_portefeuille_{stamp}.xlsx",
                              mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="download_current_workbook")
        archive.download_button("Télécharger la sauvegarde complète (.zip)", data=prepared["archive"], file_name=f"opti_route_sauvegarde_{stamp}.zip",
                                mime="application/zip", key="download_current_backup")


def _check_created(service, user, request):
    if request["action"] != "MODIFIER_CODE" and not service.blocked(user.principal_id):
        try:
            with st.spinner("Vérification AD…"):
                service.check(user, request["id"])
        except (AdUnavailable, RequeteFluxInvalide) as exc:
            st.session_state["access_request_notice"] = str(exc)
    st.session_state["access_request_last"] = request["id"]
    st.cache_data.clear()


def _scope_fields(group, prefix, defaults):
    if group == "ATC":
        return {
            "atc_code": st.text_input("Code ATC", value=defaults.get("atc_code") or "", key=prefix + "_code"),
            "atc_email": st.text_input("E-mail du commercial (facultatif)", value=defaults.get("atc_email") or "", key=prefix + "_email"),
            "atc_name": st.text_input("Nom du commercial dans le portefeuille (facultatif)", value=defaults.get("atc_name") or "", key=prefix + "_name"),
        }
    if group == "RAG":
        return {"rag_email": st.text_input("E-mail N+1 / RAG", value=defaults.get("rag_email") or "", key=prefix + "_email")}
    if group == "DR":
        return {"ugs": st.text_input("Codes UG / région", value="-".join(defaults.get("ugs") or ()), placeholder="40-45-71-37", key=prefix + "_ugs")}
    return {}


def render_scope_catalog(service, user):
    import io

    with st.expander("Prête-noms et périmètres sans compte Entra"):
        busy = {request.get("catalog_id") for request in service.pending()}
        entries = service.catalog.entries()
        clients, _ = service.store.load_clients()
        rows = []
        for entry in entries:
            try:
                count = len(scope_clients(clients, entry["group"], entry["scope"]))
            except (StorageError, ValueError):
                count = "Périmètre à compléter"
            rows.append({"Prête-nom": entry["display_name"], "Rôle": entry["group"],
                         "Code / périmètre": service._code(entry["group"], entry["scope"]),
                         "Entreprises": str(count), "État": "Attribué" if entry.get("assigned_to") else "Départ en cours" if entry.get("waiting_for") else "Reprise en cours" if entry["id"] in busy else "Disponible",
                         "Object ID du repreneur": entry.get("assigned_to") or "", "Source": entry["origin"]})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        else:
            st.caption("Aucun prête-nom identifiable dans les données chargées. La détection est automatique ; la feuille Utilisateurs permet de compléter le catalogue si nécessaire.")
        st.caption("Règle automatique : ATC, RAG ou DR sans Object ID ; ADMIN est exclu. Les anciens libellés explicites comme ATC Angers ou ATC164 sont récupérés à l'ouverture de l'administration.")
        st.caption("Un prête-nom n'a aucun accès de connexion. Un départ peut renommer les affectations clients ; une reprise les renomme après confirmation des droits.")
        st.caption("Les affectations sont modifiées dans le portefeuille de l'application. Téléchargez sa sauvegarde à jour dans l'onglet Portefeuille d'entreprises pour conserver ces changements avant un prochain import.")
        if st.checkbox("Importer les prête-noms depuis un classeur Excel", key="scope_catalog_import"):
            uploaded = st.file_uploader("Classeur contenant la feuille Utilisateurs", type=["xlsx", "xlsm", "xls", "xlsb"], key="scope_catalog_workbook")
            if uploaded is not None and st.button("Importer les prête-noms", key="scope_catalog_import_button"):
                try:
                    content = uploaded.getvalue()
                    from .data import validate_uploaded_file

                    validate_uploaded_file(content, uploaded.name)
                    sheet = users_sheet_name(list_sheet_names(io.BytesIO(content), uploaded.name))
                    if sheet is None:
                        raise ValueError("La feuille Utilisateurs est introuvable.")
                    users = read_tabular(io.BytesIO(content), filename=uploaded.name, sheet_name=sheet)
                    service.catalog.import_users(user, users, clients)
                except (StorageError, ValueError, PermissionError) as exc:
                    st.error(str(exc))
                else:
                    st.rerun()
        editable = {entry["id"]: entry for entry in entries if entry.get("available") and not entry.get("assigned_to") and entry["id"] not in busy}
        selected = st.selectbox("Prête-nom à définir", [None, *editable], format_func=lambda value: "— Créer un prête-nom —" if value is None else editable[value]["display_name"], key="scope_catalog_edit")
        previous = editable.get(selected, {})
        group = st.selectbox("Population du prête-nom", ["ATC", "RAG", "DR"], index=["ATC", "RAG", "DR"].index(previous.get("group", "ATC")), key=f"scope_catalog_group_{selected}", disabled=selected is not None)
        old_name = previous.get("display_name", "")
        city = st.text_input("Ville ou repère du prête-nom", value=old_name.removeprefix(group).strip(), placeholder="Angers", key=f"scope_catalog_city_{selected}")
        fields = _scope_fields(group, f"scope_catalog_{selected}_{group}", previous.get("scope", {}))
        st.caption(f"Libellé : {group} {city.strip()}" if city.strip() else "Exemple : ATC Angers")
        if st.button("Enregistrer le prête-nom", key="scope_catalog_save"):
            try:
                service.catalog.save(user, group=group, city=city, scope=service._scope(group, fields), identity=selected)
            except (StorageError, ValueError, PermissionError) as exc:
                st.error(str(exc))
            else:
                st.rerun()


def _summary(request):
    control = request.get("dernierControle") or {}
    return {
        "Personne": " ".join((request["nom"], request["prenom"])).strip(),
        "Action": request["action"], "Groupe": request["groupe"],
        "Ancien groupe": request.get("ancienGroupe") or "", "Périmètre": request.get("code", ""),
        "Statut": request["statut"], "Créée le": request["created_at"],
        "Dernier contrôle": control.get("date", ""), "Résultat": control.get("motif", ""),
        "Prête-nom": request.get("vacancy", {}).get("name") or request.get("catalog_name", ""),
        "Clients réaffectés": str((request.get("portfolio_change") or request.get("vacancy", {}).get("change") or {}).get("client_count", 0)),
    }


def render_access_requests(service, user, recipient):
    if not user.is_admin:
        raise PermissionError("Interface réservée aux administrateurs.")
    try:
        results = service.check_pending(user, automatic=True)
        errors = [error for _, _, error in results if error]
        if errors:
            st.warning(errors[-1])
        if any(request and request.get("statut") in {"CONFIRMEE", "CLOTUREE"} for _, request, _ in results):
            st.cache_data.clear()
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
    catalog_id = None
    rename_clients = False
    vacant_city = None
    if action != "SUPPRIMER":
        busy = {request.get("catalog_id") for request in service.pending()}
        choices = {entry["id"]: entry for entry in service.catalog.entries() if entry["group"] == group and entry.get("available") and entry.get("assigned_to") in {None, principal_id or None} and entry["id"] not in busy}
        catalog_id = st.selectbox("Portefeuille / prête-nom à reprendre", [None, *choices], format_func=lambda value: "— Périmètre saisi ci-dessous —" if value is None else choices[value]["display_name"], key=f"request_catalog_{action}_{principal_id}_{group}")
        defaults = profile or {}
        if catalog_id:
            defaults = {**choices[catalog_id]["scope"]}
            try:
                portfolio, _ = service.store.load_clients()
                st.caption(f"Périmètre repris : {len(scope_clients(portfolio, group, defaults))} entreprise(s).")
            except (StorageError, ValueError) as exc:
                st.warning(f"Complétez le périmètre du prête-nom dans Habilitations : {exc}")
            rename_clients = st.checkbox("Renommer les affectations clients au nom du collaborateur", value=True, key=f"request_rename_{catalog_id}_{principal_id}")
            if rename_clients:
                for field in ("atc_email", "rag_email"):
                    defaults[field] = (profile or {}).get(field) or ""
                st.caption("Le code et le périmètre sont repris du prête-nom. Renseignez l'e-mail du repreneur ; les affectations changent à la confirmation.")
            prefix += "_" + catalog_id
        scope = _scope_fields(group, prefix, defaults)
        if action == "MODIFIER" and group == old_group:
            st.info("Le groupe reste identique : seul le périmètre sera modifié directement, sans mail ni appel AD.")
    elif profile and group in {"ATC", "RAG", "DR"}:
        if st.checkbox("Conserver le portefeuille sous un prête-nom", key=f"request_vacate_{principal_id}"):
            vacant_city = st.text_input("Ville du prête-nom", placeholder="Angers", key=f"request_vacate_city_{principal_id}").strip()
            st.caption(f"Les affectations seront renommées « {group} {vacant_city} ». Le prête-nom sera disponible après clôture du retrait AD.")
    if st.button("Enregistrer la demande", key="create_access_request", type="primary"):
        try:
            request = service.create(user, action=action, group=group, nom=nom, prenom=prenom,
                                     principal_id=principal_id or None, old_group=old_group, scope=scope,
                                     catalog_id=catalog_id, rename_clients=rename_clients, vacant_city=vacant_city)
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
                st.cache_data.clear()
            except (StorageError, ValueError, PermissionError, AdUnavailable) as exc:
                st.session_state["access_request_notice"] = str(exc)
            st.rerun()
        if cancel.button("Annuler la demande", key="cancel_access_request"):
            try:
                service.cancel(user, selected_id)
                st.cache_data.clear()
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
                        st.cache_data.clear()
                    except (StorageError, ValueError, PermissionError, AdUnavailable) as exc:
                        st.session_state["access_request_notice"] = str(exc)
                    st.rerun()
            if not request.get("objectId"):
                corrected_nom = st.text_input("Nom corrigé", value=request["nom"], key=f"correct_nom_{selected_id}")
                corrected_prenom = st.text_input("Prénom corrigé", value=request["prenom"], key=f"correct_prenom_{selected_id}")
                if st.button("Corriger le nom", key="correct_access_name"):
                    try:
                        service.clarify(user, selected_id, nom=corrected_nom, prenom=corrected_prenom)
                        st.cache_data.clear()
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
