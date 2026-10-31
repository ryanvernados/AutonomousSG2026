"""SFO airport layout: runways, runway relationships, bounded taxiway graph and gates.

Source: FAA airport diagram AL-375 (FAA), San Francisco Intl (SFO), d-TPP cycle 2611,
amendment 26302, effective 29 OCT 2026 to 26 NOV 2026. The PDF is kept under
data/layout/source/ with its checksum because expired cycles are removed from the FAA site.

What the diagram establishes (source_type SOURCED):
  runway designators, lengths and widths, magnetic headings, which runways cross,
  taxiway and boarding area names, hot spots HS1 to HS4, field elevation.
What is not on the diagram and is therefore SYNTHETIC here:
  schematic coordinates, parallel runway spacing, the simplified taxiway topology and
  segment lengths, gate identifiers, gate counts and gate size limits.

This 2026 layout is paired with 2015 traffic. The airport geometry may have changed
between 2015 and 2026; the layout is labelled accordingly in every export.
"""
from __future__ import annotations

import collections
import heapq
import math

SOURCE = {
    "document": "FAA Airport Diagram AL-375 (FAA), San Francisco Intl (SFO)",
    "publisher": "FAA Aeronautical Information Services, d-TPP",
    "cycle": "2611",
    "amendment": "26302",
    "effective_from": "2026-10-29",
    "effective_to": "2026-11-26",
    "url": "https://aeronav.faa.gov/d-tpp/2611/00375AD.PDF",
    "local_copy": "data/layout/source/sfo_00375AD_2611.pdf",
    "magnetic_variation": "14 deg E (diagram annotation, January 2015, annual change 0.1 deg W)",
    "traffic_pairing_note": (
        "Layout effective 2026 paired with 2015 traffic. Differences between the 2015 and 2026 "
        "airfield are not modelled."
    ),
}

FT_TO_M = 0.3048
SYNTHETIC_PARALLEL_SPACING_M = 230  # not stated on the diagram

# Runway facts as printed on AL-375.
RUNWAYS = [
    {"runway_id": "10L-28R", "ends": ["10L", "28R"], "length_ft": 11870, "width_ft": 200,
     "magnetic_headings_deg": [104, 284]},
    {"runway_id": "10R-28L", "ends": ["10R", "28L"], "length_ft": 11381, "width_ft": 200,
     "magnetic_headings_deg": [104, 284]},
    {"runway_id": "01R-19L", "ends": ["01R", "19L"], "length_ft": 8650, "width_ft": 200,
     "magnetic_headings_deg": [14, 194]},
    {"runway_id": "01L-19R", "ends": ["01L", "19R"], "length_ft": 7650, "width_ft": 200,
     "magnetic_headings_deg": [14, 194]},
]

# Every 01/19 runway crosses both 10/28 runways on AL-375 (hot spot HS4 area).
CROSSINGS = [("10L-28R", "01R-19L"), ("10L-28R", "01L-19R"),
             ("10R-28L", "01R-19L"), ("10R-28L", "01L-19R")]
CLOSE_PARALLELS = [("10L-28R", "10R-28L"), ("01R-19L", "01L-19R")]

# Schematic frame (SYNTHETIC): x along magnetic 104 deg, y along magnetic 014 deg, metres.
# 10L-28R lies on y = 0 from its 10L threshold at x = 0.
_S = SYNTHETIC_PARALLEL_SPACING_M
_RUNWAY_GEOMETRY = {
    "10L-28R": ((0, 0), (11870 * FT_TO_M, 0)),
    "10R-28L": ((150, -_S), (150 + 11381 * FT_TO_M, -_S)),
    "01L-19R": ((2500, -1300), (2500, -1300 + 7650 * FT_TO_M)),
    "01R-19L": ((2500 + _S, -1500), (2500 + _S, -1500 + 8650 * FT_TO_M)),
}

