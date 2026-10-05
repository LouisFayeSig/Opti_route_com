from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    azure_maps_endpoint: str
    azure_maps_key: str | None
    geocode_cache_path: Path
    map_renderer: str = "pydeck"
    request_timeout_seconds: float = 30.0
    auth_mode: str = "password"
    auth_username: str | None = None
    auth_password: str | None = None
    admin_username: str | None = None
    admin_password: str | None = None
    admin_emails: tuple[str, ...] = ()
    entra_tenant_id: str | None = None
    entra_atc_role: str = "OptiRoute.ATC"
    entra_director_role: str = "OptiRoute.Director"
    entra_admin_role: str = "OptiRoute.Admin"
    app_storage_backend: str = "sqlite"
    app_storage_path: Path = Path(".cache/opti_route.sqlite3")
    azure_storage_account_url: str | None = None
    azure_storage_container: str = "opti-route-private"
    azure_storage_connection_string: str | None = None

    @property
    def azure_maps_enabled(self) -> bool:
        return bool(self.azure_maps_key)


def _configured_value(
    name: str,
    secrets: Mapping[str, object] | None = None,
    default: str | None = None,
) -> str | None:
    environment_value = os.getenv(name)
    if environment_value is not None:
        return environment_value

    if secrets:
        direct_value = secrets.get(name)
        if direct_value is not None:
            return str(direct_value)

        app_section = secrets.get("app")
        if isinstance(app_section, Mapping):
            section_value = app_section.get(name)
            if section_value is not None:
                return str(section_value)
    return default


def load_settings(
    project_root: Path | None = None,
    secrets: Mapping[str, object] | None = None,
) -> Settings:
    root = project_root or Path.cwd()
    load_dotenv(root / ".env", override=False)

    endpoint = (
        _configured_value("AZURE_MAPS_ENDPOINT", secrets)
        or _configured_value("AZURE_MAPS_URI", secrets)
        or "https://atlas.microsoft.com"
    ).rstrip("/")
    key = _configured_value("AZURE_MAPS_SUBSCRIPTION_KEY", secrets) or _configured_value(
        "AZURE_MAPS_KEY", secrets
    )
    cache_value = _configured_value("GEOCODE_CACHE_PATH", secrets, ".cache/geocoding.sqlite3")
    assert cache_value is not None
    cache_path = Path(cache_value)
    if not cache_path.is_absolute():
        cache_path = root / cache_path

    timeout = float(_configured_value("AZURE_MAPS_TIMEOUT_SECONDS", secrets, "30") or "30")
    map_renderer = (
        (_configured_value("MAP_RENDERER", secrets, "pydeck") or "pydeck").strip().casefold()
    )
    if map_renderer not in {"pydeck", "azure"}:
        map_renderer = "pydeck"
    auth_mode = (
        (_configured_value("AUTH_MODE", secrets, "password") or "password").strip().casefold()
    )
    auth_username = _configured_value("AUTH_USERNAME", secrets)
    auth_password = _configured_value("AUTH_PASSWORD", secrets)
    admin_username = _configured_value("ADMIN_USERNAME", secrets)
    admin_password = _configured_value("ADMIN_PASSWORD", secrets)
    admin_emails_value = _configured_value("ADMIN_EMAILS", secrets, "") or ""
    admin_emails = tuple(
        value.strip().casefold() for value in admin_emails_value.split(",") if value.strip()
    )
    entra_tenant_id = _configured_value("ENTRA_TENANT_ID", secrets)
    entra_atc_role = _configured_value("ENTRA_ROLE_ATC", secrets, "OptiRoute.ATC")
    entra_director_role = _configured_value(
        "ENTRA_ROLE_DIRECTOR", secrets, "OptiRoute.Director"
    )
    entra_admin_role = _configured_value("ENTRA_ROLE_ADMIN", secrets, "OptiRoute.Admin")
    assert entra_atc_role is not None
    assert entra_director_role is not None
    assert entra_admin_role is not None
    configured_entra_roles = (
        entra_atc_role.strip(),
        entra_director_role.strip(),
        entra_admin_role.strip(),
    )
    if not all(configured_entra_roles) or len(
        {value.casefold() for value in configured_entra_roles}
    ) != len(configured_entra_roles):
        raise ValueError("Les trois App Roles Entra doivent être non vides et distincts.")
    storage_backend = (
        (_configured_value("APP_STORAGE_BACKEND", secrets, "sqlite") or "sqlite")
        .strip()
        .casefold()
    )
    if storage_backend not in {"sqlite", "azure_blob"}:
        raise ValueError("APP_STORAGE_BACKEND doit valoir sqlite ou azure_blob.")
    azure_storage_account_url = _configured_value("AZURE_STORAGE_ACCOUNT_URL", secrets)
    azure_storage_container = _configured_value(
        "AZURE_STORAGE_CONTAINER", secrets, "opti-route-private"
    )
    azure_storage_connection_string = _configured_value(
        "AZURE_STORAGE_CONNECTION_STRING", secrets
    )
    assert azure_storage_container is not None
    storage_value = _configured_value("APP_STORAGE_PATH", secrets, ".cache/opti_route.sqlite3")
    assert storage_value is not None
    storage_path = Path(storage_value)
    if not storage_path.is_absolute():
        storage_path = root / storage_path
    return Settings(
        azure_maps_endpoint=endpoint,
        azure_maps_key=key.strip() if key else None,
        geocode_cache_path=cache_path,
        map_renderer=map_renderer,
        request_timeout_seconds=timeout,
        auth_mode=auth_mode,
        auth_username=auth_username.strip() if auth_username else None,
        auth_password=auth_password if auth_password else None,
        admin_username=admin_username.strip() if admin_username else None,
        admin_password=admin_password if admin_password else None,
        admin_emails=admin_emails,
        entra_tenant_id=entra_tenant_id.strip() if entra_tenant_id else None,
        entra_atc_role=entra_atc_role.strip(),
        entra_director_role=entra_director_role.strip(),
        entra_admin_role=entra_admin_role.strip(),
        app_storage_backend=storage_backend,
        app_storage_path=storage_path,
        azure_storage_account_url=(
            azure_storage_account_url.strip() if azure_storage_account_url else None
        ),
        azure_storage_container=azure_storage_container.strip(),
        azure_storage_connection_string=(
            azure_storage_connection_string if azure_storage_connection_string else None
        ),
    )
