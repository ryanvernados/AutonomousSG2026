import { useEffect, useMemo, useRef, useState } from "react";
import { Stage, Layer, Line, Rect, Text, Group, Circle, RegularPolygon } from "react-konva";
import type { Snapshot } from "../api/types";
import {
  aircraftAt,
  occupiedGates,
  pathPoints,
  type LayoutMap,
  type Phase,
  type Point,
} from "../map/movement";

// Map of the scenario's own layout (Member 2 geometry): runways, the taxiway network the engine
// reserves, simulated gates, and aircraft positioned from authorized assignments.
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
  // The backend clock advances in real time; interpolate at most a second between updates.
  const now =
    Date.parse(snapshot.sim_time_utc) +
    (snapshot.clock_running ? Math.min(1000, Math.max(0, tick - received.current)) : 0);

  const bounds = useMemo(() => {
    const pts: Point[] = [
      ...map.runways.flatMap((r) => Object.values(r.ends)),
      ...Object.values(map.segments).flat(),
      ...Object.values(map.gates),
    ];
    const xs = pts.map((p) => p[0]),
      ys = pts.map((p) => p[1]);
    return { minX: Math.min(...xs) - 150, maxX: Math.max(...xs) + 150, minY: Math.min(...ys) - 150, maxY: Math.max(...ys) + 150 };
  }, [map]);
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
  const aircraft = snapshot.flights.flatMap((f) => {
    if (!f.plan) return [];
    const pos = aircraftAt(map, f.plan, now);
    return pos ? [{ flight: f, ...pos }] : [];
  });
  const selectedFlight = snapshot.flights.find((f) => f.flight_id === selected);
  const route = selectedFlight?.plan ? pathPoints(map, selectedFlight.plan) : [];
  const flat = (pts: Point[]) => pts.flatMap((p) => px(p));
  const gateSize = Math.max(4, 22 * s);

  return (
    <div className="map-shell" ref={wrap}>
      <div className="map-caption">
        <span className="map-live-dot" /> SFO SURFACE · FAA AL-375 RUNWAYS · SCHEMATIC TAXIWAYS AND SIMULATED GATES
        <span className="map-caption-right">
          {aircraft.length} moving / at gate · {occupied.size} gates occupied
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
          {map.runways.map((r) => {
            const ends = Object.entries(r.ends);
            const closed = active(r.runway_id);
            return (
              <Group key={r.runway_id}>
                <Line points={flat(ends.map(([, p]) => p))} stroke={closed ? "#c2412d" : "#3d4a44"}
                  strokeWidth={Math.max(6, 60 * s)} lineCap="butt" />
                <Line points={flat(ends.map(([, p]) => p))} stroke="#f4f1e8" strokeWidth={1} dash={[8, 8]} />
                {ends.map(([end, p]) => {
                  const [x, y] = px(p);
                  return <Text key={end} x={x - 14} y={y - 20} text={end} fontSize={11} fontStyle="bold" fill={closed ? "#c2412d" : "#2c3a33"} />;
                })}
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
        </Layer>
        <Layer>
          {aircraft.map(({ flight, at, phase }) => {
            const [x, y] = px(at);
            const isSelected = flight.flight_id === selected;
            const label = `${flight.airline}${flight.flight_number}`;
            return (
              <Group key={flight.flight_id} x={x} y={y} onClick={() => onSelect(flight.flight_id)}
                onTap={() => onSelect(flight.flight_id)}>
                {isSelected && <Circle radius={11} stroke="#2e8b57" strokeWidth={2} />}
                <RegularPolygon sides={3} radius={phase === "AT_GATE" ? 5 : 7} fill={PHASE_COLOR[phase]}
                  rotation={flight.destination === "SFO" ? 180 : 0} />
                {(isSelected || phase !== "AT_GATE" || zoom >= 2) && (
                  <Text x={8} y={-6} text={label} fontSize={10} fill="#1f2d26" />
                )}
              </Group>
            );
          })}
        </Layer>
      </Stage>
      <div className="map-controls">
        <button aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(1, z / 1.5))}>−</button>
        <button aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(8, z * 1.5))}>+</button>
        <button aria-label="Fit map" onClick={() => { setZoom(1); setOffset([0, 0]); }}>Fit</button>
      </div>
      <div className="map-legend">
        <span>▲ Arrival</span> <span style={{ color: PHASE_COLOR.TAXI_OUT }}>▲ Departure</span>
        <span>■ Gate occupied</span> <span>┅ Selected route</span> <span style={{ color: "#c2412d" }}>■ Closed</span>
      </div>
    </div>
  );
}
