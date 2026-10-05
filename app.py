from __future__ import annotations

import hashlib
import io
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from opti_route.auth import AuthenticatedUser, render_account_controls, require_authentication
from opti_route.azure_maps import AzureMapsClient, AzureMapsError
from opti_route.cache import GeocodeCache
from opti_route.config import load_settings
from opti_route.data import (
    ALIASES,
    ClientDataError,
    list_sheet_names,
    read_tabular,
    standardize_clients,
    suggest_column_mapping,
    validate_uploaded_file,
)
from opti_route.exporting import google_maps_url, pdf_bytes
from opti_route.geocoding import geocode_missing_clients
from opti_route.map_view import render_map
from opti_route.planner import (
    PlanningError,
    RoutePlan,
    StartPoint,
    build_route_plan,
    rebuild_route_plan,
)
from opti_route.storage import (
    AppStore,
    PortfolioMetadata,
    RouteConfiguration,
    StorageError,
)

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
      .opti-step-arrival {background:#fff7ed;color:#c2410c;border-left:4px solid #f97316}
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

browser_location = components.declare_component(
    "opti_route_browser_location", path=str(SRC_ROOT / "opti_route" / "browser_location")
)

FIELD_LABELS = {
    "client_id": "Code client",
    "client_name": "Nom du client",
    "salesperson": "Commercial",
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
    store: AppStore,
    user: AuthenticatedUser,
    current_clients: pd.DataFrame | None,
    metadata: PortfolioMetadata | None,
) -> None:
    if current_clients is not None:
        st.success(f"Portefeuille actif : {len(current_clients)} clients.")
        if metadata is not None:
            st.caption(
                f"Source : {metadata.source_name} · Importé par {metadata.imported_by} "
                f"le {metadata.imported_at.replace('T', ' ')}"
            )
    uploaded = st.file_uploader(
        "Importer et remplacer le portefeuille clients",
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
    sheet_column, header_column = st.columns(2)
    sheet = sheet_column.selectbox(
        "Feuille",
        sheets,
        disabled=suffix == ".csv",
        key=f"admin_sheet_{file_hash}",
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
    st.caption(
        f"{len(clients)} clients · {commercial_count} commerciaux · {geocoded} déjà géocodés"
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
            st.session_state.pop("route_plan", None)
            st.success("Le portefeuille sécurisé a été remplacé.")
            st.rerun()


def _admin_settings_panel(store: AppStore, configuration: RouteConfiguration) -> None:
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
        return_to_start = st.toggle(
            "Retour au point de départ par défaut",
            value=configuration.return_to_start,
            help="Une adresse d'arrivée saisie par l'utilisateur remplace ce comportement.",
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
                    return_to_start=bool(return_to_start),
                )
            )
        except StorageError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("route_plan", None)
            st.success("Les paramètres ont été enregistrés.")
            st.rerun()


def _azure_static_map_diagnostic_panel(azure_client: AzureMapsClient | None) -> None:
    st.caption(
        "Ce test appelle uniquement l'image statique Azure Maps sur une zone neutre, "
        "sans donnée client ni affichage de clé."
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
    store: AppStore,
    user: AuthenticatedUser,
    clients: pd.DataFrame | None,
    metadata: PortfolioMetadata | None,
    configuration: RouteConfiguration,
    azure_client: AzureMapsClient | None,
) -> None:
    with st.expander("⚙️ Administration", expanded=clients is None):
        portfolio_tab, settings_tab, diagnostic_tab = st.tabs(
            ["Portefeuille clients", "Contraintes", "Diagnostic Azure"]
        )
        with portfolio_tab:
            _admin_import_panel(store, user, clients, metadata)
        with settings_tab:
            _admin_settings_panel(store, configuration)
        with diagnostic_tab:
            _azure_static_map_diagnostic_panel(azure_client)


def _geocode_address(address: str, cache: GeocodeCache, point_name: str) -> StartPoint:
    cached = cache.get(address)
    if cached:
        return StartPoint(
            cached.latitude,
            cached.longitude,
            cached.formatted_address,
            cached.formatted_address,
        )
    if azure_client is None:
        raise PlanningError(
            f"Configurez AZURE_MAPS_SUBSCRIPTION_KEY pour géocoder l'adresse {point_name}."
        )
    try:
        result = azure_client.geocode(address)
    except AzureMapsError as exc:
        raise PlanningError(str(exc)) from exc
    cache.set(address, result.latitude, result.longitude, result.formatted_address)
    return StartPoint(
        result.latitude,
        result.longitude,
        result.formatted_address,
        result.formatted_address,
    )


def _address_field(prefix: str, title: str) -> str:
    return st.text_input(
        title,
        placeholder="12 rue de la Paix, 14000 Caen, France",
        key=f"{prefix}_address",
        help="Saisissez l'adresse complète sur une seule ligne pour améliorer le géocodage.",
    )


def _validated_address(address: str, point_name: str) -> str:
    address = address.strip()
    if len(address) < 5:
        raise PlanningError(f"Renseignez une adresse complète {point_name}.")
    return address


def _format_duration(seconds: int) -> str:
    hours, remainder = divmod(round(seconds / 60), 60)
    return f"{hours} h {remainder:02d}" if hours else f"{remainder} min"


def _render_plan_summary(plan: RoutePlan) -> None:
    destination_label = (
        plan.end.label
        if plan.end is not None
        else plan.start.label
        if plan.return_to_start
        else "Dernière entreprise visitée"
    )
    st.info(
        f"**Départ :** {plan.start.label}  \n**Arrivée :** {destination_label}",
        icon="📍",
    )
    metric_columns = st.columns(3)
    metric_columns[0].metric("Distance totale", f"{plan.total_distance_m / 1000:.1f} km")
    metric_columns[1].metric("Temps de conduite", _format_duration(plan.total_duration_s))
    metric_columns[2].metric("Clients à visiter", plan.visit_count)
    st.caption(f"Calcul : {plan.provider} · {plan.candidates_in_radius} clients dans le rayon")
    render_map(
        plan,
        settings.azure_maps_key,
        height=520,
        renderer=settings.map_renderer,
    )
    st.markdown(
        "<span style='color:#1565C0'>●</span> Départ &nbsp;&nbsp; "
        "<span style='color:#D32F2F'>●</span> Visite &nbsp;&nbsp; "
        "<span style='color:#2E7D32'>●</span> Arrivée",
        unsafe_allow_html=True,
    )


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
    visit_display = plan.table[
        [
            "Ordre",
            "Client",
            "Ville",
            "Adresse",
            "Distance",
            "Temps",
            "Distance cumulée",
            "Temps cumulé",
        ]
    ].copy()
    visit_display["Rendez-vous prévu"] = plan.table.get("Rendez-vous prévu", False)
    start_row = pd.DataFrame(
        [
            {
                "Ordre": 0,
                "Client": f"Départ · {plan.start.label}",
                "Ville": "",
                "Adresse": plan.start.address or plan.start.label,
                "Distance": 0.0,
                "Temps": 0.0,
                "Distance cumulée": 0.0,
                "Temps cumulé": 0.0,
                "Rendez-vous prévu": False,
            }
        ]
    )
    display = pd.concat([start_row, visit_display], ignore_index=True)
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
            "Client",
            "Ville",
            "Adresse",
            "Distance",
            "Temps",
            "Distance cumulée",
            "Temps cumulé",
            "Rendez-vous prévu",
        ],
        column_config={
            "Conserver": st.column_config.CheckboxColumn("Visiter", required=True, width="small"),
            "Ordre": st.column_config.NumberColumn("Ordre", format="%d"),
            "Rendez-vous prévu": st.column_config.CheckboxColumn(
                "Rendez-vous", disabled=True, width="small"
            ),
            "Distance": st.column_config.NumberColumn("Distance", format="%.1f km"),
            "Temps": st.column_config.NumberColumn("Temps", format="%.0f min"),
            "Distance cumulée": st.column_config.NumberColumn("Cumul", format="%.1f km"),
            "Temps cumulé": st.column_config.NumberColumn("Temps cumulé", format="%.0f min"),
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
        st.caption("Les rendez-vous prévus sont obligatoires et restent inclus dans le recalcul.")
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
    store = AppStore(settings.app_storage_path)
    clients, portfolio_metadata = store.load_clients()
    route_configuration = store.load_route_configuration()
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
        st.info("Aucun portefeuille clients n'est disponible. Contactez l'administrateur.")
    st.stop()

salespeople = sorted(
    value
    for value in clients["salesperson"].dropna().astype(str).unique()
    if value.strip() and value != "Tous"
)
if not salespeople:
    st.error(
        "Le portefeuille actif ne contient aucun commercial exploitable. "
        "L'administrateur doit importer un fichier corrigé."
    )
    st.stop()

source_signature = (
    f"{portfolio_metadata.digest}:{route_configuration.radius_km}:"
    f"{route_configuration.max_visits}:{route_configuration.return_to_start}"
)
if st.session_state.get("source_signature") != source_signature:
    st.session_state["source_signature"] = source_signature
    st.session_state.pop("route_plan", None)

controls_column, map_column = st.columns([0.36, 0.64], gap="large")
with controls_column:
    st.subheader("Préparer la tournée")

    with st.container(border=True):
        st.markdown(
            '<div class="opti-step-title opti-step-prospects">1. Choisir les visites</div>',
            unsafe_allow_html=True,
        )
        st.caption("Obligatoire · sélectionnez au moins un prospect ou un rendez-vous prévu.")
        active_salesperson = st.selectbox("Commercial", salespeople)
        assigned_clients = clients[clients["salesperson"].astype(str) == active_salesperson].copy()
        st.caption(f"{len(assigned_clients)} entreprises dans ce portefeuille")

        selection_identifier = hashlib.sha1(
            f"{source_signature}|{active_salesperson}".encode()
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
        selected_planned_indices = st.multiselect(
            "Rendez-vous déjà planifiés (facultatif)",
            appointment_options.index.tolist(),
            format_func=lambda index: appointment_options.at[index, "display"],
            key=f"planned_appointments_{selection_identifier}",
            help="Ils sont ajoutés à la tournée, même s'ils ne sont pas cochés ci-dessus, et restent obligatoires lors d'un recalcul.",
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
            f"{len(selected_clients)} prospects sélectionnés · "
            f"{len(planned_appointment_ids)} rendez-vous prévu(s)"
        )

        minimum_visits = max(1, len(planned_appointment_ids))
        if minimum_visits > route_configuration.max_visits:
            st.error(
                f"{len(planned_appointment_ids)} rendez-vous prévus dépassent le maximum autorisé de {route_configuration.max_visits} visites."
            )
            requested_visits = route_configuration.max_visits
        else:
            requested_visits = st.select_slider(
                "Nombre total de visites",
                options=list(range(minimum_visits, route_configuration.max_visits + 1)),
                value=route_configuration.max_visits,
                help=(
                    "Les rendez-vous prévus sont compris dans ce total. Les autres prospects les plus proches "
                    "du départ sont retenus en priorité."
                ),
            )

    with st.container(border=True):
        st.markdown(
            '<div class="opti-step-title opti-step-start">2. Choisir le point de départ</div>',
            unsafe_allow_html=True,
        )
        st.caption("Obligatoire · choisissez votre position ou un client existant.")
        start_mode = st.radio(
            "Point de départ",
            ["Ma position", "Client existant"],
            index=1,
            horizontal=True,
        )
        location_value = None
        start_client_id: str | None = None
        if start_mode == "Ma position":
            location_value = browser_location(key="browser_geolocation", default=None)
            if location_value:
                st.success(
                    f"Position : {location_value['latitude']:.5f}, {location_value['longitude']:.5f}",
                    icon="📍",
                )
        else:
            selected_start_index = st.selectbox(
                "Client de départ",
                appointment_options.index,
                format_func=lambda index: appointment_options.at[index, "display"],
                key=f"start_client_{selection_identifier}",
            )
            start_client_id = str(appointment_options.at[selected_start_index, "client_id"])
            if start_client_id in planned_appointment_ids:
                st.info("Ce rendez-vous devient le point de départ (ordre 0) et ne sera donc pas une visite distincte.")

    with st.container(border=True):
        st.markdown(
            '<div class="opti-step-title opti-step-arrival">3. Choisir l’arrivée</div>',
            unsafe_allow_html=True,
        )
        st.caption("Obligatoire · définissez comment se termine la tournée.")
        arrival_choices = [
            "Retour au point de départ",
            "Terminer à la dernière visite",
            "Adresse d'arrivée spécifique",
        ]
        default_arrival = 0 if route_configuration.return_to_start else 1
        arrival_mode = st.radio(
            "Fin de tournée",
            arrival_choices,
            index=default_arrival,
        )
        custom_arrival = arrival_mode == "Adresse d'arrivée spécifique"
        return_to_start = arrival_mode == "Retour au point de départ"
        arrival_address = ""
        if custom_arrival:
            arrival_address = _address_field("arrival", "Adresse d'arrivée")

    with st.container(border=True):
        st.markdown("##### Contraintes définies par l'administrateur")
        st.caption(
            f"Maximum autorisé : **{route_configuration.max_visits} visite(s)** · "
            f"Rayon : **{route_configuration.radius_km} km** · "
            "le choix d'arrivée ci-dessus est appliqué à cette tournée."
        )

    generate = st.button("Générer ma tournée", type="primary", use_container_width=True)
    if generate:
        try:
            if clients_for_route.empty:
                raise PlanningError("Sélectionnez au moins un prospect ou un rendez-vous prévu.")
            if len(planned_appointment_ids) > route_configuration.max_visits:
                raise PlanningError(
                    "Réduisez le nombre de rendez-vous prévus avant de générer la tournée."
                )
            cache = GeocodeCache(settings.geocode_cache_path)
            progress_bar = st.progress(0, text="Vérification des coordonnées clients…")

            def update_progress(position: int, total: int, client_name: str) -> None:
                progress_bar.progress(
                    position / max(total, 1),
                    text=f"Géocodage {position}/{total} · {client_name}",
                )

            clients_to_geocode = clients_for_route.copy()
            if start_client_id is not None:
                start_source = assigned_clients[
                    assigned_clients["client_id"].astype(str) == start_client_id
                ]
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

            if start_mode == "Ma position":
                if not location_value:
                    raise PlanningError(
                        "Cliquez sur « Utiliser ma position actuelle » et autorisez la localisation."
                    )
                start = StartPoint(
                    float(location_value["latitude"]),
                    float(location_value["longitude"]),
                    "Ma position",
                )
            else:
                start_client = enriched_clients[
                    enriched_clients["client_id"].astype(str) == start_client_id
                ]
                if start_client.empty or start_client[["latitude", "longitude"]].isna().any(
                    axis=None
                ):
                    raise PlanningError("Le client choisi comme point de départ n'a pas pu être géocodé.")
                row = start_client.iloc[0]
                start = StartPoint(
                    float(row["latitude"]),
                    float(row["longitude"]),
                    f"Client · {row['client_name']}",
                    str(row.get("full_address", "")) or None,
                )

            visitable_clients = enriched_clients
            if start_client_id is not None:
                visitable_clients = visitable_clients[
                    visitable_clients["client_id"].astype(str) != start_client_id
                ]
            if visitable_clients.dropna(subset=["latitude", "longitude"]).empty:
                error_details = " · ".join(geocode_errors[:3])
                raise PlanningError(
                    "Aucun prospect ou rendez-vous prévu à visiter n'a pu être géocodé."
                    + (f" {error_details}" if error_details else "")
                )

            end: StartPoint | None = None
            if custom_arrival:
                arrival_address = _validated_address(arrival_address, "d'arrivée")
                geocoded_end = _geocode_address(arrival_address, cache, "d'arrivée")
                end = StartPoint(
                    geocoded_end.latitude,
                    geocoded_end.longitude,
                    f"Arrivée · {geocoded_end.label}",
                    geocoded_end.address,
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
                return_to_start=return_to_start,
                objective="time",
                azure_client=azure_client,
                excluded_client_id=start_client_id,
                end=end,
                required_client_ids=effective_required_ids,
            )
            if geocode_errors:
                plan.warnings.append(
                    f"{len(geocode_errors)} clients n'ont pas pu être géocodés. "
                    + " · ".join(geocode_errors[:3])
                )
            st.session_state["route_plan"] = plan
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
        _render_plan_summary(plan)

plan = st.session_state.get("route_plan")
if plan is not None:
    for warning in plan.warnings:
        st.warning(warning)
    st.divider()
    _render_results(plan, azure_client)
