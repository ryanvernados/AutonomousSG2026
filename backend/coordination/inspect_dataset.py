"""Read-only CSV profiling for the Member 3 handoff; not scenario ingestion.

Usage: python -m backend.coordination.inspect_dataset --dataset dataset
"""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


def profile(directory: Path) -> dict:
    report = {"purpose": "Read-only inspection; Member 2 owns normalization and historical scenarios",
              "source_row_numbering": "1-based data rows, excluding CSV header", "files": {}}
    for name in ("airlines.csv", "airports.csv", "flights.csv"):
        path = directory / name
        with path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        info = {"sha256": checksum, "bytes": path.stat().st_size, "rows": 0}
        with path.open(encoding="utf-8-sig", newline="") as source:
            rows = csv.DictReader(source)
            info["headers"] = rows.fieldnames
            counts = Counter()
            monthly_numeric = Counter()
            samples = []
            for row_number, row in enumerate(rows, 1):
                info["rows"] += 1
                if name == "airports.csv" and row["IATA_CODE"] == "SFO":
                    info["SFO"] = row
                if name != "flights.csv":
                    continue
                origin, destination = row["ORIGIN_AIRPORT"], row["DESTINATION_AIRPORT"]
                numeric = origin.isdigit() or destination.isdigit()
                if numeric:
                    monthly_numeric[row["MONTH"]] += 1
                counts["numeric_airport_id_rows"] += numeric
                counts["cancelled_rows"] += row["CANCELLED"] == "1"
                counts["diverted_rows"] += row["DIVERTED"] == "1"
                counts["missing_tail_number_rows"] += not row["TAIL_NUMBER"]
                counts["missing_scheduled_time_rows"] += not row["SCHEDULED_TIME"]
                counts["hhmm_2400_rows"] += any(row[k] == "2400" for k in
                                               ("SCHEDULED_DEPARTURE", "SCHEDULED_ARRIVAL", "DEPARTURE_TIME", "ARRIVAL_TIME"))
                if "SFO" in (origin, destination):
                    counts["literal_SFO_rows"] += 1
                    counts["literal_SFO_departures"] += origin == "SFO"
                    counts["literal_SFO_arrivals"] += destination == "SFO"
                    qualifying = float(row["WEATHER_DELAY"] or 0) > 15 or float(row["LATE_AIRCRAFT_DELAY"] or 0) > 20
                    counts["literal_SFO_qualifying_delay_rows"] += qualifying
                    if qualifying and len(samples) < 3:
                        samples.append({"source_row_id": f"sha256:{checksum}:row:{row_number}",
                                        "source_row_number": row_number, "raw": row})
            if name == "flights.csv":
                info.update(counts=counts, numeric_airport_rows_by_month=monthly_numeric,
                            qualifying_examples=samples,
                            limitations=["Literal SFO counts exclude numeric identifiers pending Member 2 lookup.",
                                         "HHMM fields are local wall-clock strings, not UTC timestamps.",
                                         "Delay minutes do not define disruption start times or closure durations.",
                                         "No runway, gate, fuel, crew, equipment or taxi graph fields exist in these CSVs.",
                                         "Examples are source evidence only, not a normalized historical scenario."])
        report["files"][name] = info
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("dataset"))
    parser.add_argument("--output", type=Path, default=Path("backend/coordination/fixtures/dataset_profile.json"))
    args = parser.parse_args()
    result = profile(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: {k: v for k, v in info.items() if k in ("rows", "counts", "sha256")}
                      for name, info in result["files"].items()}, indent=2))


if __name__ == "__main__":
    main()

