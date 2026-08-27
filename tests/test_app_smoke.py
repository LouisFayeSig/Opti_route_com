from pathlib import Path

from streamlit.testing.v1 import AppTest


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
