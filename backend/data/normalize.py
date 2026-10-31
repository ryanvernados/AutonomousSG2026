"""Normalize one flights.csv row into a timezone-aware UTC flight leg.

BTS clock fields are local HHMM strings without a date. The date columns give the
scheduled departure date at the origin. Every other clock field is resolved to the
calendar day that brings it closest to the instant implied by the related duration
fields (scheduled elapsed time, delay, taxi and air time). Records that cannot be
resolved, or resolve inconsistently, keep their original values and carry flags.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from . import config
from .timezones import zone

UTC = timezone.utc

CLOCK_FIELDS = ("SCHEDULED_DEPARTURE", "DEPARTURE_TIME", "WHEELS_OFF", "WHEELS_ON",
                "SCHEDULED_ARRIVAL", "ARRIVAL_TIME")
DELAY_FIELDS = ("AIR_SYSTEM_DELAY", "SECURITY_DELAY", "AIRLINE_DELAY",
                "LATE_AIRCRAFT_DELAY", "WEATHER_DELAY")


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat(timespec="seconds")[:-6] + "Z" if dt else None


def to_float(value: str) -> float | None:
    value = value.strip()
    return float(value) if value else None


def parse_hhmm(value: str) -> tuple[int | None, list[str]]:
    """Return minutes after local midnight. '2400' is 1440 (midnight ending the day)."""
    value = value.strip()
    if not value:
        return None, []
    if not value.isdigit() or len(value) > 4:
        return None, ["HHMM_INVALID"]
    hh, mm = divmod(int(value), 100)
    if mm > 59 or hh > 24 or (hh == 24 and mm != 0):
        return None, ["HHMM_INVALID"]
    return hh * 60 + mm, (["HHMM_2400"] if hh == 24 else [])


@lru_cache(maxsize=1 << 18)
def local_to_utc(day: date, minutes: int, tz_name: str) -> tuple[datetime, tuple[str, ...]]:
    # Cached: schedules repeat the same (date, clock time, zone) many times.
    tz = zone(tz_name)
    naive = datetime(day.year, day.month, day.day) + timedelta(minutes=minutes)
    aware = naive.replace(tzinfo=tz)
    if aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive:
        return aware.astimezone(UTC), ("LOCAL_TIME_NONEXISTENT_DST",)
    if aware.utcoffset() != naive.replace(tzinfo=tz, fold=1).utcoffset():
        return aware.astimezone(UTC), ("LOCAL_TIME_AMBIGUOUS_DST",)
    return aware.astimezone(UTC), ()


def resolve_near(minutes: int, tz_name: str, expected: datetime
                 ) -> tuple[datetime, float, tuple[str, ...]]:
    """Pick the calendar day for a local clock time that lands closest to `expected`.

    Start on the local date of `expected`; the nearest instance is at most one day away,
    so shift a day only when the first candidate is more than 12 hours off.
    """
    day = expected.astimezone(zone(tz_name)).date()
    utc, flags = local_to_utc(day, minutes, tz_name)
    offset_min = (utc - expected).total_seconds() / 60
    if abs(offset_min) > 720:
        day += timedelta(days=-1 if offset_min > 0 else 1)
        utc, flags = local_to_utc(day, minutes, tz_name)
    return utc, abs((utc - expected).total_seconds()) / 60, flags


def normalize_row(row: dict, row_number: int, file_sha256: str,
                  airport_zones: dict[str, str | None],
                  numeric_lookup: dict[str, str] | None = None) -> dict:
    flags: list[str] = []
    tol = config.TIME_CONSISTENCY_TOLERANCE_MIN

    def airport(raw: str) -> str | None:
        raw = raw.strip()
        if raw.isdigit():
            if numeric_lookup and raw in numeric_lookup:
                flags.append("AIRPORT_ID_FROM_BTS_LOOKUP")
                return numeric_lookup[raw]
            flags.append("NUMERIC_AIRPORT_ID_UNRESOLVED")
            return None
        if raw not in airport_zones:
            flags.append("AIRPORT_NOT_IN_AIRPORTS_CSV")
            return raw
        return raw

    origin = airport(row["ORIGIN_AIRPORT"])
    destination = airport(row["DESTINATION_AIRPORT"])
    origin_tz = airport_zones.get(origin) if origin else None
    dest_tz = airport_zones.get(destination) if destination else None
    if (origin and not origin_tz) or (destination and not dest_tz):
        flags.append("AIRPORT_TIMEZONE_UNKNOWN")

    cancelled = row["CANCELLED"].strip() == "1"
    diverted = row["DIVERTED"].strip() == "1"
    if cancelled:
        flags.append("CANCELLED")
    if diverted:
        flags.append("DIVERTED")

    nums = {k: to_float(row[k]) for k in (
        "DEPARTURE_DELAY", "TAXI_OUT", "SCHEDULED_TIME", "ELAPSED_TIME", "AIR_TIME",
        "DISTANCE", "TAXI_IN", "ARRIVAL_DELAY", *DELAY_FIELDS)}
    clocks = {}
    for field in CLOCK_FIELDS:
        minutes, f = parse_hhmm(row[field])
        clocks[field] = minutes
        flags.extend(f"{field}:{x}" for x in f)

    times = dict.fromkeys(("scheduled_gate_departure_utc", "scheduled_gate_arrival_utc",
                           "actual_gate_departure_utc", "wheels_off_utc",
                           "wheels_on_utc", "actual_gate_arrival_utc"))
    sched_date = date(int(row["YEAR"]), int(row["MONTH"]), int(row["DAY"]))

    def resolve(field: str, key: str, tz_name: str | None, expected: datetime | None,
                check: bool) -> datetime | None:
        minutes = clocks[field]
        if minutes is None or tz_name is None or expected is None:
            return None
        utc, gap, f = resolve_near(minutes, tz_name, expected)
        flags.extend(f"{field}:{x}" for x in f)
        if check and gap > tol:
            flags.append(f"{field}:INCONSISTENT_WITH_DURATIONS")
        times[key] = utc
        return utc

    sched_dep = None
    if origin_tz and clocks["SCHEDULED_DEPARTURE"] is not None:
        sched_dep, f = local_to_utc(sched_date, clocks["SCHEDULED_DEPARTURE"], origin_tz)
        flags.extend(f"SCHEDULED_DEPARTURE:{x}" for x in f)
        times["scheduled_gate_departure_utc"] = sched_dep

    if sched_dep is not None:
        elapsed = nums["SCHEDULED_TIME"]
        if elapsed is None:
            flags.append("SCHEDULED_TIME_MISSING")
        sched_arr = resolve("SCHEDULED_ARRIVAL", "scheduled_gate_arrival_utc", dest_tz,
                            sched_dep + timedelta(minutes=elapsed or 0), elapsed is not None)
        if sched_arr is not None and sched_arr <= sched_dep:
            flags.append("SCHEDULED_ARRIVAL_NOT_AFTER_DEPARTURE")

        dep_delay = nums["DEPARTURE_DELAY"]
        act_dep = resolve("DEPARTURE_TIME", "actual_gate_departure_utc", origin_tz,
                          sched_dep + timedelta(minutes=dep_delay or 0), dep_delay is not None)
        off = resolve("WHEELS_OFF", "wheels_off_utc", origin_tz,
                      act_dep + timedelta(minutes=nums["TAXI_OUT"] or 0) if act_dep else None,
                      nums["TAXI_OUT"] is not None)
        on = resolve("WHEELS_ON", "wheels_on_utc", dest_tz,
                     off + timedelta(minutes=nums["AIR_TIME"] or 0) if off else None,
                     nums["AIR_TIME"] is not None)
        arr_expected = None
        if sched_arr is not None and nums["ARRIVAL_DELAY"] is not None:
            arr_expected = sched_arr + timedelta(minutes=nums["ARRIVAL_DELAY"])
        elif on is not None:
            arr_expected = on + timedelta(minutes=nums["TAXI_IN"] or 0)
        resolve("ARRIVAL_TIME", "actual_gate_arrival_utc", dest_tz, arr_expected,
                arr_expected is not None)
        if sched_arr is not None and origin_tz and dest_tz:
            dep_local = sched_dep.astimezone(zone(origin_tz)).date()
            if sched_arr.astimezone(zone(dest_tz)).date() != dep_local:
                flags.append("OVERNIGHT_SCHEDULED")
    elif clocks["SCHEDULED_DEPARTURE"] is not None or origin_tz is None:
        flags.append("SCHEDULED_DEPARTURE_UNRESOLVED")

    tail = row["TAIL_NUMBER"].strip() or None
    if tail is None:
        flags.append("TAIL_NUMBER_MISSING")

    return {
        "flight_id": f"{file_sha256[:12]}:{row_number}",
        "source_row_id": f"flights.csv:{row_number}",
        "airline": row["AIRLINE"].strip(),
        "flight_number": row["FLIGHT_NUMBER"].strip(),
        "tail_number": tail,
        "origin": origin,
        "destination": destination,
        "origin_timezone": origin_tz,
        "destination_timezone": dest_tz,
        **{k: iso(v) for k, v in times.items()},
        "cancelled": cancelled,
        "cancellation_reason": row["CANCELLATION_REASON"].strip() or None,
        "diverted": diverted,
        "durations_min": {k.lower(): nums[k] for k in (
            "DEPARTURE_DELAY", "TAXI_OUT", "SCHEDULED_TIME", "ELAPSED_TIME", "AIR_TIME",
            "TAXI_IN", "ARRIVAL_DELAY")},
        "distance_mi": nums["DISTANCE"],
        "attributed_delay_min": {k.lower(): nums[k] for k in DELAY_FIELDS},
        "source_values": {k: row[k] for k in row},
        "quality_flags": sorted(set(flags)),
        "resolved": not any(f in flags for f in (
            "NUMERIC_AIRPORT_ID_UNRESOLVED", "AIRPORT_TIMEZONE_UNKNOWN",
            "SCHEDULED_DEPARTURE_UNRESOLVED")) and times["scheduled_gate_departure_utc"] is not None,
    }


def qualifies(leg: dict) -> bool:
    d = leg["attributed_delay_min"]
    return ((d["weather_delay"] or 0) > config.QUALIFY_WEATHER_DELAY_GT_MIN
            or (d["late_aircraft_delay"] or 0) > config.QUALIFY_LATE_AIRCRAFT_DELAY_GT_MIN)
