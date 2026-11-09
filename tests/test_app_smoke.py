import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from opti_route.access import AccessDeniedError, authorize_portfolio
from opti_route.auth import AuthenticatedUser
from opti_route.config import load_settings
from opti_route.portfolio_view import (
    SCOPE_AGENCY,
    SCOPE_ALL,
    SCOPE_COMMERCIAL,
    SCOPE_PORTFOLIOS,
    SCOPE_REGION,
)
from opti_route.storage import AppStore, UserAccessProfile


def _portfolio() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "client_id": ["A", "B"],
            "client_name": ["AMC Folliot", "Autre entreprise"],
            "salesperson": ["Morgan", "Morgan"],
            "agency": ["Caen", "Caen"],
            "agency_address": ["8 rue Ampère, 14120 Mondeville"] * 2,
            "address": ["1 rue du Test", "2 rue du Test"],
            "address_2": [pd.NA, pd.NA],
            "address_3": [pd.NA, pd.NA],
            "postal_code": ["14000", "14000"],
            "city": ["Caen", "Caen"],
            "country": ["France", "France"],
            "latitude": [49.183, 49.184],
            "longitude": [-0.370, -0.371],
            "full_address": ["1 rue du Test, 14000 Caen", "2 rue du Test, 14000 Caen"],
        }
    )


def _portfolio_with_shared_atc_code() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "client_id": ["A1", "A2", "M1", "M2"],
            "client_name": ["Antoine 1", "Antoine 2", "Mathieu 1", "Mathieu 2"],
            "salesperson_code": ["79", "79", "79", "79"],
            "salesperson": [
                "Antoine BELLUAU",
                "Antoine BELLUAU",
                "Mathieu DUBREUIL",
                "Mathieu DUBREUIL",
            ],
            "salesperson_email": [
                "a.belluau@example.test",
                "a.belluau@example.test",
                "m.dubreuil@example.test",
                "m.dubreuil@example.test",
            ],
            "agency": ["Poitiers"] * 4,
            "agency_address": ["1 rue du Test, 86000 Poitiers"] * 4,
            "address": ["1 rue du Test"] * 4,
            "address_2": [pd.NA] * 4,
            "address_3": [pd.NA] * 4,
            "postal_code": ["86000"] * 4,
            "city": ["Poitiers"] * 4,
            "country": ["France"] * 4,
            "latitude": [46.58, 46.581, 46.582, 46.583],
            "longitude": [0.34, 0.341, 0.342, 0.343],
            "full_address": ["1 rue du Test, 86000 Poitiers"] * 4,
        }
    )


def _portfolio_for_local_test_accounts() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "client_id": ["S", "M", "A"],
            "client_name": ["Sarah client", "Meline client", "Aurelie client"],
            "salesperson_code": ["31-02-004", "22-02-22", "22-02-05"],
            "salesperson": ["Sarah EDIR", "Meline TRILLAUD", "Aurelie LOURENCO"],
            "salesperson_email": [
                "s.edir@litt.fr",
                "m.trillaudstbrieuc@litt.fr",
                "a.lourenco@litt.fr",
            ],
            "rag_email": [
                "m.lecarpentier@litt.fr",
                "c.vernay@litt.fr",
                "c.vernay@litt.fr",
            ],
            "agency": ["31-02", "22-02", "22-02"],
            "agency_address": ["1 rue du Test, 22000 Saint-Brieuc"] * 3,
            "address": ["1 rue du Test", "2 rue du Test", "3 rue du Test"],
            "address_2": [pd.NA] * 3,
            "address_3": [pd.NA] * 3,
            "postal_code": ["22000"] * 3,
            "city": ["Saint-Brieuc"] * 3,
            "country": ["France"] * 3,
            "latitude": [48.51, 48.52, 48.53],
            "longitude": [-2.76, -2.77, -2.78],
            "full_address": [
                "1 rue du Test, 22000 Saint-Brieuc",
                "2 rue du Test, 22000 Saint-Brieuc",
                "3 rue du Test, 22000 Saint-Brieuc",
            ],
        }
    )


