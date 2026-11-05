from __future__ import annotations

import math
import re

import pandas as pd

# Enveloppes de plausibilité (sud, nord, ouest, est), pas des frontières administratives.
# Le code postal choisit l'enveloppe : un client métropolitain ne peut pas être validé
# par sa seule présence dans un territoire français d'outre-mer.
_METROPOLITAN_BOUNDS = (41.0, 51.6, -5.6, 10.0)
_MONACO_BOUNDS = (43.70, 43.78, 7.38, 7.46)
_FRENCH_COUNTRIES = {"", "france", "fr", "fra"}
_FOREIGN_BOUNDS = {
    "italie": (35.0, 48.0, 6.0, 19.0),
    "italy": (35.0, 48.0, 6.0, 19.0),
    "it": (35.0, 48.0, 6.0, 19.0),
    "luxembourg": (49.4, 50.3, 5.6, 6.6),
    "lu": (49.4, 50.3, 5.6, 6.6),
    "portugal": (30.0, 43.0, -32.0, -6.0),
    "pt": (30.0, 43.0, -32.0, -6.0),
}
_OVERSEAS_BOUNDS = {
    "971": (15.7, 18.2, -63.3, -60.9),
    "972": (14.3, 15.0, -61.3, -60.7),
    "973": (2.0, 6.0, -55.0, -51.0),
    "974": (-21.5, -20.8, 55.1, 55.9),
    "975": (46.7, 47.2, -56.6, -56.0),
    "976": (-13.1, -12.5, 44.9, 45.4),
    "977": (17.8, 18.0, -63.0, -62.7),
    "978": (18.0, 18.2, -63.3, -62.9),
    "984": (-51.0, -11.0, 39.0, 78.0),
    "986": (-14.5, -13.0, -178.3, -175.8),
    "987": (-28.0, -7.0, -155.0, -134.0),
    "988": (-23.0, -18.0, 158.0, 173.0),
}


def _text(value: object) -> str:
    return "" if pd.isna(value) else str(value).strip()


def normalize_postal_code(value: object, country: object = "France") -> str:
    code = re.sub(r"\.0$", "", _text(value))
    # Excel peut supprimer le zéro initial (01000 -> 1000).
    if _text(country).casefold() in _FRENCH_COUNTRIES and re.fullmatch(r"[0-9]{4,5}", code):
        return code.zfill(5)
    return code


def postal_code_from_address(address: str) -> str:
    # Les adresses normalisées séparent le CP par une virgule. On accepte aussi
    # « 12 rue du Test 14000 Caen » pour les rendez-vous saisis à la main.
    matches = re.findall(r"(?<![0-9])([0-9]{5})(?![0-9])", address)
    return matches[-1] if matches else ""


def expected_country(postal_code: object, country: object = "", city: object = "") -> str:
    code = normalize_postal_code(postal_code, country)
    if code.startswith("980") or _text(city).casefold() == "monaco":
        return "Monaco"
    if _text(country).casefold() not in _FRENCH_COUNTRIES:
        return _text(country)
    if re.fullmatch(r"[0-9]{5}", code):
        # Règle métier du portefeuille Opti Route, pas une règle postale universelle.
        return "France"
    return _text(country) or "France"


def coordinates_match_address(
    latitude: object,
    longitude: object,
    *,
    postal_code: object = "",
    country: object = "",
    city: object = "",
    address: str = "",
) -> bool:
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return False

    bounds = _address_bounds(postal_code, country, city, address)
    return not bounds or any(
        south <= lat <= north and west <= lon <= east for south, north, west, east in bounds
    )


def _address_bounds(
    postal_code: object = "", country: object = "", city: object = "", address: str = ""
) -> list[tuple[float, float, float, float]]:
    code = normalize_postal_code(postal_code, country) or postal_code_from_address(address)
    country_name = _text(country).casefold()
    is_monaco = (
        code.startswith("980")
        or country_name in {"monaco", "mc", "mco"}
        or _text(city).casefold() == "monaco"
        or (
            not code
            and re.search(r"(?:^|,)\s*monaco(?:,|\s*$)", address, flags=re.IGNORECASE) is not None
        )
    )
    if is_monaco:
        return [_MONACO_BOUNDS]
    elif country_name not in _FRENCH_COUNTRIES:
        return [_FOREIGN_BOUNDS[country_name]] if country_name in _FOREIGN_BOUNDS else []
    for name, bounds in _FOREIGN_BOUNDS.items():
        if re.search(rf"(?:^|,)\s*{name}\s*$", address, flags=re.IGNORECASE):
            return [bounds]
    if re.fullmatch(r"[0-9]{5}", code):
        return [_OVERSEAS_BOUNDS.get(code[:3], _METROPOLITAN_BOUNDS)]
    elif country_name in {"france", "fr", "fra"} or re.search(
        r"\bfrance\s*$", address, flags=re.IGNORECASE
    ):
        return [_METROPOLITAN_BOUNDS, *_OVERSEAS_BOUNDS.values()]
    return []


def geocoding_bbox(address: str) -> str | None:
    bounds = _address_bounds(address=address)
    if len(bounds) != 1:
        return None
    south, north, west, east = bounds[0]
    return f"{west},{south},{east},{north}"


def geocoding_query(address: str) -> str:
    if postal_code_from_address(address).startswith("980"):
        # Répare aussi les anciennes adresses enregistrées « Monaco, France ».
        address = re.sub(r",?\s*(?:France|Monaco)\s*$", "", address, flags=re.IGNORECASE)
        return f"{address.rstrip(', ')}, Monaco"
    return address


def sanitize_client_coordinates(clients: pd.DataFrame) -> pd.DataFrame:
    """Efface les deux coordonnées si la position contredit l'adresse attendue."""
    result = clients.copy()
    for column in ("latitude", "longitude"):
        result[column] = pd.to_numeric(
            result[column].astype("string").str.replace(",", ".", regex=False), errors="coerce"
        ).astype("float64")
    context = result.reindex(columns=["postal_code", "country", "city", "full_address"])
    valid = pd.Series(
        [
            coordinates_match_address(
                lat, lon, postal_code=code, country=country, city=city, address=_text(address)
            )
            for lat, lon, (code, country, city, address) in zip(
                result["latitude"],
                result["longitude"],
                context.itertuples(index=False, name=None),
                strict=True,
            )
        ],
        index=result.index,
        dtype=bool,
    )
    result.attrs["rejected_coordinate_count"] = int(
        (result[["latitude", "longitude"]].notna().any(axis=1) & ~valid).sum()
    )
    result.loc[~valid, ["latitude", "longitude"]] = float("nan")
    return result
