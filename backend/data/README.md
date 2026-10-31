# Member 2: historical data and airport model

Owns `backend/data/`, `backend/airport/`, `data/processed/`, `data/layout/`. Branch `member2/data`.

## Setup

```sh
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/data/requirements.txt   # Windows
```

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
python -m backend.data.build            # needs raw data, about 2 minutes
python -m backend.data.scenario         # processed files only
python -m backend.data.disruptions
python -m backend.data.contract_check
python -m backend.data.evaluate --fixture runway-10R-28L-closure --policy baseline=scheduled
python -m backend.data.supabase_import  # dry run; --apply needs SUPABASE_DB_URL
python -m pytest backend/data/tests
```

## Outputs (`data/processed/sfo_2015-12-21/`)

| File | Content |
|---|---|
| `../source_manifest.json` | Checksums, headers, row counts, units, links, selection parameters |
| `traffic_slice.json` | All 218 SFO legs in the 4 h horizon, normalized, with source values and flags |
| `context_legs.json` | SFO legs 12 h before / 6 h after, used only for initial occupancy and rotations |
| `dev_fixture.json` | 30-flight development fixture (contiguous core-window run) |
| `qualifying_row.json` | Traced qualifying row: raw source line plus normalized leg |
| `quality_report.json` | Dataset and slice flag counts, airport file issues, coverage limits |
| `scenario_full.json`, `scenario_dev.json` | Scenario objects (contract field names) |
| `expected_facts.json` | Counts the integration tests check |
| `disruptions/*.json` | Runway closure, taxiway blockage, tug failure as Instruction events |
| `supabase_rows/*.jsonl` | Importer dry-run rows for `airports`, `scenarios`, `scenario_flights` |

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
| Gate IDs, gate count (102), HEAVY-capable gates (44) | SYNTHETIC | peak scheduled demand x 1.15 |
| Initial gate occupancy (49 aircraft) | HISTORICAL tail sequence; gate assignment SYNTHETIC | `scenario.py` |
| Aircraft rotations | HISTORICAL | same airline + tail, next SFO movement |
| Wake category | SYNTHETIC | stage length >= 2000 mi is HEAVY; type not in source |
| Holding fuel, latest safe landing | SYNTHETIC | seeded 10 to 60 min per arrival |
| Taxi-in 8 min, taxi-out 12 min, turnaround 45/75 min | SYNTHETIC | `assumptions.py` |
| Crews (one per rotation, duty 90 to 360 min) | SYNTHETIC | seeded |
| Pushback tugs (6, 10 min each) | SYNTHETIC | single equipment resource |
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
  only under `flight.historical`, which is for replay and calibration, not decisions.

## Pending with other members

* **Member 1:** publish `backend.contracts` models; `contract_check` then switches to them automatically.
  Contract change request for the optional Flight fields `operation`, `segment`, `qualifying_source_row`,
  `quality_flags`, `historical`, and Scenario `data_quality`. Confirm the Supabase column mapping in
  `supabase_import.TABLES`. Separation and runway occupancy values go into `rules_config.member1_rules`.
* **Member 1 / 3:** implement `run(scenario, events, seed)` policies for `evaluate.py`.
* **Member 4:** `sfo_layout.json` gives schematic x/y in metres for the Konva view; positions are not surveyed.
