import { useEffect, useMemo, useRef, useState } from "react";
import { Stage, Layer, Line, Rect, Text, Group, Circle } from "react-konva";
import type { LiveSnapshot } from "../api/canonical";
import type { Snapshot } from "../api/types";
import { time } from "../domain";
import {
  aircraftPositions,
  countdown,
  etaTag,
  FINAL_METRES,
  inboundQueue,
  nextMilestone,
  occupiedGates,
  parkedAircraft,
  routeNames,
  runwayActivity,
  runwayDesignator,
  pathPoints,
  type LayoutMap,
  type Phase,
  type Point,
} from "../map/movement";

// Map of the scenario's own layout (Member 2 geometry): runways, the taxiway network the engine
// reserves, simulated gates, and aircraft positioned from authorized assignments.
const PUSH_WARNING_MS = 5 * 60_000; // flag a gate this long before its departure pushes back
// Top-down airliner silhouette, nose up (-y), about 18 px long at scale 1.
const PLANE = [0, -9, 1.4, -4, 8, 0.5, 8, 2.2, 1.4, 0.6, 1, 5.5, 3.6, 7.6, 3.6, 8.8, 0, 7.8, -3.6, 8.8, -3.6, 7.6,
  -1, 5.5, -1.4, 0.6, -8, 2.2, -8, 0.5, -1.4, -4];
const PARKED_COLOR = "#8d9a8c";
const PHASE_COLOR: Record<Phase, string> = {
  APPROACH: "#2f6f9f",
  LANDING: "#2f6f9f",
  TAXI_IN: "#2f6f9f",
  AT_GATE: "#5e7a61",
  TAXI_OUT: "#c0702c",
  HOLDING: "#c0702c",
  TAKEOFF: "#c0702c",
};

