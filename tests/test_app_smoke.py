from pathlib import Path
from uuid import uuid4

import pandas as pd
from streamlit.testing.v1 import AppTest

from opti_route.storage import AppStore


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
    app_path = Path(__file__).parents[1] / "app.py"

    app = AppTest.from_file(str(app_path), default_timeout=30).run()

    assert not app.exception
    assert app.title[0].value == "🧭 Opti Route Com"
    assert "AUTH_USERNAME et AUTH_PASSWORD" in app.error[0].value


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
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_USERNAME", "collaborateur-test")
    monkeypatch.setenv("AUTH_PASSWORD", "mot-de-passe-test-long")
    monkeypatch.setenv("ADMIN_USERNAME", "administrateur-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "mot-de-passe-admin-test-long")
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
            "Client de départ",
        ]
        assert [multiselect.label for multiselect in app.multiselect] == [
            "Rendez-vous déjà planifiés (facultatif)"
        ]
        assert [radio.label for radio in app.radio][-2:] == ["Point de départ", "Fin de tournée"]
    finally:
        for suffix in ("", "-wal", "-shm"):
            storage_path.with_name(storage_path.name + suffix).unlink(missing_ok=True)


def test_admin_account_can_access_portfolio_import(monkeypatch) -> None:
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_USERNAME", "collaborateur-test")
    monkeypatch.setenv("AUTH_PASSWORD", "mot-de-passe-test-long")
    monkeypatch.setenv("ADMIN_USERNAME", "administrateur-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "mot-de-passe-admin-test-long")
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=30).run()

    app.text_input[0].input("administrateur-test")
    app.text_input[1].input("mot-de-passe-admin-test-long")
    app.button[0].click().run()

    assert not app.exception
    assert any("Administrateur" in caption.value for caption in app.caption)
    assert len(app.file_uploader) == 1