# Taxiway nodes (SYNTHETIC positions). Names reuse AL-375 taxiway labels near each segment.
_NODES = {
    # Taxiway B (terminal-side parallel to 10R-28L) and A (next to the boarding areas)
    **{f"B@{x}": (x, -330) for x in (300, 900, 1500, 2100, 2400, 2900, 3500)},
    **{f"A@{x}": (x, -420) for x in (300, 900, 1500, 2100, 2400)},
    # Taxiway C (outer parallel north of 10L-28R)
    **{f"C@{x}": (x, 150) for x in (200, 1500, 2100, 2400, 2900, 3500)},
    # Taxiway L (parallel east of 01R-19L)
    **{f"L@{y}": (2900, y) for y in (-1300, -330, 150, 900)},
    # Taxiway M (apron connector near the 01 thresholds, Terminal 1)
    "M@01": (2600, -1400),
    # Runway hold points at each runway end and mid-field crossing points
    "HOLD_10L": (0, 150), "HOLD_10R": (150, -330), "HOLD_28R": (3618, 150), "HOLD_28L": (3619, -330),
    "HOLD_01L": (2450, -1350), "HOLD_01R": (2780, -1500), "HOLD_19R": (2450, 1032), "HOLD_19L": (2780, 1137),
}

# Edges: (from, to, taxiway name, kind). kind TAXIWAY or RUNWAY_CROSSING.
_EDGES = [
    *[(f"B@{a}", f"B@{b}", "B", "TAXIWAY") for a, b in zip((300, 900, 1500, 2100, 2900),
                                                         (900, 1500, 2100, 2400, 3500))],
    ("B@2400", "B@2900", "B", "RUNWAY_CROSSING"),
    *[(f"A@{a}", f"A@{b}", "A", "TAXIWAY") for a, b in zip((300, 900, 1500, 2100), (900, 1500, 2100, 2400))],
    *[(f"A@{x}", f"B@{x}", "A-B", "TAXIWAY") for x in (300, 900, 1500, 2100, 2400)],
    *[(f"C@{a}", f"C@{b}", "C", "TAXIWAY") for a, b in zip((200, 1500, 2100, 2900),
                                                         (1500, 2100, 2400, 3500))],
    ("C@2400", "C@2900", "C", "RUNWAY_CROSSING"),
    *[(f"L@{a}", f"L@{b}", "L", "TAXIWAY") for a, b in zip((-1300, -330, 150), (-330, 150, 900))],
    ("L@-330", "B@2900", "L", "TAXIWAY"),
    ("L@150", "C@2900", "L", "TAXIWAY"),
    ("M@01", "L@-1300", "M", "TAXIWAY"),
    ("M@01", "A@2400", "M", "TAXIWAY"),
    # Crossings of both 10/28 runways between the C and B parallels
    ("C@200", "B@300", "Z", "RUNWAY_CROSSING"),
    ("C@1500", "B@1500", "D", "RUNWAY_CROSSING"),
    ("C@2100", "B@2100", "E", "RUNWAY_CROSSING"),
    ("C@3500", "B@3500", "F", "RUNWAY_CROSSING"),
    # Runway end hold points
    ("HOLD_10L", "C@200", "Z", "TAXIWAY"),
    ("HOLD_10R", "B@300", "B", "TAXIWAY"),
    ("HOLD_28R", "C@3500", "C", "TAXIWAY"),
    ("HOLD_28L", "B@3500", "F", "TAXIWAY"),
    ("HOLD_01L", "M@01", "M", "TAXIWAY"),
    ("HOLD_01R", "L@-1300", "L", "TAXIWAY"),
    ("HOLD_19R", "L@900", "L", "TAXIWAY"),
    ("HOLD_19L", "L@900", "L", "TAXIWAY"),
]

# Which runways a RUNWAY_CROSSING edge crosses.
_CROSSES = {"Z": ["10L-28R", "10R-28L"], "D": ["10L-28R", "10R-28L"],
            "E": ["10L-28R", "10R-28L"], "F": ["10L-28R", "10R-28L"],
            "B": ["01L-19R", "01R-19L"], "C": ["01L-19R", "01R-19L"]}

# Runway end -> hold node where departures enter and arrivals exit (SYNTHETIC simplification).
RUNWAY_END_NODE = {"10L": "HOLD_10L", "10R": "HOLD_10R", "28R": "HOLD_28R", "28L": "HOLD_28L",
                   "01L": "HOLD_01L", "01R": "HOLD_01R", "19R": "HOLD_19R", "19L": "HOLD_19L"}
# Arrival exits: landing on a runway end, the aircraft exits near the far end.
ARRIVAL_EXIT_NODE = {"28L": "HOLD_10R", "28R": "HOLD_10L", "10L": "HOLD_28R", "10R": "HOLD_28L",
                     "19L": "HOLD_01R", "19R": "HOLD_01L", "01L": "HOLD_19R", "01R": "HOLD_19L"}

