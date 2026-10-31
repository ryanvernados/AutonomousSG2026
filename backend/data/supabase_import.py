"""Repeatable importer for the selected traffic slice into the Member 1 Supabase schema.

    python -m backend.data.supabase_import                 # dry run: writes JSONL rows
    python -m backend.data.supabase_import --apply         # needs SUPABASE_DB_URL and psycopg

Member 1 owns the migrations. Until they are published, the column mapping below is a
proposal: --apply first reads information_schema and refuses to write if any mapped
column is missing, so a schema mismatch fails loudly instead of writing partial data.
Writes run in one transaction with ON CONFLICT DO NOTHING, so re-running is idempotent.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from . import config

DB_URL_ENV = "SUPABASE_DB_URL"

# Single place to align with Member 1 migrations: table -> (conflict key, columns).
TABLES = {
    "airports": (["iata_code"], ["iata_code", "name", "timezone", "layout_id", "layout_effective_from",
                                 "layout_source"]),
    "scenarios": (["scenario_id"], ["scenario_id", "schema_version", "airport_iata", "timezone", "seed",
                                    "start_utc", "end_utc", "source_manifest", "rules_config",
                                    "infrastructure", "initial_occupancy", "data_quality"]),
    "scenario_flights": (["scenario_id", "flight_id"], [
        "scenario_id", "flight_id", "source_row_id", "airline", "flight_number", "tail_number", "origin",
        "destination", "scheduled_gate_departure_utc", "scheduled_gate_arrival_utc", "state", "operation",
        "assumptions", "quality_flags", "historical"]),
}
JSON_COLUMNS = {"layout_source", "source_manifest", "rules_config", "infrastructure", "initial_occupancy",
                "data_quality", "assumptions", "quality_flags", "historical"}


def rows_for(scenario: dict) -> dict[str, list[dict]]:
    layout = scenario["infrastructure"]["layout"]
    return {
        "airports": [{"iata_code": scenario["airport_iata"], "name": "San Francisco International Airport",
                      "timezone": scenario["timezone"], "layout_id": layout["layout_id"],
                      "layout_effective_from": layout["source"]["effective_from"],
                      "layout_source": layout["source"]}],
        "scenarios": [{c: scenario.get(c) for c in TABLES["scenarios"][1]}],
        "scenario_flights": [{"scenario_id": scenario["scenario_id"], **{c: f.get(c) for c in TABLES["scenario_flights"][1]
                                                                          if c != "scenario_id"}}
                             for f in scenario["flights"]],
    }


def dry_run(rows: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table, items in rows.items():
        with (out_dir / f"{table}.jsonl").open("w", encoding="utf-8") as f:
            for r in items:
                f.write(json.dumps(r, sort_keys=True) + "\n")
        print(f"{table}: {len(items)} rows -> {out_dir / (table + '.jsonl')}")


def apply(rows: dict) -> None:
    url = os.environ.get(DB_URL_ENV)
    if not url:
        raise SystemExit(f"Set {DB_URL_ENV} to the Supabase Postgres connection string (backend only).")
    import psycopg
    from psycopg.types.json import Jsonb

    with psycopg.connect(url) as conn, conn.transaction():
        for table, (_, cols) in TABLES.items():
            found = {r[0] for r in conn.execute(
                "select column_name from information_schema.columns where table_schema='public' and table_name=%s",
                (table,))}
            missing = [c for c in cols if c not in found]
            if missing:
                raise SystemExit(f"Schema mismatch in {table}: missing {missing}. Align TABLES with Member 1 migrations.")
        for table, (key, cols) in TABLES.items():
            sql = (f"insert into public.{table} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                   f"on conflict ({', '.join(key)}) do nothing")
            inserted = 0
            for r in rows[table]:
                cur = conn.execute(sql, [Jsonb(r[c]) if c in JSON_COLUMNS else r[c] for c in cols])
                inserted += cur.rowcount
            print(f"{table}: {inserted} inserted, {len(rows[table]) - inserted} already present")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import a Scenario JSON into Supabase")
    parser.add_argument("--scenario", default=None, help="Path to scenario JSON (default: latest scenario_full.json)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.scenario) if args.scenario else max(config.PROCESSED_DIR.glob("sfo_*")) / "scenario_full.json"
    rows = rows_for(json.loads(path.read_text(encoding="utf-8")))
    if args.apply:
        apply(rows)
    else:
        dry_run(rows, path.parent / "supabase_rows")


if __name__ == "__main__":
    main()