def test_streamlit_page_loads_without_exception(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "none")
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=30).run()
    assert not app.exception
    assert app.title[0].value == "🧭 Opti Route Com"


def test_password_mode_fails_closed_when_credentials_are_missing(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_USERNAME", "")
    monkeypatch.setenv("AUTH_PASSWORD", "")
    monkeypatch.setenv("ADMIN_USERNAME", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    monkeypatch.setenv("LOCAL_TEST_ACCOUNTS_JSON", "")
    app_path = Path(__file__).parents[1] / "app.py"

    app = AppTest.from_file(str(app_path), default_timeout=30).run()

    assert not app.exception
    assert app.title[0].value == "🧭 Opti Route Com"
    assert "AUTH_*" in app.error[0].value


def test_password_mode_accepts_configured_credentials(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_USERNAME", "collaborateur-test")
    monkeypatch.setenv("AUTH_PASSWORD", "mot-de-passe-test-long")
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=30).run()

    app.text_input[0].input("collaborateur-test")
    app.text_input[1].input("mot-de-passe-test-long")
    app.button[0].click().run()

    assert not app.exception
    assert any("Connecté : collaborateur-test" in caption.value for caption in app.caption)
    assert len(app.file_uploader) == 0


def test_user_route_form_renders_migration_workflow(monkeypatch) -> None:
    st.cache_data.clear()
    st.cache_resource.clear()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_USERNAME", "collaborateur-test")
    monkeypatch.setenv("AUTH_PASSWORD", "mot-de-passe-test-long")
    monkeypatch.setenv("ADMIN_USERNAME", "administrateur-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "mot-de-passe-admin-test-long")
    monkeypatch.setenv("AZURE_MAPS_SUBSCRIPTION_KEY", "")
    monkeypatch.setenv("AZURE_MAPS_KEY", "")
    storage_path = Path(".cache") / f"app-flow-test-{uuid4().hex}.sqlite3"
    store = AppStore(storage_path)
    store.save_clients(_portfolio(), source_name="portfolio.xlsx", imported_by="Test")
    monkeypatch.setenv("APP_STORAGE_PATH", str(storage_path.resolve()))
    app_path = Path(__file__).parents[1] / "app.py"

    try:
        app = AppTest.from_file(str(app_path), default_timeout=30).run()
        app.text_input[0].input("administrateur-test")
        app.text_input[1].input("mot-de-passe-admin-test-long")
        app.button[0].click().run()
        labels = [selectbox.label for selectbox in app.selectbox]

        assert not app.exception
        assert app.subheader[0].value == "Préparer la tournée"
        assert app.toggle[0].label == "Afficher le calque portefeuille"
        assert app.toggle[0].value is False
        assert len(app.get("deck_gl_json_chart")) == 0

        app.toggle[0].set_value(True).run()
        assert len(app.get("deck_gl_json_chart")) == 1

        app.toggle[0].set_value(False).run()
        assert len(app.get("deck_gl_json_chart")) == 0
        assert labels[-2:] == [
            "Commercial",
            "Entreprise de départ",
        ]
        assert [multiselect.label for multiselect in app.multiselect] == [
            "Autres rendez-vous déjà planifiés"
        ]
        assert "Adresse du rendez-vous chantier" in [
            text_input.label for text_input in app.text_input
        ]
        requested_visits = next(
            slider
            for slider in app.select_slider
            if slider.label == "Nombre total de visites (hors départ)"
        )
        assert requested_visits.value == 6
        radio_labels = [radio.label for radio in app.radio]
        assert "Point de départ" not in radio_labels
        assert "Fin de tournée" not in radio_labels

        app.selectbox[-1].select(0).run()
        clear_start_button = next(
            button
            for button in app.button
            if button.label == "Retirer l'entreprise de départ"
        )
        clear_start_button.click().run()
        assert app.selectbox[-1].value is None

        app.selectbox[-1].select(0).run()
        generate_button = next(button for button in app.button if button.label == "Générer ma tournée")
        generate_button.click().run()

        assert not app.exception
        assert [toggle.label for toggle in app.toggle] == [
            "Afficher le calque portefeuille"
        ]
        assert app.toggle[0].value is False

        next(button for button in app.button if button.label == "Se déconnecter").click().run()
        assert "route_plan" not in app.session_state
        assert "route_overlay_candidates" not in app.session_state
        assert "portfolio_layer_visible" not in app.session_state

        app.text_input[0].input("administrateur-test")
        app.text_input[1].input("mot-de-passe-admin-test-long")
        app.button[0].click().run()
        assert not app.exception
        assert app.toggle[0].value is False
        assert len(app.get("deck_gl_json_chart")) == 0
    finally:
        for suffix in ("", "-wal", "-shm"):
            storage_path.with_name(storage_path.name + suffix).unlink(missing_ok=True)


def test_possibility_overlay_is_available_to_an_atc(monkeypatch) -> None:
    st.cache_data.clear()
    st.cache_resource.clear()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_USERNAME", "collaborateur-test")
    monkeypatch.setenv("AUTH_PASSWORD", "mot-de-passe-test-long")
    monkeypatch.setenv("AZURE_MAPS_SUBSCRIPTION_KEY", "")
    monkeypatch.setenv("AZURE_MAPS_KEY", "")
    storage_path = Path(".cache") / f"atc-overlay-test-{uuid4().hex}.sqlite3"
    store = AppStore(storage_path)
    store.save_clients(_portfolio(), source_name="portfolio.xlsx", imported_by="Test")
    store.save_access_profile(
        UserAccessProfile(
            "local:collaborateur-test",
            "Collaborateur test",
            "atc",
            atc_code="Morgan",
        ),
        updated_by="Test",
    )
    monkeypatch.setenv("APP_STORAGE_PATH", str(storage_path.resolve()))
    app_path = Path(__file__).parents[1] / "app.py"

    try:
        app = AppTest.from_file(str(app_path), default_timeout=30).run()
        app.text_input[0].input("collaborateur-test")
        app.text_input[1].input("mot-de-passe-test-long")
        app.button[0].click().run()

        start_selector = next(
            selectbox
            for selectbox in app.selectbox
            if selectbox.label == "Entreprise de départ"
        )
        start_selector.select(0).run()
        generate_button = next(button for button in app.button if button.label == "Générer ma tournée")
        generate_button.click().run()

        assert not app.exception
        assert [toggle.label for toggle in app.toggle] == [
            "Afficher le calque portefeuille"
        ]
        assert app.toggle[0].value is False
        app.toggle[0].set_value(True).run()
        assert app.toggle[0].value is True
    finally:
        for suffix in ("", "-wal", "-shm"):
            storage_path.with_name(storage_path.name + suffix).unlink(missing_ok=True)


def test_local_rag_test_account_uses_its_configured_scope(monkeypatch) -> None:
    st.cache_data.clear()
    st.cache_resource.clear()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_USERNAME", "")
    monkeypatch.setenv("AUTH_PASSWORD", "")
    monkeypatch.setenv("ADMIN_USERNAME", "")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    monkeypatch.setenv(
        "LOCAL_TEST_ACCOUNTS_JSON",
        json.dumps(
            [
                {
                    "username": "test-rag",
                    "password": "mot-de-passe-test-long",
                    "display_name": "Test RAG",
                    "role": "rag",
                    "rag_email": "c.vernay@litt.fr",
                }
            ]
        ),
    )
    storage_path = Path(".cache") / f"local-rag-test-{uuid4().hex}.sqlite3"
    store = AppStore(storage_path)
    store.save_clients(
        _portfolio_for_local_test_accounts(),
        source_name="portfolio.xlsx",
        imported_by="Test",
    )
    monkeypatch.setenv("APP_STORAGE_PATH", str(storage_path.resolve()))
    app_path = Path(__file__).parents[1] / "app.py"

    try:
        app = AppTest.from_file(str(app_path), default_timeout=30).run()
        app.text_input[0].input("test-rag")
        app.text_input[1].input("mot-de-passe-test-long")
        app.button[0].click().run()

        assert not app.exception
        assert any(
            "Connecté : Test RAG · RAG" in caption.value
            for caption in app.caption
        )
        assert any(
            "Meline TRILLAUD · 22-02-22" in option
            for selectbox in app.selectbox
            if selectbox.label == "Commercial"
            for option in selectbox.options
        )
        assert app.toggle[0].value is False
        app.toggle[0].set_value(True).run()
        chart = app.get("deck_gl_json_chart")[0]
        points = json.loads(chart.proto.json)["layers"][0]["data"]
        assert {point["label"] for point in points} == {"Meline client", "Aurelie client"}
    finally:
        for suffix in ("", "-wal", "-shm"):
            storage_path.with_name(storage_path.name + suffix).unlink(missing_ok=True)


def test_salesperson_selector_separates_shared_atc_codes(monkeypatch) -> None:
    st.cache_data.clear()
    st.cache_resource.clear()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_USERNAME", "collaborateur-test")
    monkeypatch.setenv("AUTH_PASSWORD", "mot-de-passe-test-long")
    monkeypatch.setenv("ADMIN_USERNAME", "administrateur-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "mot-de-passe-admin-test-long")
    storage_path = Path(".cache") / f"shared-atc-code-test-{uuid4().hex}.sqlite3"
    store = AppStore(storage_path)
    store.save_clients(
        _portfolio_with_shared_atc_code(),
        source_name="portfolio.xlsx",
        imported_by="Test",
    )
    monkeypatch.setenv("APP_STORAGE_PATH", str(storage_path.resolve()))
    app_path = Path(__file__).parents[1] / "app.py"

    try:
        app = AppTest.from_file(str(app_path), default_timeout=30).run()
        app.text_input[0].input("administrateur-test")
        app.text_input[1].input("mot-de-passe-admin-test-long")
        app.button[0].click().run()

        commercial_selector = next(
            selectbox for selectbox in app.selectbox if selectbox.label == "Commercial"
        )
        assert set(commercial_selector.options) == {
            "Antoine BELLUAU · 79",
            "Mathieu DUBREUIL · 79",
        }

        commercial_selector.select("Mathieu DUBREUIL · 79").run()
        assert any(
            "2 entreprises dans ce portefeuille" in caption.value
            for caption in app.caption
        )
    finally:
        for suffix in ("", "-wal", "-shm"):
            storage_path.with_name(storage_path.name + suffix).unlink(missing_ok=True)


def test_admin_account_can_access_portfolio_import(monkeypatch) -> None:
    st.cache_data.clear()
    st.cache_resource.clear()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_USERNAME", "collaborateur-test")
    monkeypatch.setenv("AUTH_PASSWORD", "mot-de-passe-test-long")
    monkeypatch.setenv("ADMIN_USERNAME", "administrateur-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "mot-de-passe-admin-test-long")
    storage_path = Path(".cache") / f"app-import-test-{uuid4().hex}.sqlite3"
    AppStore(storage_path)
    monkeypatch.setenv("APP_STORAGE_PATH", str(storage_path.resolve()))
    app_path = Path(__file__).parents[1] / "app.py"

    try:
        app = AppTest.from_file(str(app_path), default_timeout=30).run()
        app.text_input[0].input("administrateur-test")
        app.text_input[1].input("mot-de-passe-admin-test-long")
        app.button[0].click().run()

        assert not app.exception
        assert any("Administrateur" in caption.value for caption in app.caption)
        assert len(app.file_uploader) == 1
        assert app.radio(key="access_atc_mode").value == "Saisie manuelle"
        app.text_input(key="access_principal_id").input("oid-before-import")
        app.text_input(key="access_display_name").input("Nouveau collaborateur")
        app.text_input(key="access_atc_code").input("10-03-99")
        app.text_input(key="access_atc_name").input("Nouveau commercial")
        app.button(key="save_access_profile").click().run()
        assert not app.exception
        profile = AppStore(storage_path).load_access_profile("oid-before-import")
        assert profile is not None
        assert profile.atc_code == "10-03-99"
        assert profile.atc_name == "Nouveau commercial"
    finally:
        for suffix in ("", "-wal", "-shm"):
            storage_path.with_name(storage_path.name + suffix).unlink(missing_ok=True)


@pytest.fixture
def regional_app(monkeypatch):
    st.cache_data.clear()
    st.cache_resource.clear()
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_USERNAME", "")
    monkeypatch.setenv("AUTH_PASSWORD", "")
    monkeypatch.setenv("ADMIN_USERNAME", "test-admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-secret")
    monkeypatch.setenv("AZURE_MAPS_SUBSCRIPTION_KEY", "")
    monkeypatch.setenv("AZURE_MAPS_KEY", "")
    monkeypatch.setenv("LOCAL_TEST_ACCOUNTS_JSON", json.dumps([
        {"username": "test-rag", "password": "test-secret", "role": "rag",
         "rag_email": "rag@example.test"},
        {"username": "test-dr", "password": "test-secret", "role": "dr", "ugs": ["20", "24"]},
        {"username": "test-atc", "password": "test-secret", "role": "atc", "atc_code": "20-01-001"},
    ]))
    storage_path = Path(".cache") / f"regional-map-test-{uuid4().hex}.sqlite3"
    store = AppStore(storage_path)
    clients = _portfolio_for_local_test_accounts()
    clients["ug"] = ["20", "24", "40"]
    clients["agency"] = ["20-01", "24-01", "40-01"]
    clients["salesperson_code"] = ["20-01-001", "24-01-002", "40-01-003"]
    clients["rag_email"] = ["rag@example.test", "rag@example.test", "other@example.test"]
    store.save_clients(clients, source_name="regions.xlsx", imported_by="Test")
    for profile in (
        UserAccessProfile("dr-ouest", "DR Ouest", "dr", ugs=("20", "24")),
        UserAccessProfile("dr-idf", "DR IDF", "dr", ugs=("40",)),
    ):
        store.save_access_profile(profile, updated_by="Test")
    monkeypatch.setenv("APP_STORAGE_PATH", str(storage_path.resolve()))

    def login(username: str) -> AppTest:
        app_path = Path(__file__).parents[1] / "app.py"
        app = AppTest.from_file(str(app_path), default_timeout=30).run()
        app.text_input[0].input(username)
        app.text_input[1].input("test-secret")
        app.button[0].click().run()
        assert not app.exception
        assert app.toggle[0].value is False
        assert not app.get("deck_gl_json_chart")
        app.toggle[0].set_value(True).run()
        return app

    try:
        yield login
    finally:
        for suffix in ("", "-wal", "-shm"):
            storage_path.with_name(storage_path.name + suffix).unlink(missing_ok=True)


def _map_labels(app: AppTest) -> set[str]:
    chart = app.get("deck_gl_json_chart")[0]
    data = json.loads(chart.proto.json)["layers"][0]["data"]
    return {point["label"] for point in data}


def test_admin_can_choose_a_commercial_region_and_keep_it_when_switching_views(regional_app) -> None:
    app = regional_app("test-admin")
    scope = next(radio for radio in app.radio if radio.label == "Périmètre du calque")
    assert scope.value == SCOPE_REGION
    assert _map_labels(app) == {"Sarah client", "Meline client", "Aurelie client"}

    regions = next(item for item in app.multiselect if item.label == "Régions commerciales à afficher")
    assert len(regions.options) == 2
    regions.set_value(["20-24"]).run()
    assert _map_labels(app) == {"Sarah client", "Meline client"}

    next(item for item in app.radio if item.label == "Périmètre du calque").set_value(SCOPE_COMMERCIAL).run()
    next(item for item in app.selectbox if item.label == "Commercial").select("s.edir@litt.fr").run()
    assert _map_labels(app) == {"Sarah client"}
    next(item for item in app.selectbox if item.label == "Commercial").select("m.trillaudstbrieuc@litt.fr").run()
    assert _map_labels(app) == {"Meline client"}

    next(item for item in app.radio if item.label == "Périmètre du calque").set_value(SCOPE_REGION).run()
    assert _map_labels(app) == {"Sarah client", "Meline client"}
    app.toggle[0].set_value(False).run()
    assert not app.get("deck_gl_json_chart")
    app.toggle[0].set_value(True).run()
    assert _map_labels(app) == {"Sarah client", "Meline client"}


def test_rag_can_filter_portfolios_and_agencies_without_seeing_other_accounts(regional_app) -> None:
    app = regional_app("test-rag")
    scope = next(item for item in app.radio if item.label == "Périmètre du calque")
    assert scope.value == SCOPE_PORTFOLIOS
    assert SCOPE_REGION not in scope.options
    portfolios = next(item for item in app.multiselect if item.label == "Portefeuilles à afficher")
    assert len(portfolios.options) == 2
    assert all("Aurelie" not in value for value in portfolios.options)
    portfolios.set_value(["m.trillaudstbrieuc@litt.fr"]).run()
    assert _map_labels(app) == {"Meline client"}

    next(item for item in app.radio if item.label == "Périmètre du calque").set_value(SCOPE_AGENCY).run()
    agencies = next(item for item in app.multiselect if item.label == "Agences à afficher")
    assert set(agencies.options) == {"20-01", "24-01"}
    agencies.set_value(["20-01"]).run()
    assert _map_labels(app) == {"Sarah client"}

    next(item for item in app.radio if item.label == "Périmètre du calque").set_value(SCOPE_PORTFOLIOS).run()
    assert _map_labels(app) == {"Meline client"}
    next(item for item in app.radio if item.label == "Périmètre du calque").set_value(SCOPE_ALL).run()
    assert _map_labels(app) == {"Sarah client", "Meline client"}


def test_dr_only_sees_its_commercial_region(regional_app) -> None:
    app = regional_app("test-dr")
    assert next(item for item in app.radio if item.label == "Périmètre du calque").value == SCOPE_REGION
    regions = next(item for item in app.multiselect if item.label == "Régions commerciales à afficher")
    assert len(regions.options) == 1
    assert "20, 24" in regions.options[0]
    assert _map_labels(app) == {"Sarah client", "Meline client"}

    regions.set_value([]).run()
    assert not app.exception
    assert not app.get("deck_gl_json_chart")
    app.run()
    assert not app.get("deck_gl_json_chart")


def test_atc_only_gets_its_own_portfolio_without_extra_scope_controls(regional_app) -> None:
    app = regional_app("test-atc")
    assert all(item.label != "Périmètre du calque" for item in app.radio)
    assert _map_labels(app) == {"Sarah client"}


def test_admin_can_prepare_and_update_an_atc_not_yet_in_the_portfolio(regional_app) -> None:
    app = regional_app("test-admin")
    app.radio(key="access_atc_mode").set_value("Saisie manuelle").run()
    app.text_input(key="access_principal_id").input("oid-new-atc")
    app.text_input(key="access_display_name").input("Nouveau collaborateur")
    app.text_input(key="access_atc_code").input(" 99-01-004 ")
    app.run()
    assert app.button(key="save_access_profile").disabled

    app.text_input(key="access_atc_name").input(" Nouveau commercial ").run()
    assert not app.button(key="save_access_profile").disabled
    app.button(key="save_access_profile").click().run()
    assert not app.exception
    store = AppStore(load_settings().app_storage_path)
    profile = store.load_access_profile("oid-new-atc")
    assert profile is not None
    assert profile.atc_code == "99-01-004"
    assert profile.atc_name == "Nouveau commercial"
    assert profile.atc_email is None

    clients, _ = store.load_clients()
    user = AuthenticatedUser("Nouveau collaborateur", "entra", "atc", "oid-new-atc")
    with pytest.raises(AccessDeniedError, match="Aucun client"):
        authorize_portfolio(clients, user, profile)

    new_client = clients.iloc[:1].copy()
    new_client["client_id"] = "NEW-CLIENT"
    new_client["salesperson_code"] = "99-01-004"
    new_client["salesperson"] = "Nouveau commercial"
    new_client["salesperson_email"] = "new@example.test"
    updated_clients = pd.concat([clients, new_client], ignore_index=True)
    authorized = authorize_portfolio(updated_clients, user, profile)
    assert authorized.clients["client_id"].tolist() == ["NEW-CLIENT"]

    app.text_input(key="access_atc_code").input("99-01-005")
    app.text_input(key="access_atc_name").input("Autre commercial")
    app.text_input(key="access_atc_email").input("OTHER@example.test")
    app.button(key="save_access_profile").click().run()
    assert not app.exception
    updated = store.load_access_profile("oid-new-atc")
    assert (updated.atc_code, updated.atc_name, updated.atc_email) == (
        "99-01-005", "Autre commercial", "other@example.test",
    )


def test_existing_commercial_selection_keeps_matching_code_name_and_email(regional_app) -> None:
    app = regional_app("test-admin")
    assert app.radio(key="access_atc_mode").value == "Commercial existant"
    selector = app.selectbox(key="access_atc_scope")
    option_index = next(
        index for index, label in enumerate(selector.options) if "Sarah EDIR" in label
    )
    selector.select_index(option_index).run()
    app.text_input(key="access_principal_id").input("oid-existing-atc")
    app.text_input(key="access_display_name").input("Sarah")
    app.button(key="save_access_profile").click().run()

    assert not app.exception
    profile = AppStore(load_settings().app_storage_path).load_access_profile("oid-existing-atc")
    assert (profile.atc_code, profile.atc_name, profile.atc_email) == (
        "20-01-001", "Sarah EDIR", "s.edir@litt.fr",
    )


def _import_workbook(*, embedded: bool, invalid_export: bool = False) -> bytes:
    workbook = BytesIO()
    records = [
        ("admin", "oid-import-admin", "Admin", "admin@example.test"),
        ("atc", "oid-import-atc", "Sarah EDIR", "s.edir@litt.fr"),
        ("rag", "oid-import-rag", "RAG", "c.vernay@litt.fr"),
        ("dr", "oid-import-dr", "DR", "dr@example.test"),
    ]
    users = pd.DataFrame([
        {"Role Entra": role, "Object ID Entra a restituer": oid, "Nom affiche": name,
         "UPN ou e-mail": email, "Code Region": "31-22" if role == "dr" else ""}
        for role, oid, name, email in records
    ])
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        _portfolio_for_local_test_accounts().to_excel(writer, sheet_name="Listing client", index=False)
        users.to_excel(writer, sheet_name="Utilisateurs", index=False)
        if embedded:
            for role, oid, name, email in records:
                record = {"id": oid, "displayName": name, "mail": email}
                if invalid_export and role == "atc":
                    record["app_role"] = "admin"
                pd.DataFrame([record]).to_excel(writer, sheet_name=role.upper(), index=False)
    return workbook.getvalue()


def _upload_import_workbook(monkeypatch, payload: bytes) -> None:
    original = st.file_uploader
    uploaded = SimpleNamespace(name="Listing_client_avec_comptes.xlsx", getvalue=lambda: payload)

    def uploader(label, *args, **kwargs):
        if label == "Importer et remplacer le portefeuille d'entreprises":
            return uploaded
        return original(label, *args, **kwargs)

    monkeypatch.setattr(st, "file_uploader", uploader)


@pytest.mark.parametrize("embedded", [True, False])
def test_import_uses_embedded_exports_or_users_ids_only_when_confirmed(regional_app, monkeypatch, embedded) -> None:
    _upload_import_workbook(monkeypatch, _import_workbook(embedded=embedded))
    app = regional_app("test-admin")
    source = next(item for item in app.radio if item.label == "Source des membres Entra")
    assert source.value == (
        "Feuilles ADMIN, ATC, DR et RAG du classeur" if embedded
        else "Object ID renseignés dans Utilisateurs"
    )
    assert not app.file_uploader  # Aucun export CSV supplémentaire demandé.
    store = AppStore(load_settings().app_storage_path)
    assert store.load_access_profile("oid-import-atc") is None
    button = next(item for item in app.button if item.label.startswith("Enregistrer ce portefeuille"))
    assert "appliquer 3 habilitation(s)" in button.label
    assert not button.disabled
    button.click().run()

    assert not app.exception
    assert store.load_access_profile("oid-import-atc").atc_email == "s.edir@litt.fr"
    assert store.load_access_profile("oid-import-rag").rag_email == "c.vernay@litt.fr"
    assert store.load_access_profile("oid-import-dr").ugs == ("31", "22")
    assert store.load_access_profile("oid-import-admin") is None


def test_invalid_embedded_export_blocks_import_until_explicit_opt_out(regional_app, monkeypatch) -> None:
    _upload_import_workbook(monkeypatch, _import_workbook(embedded=True, invalid_export=True))
    app = regional_app("test-admin")
    button = next(item for item in app.button if item.label.startswith("Enregistrer ce portefeuille"))
    assert button.disabled
    assert any("rôle différent" in item.value for item in app.error)
    assert AppStore(load_settings().app_storage_path).load_access_profile("oid-import-atc") is None
    source = next(item for item in app.radio if item.label == "Source des membres Entra")
    source.set_value("Ne pas modifier les habilitations").run()
    button = next(item for item in app.button if item.label.startswith("Enregistrer ce portefeuille"))
    assert not button.disabled


def test_admin_can_see_which_portfolios_a_rag_will_receive(regional_app) -> None:
    app = regional_app("test-admin")
    app.radio(key="access_role").set_value("RAG").run()
    selector = app.selectbox(key="access_rag_scope")
    index = next(i for i, label in enumerate(selector.options) if label.endswith(" · rag@example.test"))
    selector.select_index(index).run()
    assert any("2 portefeuille(s) et 2 entreprise(s)" in item.value for item in app.caption)
    overviews = [item.value for item in app.dataframe if "Entreprises" in item.value.columns]
    assert any(set(frame["Commercial"]) == {"Sarah EDIR", "Meline TRILLAUD"} for frame in overviews)
    app.text_input(key="access_principal_id").input("oid-rag-summary")
    app.text_input(key="access_display_name").input("RAG Summary")
    app.button(key="save_access_profile").click().run()
    assert not app.exception
    table = next(item.value for item in app.dataframe if "Périmètre RAG" in item.value.columns)
    row = table.loc[table["Object ID Entra"].eq("oid-rag-summary")].iloc[0]
    assert row["Périmètre RAG"] == "2 portefeuille(s) · 2 entreprise(s)"


def test_atc_with_two_portfolios_can_choose_between_them(regional_app, monkeypatch) -> None:
    store = AppStore(load_settings().app_storage_path)
    clients, _ = store.load_clients()
    clients.loc[:1, "salesperson_email"] = "multi@example.test"
    store.save_clients(clients, source_name="multi.xlsx", imported_by="Test")
    store.save_access_profile(UserAccessProfile(
        "local:test-multi", "Multiple portfolios", "atc", atc_email="multi@example.test",
    ), updated_by="Test")
    monkeypatch.setenv("AUTH_USERNAME", "test-multi")
    monkeypatch.setenv("AUTH_PASSWORD", "test-secret")
    app = regional_app("test-multi")
    selector = next(item for item in app.selectbox if item.label == "Commercial")
    assert not selector.disabled
    assert len(selector.options) == 2
    assert all("Aurelie" not in label for label in selector.options)
    selector.select_index(0).run()
    first = _map_labels(app)
    next(item for item in app.selectbox if item.label == "Commercial").select_index(1).run()
    second = _map_labels(app)
    assert first != second
    assert first | second == {"Sarah client", "Meline client"}
