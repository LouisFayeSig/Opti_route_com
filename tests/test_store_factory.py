from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from opti_route.config import Settings
from opti_route.storage import AppStore
from opti_route.store_factory import create_app_store


def test_factory_keeps_sqlite_for_local_development() -> None:
    store_path = Path(".cache") / f"factory-test-{uuid4().hex}.sqlite3"
    settings = Settings(
        azure_maps_endpoint="https://atlas.microsoft.com",
        azure_maps_key=None,
        geocode_cache_path=Path(".cache/geocode.sqlite3"),
        app_storage_path=store_path,
    )

    try:
        store = create_app_store(settings)

        assert isinstance(store, AppStore)
    finally:
        for suffix in ("", "-wal", "-shm"):
            store_path.with_name(store_path.name + suffix).unlink(missing_ok=True)
