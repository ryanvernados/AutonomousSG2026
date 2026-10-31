"""Member 2 ingestion command.

    python -m backend.data.build --raw-dir <folder with the Kaggle CSVs>

pandas reads flights.csv as strings in chunks and keeps the SFO rows with their
1-based data row numbers. Every SFO leg is normalized for the quality report and
per-day statistics. A deterministic rule picks the airport day and the two-hour
window, and the selected rows are exported as the traffic slice, the development
fixture, the traceable qualifying row and the data quality report under
data/processed/.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json

import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, timezones
from .normalize import iso, normalize_row, qualifies
from .timezones import zone

UTC = timezone.utc


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def file_entry(path: Path, rows: int | None = None, sha256: str | None = None) -> dict:
    with path.open(newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
        if rows is None:
            rows = sum(1 for _ in f)
    return {"file": path.name, "sha256": sha256 or sha256_of(path), "bytes": path.stat().st_size,
            "header": header, "data_rows": rows}


def load_numeric_lookup(raw: Path) -> tuple[dict | None, dict | None]:
    """Map BTS numeric AIRPORT_IDs to IATA codes if the Master Coordinate export is supplied.

    Without it, numeric IDs stay unresolved and are never guessed.
    """
    path = raw / config.BTS_AIRPORT_ID_LOOKUP
    if not path.is_file():
        return None, None
    mapping = {r["AIRPORT_ID"].strip(): r["AIRPORT"].strip() for r in read_csv(path)
               if r.get("AIRPORT_ID") and r.get("AIRPORT")}
    return (mapping or None), file_entry(path)


def airport_quality(airports: dict[str, dict], zones: dict[str, str | None]) -> list[dict]:
    issues = []
    for code, a in sorted(airports.items()):
        if not a["LATITUDE"] or not a["LONGITUDE"]:
            issues.append({"airport": code, "issue": "COORDINATES_MISSING"})
        elif float(a["LONGITUDE"]) > 0 and a["STATE"] not in ("GU", "AS"):
            issues.append({"airport": code, "issue": "LONGITUDE_POSITIVE_WEST_EXPECTED"})
        elif a["STATE"] == "GU" and float(a["LONGITUDE"]) < 0:
            issues.append({"airport": code, "issue": "LONGITUDE_SIGN_SUSPECT",
                           "detail": "Guam lies east of 180; airports.csv gives a west longitude"})
        if zones.get(code) is None:
            issues.append({"airport": code, "issue": "TIMEZONE_UNASSIGNED"})
    return issues


def sfo_movement(leg: dict) -> tuple[str, str | None]:
    if leg["destination"] == config.AIRPORT_IATA:
        return "ARRIVAL", leg["scheduled_gate_arrival_utc"]
    return "DEPARTURE", leg["scheduled_gate_departure_utc"]


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)  # ISO 8601 with Z; C parser, much faster than strptime


def load_sfo_rows(flights_path: Path, numeric_lookup: dict | None) -> tuple[pd.DataFrame, dict]:
    """Read flights.csv with pandas, keeping SFO rows as strings plus their data row number."""
    sfo = config.AIRPORT_IATA
    stats = {"data_rows": 0, "numeric_airport_rows_by_month": collections.Counter()}
    kept = []
    reader = pd.read_csv(flights_path, dtype=str, keep_default_na=False, chunksize=500_000)
    for chunk in reader:
        chunk.insert(0, "_row_number", chunk.index + 1)
        stats["data_rows"] += len(chunk)
        o, d = chunk["ORIGIN_AIRPORT"], chunk["DESTINATION_AIRPORT"]
        numeric = o.str.isdigit() | d.str.isdigit()
        stats["numeric_airport_rows_by_month"].update(chunk.loc[numeric, "MONTH"].astype(int).tolist())
        if numeric_lookup:
            o, d = o.replace(numeric_lookup), d.replace(numeric_lookup)
        kept.append(chunk[(o == sfo) | (d == sfo)])
    return pd.concat(kept, ignore_index=True), stats


def iter_rows(frame: pd.DataFrame):
    # Object arrays iterate far faster than pandas string arrays row by row.
    columns = [c for c in frame.columns if c != "_row_number"]
    arrays = [frame[c].to_numpy(dtype=object) for c in columns]
    for row_number, *values in zip(frame["_row_number"].tolist(), *arrays):
        yield row_number, dict(zip(columns, values))


def scan(sfo_rows: pd.DataFrame, stats: dict, file_sha: str, zones: dict, numeric_lookup: dict | None):
    stats.update({
        "sfo_rows": len(sfo_rows),
        "sfo_rows_by_month": collections.Counter(sfo_rows["MONTH"].astype(int).tolist()),
        "sfo_flag_counts": collections.Counter(),
        "sfo_unresolved_rows": [],
    })
    # local SFO date -> list of (sfo_movement_utc, row_number, operation, qualifies, cancelled)
    by_day: dict[str, list] = collections.defaultdict(list)
    for row_number, row in iter_rows(sfo_rows):
        leg = normalize_row(row, row_number, file_sha, zones, numeric_lookup)
        stats["sfo_flag_counts"].update(leg["quality_flags"])
        op, when = sfo_movement(leg)
        if not leg["resolved"] or when is None:
            stats["sfo_unresolved_rows"].append(
                {"source_row_id": leg["source_row_id"], "flags": leg["quality_flags"]})
            continue
        local_day = parse_iso(when).astimezone(zone(config.AIRPORT_TZ)).date().isoformat()
        by_day[local_day].append((when, row_number, op, qualifies(leg), leg["cancelled"]))
    return by_day


def read_line(path: Path, line_number: int) -> str:
    with path.open(encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            if i == line_number:
                return line.rstrip("\r\n")
    raise IndexError(line_number)


def select_day(by_day: dict[str, list], excluded_months: set[int]) -> tuple[str, dict]:
    ranking = []
    for day, items in by_day.items():
        if int(day[5:7]) in excluded_months or int(day[:4]) != config.DATA_YEAR:
            continue
        q = sum(1 for i in items if i[3])
        ranking.append((-q, -len(items), day))
    ranking.sort()
    best = ranking[0]
    return best[2], {
        "rule": "Most qualifying SFO rows on the local SFO day; ties by total movements, then earliest date",
        "excluded_months": sorted(excluded_months),
        "candidate_days": len(ranking),
        "days_with_qualifying_rows": sum(1 for r in ranking if r[0] < 0),
        "top_days": [{"date": d, "qualifying_rows": -q, "movements": -n} for q, n, d in ranking[:5]],
    }


def select_window(day: str, items: list) -> tuple[datetime, dict]:
    tz = zone(config.AIRPORT_TZ)
    y, m, d = map(int, day.split("-"))
    scored = []
    for hour in range(config.WINDOW_START_EARLIEST_LOCAL_HOUR, config.WINDOW_START_LATEST_LOCAL_HOUR + 1):
        start = datetime(y, m, d, hour, tzinfo=tz).astimezone(UTC)
        end = start + timedelta(minutes=config.WINDOW_MINUTES)
        inside = [i for i in items if start <= parse_iso(i[0]) < end]
        q = sum(1 for i in inside if i[3])
        scored.append((-q, -len(inside), hour, start))
    scored.sort()
    q, n, hour, start = scored[0]
    return start, {
        "rule": "Whole-hour local start with the most qualifying SFO movements; ties by movements, then earliest",
        "start_local_hour": hour,
        "qualifying_rows_in_window": -q,
        "movements_in_window": -n,
    }


def pick_dev_fixture(core: list[dict]) -> list[dict]:
    """Contiguous run of DEV_FIXTURE_SIZE operating legs with the most qualifying rows."""
    legs = [l for l in core if not l["cancelled"]]
    size = min(config.DEV_FIXTURE_SIZE, len(legs))
    best, best_key = None, None
    for i in range(len(legs) - size + 1):
        run = legs[i:i + size]
        ops = {l["operation"] for l in run}
        if ops != {"ARRIVAL", "DEPARTURE"}:
            continue
        key = (-sum(1 for l in run if l["qualifying"]), i)
        if best_key is None or key < best_key:
            best, best_key = run, key
    return best or legs[:size]


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", help=f"Folder with the Kaggle CSVs (or set {config.RAW_DIR_ENV})")
    parser.add_argument("--out-dir", default=str(config.PROCESSED_DIR))
    args = parser.parse_args(argv)
    raw = config.raw_dir(args.raw_dir)
    out = Path(args.out_dir)

    airports = {a["IATA_CODE"]: a for a in read_csv(raw / "airports.csv")}
    airlines = {a["IATA_CODE"]: a["AIRLINE"] for a in read_csv(raw / "airlines.csv")}
    zones = timezones.build_zone_table(airports)
    numeric_lookup, lookup_entry = load_numeric_lookup(raw)

    flights_path = raw / "flights.csv"
    print("Hashing flights.csv ...")
    flights_sha = sha256_of(flights_path)
    print("Reading flights.csv with pandas ...")
    sfo_rows, stats = load_sfo_rows(flights_path, numeric_lookup)
    print(f"Normalizing {len(sfo_rows)} SFO legs ...")
    by_day = scan(sfo_rows, stats, flights_sha, zones, numeric_lookup)

    excluded = set() if numeric_lookup else set(stats["numeric_airport_rows_by_month"])
    day, day_meta = select_day(by_day, excluded)
    start, window_meta = select_window(day, by_day[day])
    end = start + timedelta(minutes=config.WINDOW_MINUTES)
    buf_start = start - timedelta(minutes=config.INIT_BUFFER_MINUTES)
    buf_end = end + timedelta(minutes=config.RECOVERY_BUFFER_MINUTES)
    print(f"Selected {day}, window {iso(start)} to {iso(end)}")

    wanted, context_rows = {}, {}
    ctx_start = buf_start - timedelta(hours=config.CONTEXT_BEFORE_HOURS)
    ctx_end = buf_end + timedelta(hours=config.CONTEXT_AFTER_HOURS)
    for d in by_day:
        for when, row_number, op, _, cancelled in by_day[d]:
            moment = parse_iso(when)
            if buf_start <= moment < buf_end:
                wanted[row_number] = moment
            elif ctx_start <= moment < ctx_end:
                context_rows[row_number] = (op, when, cancelled)

    legs = []
    selected = sfo_rows[sfo_rows["_row_number"].isin(wanted)]
    for row_number, row in iter_rows(selected):
        leg = normalize_row(row, row_number, flights_sha, zones, numeric_lookup)
        op, when = sfo_movement(leg)
        moment = parse_iso(when)
        leg.update({
            "operation": op,
            "sfo_scheduled_movement_utc": when,
            "in_core_window": start <= moment < end,
            "segment": ("CORE" if start <= moment < end
                        else "INIT_BUFFER" if moment < start else "RECOVERY_BUFFER"),
            "qualifying": qualifies(leg),
            "airline_name": airlines.get(leg["airline"]),
        })
        legs.append(leg)
    legs.sort(key=lambda l: (l["sfo_scheduled_movement_utc"], l["flight_id"]))

    slug = f"sfo_{day}"
    core = [l for l in legs if l["in_core_window"]]
    dev = pick_dev_fixture(core)
    traced = next((l for l in dev if l["qualifying"]), None) or next(l for l in core if l["qualifying"])
    row_no = int(traced["source_row_id"].split(":")[1])

    manifest = {
        "generated_by": "python -m backend.data.build",
        "source_links": config.SOURCE_LINKS,
        "files": [
            file_entry(flights_path, stats["data_rows"], flights_sha),
            file_entry(raw / "airlines.csv"),
            file_entry(raw / "airports.csv"),
            *([lookup_entry] if lookup_entry else []),
        ],
        "units": {
            "clock_fields": "Local HHMM at the origin (departure fields) or destination (arrival fields); 2400 is midnight ending the day",
            "durations_and_delays": "minutes",
            "distance": "statute miles",
            "normalized_timestamps": "UTC, ISO 8601 with Z suffix",
        },
        "identifiers": {
            "flight_id": "first 12 hex chars of flights.csv sha256, colon, 1-based data row number (header excluded)",
            "source_row_id": "flights.csv:<1-based data row number>",
        },
        "timezone_assignment": timezones.PROVENANCE,
        "selection_parameters": config.selection_parameters(),
    }
    write_json(out / "source_manifest.json", manifest)

    window = {
        "airport_iata": config.AIRPORT_IATA, "timezone": config.AIRPORT_TZ, "local_date": day,
        "start_utc": iso(start), "end_utc": iso(end),
        "buffer_start_utc": iso(buf_start), "buffer_end_utc": iso(buf_end),
        "day_selection": day_meta, "window_selection": window_meta,
    }
    common = {"schema_version": "member2-traffic-0.1", "source_flights_sha256": flights_sha,
              "window": window}
    write_json(out / slug / "traffic_slice.json", {**common, "flight_count": len(legs), "flights": legs})
    ctx = sfo_rows[sfo_rows["_row_number"].isin(context_rows)]
    context = []
    for row_number, row in iter_rows(ctx):
        op, when, cancelled = context_rows[row_number]
        context.append({
            "flight_id": f"{flights_sha[:12]}:{row_number}",
            "source_row_id": f"flights.csv:{row_number}",
            "airline": row["AIRLINE"], "flight_number": row["FLIGHT_NUMBER"],
            "tail_number": row["TAIL_NUMBER"] or None,
            "origin": row["ORIGIN_AIRPORT"], "destination": row["DESTINATION_AIRPORT"],
            "operation": op, "sfo_scheduled_movement_utc": when, "cancelled": cancelled,
            "distance_mi": float(row["DISTANCE"]) if row["DISTANCE"] else None,
        })
    context.sort(key=lambda l: (l["sfo_scheduled_movement_utc"], l["flight_id"]))
    write_json(out / slug / "context_legs.json", {
        **common,
        "purpose": "SFO legs outside the slice, used only to derive initial gate occupancy and aircraft rotations",
        "context_start_utc": iso(ctx_start), "context_end_utc": iso(ctx_end),
        "flight_count": len(context), "flights": context})
    write_json(out / slug / "dev_fixture.json", {
        **common, "selection": "Contiguous run of operating core-window legs with the most qualifying rows",
        "flight_count": len(dev), "flights": dev})
    write_json(out / slug / "qualifying_row.json", {
        "source_file": "flights.csv", "source_sha256": flights_sha,
        "data_row_number": row_no, "file_line_number": row_no + 1,
        "raw_line": read_line(flights_path, row_no + 1),
        "header": [c for c in sfo_rows.columns if c != "_row_number"],
        "qualify_rule": config.selection_parameters()["qualify_rule"],
        "note": "Attributed delay minutes; they do not establish a closure start time or duration.",
        "normalized": traced,
    })

    seg = collections.Counter(l["segment"] for l in legs)
    slice_flags = collections.Counter(f for l in legs for f in l["quality_flags"])
    report = {
        "source_flights_sha256": flights_sha,
        "dataset": {
            "data_rows": stats["data_rows"],
            "numeric_airport_id_rows_by_month": dict(sorted(stats["numeric_airport_rows_by_month"].items())),
            "numeric_airport_ids_resolved": bool(numeric_lookup),
        },
        "sfo": {
            "identified_rows": stats["sfo_rows"],
            "identified_rows_by_month": dict(sorted(stats["sfo_rows_by_month"].items())),
            "flag_counts": dict(stats["sfo_flag_counts"].most_common()),
            "unresolved_rows": len(stats["sfo_unresolved_rows"]),
            "unresolved_examples": stats["sfo_unresolved_rows"][:20],
        },
        "airports_csv_issues": airport_quality(airports, zones),
        "selected_slice": {
            **window,
            "flights": len(legs),
            "by_segment": dict(seg),
            "arrivals": sum(1 for l in legs if l["operation"] == "ARRIVAL"),
            "departures": sum(1 for l in legs if l["operation"] == "DEPARTURE"),
            "cancelled": sum(1 for l in legs if l["cancelled"]),
            "diverted": sum(1 for l in legs if l["diverted"]),
            "qualifying_rows": sum(1 for l in legs if l["qualifying"]),
            "flag_counts": dict(slice_flags.most_common()),
            "dev_fixture_flights": len(dev),
        },
        "coverage_limits": [
            "BTS on-time data covers reporting carriers' scheduled domestic passenger legs only; "
            "international, cargo, general aviation and non-reporting regional traffic at SFO are absent.",
            "Clock fields have one-minute resolution and no seconds.",
            "No radar positions, runway, taxiway, gate or aircraft type are in the source rows.",
            *(["October 2015 rows use numeric BTS airport IDs; without the BTS lookup they cannot be "
               "matched to SFO and October is excluded from day selection."] if not numeric_lookup else []),
            "Time zone assignment per airport is curated and awaits review (see source_manifest.json).",
        ],
    }
    write_json(out / slug / "quality_report.json", report)
    print(f"Wrote {len(legs)} legs ({len(core)} core, {len(dev)} dev fixture) to {out / slug}")


if __name__ == "__main__":
    main()
