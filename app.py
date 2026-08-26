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

from opti_route.azure_maps import AzureMapsClient, AzureMapsError
from opti_route.cache import GeocodeCache
from opti_route.config import load_settings
from opti_route.data import (
    ALIASES,
    ClientDataError,
    discover_client_files,
    list_sheet_names,
    read_tabular,
    standardize_clients,
    suggest_column_mapping,
)
from opti_route.exporting import csv_bytes, excel_bytes, google_maps_url, pdf_bytes
from opti_route.geocoding import geocode_missing_clients
from opti_route.map_view import render_map
from opti_route.planner import PlanningError, RoutePlan, StartPoint, build_route_plan

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
      div[data-testid="stMetric"] {background:#f7f9fc; border:1px solid #e3e8ef;
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
    </style>
    """,
    unsafe_allow_html=True,
)

settings = load_settings(PROJECT_ROOT)
azure_client = (
    AzureMapsClient(
        settings.azure_maps_endpoint,
        settings.azure_maps_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
    if settings.azure_maps_enabled
    else None
)

browser_location = components.declare_component(
    "opti_route_browser_location", path=str(SRC_ROOT / "opti_route" / "browser_location")
)

FIELD_LABELS = {
    "client_id": "Code client",
    "client_name": "Nom du client",
    "salesperson": "Commercial",
    "address": "Adresse / rue",
    "address_2": "Complément d'adresse 1",
    "address_3": "Complément d'adresse 2",
    "postal_code": "Code postal",
    "city": "Ville",
    "country": "Pays",
    "latitude": "Latitude (optionnel)",
    "longitude": "Longitude (optionnel)",
}


def _read_source_bytes() -> tuple[bytes, str] | None:
    uploaded = st.file_uploader(
        "Importer un portefeuille clients",
        type=["csv", "xls", "xlsx", "xlsm", "xlsb"],
        help="Formats acceptés : CSV, XLS, XLSX, XLSM et XLSB. Le fichier reste dans la session.",
    )
    if uploaded is not None:
        return uploaded.getvalue(), uploaded.name

    local_files = discover_client_files(PROJECT_ROOT)
    if settings.clients_file and settings.clients_file.exists():
        local_files = [settings.clients_file, *[path for path in local_files if path != settings.clients_file]]
    if not local_files:
        return None
    selected = st.selectbox(
        "Ou utiliser un fichier disponible sur le serveur",
        options=local_files,
        format_func=lambda path: path.name,
    )
    return selected.read_bytes(), selected.name


def _prepare_clients() -> tuple[pd.DataFrame | None, str | None]:
    with st.expander("1 · Importer et contrôler les données clients", expanded=True):
        source = _read_source_bytes()
        if source is None:
            st.info("Importez un fichier clients pour commencer.")
            return None, None
        file_bytes, filename = source
        file_hash = hashlib.sha256(file_bytes).hexdigest()[:12]
        suffix = Path(filename).suffix.casefold()

        sheet_column, header_column = st.columns(2)
        try:
            sheets = list_sheet_names(io.BytesIO(file_bytes), filename=filename)
        except Exception as exc:
            st.error(f"Impossible de lire le classeur : {exc}")
            return None, None
        with sheet_column:
            sheet = st.selectbox(
                "Feuille",
                sheets,
                disabled=suffix == ".csv",
                key=f"sheet_{file_hash}",
            )
        with header_column:
            header_line = st.number_input(
                "Ligne contenant les en-têtes",
                min_value=1,
                max_value=100,
                value=1,
                step=1,
                key=f"header_{file_hash}",
            )
        try:
            raw = read_tabular(
                io.BytesIO(file_bytes),
                filename=filename,
                sheet_name=sheet if suffix != ".csv" else 0,
                header_row=int(header_line) - 1,
            )
        except Exception as exc:
            st.error(f"Lecture impossible : {exc}")
            return None, None

        suggestions = suggest_column_mapping(raw.columns)
        columns = [str(column) for column in raw.columns]
        options: list[str | None] = [None, *columns]
        mapping: dict[str, str | None] = {}
        with st.expander("Correspondance des colonnes", expanded=False):
            st.caption(
                "Les correspondances sont proposées automatiquement. Corrigez-les si votre fichier utilise d'autres intitulés."
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
                        key=f"mapping_{file_hash}_{sheet}_{header_line}_{target}",
                    )
            st.dataframe(raw.head(8), use_container_width=True, hide_index=True, height=240)

        try:
            clients = standardize_clients(raw, column_mapping=mapping)
        except ClientDataError as exc:
            st.error(str(exc))
            return None, None
        mapping_signature = "|".join(str(mapping.get(target)) for target in ALIASES)
        signature = f"{file_hash}:{sheet}:{header_line}:{mapping_signature}"
        geocoded = clients[["latitude", "longitude"]].notna().all(axis=1).sum()
        st.caption(
            f"{len(clients):,} clients chargés · {geocoded:,} déjà géocodés · "
            f"{clients['salesperson'].nunique():,} commerciaux".replace(",", " ")
        )
        return clients, signature


def _geocode_start_address(address: str, cache: GeocodeCache) -> StartPoint:
    cached = cache.get(address)
    if cached:
        return StartPoint(cached.latitude, cached.longitude, cached.formatted_address)
    if azure_client is None:
        raise PlanningError("Configurez AZURE_MAPS_SUBSCRIPTION_KEY pour géocoder l'adresse de départ.")
    try:
        result = azure_client.geocode(address)
    except AzureMapsError as exc:
        raise PlanningError(str(exc)) from exc
    cache.set(address, result.latitude, result.longitude, result.formatted_address)
    return StartPoint(result.latitude, result.longitude, result.formatted_address)


def _format_duration(seconds: int) -> str:
    hours, remainder = divmod(round(seconds / 60), 60)
    return f"{hours} h {remainder:02d}" if hours else f"{remainder} min"


def _render_plan_summary(plan: RoutePlan) -> None:
    metric_columns = st.columns(3)
    metric_columns[0].metric("Distance totale", f"{plan.total_distance_m / 1000:.1f} km")
    metric_columns[1].metric("Temps de conduite", _format_duration(plan.total_duration_s))
    metric_columns[2].metric("Clients à visiter", plan.visit_count)
    st.caption(
        f"Calcul : {plan.provider} · {plan.candidates_in_radius} clients dans le rayon"
        + (f" · {plan.omitted_for_duration} retirés par la contrainte de durée" if plan.omitted_for_duration else "")
    )
    render_map(plan, settings.azure_maps_key, height=520)
    st.markdown(
        "<span style='color:#1565C0'>●</span> Départ &nbsp;&nbsp; "
        "<span style='color:#D32F2F'>●</span> Visite &nbsp;&nbsp; "
        "<span style='color:#2E7D32'>●</span> Arrivée",
        unsafe_allow_html=True,
    )


def _render_results(plan: RoutePlan) -> None:
    st.subheader("Ordre de visite")
    display = plan.table[
        ["Ordre", "Client", "Ville", "Distance", "Temps", "Distance cumulée", "Temps cumulé"]
    ].copy()
    st.dataframe(
        display,
        hide_index=True,
        use_container_width=True,
        height=min(590, 42 + 35 * len(display)),
        column_config={
            "Ordre": st.column_config.NumberColumn("Ordre", format="%d"),
            "Distance": st.column_config.NumberColumn("Distance", format="%.1f km"),
            "Temps": st.column_config.NumberColumn("Temps", format="%.0f min"),
            "Distance cumulée": st.column_config.NumberColumn("Cumul", format="%.1f km"),
            "Temps cumulé": st.column_config.NumberColumn("Temps cumulé", format="%.0f min"),
        },
    )
    with st.expander("Exporter ou partager la tournée", expanded=True):
        export_columns = st.columns([1, 1, 1, 1.25])
        export_columns[0].download_button(
            "Télécharger Excel",
            data=excel_bytes(plan),
            file_name="tournee_commerciale.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
        export_columns[1].download_button(
            "Télécharger CSV",
            data=csv_bytes(plan),
            file_name="tournee_commerciale.csv",
            mime="text/csv",
            use_container_width=True,
        )
        export_columns[2].download_button(
            "Télécharger PDF",
            data=pdf_bytes(plan),
            file_name="tournee_commerciale.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
        export_columns[3].link_button(
            "Ouvrir dans Google Maps",
            google_maps_url(plan),
            use_container_width=True,
        )


title_column, status_column = st.columns([4, 1])
with title_column:
    st.title("🧭 Opti Route Com")
    st.markdown(
        '<div class="opti-subtitle">Préparez une tournée commerciale optimisée en quelques minutes.</div>',
        unsafe_allow_html=True,
    )
with status_column:
    if settings.azure_maps_enabled:
        st.markdown('<span class="opti-badge opti-ok">● Azure Maps connecté</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="opti-badge opti-warn">● Mode estimation</span>', unsafe_allow_html=True)

clients, source_signature = _prepare_clients()
if clients is None:
    st.stop()

if st.session_state.get("source_signature") != source_signature:
    st.session_state["source_signature"] = source_signature
    st.session_state.pop("route_plan", None)

salespeople = sorted(
    value for value in clients["salesperson"].dropna().astype(str).unique() if value.strip()
)
if not salespeople:
    salespeople = ["Tous"]

controls_column, map_column = st.columns([0.34, 0.66], gap="large")
with controls_column:
    st.subheader("Préparer la tournée")
    salesperson = st.selectbox("Commercial", salespeople)
    assigned_clients = clients[
        clients["salesperson"].astype(str) == salesperson
    ].copy()
    if salesperson == "Tous":
        assigned_clients = clients.copy()
    st.caption(f"{len(assigned_clients)} clients dans ce portefeuille")

    start_mode = st.radio(
        "Point de départ",
        ["Ma position", "Adresse personnalisée", "Client existant"],
        horizontal=True,
    )
    location_value = None
    address_parts: list[str] = []
    appointment_id: str | None = None
    if start_mode == "Ma position":
        location_value = browser_location(key="browser_geolocation", default=None)
        if location_value:
            st.success(
                f"Position : {location_value['latitude']:.5f}, {location_value['longitude']:.5f}",
                icon="📍",
            )
    elif start_mode == "Adresse personnalisée":
        street = st.text_input("Rue et numéro", placeholder="12 rue de la Paix")
        postal_column, city_column = st.columns([0.38, 0.62])
        postal_code = postal_column.text_input("Code postal", placeholder="14000")
        city = city_column.text_input("Ville", placeholder="Caen")
        country = st.text_input("Pays", value="France")
        address_parts = [street, postal_code, city, country]
    else:
        appointment_options = assigned_clients.copy()
        appointment_options["display"] = (
            appointment_options["client_name"].astype(str)
            + " — "
            + appointment_options["city"].fillna("").astype(str)
        )
        selected_appointment = st.selectbox(
            "Client du rendez-vous",
            appointment_options.index,
            format_func=lambda index: appointment_options.at[index, "display"],
        )
        appointment_id = str(appointment_options.at[selected_appointment, "client_id"])

    st.markdown("##### Contraintes")
    visits_label = "Visites complémentaires" if start_mode == "Client existant" else "Nombre maximal de visites"
    max_visits = st.select_slider(visits_label, options=[5, 10, 15, 20], value=10)
    radius_km = st.select_slider("Rayon maximal", options=[10, 20, 30, 50, 100], value=30, format_func=lambda x: f"{x} km")
    duration_hours = st.segmented_control("Durée maximale", options=[4, 6, 8], default=8, format_func=lambda x: f"{x} h")
    return_to_start = st.toggle("Retour au point de départ", value=True)
    objective_label = st.radio("Optimiser", ["Le temps", "La distance"], horizontal=True)

    generate = st.button("Générer ma tournée", type="primary", use_container_width=True)
    if generate:
        try:
            cache = GeocodeCache(settings.geocode_cache_path)
            progress_bar = st.progress(0, text="Vérification des coordonnées clients…")

            def update_progress(position: int, total: int, client_name: str) -> None:
                progress_bar.progress(
                    position / max(total, 1), text=f"Géocodage {position}/{total} · {client_name}"
                )

            enriched_clients, geocode_errors = geocode_missing_clients(
                assigned_clients, azure_client, cache, progress=update_progress
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
            elif start_mode == "Adresse personnalisée":
                address = ", ".join(part.strip() for part in address_parts if part.strip())
                if len(address_parts[0].strip()) == 0 or len(address_parts[2].strip()) == 0:
                    raise PlanningError("Renseignez au minimum la rue et la ville de départ.")
                start = _geocode_start_address(address, cache)
            else:
                appointment = enriched_clients[
                    enriched_clients["client_id"].astype(str) == appointment_id
                ]
                if appointment.empty or appointment[["latitude", "longitude"]].isna().any(axis=None):
                    raise PlanningError("Le client du rendez-vous n'a pas pu être géocodé.")
                row = appointment.iloc[0]
                start = StartPoint(
                    float(row["latitude"]),
                    float(row["longitude"]),
                    f"Rendez-vous · {row['client_name']}",
                )

            plan = build_route_plan(
                enriched_clients,
                start,
                radius_km=float(radius_km),
                max_visits=int(max_visits),
                max_duration_hours=float(duration_hours or 8),
                return_to_start=return_to_start,
                objective="time" if objective_label == "Le temps" else "distance",
                azure_client=azure_client,
                excluded_client_id=appointment_id,
            )
            if geocode_errors:
                plan.warnings.append(
                    f"{len(geocode_errors)} clients n'ont pas pu être géocodés. "
                    + " · ".join(geocode_errors[:3])
                )
            st.session_state["route_plan"] = plan
        except (PlanningError, ClientDataError, ValueError) as exc:
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
              <div>Importez le portefeuille, choisissez le point de départ et lancez le calcul.</div>
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
    _render_results(plan)
