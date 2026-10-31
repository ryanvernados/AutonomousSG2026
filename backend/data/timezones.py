"""Airport to IANA time zone assignment.

airports.csv has no time zone column, so each airport is assigned an IANA zone
from its state plus a per-airport override where a state spans zones. Eastern
airports in Indiana and Kentucky use America/New_York, which has the same 2015
offsets and DST dates as America/Indiana/Indianapolis and America/Kentucky/Louisville.

Provenance: zone names and 2015 offset rules come from the IANA tz database
(pinned tzdata package). The per-airport county/zone assignment below was curated
by hand from the airport's location and needs review before it counts as SOURCED;
it is reported as source_type SOURCED_UNREVIEWED in the manifest.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

STATE_DEFAULT_ZONE = {
    # Eastern
    **{s: "America/New_York" for s in (
        "CT", "DE", "FL", "GA", "IN", "KY", "MA", "MD", "ME", "MI", "NC", "NH",
        "NJ", "NY", "OH", "PA", "RI", "SC", "VA", "VT", "WV",
    )},
    # Central
    **{s: "America/Chicago" for s in (
        "AL", "AR", "IA", "IL", "KS", "LA", "MN", "MO", "MS", "ND", "NE", "OK",
        "SD", "TN", "TX", "WI",
    )},
    # Mountain
    **{s: "America/Denver" for s in ("CO", "MT", "NM", "UT", "WY")},
    "ID": "America/Boise",
    "AZ": "America/Phoenix",
    # Pacific
    **{s: "America/Los_Angeles" for s in ("CA", "NV", "OR", "WA")},
    "AK": "America/Anchorage",
    "HI": "Pacific/Honolulu",
    "PR": "America/Puerto_Rico",
    "VI": "America/St_Thomas",
    "GU": "Pacific/Guam",
    "AS": "Pacific/Pago_Pago",
}

# Airports in a different zone from their state default.
AIRPORT_ZONE_OVERRIDES = {
    "ELP": "America/Denver",       # El Paso, TX: Mountain
    "PNS": "America/Chicago",      # Pensacola, FL: Central
    "VPS": "America/Chicago",      # Valparaiso, FL: Central
    "ECP": "America/Chicago",      # Panama City, FL: Central
    "EVV": "America/Chicago",      # Evansville, IN: Central
    "PAH": "America/Chicago",      # Paducah, KY: Central
    "IMT": "America/Chicago",      # Iron Mountain, MI: Central
    "CHA": "America/New_York",     # Chattanooga, TN: Eastern
    "TRI": "America/New_York",     # Bristol, TN: Eastern
    "TYS": "America/New_York",     # Knoxville, TN: Eastern
    "DIK": "America/Denver",       # Dickinson, ND: Mountain
    "RAP": "America/Denver",       # Rapid City, SD: Mountain
    "LWS": "America/Los_Angeles",  # Lewiston, ID: Pacific
    "ADK": "America/Adak",         # Adak, AK: Hawaii-Aleutian
}

PROVENANCE = {
    "source_type": "SOURCED_UNREVIEWED",
    "zone_rules": "IANA tz database via the pinned tzdata package",
    "assignment": "State default plus per-airport overrides in backend/data/timezones.py",
}

_cache: dict[str, ZoneInfo] = {}


def zone_name_for(iata: str, state: str) -> str | None:
    return AIRPORT_ZONE_OVERRIDES.get(iata) or STATE_DEFAULT_ZONE.get(state)


def build_zone_table(airports: dict[str, dict]) -> dict[str, str | None]:
    return {code: zone_name_for(code, a["STATE"]) for code, a in airports.items()}


def zone(name: str) -> ZoneInfo:
    if name not in _cache:
        _cache[name] = ZoneInfo(name)
    return _cache[name]
