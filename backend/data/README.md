# Member 2: historical data and airport model

Owns `backend/data/`, `backend/airport/`, `data/processed/`, `data/layout/`. Branch `member2/data`.

## Setup

The pipeline imports Member 1's `backend.contracts` (Pydantic) and, for engine checks, `backend.engine`
(SimPy). Use the root `uv` environment plus pandas and tzdata:

```sh
uv sync --frozen
alias m2='uv run --frozen --with pandas==3.1.0 --with numpy==2.4.6 --with tzdata==2026.6'
```

Without uv: `pip install -r backend/data/requirements.txt pydantic simpy`.

### Raw data (not in git)

Download the [2015 Flight Delays and Cancellations](https://www.kaggle.com/datasets/usdot/flight-delays)
dataset and unzip `flights.csv`, `airlines.csv` and `airports.csv` into one folder. Point the
pipeline at it with `SIM_RAW_DATA_DIR` or `--raw-dir`.

Expected checksums (SHA-256):

| File | Rows | SHA-256 |
|---|---|---|
| flights.csv | 5,819,079 | `afa4e42188b113dd69427a3fbd00a4b6979c6235c866f963c2899d18a3403a83` |
| airlines.csv | 14 | `ec54f453260c2a46fc76d497ab0eb0a4c13724b566fba8a7ce3bb8ad712acca3` |
| airports.csv | 322 | `23c4cea98c318225bd923b2068ead8af089845071f79fd9ea0037ba6a61cd4e2` |

October 2015 rows use numeric BTS airport IDs. To resolve them, export the BTS TranStats
Master Coordinate table with columns `AIRPORT_ID,AIRPORT` as `airport_id_to_iata.csv` into the
same folder. Without it, October is excluded from day selection and reported in the quality report.

## Commands

```sh
export SIM_RAW_DATA_DIR=/path/to/kaggle/folder
m2 python -m backend.data.build            # needs raw data, about 25 s
m2 python -m backend.data.scenario         # processed files only; validates with backend.contracts
m2 python -m backend.data.disruptions
m2 python -m backend.data.contract_check   # Member 1 models + engine-required assumptions
m2 python -m backend.data.evaluate --fixture runway-10R-28L-closure --variant dev \
      --policy baseline=engine-baseline --policy sched=scheduled
m2 python -m backend.data.supabase_import  # dry run; --apply needs DATABASE_URL; --verify-https is read-only
m2 pytest tests backend/data/tests
```

To serve the historical scenarios from Member 1's API, set `SCENARIO_DIR=data/processed/scenarios`.

## Outputs

| File | Content |
|---|---|
| `data/processed/scenarios/sfo-2015-12-21-2000-full.json` | Contract v1 Scenario, 218 flights (point `SCENARIO_DIR` here) |
| `data/processed/scenarios/sfo-2015-12-21-2000-dev.json` | Contract v1 Scenario, 30-flight development fixture |
| `data/processed/source_manifest.json` | Checksums, headers, row counts, units, links, selection parameters |
| `data/processed/supabase_rows/*.jsonl` | Importer dry-run rows for `airports`, `scenarios`, `scenario_flights` |

Under `data/processed/sfo_2015-12-21/`:

| File | Content |
|---|---|
| `traffic_slice.json` | All 218 SFO legs in the 4 h horizon, normalized, with source values and flags |
| `context_legs.json` | SFO legs 12 h before / 6 h after, used only for initial occupancy and rotations |
| `dev_fixture.json` | 30-flight development fixture (contiguous core-window run) |
| `qualifying_row.json` | Traced qualifying row: raw source line plus normalized leg |
| `quality_report.json` | Dataset and slice flag counts, airport file issues, coverage limits |
| `expected_facts.json` | Counts the integration tests check |
| `disruptions/*.json` | `RUNWAY_UNAVAILABLE`, `TAXIWAY_BLOCKED`, `EQUIPMENT_FAILURE` contract Instructions plus expected facts |
| `evaluations/*.json` | Evaluation runner reports |

### Where Member 2 data sits in the contract

* Flight `assumptions` carry the engine-required `wake_category` (`MEDIUM`/`HEAVY`), `gate_id`, `crew_id`,
  `equipment_id` and arrival `latest_safe_arrival_utc`, plus rotation links, the original local HHMM values
  and normalization flags. Each has `source_type`, `unit` and `explanation`.
* `infrastructure.resources`: 4 runways (all mutually dependent, see below), taxiway pieces whose endpoints
  are runway and gate IDs, 133 gates, 162 crews, the pushback tug pool.
* `initial_occupancy`: one GATE `Reservation` per aircraft already on gate, with flight_id `OCCUPANT:<leg>`.
* `rules_config`: Member 1 defaults, unchanged.
* `source_manifest.member2`: window and buffers, every assumption parameter, gate sizing, occupancy provenance,
  synthetic connections, layout reference and node positions, data quality, and `historical_outcomes`
  (cancellations, diversions, delay minutes) kept for replay and calibration only.

`data/layout/sfo_layout.json` is the airport graph; `data/layout/source/` holds the FAA diagram PDF.

## Selection

The plan's qualifying rule (`WEATHER_DELAY > 15` or `LATE_AIRCRAFT_DELAY > 20`) matches every
2015 SFO day, so selection is ranked: the local SFO day with the most qualifying rows
(**2015-12-21**, 293 of 970 movements), then the whole-hour 2 h window with the most qualifying movements
(**20:00 to 22:00 PST**, 48 of 128 movements). Buffers are 60 min each side. Ties break on
movement count, then earliest.

Traced row: `flights.csv` line 5663453, VX 211 ORD to SFO, `LATE_AIRCRAFT_DELAY` 137 min,
flight_id `afa4e42188b1:5663452`.

## Time normalization

* Date columns are the scheduled departure date at the origin. Every other HHMM field is resolved to
  the calendar day closest to the instant implied by durations (scheduled elapsed, delays, taxi, air time).
* `2400` is midnight at the end of the day and is flagged `HHMM_2400`.
* Nonexistent or ambiguous DST local times are flagged. A resolved time more than 5 min from the duration
  check is flagged `INCONSISTENT_WITH_DURATIONS`.
* Scheduled gate, actual gate, wheels-off and wheels-on times stay separate.

## Source type of every field

| Item | Source type | Basis |
|---|---|---|
| Flight times, carrier, tail, delays, cancellations | HISTORICAL | flights.csv |
| Airport time zones | SOURCED zone rules, curated assignment awaiting review | `timezones.py` |
| Runways, lengths, headings, crossings, taxiway and boarding-area names | SOURCED | FAA AL-375, cycle 2611, effective 2026-10-29 to 2026-11-26 |
| Parallel spacing, coordinates, taxiway topology, segment lengths | SYNTHETIC | schematic |
| Gate IDs, gate count (133), HEAVY-capable gates (53) | SYNTHETIC | peak scheduled demand + 30 min buffer, x 1.15 |
| Gate per flight | SYNTHETIC | interval colouring; an aircraft keeps one gate from arrival to its next departure |
| Initial gate occupancy (44 reservations) | HISTORICAL tail sequence; gate assignment SYNTHETIC | `scenario.py` |
| Aircraft rotations | HISTORICAL | same airline + tail, next SFO movement |
| Wake category | SYNTHETIC | stage length >= 2000 mi is HEAVY; type not in source |
| Holding fuel, latest safe landing | SYNTHETIC | seeded 10 to 60 min per arrival |
| Taxi-in 8 min, taxi-out 12 min (wheels-on/off estimates) | SYNTHETIC | `assumptions.py` |
| Taxi segment traversal (10 m/s, pieces of at most 400 m, minimum 30 s) | SYNTHETIC | `assumptions.py` |
| Crews (one per rotation, work limit 90 to 360 min, available for the horizon + 60 min) | SYNTHETIC | seeded |
| Pushback tugs (12) | SYNTHETIC | single equipment resource |
| Separation, runway occupancy, turnaround, search limits | SYNTHETIC | Member 1 `RulesConfig` defaults |
| Passenger connections (68) | SYNTHETIC | not in source |
| Disruption times and durations | SYNTHETIC | not derived from delay minutes |

Seed: `20151221`.

## Coverage limits and unresolved issues

* Only reporting carriers' scheduled domestic passenger legs. International, cargo, GA and
  non-reporting regional traffic at SFO are missing, so real runway and gate load is higher.
* October 2015 (486,165 rows) is unresolved until the BTS lookup is supplied.
* `airports.csv`: ECP, PBG, UST have no coordinates; GUM longitude has the wrong sign.
* 2 SFO rows have a scheduled arrival that disagrees with scheduled elapsed time by more than 5 min; 1 actual
  arrival and 1 wheels-on are similarly inconsistent. All are flagged, none dropped.
* The 2026 layout is paired with 2015 traffic.
* Historically cancelled flights (35 in the slice) are included as scheduled demand. Their outcome is kept
  only under `source_manifest.member2.historical_outcomes`, for replay and calibration, not decisions.
  Actual gate and wheels times fill the contract's optional Flight fields.

## Engine integration results

Both scenarios pass `backend.contracts.Scenario`, the engine loads them through `ScenarioRepository`,
every disruption instruction passes `instruction_issues`, and a single flight placed by the engine passes
every hard check. Member 1's FCFS baseline still returns `NO_FEASIBLE_PLAN_FOUND` for the dev scenario:

* AL-375 shows both 01/19 runways crossing both 10/28 runways, and the close parallels are dependent, so all
  four runways are mutually dependent. With the default 120 to 180 s wake pairs this is roughly single-runway
  capacity (about 30 movements per hour) against 64 scheduled per hour. Queued arrivals eventually pass
  their synthetic `latest_safe_arrival_utc`.
* The engine's search budget is 100 placements for the whole plan, and every flight tries up to
  4 runways x 4 routes per 120 s step, so a 30-flight plan with any queueing exhausts it.

## Pending with other members

* **Member 1:** decide whether crossing runways should share wake separation or only an intersection
  clearance, and whether the 100-placement budget should apply per flight; both currently prevent a plan.
  `EQUIPMENT_FAILURE` blocks the whole tug pool; a partial outage needs a capacity parameter.
  Fold `pandas` and `tzdata` into the root lockfile.
  Scenario writes over the HTTPS adapter need a backend RPC (service_role only has SELECT on
  `airports`, `scenarios`, `scenario_flights`); until then import with `--apply` over `DATABASE_URL`
  and check with `--verify-https`. Pins work on Python 3.11 (numpy 2.4.6).
* **Member 3:** supply an agent `run(scenario, events, seed)` policy for `evaluate.py`.
* **Member 4:** `sfo_layout.json` gives schematic x/y in metres for the Konva view; positions are not surveyed.
