import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

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
            "director_email": [
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
            if slider.label == "Nombre total de visites"
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
            "Afficher le calque des possibilités"
        ]
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
            "Afficher le calque des possibilités"
        ]
    finally:
        for suffix in ("", "-wal", "-shm"):
            storage_path.with_name(storage_path.name + suffix).unlink(missing_ok=True)


def test_local_director_test_account_uses_its_configured_scope(monkeypatch) -> None:
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
                    "username": "test-directeur",
                    "password": "mot-de-passe-test-long",
                    "display_name": "Test Directeur",
                    "role": "director",
                    "director_email": "c.vernay@litt.fr",
                }
            ]
        ),
    )
    storage_path = Path(".cache") / f"local-director-test-{uuid4().hex}.sqlite3"
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
        app.text_input[0].input("test-directeur")
        app.text_input[1].input("mot-de-passe-test-long")
        app.button[0].click().run()

        assert not app.exception
        assert any(
            "Connecté : Test Directeur · Directeur" in caption.value
            for caption in app.caption
        )
        assert any(
            "Meline TRILLAUD · 22-02-22" in option
            for selectbox in app.selectbox
            if selectbox.label == "Commercial"
            for option in selectbox.options
        )
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
    finally:
        for suffix in ("", "-wal", "-shm"):
            storage_path.with_name(storage_path.name + suffix).unlink(missing_ok=True)