# Boarding areas on AL-375 with the A-taxiway node each apron connects to (SYNTHETIC).
BOARDING_AREAS = {"A": "A@2400", "B": "A@2100", "C": "A@2100", "D": "A@1500",
                  "E": "A@1500", "F": "A@900", "G": "A@300"}
HEAVY_AREA_PREFERENCE = ["A", "G", "F", "E", "D", "C", "B"]  # SYNTHETIC gate size rule


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def build_gates(gate_count: int, heavy_count: int) -> list[dict]:
    """Declared simulated gate subset spread evenly across the AL-375 boarding areas.

    The first `heavy_count` gates in HEAVY_AREA_PREFERENCE order accept HEAVY aircraft.
    """
    areas = list(BOARDING_AREAS)
    per_area = collections.Counter(areas[i % len(areas)] for i in range(gate_count))
    gates = [{
        "gate_id": f"SIM-{area}{n:02d}",
        "boarding_area": area,
        "boarding_area_source_type": "SOURCED",
        "apron_node": f"APRON_{area}",
        "max_wake_category": "MEDIUM",
        "source_type": "SYNTHETIC",
    } for area in areas for n in range(1, per_area[area] + 1)]
    order = sorted(gates, key=lambda g: (HEAVY_AREA_PREFERENCE.index(g["boarding_area"]), g["gate_id"]))
    for g in order[:heavy_count]:
        g["max_wake_category"] = "HEAVY"
    return gates


def build_layout(gate_count: int, heavy_count: int) -> dict:
    nodes = dict(_NODES)
    edges = list(_EDGES)
    for area, attach in BOARDING_AREAS.items():
        ax, ay = nodes[attach]
        nodes[f"APRON_{area}"] = (ax + {"B": 120, "C": -120, "D": 120, "E": -120}.get(area, 0), ay - 280)
        edges.append((f"APRON_{area}", attach, f"RAMP_{area}", "RAMP"))

    taxi_edges = []
    for a, b, name, kind in edges:
        seg = {
            "segment_id": f"{name}:{a}-{b}",
            "taxiway": name,
            "kind": kind,
            "from_node": a, "to_node": b,
            "length_m": round(_dist(nodes[a], nodes[b])),
            "bidirectional": True,
            "name_source_type": "SOURCED" if kind != "RAMP" and name != "A-B" else "SYNTHETIC",
            "topology_source_type": "SYNTHETIC",
        }
        if kind == "RUNWAY_CROSSING":
            seg["crosses_runways"] = _CROSSES[name]
        taxi_edges.append(seg)

    runways = []
    for r in RUNWAYS:
        (x0, y0), (x1, y1) = _RUNWAY_GEOMETRY[r["runway_id"]]
        runways.append({
            **r,
            "length_m": round(r["length_ft"] * FT_TO_M),
            "source_type": "SOURCED",
            "end_nodes": {e: RUNWAY_END_NODE[e] for e in r["ends"]},
            "schematic": {"from": [round(x0), round(y0)], "to": [round(x1), round(y1)],
                          "source_type": "SYNTHETIC"},
        })

    relationships = [
        {"runways": list(p), "relationship": "INTERSECTING", "source_type": "SOURCED",
         "rule": "Not independent capacity: occupancy may not overlap (intersecting_runway_ids); "
                 "wake pairs do not apply across the crossing"}
        for p in CROSSINGS
    ] + [
        {"runways": list(p), "relationship": "CLOSELY_SPACED_PARALLEL", "source_type": "SOURCED",
         "spacing_m": SYNTHETIC_PARALLEL_SPACING_M, "spacing_source_type": "SYNTHETIC",
         "rule": "Dependent capacity (dependent_runway_ids): occupancy and wake pairs from Member 1 "
                 "rules_config apply"}
        for p in CLOSE_PARALLELS
    ]

    return {
        "layout_id": "sfo-al375-2611",
        "airport_iata": "SFO",
        "source": SOURCE,
        "coordinate_frame": {
            "units": "metres", "x_axis": "magnetic 104 deg (runway 10 direction)",
            "y_axis": "magnetic 014 deg (runway 01 direction)", "source_type": "SYNTHETIC",
            "note": "Schematic positions for display and taxi distance; not surveyed coordinates",
        },
        "runways": runways,
        "runway_relationships": relationships,
        "arrival_exit_nodes": ARRIVAL_EXIT_NODE,
        "hot_spots": [{"hot_spot_id": f"HS{i}", "source_type": "SOURCED"} for i in range(1, 5)],
        "taxiway_nodes": [{"node_id": k, "x": round(v[0]), "y": round(v[1])} for k, v in sorted(nodes.items())],
        "taxiway_segments": taxi_edges,
        "gates": build_gates(gate_count, heavy_count),
        "coverage_limits": [
            "Taxiway graph is a bounded abstraction of AL-375: main parallels A, B, C, L, the M apron "
            "connector and four runway crossings. Many real connectors and holding bays are omitted.",
            "Gate identifiers and counts are simulated; AL-375 shows boarding areas, not gates.",
            "Parallel runway spacing and all coordinates are schematic.",
            "Layout effective 2026-10-29 is paired with 2015 traffic.",
        ],
    }


