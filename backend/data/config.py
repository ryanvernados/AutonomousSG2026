"""Paths and fixed selection parameters for the Member 2 data pipeline.

Every numeric value here is a declared simulation rule (source_type SYNTHETIC)
unless stated otherwise. They are written into the source manifest so a run
can be reproduced exactly.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROCESSED_DIR = REPO_ROOT / "data" / "processed"

# Raw Kaggle files stay outside version control. Set SIM_RAW_DATA_DIR or pass --raw-dir.
RAW_DIR_ENV = "SIM_RAW_DATA_DIR"
RAW_FILES = ("flights.csv", "airlines.csv", "airports.csv")
# Optional BTS Master Coordinate export (columns AIRPORT_ID, AIRPORT) placed in the raw
# folder; needed to resolve October 2015 numeric airport IDs.
BTS_AIRPORT_ID_LOOKUP = "airport_id_to_iata.csv"

SOURCE_LINKS = {
    "dataset": "https://www.kaggle.com/datasets/usdot/flight-delays",
    "field_definitions": "https://www.transtats.bts.gov/Fields.asp?gnoyr_VQ=FGJ",
    "bts_master_coordinate": "BTS TranStats Aviation Support Tables, Master Coordinate (https://www.transtats.bts.gov/)",
}

AIRPORT_IATA = "SFO"
AIRPORT_TZ = "America/Los_Angeles"
DATA_YEAR = 2015

# Qualifying-row rule from the delivery plan (minutes, HISTORICAL attributed delay).
QUALIFY_WEATHER_DELAY_GT_MIN = 15
QUALIFY_LATE_AIRCRAFT_DELAY_GT_MIN = 20

# Window selection (SYNTHETIC simulation rules).
WINDOW_MINUTES = 120
INIT_BUFFER_MINUTES = 60
RECOVERY_BUFFER_MINUTES = 60
WINDOW_START_EARLIEST_LOCAL_HOUR = 5
WINDOW_START_LATEST_LOCAL_HOUR = 21

# Context legs outside the slice, used to derive initial gate occupancy and rotations.
CONTEXT_BEFORE_HOURS = 12
CONTEXT_AFTER_HOURS = 6

# Development fixture size (plan: 20 to 40 flights).
DEV_FIXTURE_SIZE = 30

# A resolved local clock time must sit within this many minutes of the value implied
# by the other fields (e.g. scheduled departure + scheduled elapsed time), otherwise
# the record is flagged.
TIME_CONSISTENCY_TOLERANCE_MIN = 5


def raw_dir(cli_value: str | None) -> Path:
    value = cli_value or os.environ.get(RAW_DIR_ENV)
    if not value:
        raise SystemExit(
            f"Raw data directory not set. Pass --raw-dir or set {RAW_DIR_ENV} "
            "to the folder holding flights.csv, airlines.csv and airports.csv."
        )
    path = Path(value)
    missing = [name for name in RAW_FILES if not (path / name).is_file()]
    if missing:
        raise SystemExit(f"Missing raw files in {path}: {', '.join(missing)}")
    return path


def selection_parameters() -> dict:
    return {
        "airport_iata": AIRPORT_IATA,
        "airport_timezone": AIRPORT_TZ,
        "qualify_rule": (
            f"WEATHER_DELAY > {QUALIFY_WEATHER_DELAY_GT_MIN} min or "
            f"LATE_AIRCRAFT_DELAY > {QUALIFY_LATE_AIRCRAFT_DELAY_GT_MIN} min"
        ),
        "window_minutes": WINDOW_MINUTES,
        "init_buffer_minutes": INIT_BUFFER_MINUTES,
        "recovery_buffer_minutes": RECOVERY_BUFFER_MINUTES,
        "window_start_local_hours": [WINDOW_START_EARLIEST_LOCAL_HOUR, WINDOW_START_LATEST_LOCAL_HOUR],
        "dev_fixture_size": DEV_FIXTURE_SIZE,
        "time_consistency_tolerance_minutes": TIME_CONSISTENCY_TOLERANCE_MIN,
        "source_type": "SYNTHETIC",
    }
