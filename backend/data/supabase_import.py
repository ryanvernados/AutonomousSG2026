"""Repeatable importer for Member 2 scenarios into the Member 1 Supabase schema.

    python -m backend.data.supabase_import                         # dry run: writes JSONL rows
    python -m backend.data.supabase_import --apply                 # needs SUPABASE_DB_URL and psycopg
    python -m backend.data.supabase_import --project-id <uuid>     # import into another project

Target tables (supabase/migrations/202610310001_platform.sql):
    airports(project_id, airport_iata, document)
    scenarios(scenario_id, project_id, document)
    scenario_flights(scenario_id, flight_id, project_id, document)
Each document is the contract JSON. Scenarios are validated with backend.contracts.Scenario
before any row is built. Writes run in one transaction with ON CONFLICT DO NOTHING, so a
re-run is idempotent; --apply checks the columns in information_schema before writing.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from backend.contracts import Scenario

from . import config
from .scenario import SCENARIO_DIR

DB_URL_ENV = "SUPABASE_DB_URL"

TABLES = {
    "airports": (["project_id", "airport_iata"], ["project_id", "airport_iata", "document"]),
    "scenarios": (["scenario_id"], ["scenario_id", "project_id", "document"]),
    "scenario_flights": (["scenario_id", "flight_id"], ["scenario_id", "flight_id", "project_id", "document"]),
}


def rows_for(scenarios: list[Scenario], project_id: str | None) -> dict[str, list[dict]]:
    rows = {t: [] for t in TABLES}
    airports = {}
    for s in scenarios:
        project = project_id or s.project_id
        doc = s.model_dump(mode="json")
        doc["project_id"] = project
        layout = doc["source_manifest"]["layout_source"]
        airports[(project, s.airport_iata)] = {
            "project_id": project, "airport_iata": s.airport_iata,
            "document": {"source_type": doc["infrastructure"]["source_type"], "timezone": s.timezone,
                         "layout_source": layout, "effective_date": doc["infrastructure"]["effective_date"]}}
        rows["scenarios"].append({"scenario_id": s.scenario_id, "project_id": project, "document": doc})
        rows["scenario_flights"] += [{"scenario_id": s.scenario_id, "flight_id": f["flight_id"],
                                      "project_id": project, "document": f} for f in doc["flights"]]
    rows["airports"] = list(airports.values())
    return rows


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
                raise SystemExit(f"Schema mismatch in {table}: missing {missing}.")
        for table, (key, cols) in TABLES.items():
            sql = (f"insert into public.{table} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                   f"on conflict ({', '.join(key)}) do nothing")
            inserted = 0
            for r in rows[table]:
                cur = conn.execute(sql, [Jsonb(r[c]) if c == "document" else r[c] for c in cols])
                inserted += cur.rowcount
            print(f"{table}: {inserted} inserted, {len(rows[table]) - inserted} already present")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import contract scenarios into Supabase")
    parser.add_argument("--scenario", action="append", default=[],
                        help="Scenario JSON path (repeatable; default: every file in data/processed/scenarios)")
    parser.add_argument("--project-id", default=None, help="Project UUID; default is the scenario's project_id")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    paths = [Path(p) for p in args.scenario] or sorted(SCENARIO_DIR.glob("*.json"))
    scenarios = [Scenario.model_validate_json(p.read_text(encoding="utf-8")) for p in paths]
    rows = rows_for(scenarios, args.project_id)
    if args.apply:
        apply(rows)
    else:
        dry_run(rows, config.PROCESSED_DIR / "supabase_rows")


if __name__ == "__main__":
    main()