def adjacency(layout: dict) -> dict[str, list[tuple[str, str, float]]]:
    adj = collections.defaultdict(list)
    for s in layout["taxiway_segments"]:
        adj[s["from_node"]].append((s["to_node"], s["segment_id"], s["length_m"]))
        if s["bidirectional"]:
            adj[s["to_node"]].append((s["from_node"], s["segment_id"], s["length_m"]))
    return adj


def shortest_path(layout: dict, start: str, goal: str, blocked: set[str] = frozenset()
                  ) -> tuple[float, list[str]] | None:
    """Dijkstra over taxiway segments, skipping blocked segment IDs. Returns (metres, segment IDs)."""
    adj = adjacency(layout)
    queue = [(0.0, start, [])]
    seen = set()
    while queue:
        dist, node, path = heapq.heappop(queue)
        if node == goal:
            return dist, path
        if node in seen:
            continue
        seen.add(node)
        for nxt, seg, length in adj[node]:
            if seg not in blocked and nxt not in seen:
                heapq.heappush(queue, (dist + length, nxt, path + [seg]))
    return None


def is_connected(layout: dict) -> bool:
    adj = adjacency(layout)
    nodes = [n["node_id"] for n in layout["taxiway_nodes"]]
    seen, stack = {nodes[0]}, [nodes[0]]
    while stack:
        for nxt, _, _ in adj[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen == set(nodes)


def split_segment(seg: dict, max_piece_m: float) -> list[tuple[str, str, str, float]]:
    """Split a segment into equal pieces of at most max_piece_m, in order from_node -> to_node.

    The engine sizes a taxi interval as (segments x slowest segment), so uniform pieces keep
    that estimate close to the route's real length. Single pieces keep the segment ID.
    """
    n = max(1, math.ceil(seg["length_m"] / max_piece_m))
    if n == 1:
        return [(seg["segment_id"], seg["from_node"], seg["to_node"], seg["length_m"])]
    nodes = [seg["from_node"], *[f"{seg['segment_id']}~{k}" for k in range(1, n)], seg["to_node"]]
    return [(f"{seg['segment_id']}#{k + 1}", nodes[k], nodes[k + 1], seg["length_m"] / n) for k in range(n)]


def to_resources(layout: dict, *, taxi_speed_mps: float, max_piece_m: float,
                 min_traversal_s: int = 30) -> list[dict]:
    """Convert the layout to Member 1 contract Resource dicts (RUNWAY, TAXIWAY, GATE).

    The engine routes over TAXIWAY edges whose endpoints are runway and gate resource IDs,
    so each runway is a node joined to the hold points at both of its ends, and each gate
    is a node joined to its boarding area apron. Runway direction is not fixed here.
    Segments longer than max_piece_m are split into equal pieces (see split_segment).
    """
    # Close parallels share wake separation (dependent); crossing runways share an intersection,
    # so the engine keeps their occupancy apart without applying wake pairs.
    field = {"CLOSELY_SPACED_PARALLEL": "dependent_runway_ids", "INTERSECTING": "intersecting_runway_ids"}
    rel: dict[str, dict[str, set[str]]] = {r["runway_id"]: {f: set() for f in field.values()}
                                           for r in layout["runways"]}
    for item in layout["runway_relationships"]:
        a, b = item["runways"]
        rel[a][field[item["relationship"]]].add(b)
        rel[b][field[item["relationship"]]].add(a)
    resources = [{"resource_id": r["runway_id"], "kind": "RUNWAY", "capacity": 1,
                  **{f: sorted(ids) for f, ids in rel[r["runway_id"]].items()}} for r in layout["runways"]]

    def edge(rid, a, b, length_m):
        return {"resource_id": rid, "kind": "TAXIWAY", "capacity": 1, "node_from": a, "node_to": b,
                "bidirectional": True,
                "minimum_traversal_seconds": max(min_traversal_s, round(length_m / taxi_speed_mps))}

    for r in layout["runways"]:
        for end, hold in r["end_nodes"].items():
            resources.append(edge(f"RWYLINK:{r['runway_id']}@{end}", r["runway_id"], hold, 0))
    for seg in layout["taxiway_segments"]:
        resources += [edge(rid, a, b, length) for rid, a, b, length in split_segment(seg, max_piece_m)]
    for g in layout["gates"]:
        resources.append(edge(f"STAND:{g['gate_id']}", g["apron_node"], g["gate_id"], 0))
        resources.append({"resource_id": g["gate_id"], "kind": "GATE", "capacity": 1,
                          "compatible_categories": ["MEDIUM", "HEAVY"] if g["max_wake_category"] == "HEAVY"
                          else ["MEDIUM"]})
    return resources


GATE_COLUMNS = 4
GATE_SPACING_M = 45


def gate_positions(layout: dict) -> dict[str, list[int]]:
    """Schematic gate positions (SYNTHETIC): a grid on each boarding area's apron, away from the field."""
    nodes = {n["node_id"]: (n["x"], n["y"]) for n in layout["taxiway_nodes"]}
    by_area: dict[str, list[str]] = collections.defaultdict(list)
    for g in layout["gates"]:
        by_area[g["boarding_area"]].append(g["gate_id"])
    out = {}
    for area, gates in by_area.items():
        ax, ay = nodes[f"APRON_{area}"]
        for i, gate in enumerate(gates):
            col, row = i % GATE_COLUMNS, i // GATE_COLUMNS
            out[gate] = [round(ax + (col - (GATE_COLUMNS - 1) / 2) * GATE_SPACING_M),
                         round(ay - 60 - row * GATE_SPACING_M)]
    return out


def map_geometry(layout: dict, *, max_piece_m: float) -> dict:
    """Drawing geometry keyed by the contract resource IDs the engine reserves.

    Every TAXIWAY resource from to_resources (pieces, runway links, stands) gets a two-point
    polyline, so a UI can draw an assigned taxi path exactly as planned. Coordinates use the
    layout's schematic frame (metres, SYNTHETIC).
    """
    nodes = {n["node_id"]: (n["x"], n["y"]) for n in layout["taxiway_nodes"]}
    gates = gate_positions(layout)
    runways, segments = [], {}
    for r in layout["runways"]:
        a, b = r["schematic"]["from"], r["schematic"]["to"]
        ends = dict(zip(r["ends"], (a, b)))
        runways.append({"runway_id": r["runway_id"], "ends": ends})
        for end, hold in r["end_nodes"].items():
            segments[f"RWYLINK:{r['runway_id']}@{end}"] = [list(ends[end]), list(nodes[hold])]
    for seg in layout["taxiway_segments"]:
        (x0, y0), (x1, y1) = nodes[seg["from_node"]], nodes[seg["to_node"]]
        pieces = split_segment(seg, max_piece_m)
        n = len(pieces)
        for k, (rid, *_rest) in enumerate(pieces):
            segments[rid] = [[round(x0 + (x1 - x0) * k / n), round(y0 + (y1 - y0) * k / n)],
                             [round(x0 + (x1 - x0) * (k + 1) / n), round(y0 + (y1 - y0) * (k + 1) / n)]]
    for g in layout["gates"]:
        segments[f"STAND:{g['gate_id']}"] = [list(nodes[g["apron_node"]]), gates[g["gate_id"]]]
    labels = []
    for seg in layout["taxiway_segments"]:
        if seg["kind"] in ("TAXIWAY", "RUNWAY_CROSSING") and seg["taxiway"] != "A-B":
            (x0, y0), (x1, y1) = nodes[seg["from_node"]], nodes[seg["to_node"]]
            labels.append({"text": seg["taxiway"], "at": [round((x0 + x1) / 2), round((y0 + y1) / 2)]})
    return {"frame": layout["coordinate_frame"], "runways": runways, "segments": segments,
            "gates": gates, "taxiway_labels": labels,
            "boarding_areas": {g["boarding_area"]: list(nodes[g["apron_node"]]) for g in layout["gates"]},
            "source_type": "SYNTHETIC", "source_note": "Schematic positions; runway set and names SOURCED from AL-375"}