export default function RealAirportMap({
  snapshot,
  map,
  selected,
  onSelect,
}: {
  snapshot: Snapshot;
  map: LayoutMap;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState<Point>([0, 0]);
  const [tick, setTick] = useState(Date.now());
  const received = useRef(Date.now());
  useEffect(() => {
    received.current = Date.now();
  }, [snapshot.sim_time_utc]);
  // Simulated ms per wall ms: the session's clock speed (1 = real time).
  const rate = (snapshot as LiveSnapshot).clock_speed ?? 1;
  useEffect(() => {
    const observer = new ResizeObserver((e) => setWidth(e[0].contentRect.width));
    if (wrap.current) observer.observe(wrap.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!snapshot.clock_running || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let frame = 0;
    const update = () => {
      setTick(Date.now());
      frame = requestAnimationFrame(update);
    };
    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, [snapshot.clock_running]);
  // The backend clock ticks once per second; interpolate at most one tick between updates.
  const now =
    Date.parse(snapshot.sim_time_utc) +
    (snapshot.clock_running ? Math.min(1000, Math.max(0, tick - received.current)) * rate : 0);

  // Extended centrelines beyond each threshold: where arrivals fly final and departures climb out.
  const extensions = useMemo(
    () =>
      map.runways.flatMap((r) => {
        const [a, b] = Object.values(r.ends);
        if (!a || !b) return [];
        const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
        const ux = (b[0] - a[0]) / len, uy = (b[1] - a[1]) / len;
        return [
          [a, [a[0] - ux * FINAL_METRES, a[1] - uy * FINAL_METRES]],
          [b, [b[0] + ux * FINAL_METRES, b[1] + uy * FINAL_METRES]],
        ] as [Point, Point][];
      }),
    [map],
  );
  const bounds = useMemo(() => {
    const pts: Point[] = [
      ...map.runways.flatMap((r) => Object.values(r.ends)),
      ...extensions.map(([, far]) => far),
      ...Object.values(map.segments).flat(),
      ...Object.values(map.gates),
    ];
    const xs = pts.map((p) => p[0]),
      ys = pts.map((p) => p[1]);
    return { minX: Math.min(...xs) - 150, maxX: Math.max(...xs) + 150, minY: Math.min(...ys) - 150, maxY: Math.max(...ys) + 150 };
  }, [map, extensions]);
  const height = Math.max(320, Math.round(width * 0.62));
  const base = Math.min(width / (bounds.maxX - bounds.minX), height / (bounds.maxY - bounds.minY));
  const s = base * zoom;
  // Schematic frame: x along runway 10, y along runway 01; screen y grows downward.
  const px = (p: Point): [number, number] => [
    (p[0] - bounds.minX) * s + offset[0] + (width - (bounds.maxX - bounds.minX) * s) / 2,
    (bounds.maxY - p[1]) * s + offset[1] + (height - (bounds.maxY - bounds.minY) * s) / 2,
  ];
  const active = (id: string) =>
    snapshot.active_instructions.some(
      (i) => i.resource_id === id && Date.parse(i.starts_at_utc) <= now && (!i.ends_at_utc || Date.parse(i.ends_at_utc) > now),
    );
  const gateIds = useMemo(() => new Set(Object.keys(map.gates)), [map]);
  const occupied = occupiedGates(snapshot, gateIds, now);
  const aircraft = aircraftPositions(snapshot, map, now);
  const parked = parkedAircraft(snapshot, map, now, aircraft);
  const inbound = inboundQueue(snapshot, map, now);
  const runwayUse = runwayActivity(aircraft);
  // Arrivals without an authorized slot orbit holding fixes at the outer ends of the finals.
  const holdingFixes = extensions.map(([, far]) => far);
  const holders = inbound.filter((i) => i.holding);
  const HOLD_RADIUS = 250;
  const holdingPositions = holders.map((h, i) => {
    const fix = holdingFixes[i % Math.max(1, holdingFixes.length)] ?? [0, 0];
    const lap = 4 * 60_000; // one simulated orbit every 4 minutes
    const angle = ((now % lap) / lap) * 2 * Math.PI + (i * 2 * Math.PI) / Math.max(1, holders.length);
    const at: Point = [fix[0] + HOLD_RADIUS * Math.cos(angle), fix[1] + HOLD_RADIUS * Math.sin(angle)];
    const ahead: Point = [fix[0] + HOLD_RADIUS * Math.cos(angle + 0.2), fix[1] + HOLD_RADIUS * Math.sin(angle + 0.2)];
    return { flight: h.flight, at, ahead, since: h.eta };
  });
  const selectedFlight = snapshot.flights.find((f) => f.flight_id === selected);
  const route = selectedFlight?.plan ? pathPoints(map, selectedFlight.plan) : [];
  const selectedPos = aircraft.find((a) => a.flight.flight_id === selected);
  const selectedNext = selectedFlight?.plan ? nextMilestone(selectedFlight.plan, now, map) : undefined;
  // Departures about to leave their gate, so the controller sees where the next pushbacks are.
  const pushingSoon = snapshot.flights.flatMap((f) => {
    const a = f.plan;
    if (a?.operation !== "DEPARTURE" || !a.gate_id || !map.gates[a.gate_id]) return [];
    const push = Date.parse(a.taxi_start_utc);
    return push > now && push - now <= PUSH_WARNING_MS ? [{ flight: f, gate: map.gates[a.gate_id], push }] : [];
  });
  const flat = (pts: Point[]) => pts.flatMap((p) => px(p));
  const gateSize = Math.max(4, 22 * s);
  const planeScale = Math.max(0.7, Math.min(2.4, gateSize / 9));

  return (
    <div className="map-shell" ref={wrap}>
      <div className="map-caption">
        <span className="map-live-dot" /> SFO SURFACE · FAA AL-375 RUNWAYS · SCHEMATIC TAXIWAYS AND SIMULATED GATES
        <span className="map-caption-right">
          {aircraft.filter((a) => a.phase !== "AT_GATE").length} moving ·{" "}
          {aircraft.length - aircraft.filter((a) => a.phase !== "AT_GATE").length + parked.length} parked ·{" "}
          {inbound.length} inbound
        </span>
      </div>
      <Stage width={width} height={height} aria-label="SFO airport surface map"
        draggable onDragEnd={(e) => {
          setOffset([offset[0] + e.target.x(), offset[1] + e.target.y()]);
          e.target.position({ x: 0, y: 0 });
        }}>
        <Layer listening={false}>
          <Rect width={width} height={height} fill="#e9efec" />
          {Object.entries(map.segments).map(([id, seg]) => (
            <Line key={id} points={flat(seg)} lineCap="round"
              stroke={active(id) ? "#c2412d" : id.startsWith("STAND:") ? "#cfd8cf" : "#b9ae8f"}
              strokeWidth={active(id) ? 4 : id.startsWith("STAND:") ? 1 : Math.max(2, 18 * s)} />
          ))}
          {extensions.map(([near, far], i) => (
            <Line key={"final" + i} points={flat([near, far])} stroke="#9fb3c4" strokeWidth={1} dash={[3, 6]} />
          ))}
          {map.runways.map((r) => {
            const ends = Object.entries(r.ends);
            const closed = active(r.runway_id);
            const inUse = runwayUse.get(r.runway_id);
            return (
              <Group key={r.runway_id}>
                {inUse && !closed && (
                  <Line points={flat(ends.map(([, p]) => p))} stroke="#e7b75f" opacity={0.85}
                    strokeWidth={Math.max(6, 60 * s) + 8} lineCap="butt" />
                )}
                <Line points={flat(ends.map(([, p]) => p))} stroke={closed ? "#c2412d" : "#3d4a44"}
                  strokeWidth={Math.max(6, 60 * s)} lineCap="butt" />
                <Line points={flat(ends.map(([, p]) => p))} stroke="#f4f1e8" strokeWidth={1} dash={[8, 8]} />
                {ends.map(([end, p]) => {
                  const [x, y] = px(p);
                  return <Text key={end} x={x - 14} y={y - 20} text={end} fontSize={11} fontStyle="bold" fill={closed ? "#c2412d" : "#2c3a33"} />;
                })}
                {inUse && !closed && (() => {
                  const [x, y] = px([(ends[0][1][0] + ends[1][1][0]) / 2, (ends[0][1][1] + ends[1][1][1]) / 2]);
                  return <Text x={x + 8} y={y - 22} text={inUse} fontSize={11} fontStyle="bold" fill="#9c6a12" />;
                })()}
                {closed && (() => {
                  const [x, y] = px([(ends[0][1][0] + ends[1][1][0]) / 2, (ends[0][1][1] + ends[1][1][1]) / 2]);
                  return <Text x={x - 30} y={y + 8} text="CLOSED" fontSize={11} fontStyle="bold" fill="#c2412d" />;
                })()}
              </Group>
            );
          })}
          {map.taxiway_labels.map((l, i) => {
            const [x, y] = px(l.at);
            return <Text key={i} x={x + 3} y={y - 12} text={l.text} fontSize={10} fill="#8a7a4e" />;
          })}
          {Object.entries(map.boarding_areas).map(([area, p]) => {
            const [x, y] = px(p);
            return <Text key={area} x={x - 30} y={y + 6} text={`B/A ${area}`} fontSize={10} fill="#5e7a61" />;
          })}
          {Object.entries(map.gates).map(([id, p]) => {
            const [x, y] = px(p);
            const taken = occupied.has(id);
            return (
              <Rect key={id} x={x - gateSize / 2} y={y - gateSize / 2} width={gateSize} height={gateSize}
                fill={taken ? "#e7b75f" : "#f8faf6"} stroke={taken ? "#a8781f" : "#a9b8aa"} strokeWidth={1} />
            );
          })}
          {route.length > 1 && <Line points={flat(route)} stroke="#2e8b57" strokeWidth={3} dash={[6, 4]} />}
          {route.length > 1 && selectedFlight?.plan && (() => {
            const departure = selectedFlight.plan.operation === "DEPARTURE";
            const [sx, sy] = px(route[0]), [ex, ey] = px(route[route.length - 1]);
            const rwy = runwayDesignator(map, selectedFlight.plan);
            const gate = selectedFlight.plan.gate_id ?? "stand";
            return (
              <>
                <Circle x={sx} y={sy} radius={5} fill="#2e8b57" />
                <Text x={sx + 7} y={sy + 4} text={departure ? `OFF GATE ${gate}` : `VACATE ${selectedFlight.plan.runway_id}`}
                  fontSize={10} fontStyle="bold" fill="#2e8b57" />
                <Rect x={ex - 5} y={ey - 5} width={10} height={10} fill="#2e8b57" />
                <Text x={ex + 7} y={ey - 14} text={departure ? `HOLD SHORT ${rwy}` : `GATE ${gate}`}
                  fontSize={10} fontStyle="bold" fill="#2e8b57" />
              </>
            );
          })()}
          {pushingSoon.map(({ flight, gate, push }) => {
            const [x, y] = px(gate);
            return (
              <Group key={flight.flight_id}>
                <Circle x={x} y={y} radius={gateSize} stroke="#c0702c" strokeWidth={2} />
                <Text x={x + gateSize + 2} y={y - 5} fontSize={9} fontStyle="bold" fill="#c0702c"
                  text={`PUSH ${Math.max(1, Math.ceil((push - now) / 60000))}m`} />
              </Group>
            );
          })}
        </Layer>
        <Layer>
          {parked.map(({ gate, at, flight, awaitingPlan }) => {
            const [x, y] = px(at);
            const isSelected = !!flight && flight.flight_id === selected;
            return (
              <Group key={"parked:" + gate} x={x} y={y}
                onClick={() => flight && onSelect(flight.flight_id)} onTap={() => flight && onSelect(flight.flight_id)}>
                {isSelected && <Circle radius={11} stroke="#2e8b57" strokeWidth={2} />}
                <Line points={PLANE} closed scaleX={planeScale * 0.8} scaleY={planeScale * 0.8}
                  fill={awaitingPlan ? "#b9a37a" : PARKED_COLOR} opacity={0.9} />
                {flight && (isSelected || zoom >= 2) && (
                  <Text x={8} y={-6} fontSize={10} fill="#4d5c50"
                    text={`${flight.airline}${flight.flight_number}${awaitingPlan ? " · awaiting plan" : ""}`} />
                )}
              </Group>
            );
          })}
          {holdingFixes.map((fix, i) => {
            const [x, y] = px(fix);
            return holders.length > 0 && (
              <Circle key={"fix" + i} x={x} y={y} radius={HOLD_RADIUS * s} stroke="#9fb3c4" strokeWidth={1} dash={[4, 4]} />
            );
          })}
          {holdingPositions.map(({ flight, at, ahead, since }) => {
            const [x, y] = px(at), [ax, ay] = px(ahead);
            const heading = (Math.atan2(ax - x, -(ay - y)) * 180) / Math.PI;
            const isSelected = flight.flight_id === selected;
            return (
              <Group key={"hold:" + flight.flight_id} x={x} y={y}
                onClick={() => onSelect(flight.flight_id)} onTap={() => onSelect(flight.flight_id)}>
                {isSelected && <Circle radius={11} stroke="#2e8b57" strokeWidth={2} />}
                <Line points={PLANE} closed rotation={heading} fill="#7b5ea7" stroke="#ffffff" strokeWidth={0.6}
                  scaleX={planeScale} scaleY={planeScale} />
                <Text x={8} y={-6} fontSize={10} fill="#4b3a6b"
                  text={`${flight.airline}${flight.flight_number} · HOLD ${Math.max(0, Math.round((now - since) / 60000))}m`} />
              </Group>
            );
          })}
          {aircraft.map(({ flight, at, phase, ahead }) => {
            const [x, y] = px(at);
            const isSelected = flight.flight_id === selected;
            const [ax, ay] = ahead ? px(ahead) : [x, y - 1];
            const heading = (Math.atan2(ax - x, -(ay - y)) * 180) / Math.PI;
            const eta = phase === "AT_GATE" || !flight.plan ? "" : etaTag(flight.plan, now, map);
            const label = `${flight.airline}${flight.flight_number}${eta ? " · " + eta : ""}`;
            return (
              <Group key={flight.flight_id} x={x} y={y} onClick={() => onSelect(flight.flight_id)}
                onTap={() => onSelect(flight.flight_id)}>
                {isSelected && <Circle radius={11} stroke="#2e8b57" strokeWidth={2} />}
                <Line points={PLANE} closed rotation={heading} fill={PHASE_COLOR[phase]}
                  stroke="#ffffff" strokeWidth={0.6}
                  scaleX={planeScale * (phase === "AT_GATE" ? 0.8 : 1)} scaleY={planeScale * (phase === "AT_GATE" ? 0.8 : 1)} />
                {(isSelected || phase !== "AT_GATE" || zoom >= 2) && (
                  <Text x={8} y={-6} text={label} fontSize={10} fill="#1f2d26" />
                )}
              </Group>
            );
          })}
        </Layer>
      </Stage>
      {inbound.length > 0 && (
        <div className="map-inbound" aria-label="Inbound arrivals">
          <strong>Inbound</strong>
          {inbound.slice(0, 6).map((i) => (
            <button key={i.flight.flight_id} type="button" onClick={() => onSelect(i.flight.flight_id)}
              className={i.flight.flight_id === selected ? "selected" : ""}>
              <span>
                {i.flight.airline}
                {i.flight.flight_number}
              </span>
              <span>{i.flight.origin}</span>
              <span>{i.awaitingPlan ? "no slot" : i.runway}</span>
              <span className={i.holding ? "eta holding" : "eta"}>
                {i.holding ? `HOLD ${Math.round((now - i.eta) / 60000)}m` : countdown(i.eta, now).replace("in ", "")}
              </span>
            </button>
          ))}
          {inbound.length > 6 && <small>+{inbound.length - 6} more in 45 min</small>}
          {holders.length > 0 && (
            <small className="holding-note">
              {holders.length} holding: no approved plan. Request a plan and approve it to land them.
            </small>
          )}
        </div>
      )}
      {selectedFlight?.plan && (
        <div className="map-flight-card" aria-live="polite">
          <strong>
            {selectedFlight.airline}
            {selectedFlight.flight_number} · {selectedFlight.origin} → {selectedFlight.destination}
          </strong>
          {selectedFlight.plan.operation === "DEPARTURE" ? (
            <>
              Gate {selectedFlight.plan.gate_id ?? "—"} · pushback {time(selectedFlight.plan.taxi_start_utc)}
              <br />
              Taxi {routeNames(selectedFlight.plan).join(" → ") || "direct"} → hold short{" "}
              {runwayDesignator(map, selectedFlight.plan)} {time(selectedFlight.plan.taxi_end_utc)}
              <br />
              Takeoff {runwayDesignator(map, selectedFlight.plan)} {time(selectedFlight.plan.runway_time_utc)}
            </>
          ) : (
            <>
              Land {runwayDesignator(map, selectedFlight.plan)} {time(selectedFlight.plan.runway_time_utc)}
              <br />
              Taxi {routeNames(selectedFlight.plan).join(" → ") || "direct"} → gate{" "}
              {selectedFlight.plan.gate_id ?? "—"} {time(selectedFlight.plan.taxi_end_utc)}
            </>
          )}
          <br />
          {selectedPos ? `Now: ${selectedPos.phase.replace("_", " ").toLowerCase()} · ` : "Not on the surface · "}
          {selectedNext ? (
            <span className="eta">
              {selectedNext.label} {countdown(selectedNext.at, now)}
            </span>
          ) : (
            "movement complete"
          )}
        </div>
      )}
      <div className="map-controls">
        <button aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(1, z / 1.5))}>−</button>
        <button aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(8, z * 1.5))}>+</button>
        <button aria-label="Fit map" onClick={() => { setZoom(1); setOffset([0, 0]); }}>Fit</button>
      </div>
      <div className="map-legend">
        <span style={{ color: PHASE_COLOR.TAXI_IN }}>✈ Arrival</span>{" "}
        <span style={{ color: PHASE_COLOR.TAXI_OUT }}>✈ Departure</span>{" "}
        <span style={{ color: PHASE_COLOR.AT_GATE }}>✈ At gate</span>{" "}
        <span style={{ color: PARKED_COLOR }}>✈ Parked</span>{" "}
        <span style={{ color: "#7b5ea7" }}>✈ Holding</span>
        <span>■ Gate occupied</span> <span style={{ color: PHASE_COLOR.TAXI_OUT }}>◯ Pushback ≤5 min</span>
        <span>┅ Selected route</span> <span style={{ color: "#c79a3b" }}>▬ Runway in use</span> <span style={{ color: "#c2412d" }}>■ Closed</span>
      </div>
    </div>
  );
}
