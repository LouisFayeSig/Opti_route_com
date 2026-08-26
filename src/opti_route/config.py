from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    azure_maps_endpoint: str
    azure_maps_key: str | None
    geocode_cache_path: Path
    clients_file: Path | None
    request_timeout_seconds: float = 30.0

    @property
    def azure_maps_enabled(self) -> bool:
        return bool(self.azure_maps_key)


def load_settings(project_root: Path | None = None) -> Settings:
    root = project_root or Path.cwd()
    load_dotenv(root / ".env", override=False)

    endpoint = (
        os.getenv("AZURE_MAPS_ENDPOINT")
        or os.getenv("AZURE_MAPS_URI")
        or "https://atlas.microsoft.com"
    ).rstrip("/")
    key = os.getenv("AZURE_MAPS_SUBSCRIPTION_KEY") or os.getenv("AZURE_MAPS_KEY")
    configured_file = os.getenv("CLIENTS_FILE")
    clients_file = Path(configured_file) if configured_file else None
    if clients_file and not clients_file.is_absolute():
        clients_file = root / clients_file

    cache_value = os.getenv("GEOCODE_CACHE_PATH", ".cache/geocoding.sqlite3")
    cache_path = Path(cache_value)
    if not cache_path.is_absolute():
        cache_path = root / cache_path

    timeout = float(os.getenv("AZURE_MAPS_TIMEOUT_SECONDS", "30"))
    return Settings(
        azure_maps_endpoint=endpoint,
        azure_maps_key=key.strip() if key else None,
        geocode_cache_path=cache_path,
        clients_file=clients_file,
        request_timeout_seconds=timeout,
    )

