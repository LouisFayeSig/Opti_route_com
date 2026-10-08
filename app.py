from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from opti_route.access import AccessDeniedError, authorize_portfolio
from opti_route.auth import (
    ROLE_ATC,
    ROLE_DIRECTOR,
    AuthenticatedUser,
    render_account_controls,
    require_authentication,
)
from opti_route.azure_maps import AzureMapsClient
from opti_route.cache import GeocodeCache
from opti_route.config import Settings, load_settings
from opti_route.data import (
    ALIASES,
    ClientDataError,
    list_sheet_names,
    preferred_portfolio_sheet_index,
    read_tabular,
    standardize_clients,
    suggest_column_mapping,
    validate_uploaded_file,
)
from opti_route.exporting import google_maps_url, pdf_bytes
from opti_route.geocoding import geocode_missing_clients
from opti_route.map_view import possibility_points_from_candidates, render_map
from opti_route.planner import (
    PlanningError,
    RoutePlan,
    StartPoint,
    build_route_plan,
    rebuild_route_plan,
)
from opti_route.storage import (
    ApplicationStore,
    PortfolioMetadata,
    RouteConfiguration,
    StorageError,
    UserAccessProfile,
)
from opti_route.store_factory import create_app_store

st.set_page_config(
    page_title="Opti Route Com",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1550px;}
      h1 {font-size: 2rem !important; letter-spacing: -.03em; margin-bottom: .15rem !important;}
      h2, h3 {letter-spacing: -.02em;}
      div[data-testid="stMetric"] {background:rgba(128,128,128,.09); border:1px solid rgba(128,128,128,.22);
        padding:12px 14px; border-radius:12px;}
      div[data-testid="stFileUploader"] section {padding: .7rem;}
      .opti-subtitle {color:#607080; margin-bottom:1rem;}
      .opti-badge {display:inline-block; padding:4px 9px; border-radius:99px; font-size:.78rem;
        font-weight:650; margin-right:6px;}
      .opti-ok {background:#e8f5e9;color:#256029}.opti-warn {background:#fff3e0;color:#9a5800}
      .opti-empty {height:520px;border:1px dashed #b8c4d1;border-radius:14px;display:flex;
        flex-direction:column;align-items:center;justify-content:center;text-align:center;color:#607080;
        background:linear-gradient(145deg,#f8fbff,#f3f7fa);padding:2rem}
      .opti-empty-icon {font-size:3rem;margin-bottom:.8rem}
      [data-testid="stHorizontalBlock"] {align-items:stretch;}
      .opti-step-title {padding:.55rem .7rem;border-radius:9px;font-weight:700;margin-bottom:.65rem}
      .opti-step-prospects {background:#eff6ff;color:#1d4ed8;border-left:4px solid #2563eb}
      .opti-step-start {background:#ecfdf5;color:#047857;border-left:4px solid #10b981}
      .opti-step-appointments {background:#fff7ed;color:#c2410c;border-left:4px solid #f97316}
      .opti-step-review {background:#f5f3ff;color:#6d28d9;border-left:4px solid #8b5cf6}
      div[data-testid="stVerticalBlockBorderWrapper"] {border-radius:12px;}
    </style>
    """,
    unsafe_allow_html=True,
)

try:
    runtime_secrets = st.secrets.to_dict()
except FileNotFoundError:
    runtime_secrets = {}
settings = load_settings(PROJECT_ROOT, secrets=runtime_secrets)


@st.cache_resource
def _initialize_app_store(_settings: Settings) -> ApplicationStore:
    return create_app_store(_settings)


@st.cache_data(ttl=30, show_spinner=False)
def _load_persistent_app_state(
    storage_backend: str, _store: ApplicationStore
) -> tuple[pd.DataFrame | None, PortfolioMetadata | None, RouteConfiguration]:
    # storage_backend fait partie de la clé de cache ; _store est une ressource non sérialisable.
    del storage_backend
    clients, metadata = _store.load_clients()
    return clients, metadata, _store.load_route_configuration()

FIELD_LABELS = {
    "client_id": "Code client",
    "client_name": "Nom de l'entreprise",
    "salesperson_code": "Code ATC",
    "salesperson": "Commercial",
    "salesperson_email": "E-mail du commercial",
    "director_name": "N+1 / directeur",
    "director_email": "E-mail du directeur",
    "agency": "Agence",
    "agency_address": "Adresse agence",
    "address": "Adresse / rue",
    "address_2": "Complément d'adresse 1",
    "address_3": "Complément d'adresse 2",
    "postal_code": "Code postal",
    "city": "Ville",
    "country": "Pays",
    "latitude": "Latitude (optionnel)",
    "longitude": "Longitude (optionnel)",
}


def _admin_import_panel(
    store: ApplicationStore,
    user: AuthenticatedUser,
    current_clients: pd.DataFrame | None,
    metadata: PortfolioMetadata | None,
) -> None:
    if current_clients is not None:
        st.success(f"Portefeuille actif : {len(current_clients)} entreprises.")
        if metadata is not None:
            st.caption(
                f"Source : {metadata.source_name} · Importé par {metadata.imported_by} "
                f"le {metadata.imported_at.replace('T', ' ')}"
            )
    uploaded = st.file_uploader(
        "Importer et remplacer le portefeuille d'entreprises",
        type=["csv", "xls", "xlsx", "xlsm", "xlsb"],
        help=(
            "20 Mo maximum. Le fichier brut n'est pas conservé : seules les données "
            "normalisées nécessaires à la tournée sont stockées."
        ),
        key="admin_portfolio_upload",
    )
    if uploaded is None:
        return

    file_bytes = uploaded.getvalue()
    try:
        validate_uploaded_file(file_bytes, uploaded.name)
        sheets = list_sheet_names(io.BytesIO(file_bytes), filename=uploaded.name)
    except (ClientDataError, ValueError) as exc:
        st.error(str(exc))
        return

    file_hash = hashlib.sha256(file_bytes).hexdigest()[:12]
    suffix = Path(uploaded.name).suffix.casefold()
    default_sheet_index = preferred_portfolio_sheet_index(sheets)
    sheet_column, header_column = st.columns(2)
    sheet = sheet_column.selectbox(
        "Feuille",
        sheets,
        index=default_sheet_index,
        disabled=suffix == ".csv",
        key=f"admin_sheet_v2_{file_hash}",
    )
    header_line = header_column.number_input(
        "Ligne contenant les en-têtes",
        min_value=1,
        max_value=100,
        value=1,
        step=1,
        key=f"admin_header_{file_hash}",
    )
    try:
        raw = read_tabular(
            io.BytesIO(file_bytes),
            filename=uploaded.name,
            sheet_name=sheet if suffix != ".csv" else 0,
            header_row=int(header_line) - 1,
        )
        if len(raw) > 70_000 or len(raw.columns) > 200:
            raise ClientDataError("Le fichier est limité à 70 000 lignes et 200 colonnes.")
    except Exception as exc:
        st.error(f"Lecture impossible : {exc}")
        return

    suggestions = suggest_column_mapping(raw.columns)
    columns = [str(column) for column in raw.columns]
    options: list[str | None] = [None, *columns]
    mapping: dict[str, str | None] = {}
    with st.expander("Correspondance des colonnes", expanded=True):
        st.caption(
            "Vérifiez les correspondances proposées. Le commercial est obligatoire pour chaque ligne."
        )
        mapping_columns = st.columns(3)
        for position, target in enumerate(ALIASES):
            suggested = suggestions.get(target)
            default_index = options.index(suggested) if suggested in options else 0
            with mapping_columns[position % 3]:
                mapping[target] = st.selectbox(
                    FIELD_LABELS[target],
                    options,
                    index=default_index,
                    format_func=lambda value: "— Non renseigné —" if value is None else value,
                    key=f"admin_mapping_{file_hash}_{sheet}_{header_line}_{target}",
                )
        st.dataframe(raw.head(8), use_container_width=True, hide_index=True, height=240)

    try:
        clients = standardize_clients(raw, column_mapping=mapping)
        salespeople = clients["salesperson"].fillna("").astype(str).str.strip()
        missing_salesperson_count = int((salespeople.eq("") | salespeople.eq("Tous")).sum())
        if missing_salesperson_count:
            raise ClientDataError(
                f"{missing_salesperson_count} ligne(s) n'ont pas de commercial. "
                "Associez la colonne correspondante avant d'enregistrer."
            )
    except ClientDataError as exc:
        st.error(str(exc))
        return

    geocoded = clients[["latitude", "longitude"]].notna().all(axis=1).sum()
    commercial_count = clients["salesperson"].nunique()
    atc_scopes = clients[
        ["salesperson_code", "salesperson", "salesperson_email", "director_email"]
    ].drop_duplicates()
    atc_with_email = int(atc_scopes["salesperson_email"].notna().sum())
    atc_with_director = int(atc_scopes["director_email"].notna().sum())
    st.caption(
        f"{len(clients)} entreprises · {commercial_count} commerciaux · {geocoded} déjà géocodées"
    )
    st.caption(
        f"Cloisonnement : {atc_with_email}/{len(atc_scopes)} commerciaux avec e-mail "
        f"et {atc_with_director}/{len(atc_scopes)} rattaches a un directeur."
    )
    if st.button(
        "Enregistrer ce portefeuille",
        type="primary",
        use_container_width=True,
        key=f"save_portfolio_{file_hash}_{sheet}_{header_line}",
    ):
        try:
            store.save_clients(
                clients,
                source_name=uploaded.name,
                imported_by=user.display_name,
            )
        except StorageError as exc:
            st.error(str(exc))
        else:
            _load_persistent_app_state.clear()
            st.session_state.pop("route_plan", None)
            st.success("Le portefeuille sécurisé a été remplacé.")
            st.rerun()


def _admin_settings_panel(
    store: ApplicationStore, configuration: RouteConfiguration
) -> None:
    st.caption(
        "Ces contraintes sont communes à tous les utilisateurs et modifiables ici uniquement."
    )
    with st.form("admin_route_configuration"):
        max_visits = st.slider(
            "Nombre maximal de visites",
            min_value=1,
            max_value=10,
            value=configuration.max_visits,
        )
        radius_km = st.select_slider(
            "Rayon maximal",
            options=[10, 20, 30, 50, 100],
            value=configuration.radius_km,
            format_func=lambda value: f"{value} km",
        )
        st.info(
            "Chaque tournée se termine à la dernière entreprise visitée : aucun trajet de retour "
            "n'est calculé.",
            icon="ℹ️",
        )
        submitted = st.form_submit_button(
            "Enregistrer les paramètres",
            type="primary",
            use_container_width=True,
        )
    if submitted:
        try:
            store.save_route_configuration(
                RouteConfiguration(
                    radius_km=int(radius_km),
                    max_visits=int(max_visits),
                    return_to_start=False,
                )
            )
        except StorageError as exc:
            st.error(str(exc))
        else:
            _load_persistent_app_state.clear()
            st.session_state.pop("route_plan", None)
            st.success("Les paramètres ont été enregistrés.")
            st.rerun()


def _admin_access_panel(
    store: ApplicationStore,
    user: AuthenticatedUser,
    clients: pd.DataFrame | None,
) -> None:
    st.caption(
        "Les rôles sont attribués dans Microsoft Entra ID. Ce panneau définit uniquement "
        "le périmètre de données des ATC et des directeurs."
    )
    profiles = store.list_access_profiles()
    if profiles:
        profile_table = pd.DataFrame(
            [
                {
                    "Collaborateur": profile.display_name,
                    "Object ID Entra": profile.principal_id,
                    "Rôle attendu": "ATC" if profile.role == ROLE_ATC else "Directeur",
                    "Code ATC": profile.atc_code or "",
                    "E-mail ATC": profile.atc_email or "",
                    "Commercial ATC": profile.atc_name or "",
                    "E-mail directeur": profile.director_email or "",
                    "Agences": ", ".join(profile.agencies),
                    "Modifié le": profile.updated_at.replace("T", " "),
                }
                for profile in profiles
            ]
        )
        st.dataframe(profile_table, hide_index=True, use_container_width=True)
    else:
        st.info("Aucune habilitation ATC ou directeur n'est encore configurée.")

    st.markdown("##### Ajouter ou mettre à jour une habilitation")
    st.caption(
        "Utilisez le claim `oid` du compte Entra. Un rôle administrateur n'a pas besoin "
        "d'habilitation, car son périmètre est global."
    )
    principal_id = st.text_input(
        "Object ID Entra",
        key="access_principal_id",
        placeholder="00000000-0000-0000-0000-000000000000",
    )
    display_name = st.text_input(
        "Nom affiché",
        key="access_display_name",
        placeholder="Prénom NOM",
    )
    role_label = st.radio(
        "Rôle attendu",
        ["ATC", "Directeur"],
        horizontal=True,
        key="access_role",
    )

    atc_rows = pd.DataFrame(
        columns=["salesperson_code", "salesperson", "salesperson_email"]
    )
    director_rows = pd.DataFrame(columns=["director_name", "director_email"])
    agencies: list[str] = []
    if clients is not None:
        atc_rows = (
            clients.reindex(
                columns=["salesperson_code", "salesperson", "salesperson_email"]
            )
            .fillna("")
            .astype(str)
            .drop_duplicates()
            .sort_values(["salesperson", "salesperson_code"], key=lambda values: values.str.casefold())
        )
        director_rows = (
            clients.reindex(columns=["director_name", "director_email"])
            .dropna(subset=["director_email"])
            .fillna("")
            .astype(str)
            .drop_duplicates()
            .sort_values(["director_name", "director_email"], key=lambda values: values.str.casefold())
        )
        agencies = sorted(
            {
                value.strip()
                for value in clients["agency"].fillna("").astype(str)
                if value.strip()
            },
            key=str.casefold,
        )

    selected_atc_code: str | None = None
    selected_atc_email: str | None = None
    selected_atc_name: str | None = None
    selected_director_email: str | None = None
    selected_agencies: tuple[str, ...] = ()
    if role_label == "ATC":
        atc_options = atc_rows.index.tolist()
        if atc_options:
            selected_atc_index = st.selectbox(
                "Code ATC et commercial",
                atc_options,
                format_func=lambda index: (
                    f"{atc_rows.at[index, 'salesperson']} · "
                    f"{atc_rows.at[index, 'salesperson_code']}"
                ),
                key="access_atc_scope",
            )
            selected_atc_code = str(atc_rows.at[selected_atc_index, "salesperson_code"])
            selected_atc_email = (
                str(atc_rows.at[selected_atc_index, "salesperson_email"]).strip() or None
            )
            selected_atc_name = str(
                atc_rows.at[selected_atc_index, "salesperson"]
            ).strip() or None
        else:
            st.warning("Importez d'abord un portefeuille contenant des codes ATC.")
    else:
        if not director_rows.empty:
            director_options = director_rows.index.tolist()
            selected_director_index = st.selectbox(
                "Directeur et e-mail",
                director_options,
                format_func=lambda index: (
                    f"{director_rows.at[index, 'director_name']} · "
                    f"{director_rows.at[index, 'director_email']}"
                ),
                key="access_director_scope",
            )
            selected_director_email = str(
                director_rows.at[selected_director_index, "director_email"]
            ).strip() or None
            st.caption("Le directeur verra les portefeuilles associes a cet e-mail N+1.")
        if director_rows.empty:
            selected_agencies = tuple(
                st.multiselect(
                    "Agences autorisées (portefeuille historique)",
                    agencies,
                    key="access_agency_scope",
                )
            )
            if not agencies:
                st.warning("Importez d'abord un portefeuille contenant des agences.")

    if st.button(
        "Enregistrer l'habilitation",
        type="primary",
        disabled=not principal_id.strip(),
        key="save_access_profile",
    ):
        try:
            store.save_access_profile(
                UserAccessProfile(
                    principal_id=principal_id,
                    display_name=display_name,
                    role=ROLE_ATC if role_label == "ATC" else ROLE_DIRECTOR,
                    atc_code=selected_atc_code,
                    atc_email=selected_atc_email,
                    atc_name=selected_atc_name,
                    director_email=selected_director_email,
                    agencies=selected_agencies,
                ),
                updated_by=user.display_name,
            )
        except StorageError as exc:
            st.error(str(exc))
        else:
            st.success("L'habilitation a été enregistrée.")
            st.rerun()

    if profiles:
        with st.expander("Révoquer une habilitation"):
            profile_by_id = {profile.principal_id: profile for profile in profiles}
            revoked_id = st.selectbox(
                "Compte",
                list(profile_by_id),
                format_func=lambda value: (
                    f"{profile_by_id[value].display_name} · {value}"
                ),
                key="revoke_access_profile",
            )
            confirmed = st.checkbox(
                "Je confirme la révocation de cet accès aux données.",
                key="confirm_revoke_access_profile",
            )
            if st.button(
                "Révoquer l'habilitation",
                disabled=not confirmed,
                key="delete_access_profile",
            ):
                try:
                    deleted = store.delete_access_profile(revoked_id)
                except StorageError as exc:
                    st.error(str(exc))
                else:
                    if deleted:
                        st.success("L'habilitation a été révoquée.")
                    st.rerun()


def _azure_static_map_diagnostic_panel(azure_client: AzureMapsClient | None) -> None:
    st.caption(
        "Ce test appelle uniquement l'image statique Azure Maps sur une zone neutre, "
        "sans donnée d'entreprise ni affichage de clé."
    )
    if azure_client is None:
        st.info("Azure Maps n'est pas configuré : ajoutez la clé dans les secrets Streamlit.")
        return

    run_diagnostic = getattr(azure_client, "static_map_diagnostic", None)
    if not callable(run_diagnostic):
        st.warning(
            "Le processus Streamlit utilise encore une ancienne version du module Azure Maps. "
            "Redémarrez complètement l'application depuis Manage app, puis réessayez."
        )
        return

    if not st.button(
        "Tester la capture de carte Azure",
        use_container_width=True,
        key="azure_static_map_diagnostic",
    ):
        return

    with st.spinner("Test de l'endpoint Azure Maps…"):
        diagnostic = run_diagnostic()

    details = [
        f"HTTP : {diagnostic.status_code if diagnostic.status_code is not None else 'sans réponse'}",
        f"Type : {diagnostic.content_type or 'non communiqué'}",
        f"Format : {diagnostic.response_kind}",
    ]
    if diagnostic.available:
        st.success(diagnostic.message)
    else:
        st.error(diagnostic.message)
        if diagnostic.response_kind == "html":
            st.warning(
                "La réponse est une page HTML, et non une erreur Azure Maps JSON. "
                "Cela indique généralement un proxy, un WAF ou une URL intermédiaire."
            )
    st.caption(" · ".join(details))
    if diagnostic.request_id:
        st.caption(f"Identifiant de requête Azure : `{diagnostic.request_id}`")


def _render_admin_panel(
    store: ApplicationStore,
    user: AuthenticatedUser,
    clients: pd.DataFrame | None,
    metadata: PortfolioMetadata | None,
    configuration: RouteConfiguration,
    azure_client: AzureMapsClient | None,
) -> None:
    with st.expander("⚙️ Administration", expanded=clients is None):
        portfolio_tab, access_tab, settings_tab, diagnostic_tab = st.tabs(
            ["Portefeuille d'entreprises", "Habilitations", "Contraintes", "Diagnostic Azure"]
        )
        with portfolio_tab:
            _admin_import_panel(store, user, clients, metadata)
        with access_tab:
            _admin_access_panel(store, user, clients)
        with settings_tab:
            _admin_settings_panel(store, configuration)
        with diagnostic_tab:
            _azure_static_map_diagnostic_panel(azure_client)


def _format_duration(seconds: int) -> str:
    hours, remainder = divmod(round(seconds / 60), 60)
    return f"{hours} h {remainder:02d}" if hours else f"{remainder} min"


def _render_plan_summary(
    plan: RoutePlan, possibility_points: list[dict[str, object]] | None = None
) -> None:
    last_company = str(plan.table.iloc[-1]["Client"])
    st.info(
        f"**Départ :** {plan.start.label}  \n"
        f"**Fin de tournée :** après la dernière visite ({last_company})",
        icon="📍",
    )
    metric_columns = st.columns(3)
    metric_columns[0].metric("Distance totale", f"{plan.total_distance_m / 1000:.1f} km")
    metric_columns[1].metric("Temps de conduite", _format_duration(plan.total_duration_s))
    metric_columns[2].metric("Entreprises à visiter", plan.visit_count)
    st.caption(
        f"Calcul : {plan.provider} · {plan.candidates_in_radius} entreprises dans le rayon"
    )
    render_map(
        plan,
        settings.azure_maps_key,
        height=520,
        renderer=settings.map_renderer,
        possibility_points=possibility_points,
    )
    legend = (
        "<span style='color:#1565C0'>●</span> Départ &nbsp;&nbsp; "
        "<span style='color:#D32F2F'>●</span> Tournée"
    )
    if possibility_points:
        legend += (
            " &nbsp;&nbsp; <span style='color:#D97706'>●</span> Possibilité dans le périmètre "
            "&nbsp;&nbsp; <span style='color:#16A34A'>●</span> Plus loin"
        )
    st.markdown(legend, unsafe_allow_html=True)


def _render_results(
    plan: RoutePlan,
    route_client: AzureMapsClient | None,
) -> None:
    st.markdown(
        '<div class="opti-step-title opti-step-review">4. Revoir le parcours</div>',
        unsafe_allow_html=True,
    )
    st.subheader("Ordre de visite")
    st.caption(
        "Décochez une visite facultative, puis appliquez la sélection pour recalculer la tournée."
    )
    display = plan.review_table()
    display.insert(0, "Conserver", True)
    result_identifier = hashlib.sha1(
        (
            f"{plan.created_at.isoformat()}|" + "|".join(plan.table["Code client"].astype(str))
        ).encode()
    ).hexdigest()[:12]
    edited_display = st.data_editor(
        display,
        key=f"result_visit_selection_{result_identifier}",
        hide_index=True,
        use_container_width=True,
        height=min(590, 42 + 35 * len(display)),
        disabled=[
            "Ordre",
            "Entreprise",
            "Ville",
            "Adresse",
            "Distance",
            "Temps",
            "Distance cumulée",
            "Temps cumulé",
            "Rendez-vous planifié",
        ],
        column_config={
            "Conserver": st.column_config.CheckboxColumn("Visiter", required=True, width="small"),
            "Ordre": st.column_config.NumberColumn("Ordre", format="%d"),
            "Entreprise": st.column_config.TextColumn("Entreprise", width="medium"),
            "Rendez-vous planifié": st.column_config.CheckboxColumn(
                "Rendez-vous", disabled=True, width="small"
            ),
            "Distance": st.column_config.NumberColumn("Distance", format="%.1f km"),
            "Temps": st.column_config.NumberColumn("Temps", format="%.0f min"),
            "Distance cumulée": st.column_config.NumberColumn(
                "Cumul (km)", format="%.1f", width="medium"
            ),
            "Temps cumulé": st.column_config.NumberColumn(
                "Cumul temps", format="%.0f min", width="medium"
            ),
        },
    )
    selected_positions = [
        position
        for position, retained in enumerate(
            edited_display.iloc[1:]["Conserver"].fillna(False).astype(bool).tolist()
        )
        if retained
    ]
    required_positions = [
        position
        for position, client_id in enumerate(plan.table["Code client"].astype(str))
        if client_id in plan.required_client_ids
    ]
    retained_positions = sorted(set(selected_positions).union(required_positions))
    selection_changed = set(retained_positions) != set(range(plan.visit_count))
    if required_positions:
        st.caption("Les rendez-vous planifiés restent inclus lors d'un recalcul.")
    if not retained_positions:
        st.warning("Conservez au moins une entreprise pour recalculer la tournée.")
    if st.button(
        "Recalculer avec les adresses conservées",
        disabled=not selection_changed or not retained_positions,
        use_container_width=True,
        key=f"apply_result_selection_{result_identifier}",
    ):
        try:
            with st.spinner("Recalcul de la tournée…"):
                updated_plan = rebuild_route_plan(
                    plan,
                    retained_positions,
                    azure_client=route_client,
                )
            st.session_state["route_plan"] = updated_plan
            st.rerun()
        except (PlanningError, ValueError) as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Le recalcul de la tournée a échoué : {exc}")

    with st.expander("Exporter ou partager la tournée", expanded=True):
        export_columns = st.columns([1, 1.25])
        export_columns[0].download_button(
            "Télécharger PDF",
            data=pdf_bytes(plan),
            file_name=f"{plan.export_stem}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
        export_columns[1].link_button(
            "Ouvrir dans Google Maps",
            google_maps_url(plan),
            use_container_width=True,
        )


authenticated_user = require_authentication(settings)
azure_client = (
    AzureMapsClient(
        settings.azure_maps_endpoint,
        settings.azure_maps_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
    if settings.azure_maps_enabled
    else None
)

title_column, status_column, account_column = st.columns([3.7, 1, 1.35])
with title_column:
    st.title("🧭 Opti Route Com")
    st.markdown(
        '<div class="opti-subtitle">Préparez une tournée commerciale optimisée en quelques minutes.</div>',
        unsafe_allow_html=True,
    )
with status_column:
    if settings.azure_maps_enabled:
        st.markdown(
            '<span class="opti-badge opti-ok">● Azure Maps connecté</span>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<span class="opti-badge opti-warn">● Mode estimation</span>',
            unsafe_allow_html=True,
        )
with account_column:
    render_account_controls(authenticated_user)

try:
    store = _initialize_app_store(settings)
    clients, portfolio_metadata, route_configuration = _load_persistent_app_state(
        settings.app_storage_backend,
        store,
    )
except StorageError as exc:
    st.error(str(exc))
    st.stop()

if authenticated_user.is_admin:
    _render_admin_panel(
        store,
        authenticated_user,
        clients,
        portfolio_metadata,
        route_configuration,
        azure_client,
    )

if clients is None or portfolio_metadata is None:
    if authenticated_user.is_admin:
        st.warning("Aucun portefeuille actif. Importez-en un depuis le panneau Administration.")
    else:
        st.info("Aucun portefeuille d'entreprises n'est disponible. Contactez l'administrateur.")
    st.stop()

try:
    access_profile = (
        None
        if authenticated_user.is_admin
        else store.load_access_profile(authenticated_user.principal_id)
    )
    authorized_portfolio = authorize_portfolio(clients, authenticated_user, access_profile)
except (AccessDeniedError, StorageError) as exc:
    st.error(str(exc))
    if authenticated_user.principal_id:
        st.caption(f"Identifiant du compte : `{authenticated_user.principal_id}`")
    st.stop()

scoped_clients = authorized_portfolio.clients
salesperson_rows = (
    scoped_clients[["salesperson_code", "salesperson"]]
    .fillna("")
    .astype(str)
    .drop_duplicates(subset=["salesperson_code"], keep="first")
)
salesperson_rows = salesperson_rows[
    salesperson_rows["salesperson_code"].str.strip().ne("")
    & salesperson_rows["salesperson"].str.strip().ne("")
    & salesperson_rows["salesperson"].ne("Tous")
].sort_values(["salesperson", "salesperson_code"], key=lambda values: values.str.casefold())
salesperson_codes = salesperson_rows["salesperson_code"].tolist()
salesperson_labels = {
    row.salesperson_code: (
        row.salesperson
        if row.salesperson.strip().casefold() == row.salesperson_code.strip().casefold()
        else f"{row.salesperson} · {row.salesperson_code}"
    )
    for row in salesperson_rows.itertuples(index=False)
}
if not salesperson_codes:
    st.error(
        "Le portefeuille actif ne contient aucun commercial exploitable. "
        "L'administrateur doit importer un fichier corrigé."
    )
    st.stop()

source_signature = (
    f"{portfolio_metadata.digest}:{route_configuration.radius_km}:"
    f"{route_configuration.max_visits}:"
    f"{authenticated_user.principal_id}:{authorized_portfolio.signature}"
)
if st.session_state.get("source_signature") != source_signature:
    st.session_state["source_signature"] = source_signature
    st.session_state.pop("route_plan", None)
    st.session_state.pop("route_overlay_candidates", None)
    st.session_state.pop("route_overlay_start_client_id", None)
    st.session_state.pop("route_overlay_geocode_errors", None)

controls_column, map_column = st.columns([0.36, 0.64], gap="large")
with controls_column:
    st.subheader("Préparer la tournée")

    with st.container(border=True):
        st.markdown(
            '<div class="opti-step-title opti-step-prospects">1. Sélectionner votre portefeuille</div>',
            unsafe_allow_html=True,
        )
        st.caption("Choisissez les entreprises à intégrer à votre tournée.")
        active_salesperson_code = st.selectbox(
            "Commercial",
            salesperson_codes,
            format_func=lambda code: salesperson_labels.get(code, code),
            disabled=authenticated_user.is_atc,
            help=(
                "Votre portefeuille est imposé par votre habilitation."
                if authenticated_user.is_atc
                else "Vous pouvez préparer une tournée pour un commercial de votre périmètre."
            ),
        )
        assigned_clients = scoped_clients[
            scoped_clients["salesperson_code"].astype(str) == active_salesperson_code
        ].copy()
        st.caption(f"{len(assigned_clients)} entreprises dans ce portefeuille")

        selection_identifier = hashlib.sha1(
            f"{source_signature}|{active_salesperson_code}".encode()
        ).hexdigest()[:12]
        selection_default_key = f"selection_default_{selection_identifier}"
        selection_version_key = f"selection_version_{selection_identifier}"
        st.session_state.setdefault(selection_default_key, True)
        st.session_state.setdefault(selection_version_key, 0)
        select_column, deselect_column = st.columns(2)
        if select_column.button(
            "Tout sélectionner",
            key=f"select_all_{selection_identifier}",
            use_container_width=True,
        ):
            st.session_state[selection_default_key] = True
            st.session_state[selection_version_key] += 1
            st.rerun()
        if deselect_column.button(
            "Tout désélectionner",
            key=f"deselect_all_{selection_identifier}",
            use_container_width=True,
        ):
            st.session_state[selection_default_key] = False
            st.session_state[selection_version_key] += 1
            st.rerun()

        selection_source = assigned_clients.reset_index(drop=True)
        selection_table = pd.DataFrame(
            {
                "Sélectionner": st.session_state[selection_default_key],
                "Entreprise": selection_source["client_name"].astype(str),
                "Ville": selection_source["city"].fillna("").astype(str),
                "Adresse": selection_source["full_address"].fillna("").astype(str),
            }
        )
        edited_selection = st.data_editor(
            selection_table,
            key=(f"company_selection_{selection_identifier}_{st.session_state[selection_version_key]}"),
            hide_index=True,
            use_container_width=True,
            height=min(300, max(145, 38 + 35 * len(selection_table))),
            disabled=["Entreprise", "Ville", "Adresse"],
            column_config={
                "Sélectionner": st.column_config.CheckboxColumn(
                    "Visiter", required=True, width="small"
                ),
                "Entreprise": st.column_config.TextColumn("Entreprise", width="medium"),
                "Ville": st.column_config.TextColumn("Ville", width="small"),
                "Adresse": st.column_config.TextColumn("Adresse", width="large"),
            },
        )
        selected_mask = edited_selection["Sélectionner"].fillna(False).astype(bool).to_numpy()
        selected_clients = selection_source.loc[selected_mask].copy()

        appointment_options = assigned_clients.reset_index(drop=True).copy()
        appointment_options["display"] = (
            appointment_options["client_name"].astype(str)
            + " — "
            + appointment_options["city"].fillna("").astype(str)
        )
        st.caption(f"{len(selected_clients)} entreprises sélectionnées")

    with st.container(border=True):
        st.markdown(
            '<div class="opti-step-title opti-step-start">2. Choisir l\'entreprise de départ</div>',
            unsafe_allow_html=True,
        )
        selected_start_index = st.selectbox(
            "Entreprise de départ",
            [None, *appointment_options.index.tolist()],
            format_func=lambda index: (
                "— Choisir une entreprise —"
                if index is None
                else appointment_options.at[index, "display"]
            ),
            key=f"start_client_{selection_identifier}",
        )
        st.caption("ou")
        worksite_address = st.text_input(
            "Adresse du rendez-vous chantier",
            placeholder="14 rue …, 14000 Caen",
            key=f"worksite_address_{selection_identifier}",
            help="Saisissez l'adresse complète du lieu de rendez-vous lorsque le départ ne se fait pas depuis une entreprise.",
        ).strip()
        start_client_id = (
            str(appointment_options.at[selected_start_index, "client_id"])
            if selected_start_index is not None
            else None
        )

    with st.container(border=True):
        st.markdown(
            '<div class="opti-step-title opti-step-appointments">3. Ajouter d\'autres rendez-vous déjà planifiés (facultatif)</div>',
            unsafe_allow_html=True,
        )
        additional_appointment_options = appointment_options[
            appointment_options["client_id"].astype(str) != start_client_id
        ]
        selected_planned_indices = st.multiselect(
            "Autres rendez-vous déjà planifiés",
            additional_appointment_options.index.tolist(),
            format_func=lambda index: appointment_options.at[index, "display"],
            key=f"planned_appointments_{selection_identifier}",
            help="Ils sont ajoutés à la tournée, même s'ils ne sont pas sélectionnés ci-dessus, et restent inclus lors d'un recalcul.",
        )
        planned_appointment_ids = list(
            dict.fromkeys(
                str(appointment_options.at[index, "client_id"])
                for index in selected_planned_indices
            )
        )
        planned_clients = appointment_options.loc[selected_planned_indices].drop(
            columns=["display"], errors="ignore"
        ).drop_duplicates(subset=["client_id"], keep="first")
        clients_for_route = pd.concat(
            [selected_clients, planned_clients], ignore_index=True
        ).drop_duplicates(subset=["client_id"], keep="first")
        st.caption(
            f"{len(selected_clients)} entreprises sélectionnées · "
            f"{len(planned_appointment_ids)} rendez-vous ajouté(s)"
        )

        minimum_visits = max(1, len(planned_appointment_ids))
        if minimum_visits > route_configuration.max_visits:
            st.error(
                f"{len(planned_appointment_ids)} rendez-vous ajoutés dépassent le maximum autorisé de {route_configuration.max_visits} visites."
            )
            requested_visits = route_configuration.max_visits
        else:
            default_requested_visits = max(
                minimum_visits, min(6, route_configuration.max_visits)
            )
            requested_visits = st.select_slider(
                "Nombre total de visites",
                options=list(range(minimum_visits, route_configuration.max_visits + 1)),
                value=default_requested_visits,
                help=(
                    "Les rendez-vous ajoutés sont compris dans ce total. Les entreprises les plus proches "
                    "du départ sont retenues en priorité."
                ),
            )

    with st.container(border=True):
        st.markdown("##### Contraintes définies par l'administrateur")
        st.caption(
            f"Maximum autorisé : **{route_configuration.max_visits} visite(s)** · "
            f"Rayon : **{route_configuration.radius_km} km**."
        )

    generate = st.button("Générer ma tournée", type="primary", use_container_width=True)
    if generate:
        try:
            if start_client_id is not None and worksite_address:
                raise PlanningError(
                    "Choisissez soit une entreprise de départ, soit une adresse de rendez-vous chantier."
                )
            if start_client_id is None and not worksite_address:
                raise PlanningError(
                    "Choisissez une entreprise de départ ou renseignez une adresse de rendez-vous chantier."
                )
            if clients_for_route.empty:
                raise PlanningError("Sélectionnez au moins une entreprise ou ajoutez un rendez-vous planifié.")
            if len(planned_appointment_ids) > route_configuration.max_visits:
                raise PlanningError(
                    "Réduisez le nombre de rendez-vous ajoutés avant de générer la tournée."
                )
            cache = GeocodeCache(settings.geocode_cache_path)
            progress_bar = st.progress(0, text="Vérification des coordonnées des entreprises…")

            def update_progress(position: int, total: int, client_name: str) -> None:
                progress_bar.progress(
                    position / max(total, 1),
                    text=f"Géocodage {position}/{total} · {client_name}",
                )

            clients_to_geocode = clients_for_route.copy()
            start_reference_id: str
            if start_client_id is not None:
                start_source = assigned_clients[
                    assigned_clients["client_id"].astype(str) == start_client_id
                ]
                start_reference_id = start_client_id
            else:
                start_reference_id = "worksite-" + hashlib.sha1(
                    worksite_address.casefold().encode()
                ).hexdigest()[:12]
                start_source = pd.DataFrame(
                    [
                        {
                            "client_id": start_reference_id,
                            "client_name": "Rendez-vous chantier",
                            "full_address": worksite_address,
                            "latitude": pd.NA,
                            "longitude": pd.NA,
                        }
                    ]
                )
            clients_to_geocode = pd.concat(
                [clients_to_geocode, start_source], ignore_index=True
            ).drop_duplicates(subset=["client_id"], keep="first")
            enriched_clients, geocode_errors = geocode_missing_clients(
                clients_to_geocode,
                azure_client,
                cache,
                progress=update_progress,
            )
            progress_bar.empty()

            start_client = enriched_clients[
                enriched_clients["client_id"].astype(str) == start_reference_id
            ]
            if start_client.empty or start_client[["latitude", "longitude"]].isna().any(axis=None):
                start_description = (
                    "L'entreprise choisie comme point de départ"
                    if start_client_id is not None
                    else "L'adresse du rendez-vous chantier"
                )
                raise PlanningError(
                    f"{start_description} n'a pas pu être géocodée."
                )
            row = start_client.iloc[0]
            start = StartPoint(
                float(row["latitude"]),
                float(row["longitude"]),
                (
                    f"Entreprise · {row['client_name']}"
                    if start_client_id is not None
                    else "Rendez-vous chantier"
                ),
                str(row.get("full_address", "")) or None,
            )

            visitable_clients = enriched_clients[
                enriched_clients["client_id"].astype(str) != start_reference_id
            ]
            if visitable_clients.dropna(subset=["latitude", "longitude"]).empty:
                error_details = " · ".join(geocode_errors[:3])
                raise PlanningError(
                    "Aucune entreprise ou aucun rendez-vous planifié à visiter n'a pu être géocodé."
                    + (f" {error_details}" if error_details else "")
                )

            effective_required_ids = [
                client_id
                for client_id in planned_appointment_ids
                if client_id != start_client_id
            ]
            plan = build_route_plan(
                visitable_clients,
                start,
                radius_km=float(route_configuration.radius_km),
                max_visits=int(requested_visits),
                max_duration_hours=None,
                return_to_start=False,
                objective="time",
                azure_client=azure_client,
                excluded_client_id=start_client_id,
                required_client_ids=effective_required_ids,
                prefer_nearest_first=True,
            )
            if geocode_errors:
                plan.warnings.append(
                    f"{len(geocode_errors)} entreprises n'ont pas pu être géocodées. "
                    + " · ".join(geocode_errors[:3])
                )
            st.session_state["route_plan"] = plan
            if authenticated_user.is_admin:
                st.session_state["route_overlay_candidates"] = assigned_clients.copy()
                st.session_state["route_overlay_start_client_id"] = start_client_id
                st.session_state.pop("route_overlay_geocode_errors", None)
        except (PlanningError, ClientDataError, StorageError, ValueError) as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Le calcul de la tournée a échoué : {exc}")

with map_column:
    plan: RoutePlan | None = st.session_state.get("route_plan")
    if plan is None:
        st.markdown(
            """
            <div class="opti-empty">
              <div class="opti-empty-icon">🗺️</div>
              <h3>Votre tournée apparaîtra ici</h3>
              <div>Choisissez le commercial, les entreprises et le point de départ.</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        possibility_points: list[dict[str, object]] | None = None
        if authenticated_user.is_admin:
            show_possibility_layer = st.toggle(
                "Afficher le calque des possibilités",
                key=f"possibility_layer_{plan.created_at.isoformat()}",
                help=(
                    "Jaune : entreprises situées dans le rayon du point sélectionné le plus "
                    "éloigné du départ. Vert : entreprises plus éloignées."
                ),
            )
            if show_possibility_layer:
                overlay_candidates = st.session_state.get(
                    "route_overlay_candidates", assigned_clients
                )
                missing_overlay_coordinates = (
                    overlay_candidates["latitude"].isna()
                    | overlay_candidates["longitude"].isna()
                )
                if missing_overlay_coordinates.any():
                    progress_bar = st.progress(
                        0,
                        text="Préparation du calque : vérification des coordonnées…",
                    )

                    def update_overlay_progress(
                        position: int, total: int, company_name: str
                    ) -> None:
                        progress_bar.progress(
                            position / max(total, 1),
                            text=f"Calque {position}/{total} · {company_name}",
                        )

                    overlay_candidates, overlay_geocode_errors = geocode_missing_clients(
                        overlay_candidates,
                        azure_client,
                        GeocodeCache(settings.geocode_cache_path),
                        progress=update_overlay_progress,
                    )
                    progress_bar.empty()
                    st.session_state["route_overlay_candidates"] = overlay_candidates
                    st.session_state["route_overlay_geocode_errors"] = overlay_geocode_errors
                overlay_start_client_id = st.session_state.get(
                    "route_overlay_start_client_id", start_client_id
                )
                (
                    possibility_points,
                    nearby_count,
                    distant_count,
                    furthest_selected_km,
                ) = possibility_points_from_candidates(
                    plan, overlay_candidates, overlay_start_client_id
                )
                st.caption(
                    f"Référence : {furthest_selected_km:.1f} km à vol d'oiseau depuis le départ · "
                    f"{nearby_count} possibilité(s) en jaune · {distant_count} en vert"
                )
                overlay_geocode_errors = st.session_state.get("route_overlay_geocode_errors", [])
                if overlay_geocode_errors:
                    st.warning(
                        f"{len(overlay_geocode_errors)} entreprise(s) ne peuvent pas être "
                        "affichées dans le calque faute de coordonnées."
                    )
        _render_plan_summary(plan, possibility_points=possibility_points)

plan = st.session_state.get("route_plan")
if plan is not None:
    for warning in plan.warnings:
        st.warning(warning)
    st.divider()
    _render_results(plan, azure_client)
