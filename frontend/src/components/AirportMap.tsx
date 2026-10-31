import { useEffect, useRef, useState } from "react";
import {
  Stage,
  Layer,
  Rect,
  Line,
  Text,
  Group,
  Circle,
  Path,
} from "react-konva";
import type { Snapshot } from "../api/types";
import type { LiveSnapshot } from "../api/canonical";
import RealAirportMap from "./RealAirportMap";

// Scenarios that carry layout geometry get the real map; others keep the illustrative schematic.
export default function AirportMap(props: {
  snapshot: Snapshot;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const layout = (props.snapshot as LiveSnapshot).layout_map;
  return layout ? <RealAirportMap {...props} map={layout} /> : <IllustrativeMap {...props} />;
}

function IllustrativeMap({
  snapshot,
  selected,
  onSelect,
}: {
  snapshot: Snapshot;
  selected: string;
  onSelect: (id: string) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  const [zoom, setZoom] = useState(1);
  const [tick, setTick] = useState(0);
  const received = useRef(Date.now());
  useEffect(() => {
    received.current = Date.now();
  }, [snapshot.sim_time_utc]);
  useEffect(() => {
    const observer = new ResizeObserver((entries) =>
      setWidth(entries[0].contentRect.width),
    );
    if (wrap.current) observer.observe(wrap.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (
      !snapshot.clock_running ||
      matchMedia("(prefers-reduced-motion: reduce)").matches
    )
      return;
    let frame = 0;
    const update = () => {
      setTick(Date.now());
      frame = requestAnimationFrame(update);
    };
    frame = requestAnimationFrame(update);
    return () => cancelAnimationFrame(frame);
  }, [snapshot.clock_running]);
  const now =
    Date.parse(snapshot.sim_time_utc) +
    (snapshot.clock_running
      ? Math.min(10000, Math.max(0, tick - received.current) * 10)
      : 0);
  const closed = (id: string) =>
    snapshot.active_instructions.some(
      (i) =>
        i.resource_id === id &&
        Date.parse(i.starts_at_utc) <= now &&
        (!i.ends_at_utc || Date.parse(i.ends_at_utc) > now),
    );
  const scale = width / 850;
  return (
    <div className="map-shell" ref={wrap}>
      <div className="map-caption">
        <span className="map-live-dot" /> SIMULATED SURFACE{" "}
        <span className="map-caption-right">N ↗</span>
      </div>
      <div
        role="img"
        aria-label="Illustrative airport map. Select flights from the accessible flight list below; positions are simulated."
      >
        <Stage
          width={width}
          height={390}
          scaleX={scale * zoom}
          scaleY={scale * zoom}
          draggable={zoom > 1}
        >
          <Layer>
            <Rect width={1600} height={1000} fill="#e9efec" />
            {Array.from({ length: 32 }, (_, i) => (
              <Line
                key={`v${i}`}
                points={[i * 35, 0, i * 35, 900]}
                stroke="#dfe7e2"
                strokeWidth={0.7}
              />
            ))}
            {Array.from({ length: 22 }, (_, i) => (
              <Line
                key={`h${i}`}
                points={[0, i * 35, 1400, i * 35]}
                stroke="#dfe7e2"
                strokeWidth={0.7}
              />
            ))}
            <Line
              points={[
                620, 0, 850, 0, 850, 650, 770, 650, 760, 490, 720, 370, 690,
                300, 650, 190,
              ]}
              fill="#d7e6e5"
              closed
            />
            <Text
              x={744}
              y={100}
              text="SAN\nFRANCISCO\nBAY"
              fontSize={11}
              lineHeight={1.5}
              fill="#86a6a4"
              rotation={22}
            />
            <Line
              points={[190, 345, 710, 120]}
              stroke="#bcc9bf"
              strokeWidth={70}
            />
            <Line
              points={[255, 80, 590, 430]}
              stroke="#c9d2ca"
              strokeWidth={57}
            />
            {[
              { id: "28R", x: 188, y: 291 },
              { id: "28L", x: 209, y: 338 },
            ].map((r) => (
              <Group key={r.id} x={r.x} y={r.y} rotation={-24}>
                <Rect
                  width={510}
                  height={29}
                  fill={closed(r.id) ? "#b58263" : "#71827a"}
                  cornerRadius={2}
                />
                <Line
                  points={[30, 14, 480, 14]}
                  stroke="#f1f5ec"
                  strokeWidth={1.3}
                  dash={[13, 11]}
                />
                {[8, 15, 22, 483, 490, 497].map((x) => (
                  <Line
                    key={x}
                    points={[x, 5, x, 24]}
                    stroke="#dce5dc"
                    strokeWidth={2}
                  />
                ))}
                <Text x={55} y={7} text={r.id} fill="white" fontSize={13} />
                {closed(r.id) && (
                  <Text
                    x={220}
                    y={6}
                    text="× CLOSED"
                    fill="white"
                    fontSize={15}
                    fontStyle="bold"
                  />
                )}
              </Group>
            ))}
            <Group x={275} y={85} rotation={45}>
              <Rect width={380} height={22} fill="#a7b3a8" />
              <Line
                points={[10, 11, 370, 11]}
                stroke="#ecf0e8"
                dash={[10, 10]}
              />
              <Text
                x={15}
                y={3}
                text="01L / 19R · out of scope"
                fill="#f6f8f3"
                fontSize={10}
              />
            </Group>
            <Line
              points={[150, 219, 300, 190, 590, 75]}
              stroke={closed("A") ? "#c27048" : "#d4b67a"}
              strokeWidth={12}
            />
            <Line
              points={[200, 255, 375, 208, 650, 90]}
              stroke={closed("B") ? "#c27048" : "#d4b67a"}
              strokeWidth={10}
            />
            <Line
              points={[150, 219, 200, 255, 240, 282]}
              stroke={closed("C") ? "#c27048" : "#d4b67a"}
              strokeWidth={9}
            />
            <Text x={425} y={126} text="A" fill="#8a754c" fontSize={12} />
            <Text x={465} y={175} text="B" fill="#8a754c" fontSize={12} />
            <Text x={169} y={248} text="C" fill="#8a754c" fontSize={12} />
            <Rect
              x={58}
              y={71}
              width={115}
              height={94}
              fill="#ccd6ca"
              stroke="#b6c4b5"
              cornerRadius={10}
            />
            <Text x={80} y={87} text="TERMINAL" fontSize={11} fill="#758774" />
            <Text
              x={80}
              y={106}
              text="GATE SUBSET"
              fontSize={9}
              fill="#869581"
            />
            {Array.from({ length: 6 }, (_, i) => {
              const x = 75 + (i % 3) * 31,
                y = 148 + Math.floor(i / 3) * 35;
              const occupied = snapshot.resource_reservations.some(
                (r) =>
                  r.resource_id === `G${i + 1}` &&
                  Date.parse(r.starts_at_utc) <= now &&
                  Date.parse(r.ends_at_utc) > now,
              );
              return (
                <Group key={i}>
                  <Rect
                    x={x}
                    y={y}
                    width={23}
                    height={25}
                    fill={occupied ? "#96b9a7" : "#d6e0d2"}
                    stroke="#97aa94"
                  />
                  <Text
                    x={x + 4}
                    y={y + 8}
                    text={`G${i + 1}`}
                    fontSize={9}
                    fill="#54715c"
                  />
                </Group>
              );
            })}
            {snapshot.flights
              .filter((f) => f.state !== "DEPARTED" && f.plan)
              .slice(0, 12)
              .map((f, i) => {
                const start = Date.parse(f.plan!.taxi_start_utc),
                  end = Date.parse(f.plan!.taxi_end_utc),
                  progress = Math.max(
                    0,
                    Math.min(1, (now - start) / (end - start)),
                  );
                const inbound = f.plan!.operation === "ARRIVAL";
                const x = inbound
                  ? 650 - i * 16 - progress * 460
                  : 110 + progress * 480;
                const y = inbound
                  ? 125 + i * 12 + progress * 55
                  : 180 + (i % 3) * 17 - progress * 15;
                const active = f.flight_id === selected;
                return (
                  <Group
                    key={f.flight_id}
                    x={x}
                    y={y}
                    onClick={() => onSelect(f.flight_id)}
                    onTap={() => onSelect(f.flight_id)}
                  >
                    {active && (
                      <Circle
                        radius={20}
                        fill="#ffffffbb"
                        stroke="#147f70"
                        strokeWidth={1}
                      />
                    )}
                    <Path
                      data="M 0 -10 L 3 -2 L 12 3 L 12 5 L 3 3 L 3 9 L 6 12 L 6 14 L 0 12 L -6 14 L -6 12 L -3 9 L -3 3 L -12 5 L -12 3 L -3 -2 Z"
                      fill={active ? "#126c60" : "#568b78"}
                      rotation={inbound ? -115 : 65}
                      scaleX={0.65}
                      scaleY={0.65}
                    />
                    <Text
                      x={14}
                      y={-9}
                      text={`${f.airline}${f.flight_number}`}
                      fontSize={10}
                      fill={active ? "#125e55" : "#5d766a"}
                      fontStyle={active ? "bold" : "normal"}
                    />
                  </Group>
                );
              })}
            <Text
              x={60}
              y={375}
              text="ILLUSTRATIVE LAYOUT · NOT TO SCALE"
              fontSize={10}
              fill="#8b9d8f"
            />
          </Layer>
        </Stage>
      </div>
      <div className="map-controls">
        <button
          aria-label="Zoom out"
          onClick={() => setZoom((z) => Math.max(1, z - 0.2))}
        >
          −
        </button>
        <button
          aria-label="Zoom in"
          onClick={() => setZoom((z) => Math.min(2, z + 0.2))}
        >
          +
        </button>
        <button onClick={() => setZoom(1)}>Fit</button>
      </div>
      <div className="map-legend">
        <span>◆ Aircraft</span>
        <span>━ Taxiway</span>
        <span>▰ Runway</span>
        <span>▦ Gate occupancy</span>
      </div>
    </div>
  );
}
