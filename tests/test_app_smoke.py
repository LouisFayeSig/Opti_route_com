from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_page_loads_without_exception() -> None:
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=30).run()
    assert not app.exception
    assert app.title[0].value == "🧭 Opti Route Com"

