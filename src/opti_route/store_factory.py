from __future__ import annotations

from .config import Settings
from .storage import ApplicationStore, AppStore, StorageError


def create_app_store(settings: Settings) -> ApplicationStore:
    if settings.app_storage_backend == "sqlite":
        return AppStore(settings.app_storage_path)
    if settings.app_storage_backend == "azure_blob":
        from .azure_storage import AzureBlobAppStore

        return AzureBlobAppStore(
            account_url=settings.azure_storage_account_url,
            container_name=settings.azure_storage_container,
            connection_string=settings.azure_storage_connection_string,
        )
    raise StorageError("APP_STORAGE_BACKEND doit valoir sqlite ou azure_blob.")
