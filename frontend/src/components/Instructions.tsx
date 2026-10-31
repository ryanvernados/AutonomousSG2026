import { useEffect, useState } from "react";
import { ArrowRight, X, AlertTriangle } from "lucide-react";
import type {
  AirportClient,
  InstructionDraft,
  Snapshot,
  Preview,
} from "../api/types";
import { dateTime, instructionLabel } from "../domain";
import type { LiveSnapshot } from "../api/canonical";
export default function Instructions({
  snapshot,
  client,
  sessionId,
  onConfirm,
  onClose,
}: {
  snapshot: Snapshot;
  client: AirportClient;
  sessionId: string;
  onConfirm: (draft: InstructionDraft, version: number) => Promise<void>;
  onClose: () => void;
}) {
  const [kind, setKind] = useState<InstructionDraft["kind"]>("RUNWAY_CLOSURE");
  // LIVE snapshots carry resource IDs discovered from the scenario; mock keeps its fixture set.
  const catalog = (snapshot as LiveSnapshot).resource_catalog;
  const resourcesFor = (k: InstructionDraft["kind"]) =>
    k === "RUNWAY_CLOSURE"
      ? catalog?.runways.length
        ? catalog.runways
        : ["28R", "28L"]
      : k === "TAXIWAY_BLOCKAGE"
        ? catalog?.taxiways.length
          ? catalog.taxiways
          : ["A", "B", "C"]
        : catalog?.equipment.length
          ? catalog.equipment
          : ["TUG-1"];
  const [resource, setResource] = useState(resourcesFor("RUNWAY_CLOSURE")[0]);
  const [flight, setFlight] = useState(snapshot.flights[0]?.flight_id ?? "");
  const [offset, setOffset] = useState(0);
  const [duration, setDuration] = useState(20);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [version, setVersion] = useState(snapshot.state_version);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    document.getElementById("instruction-kind")?.focus();
  }, []);
  const resources = resourcesFor(kind);
  const start = new Date(
    Date.parse(snapshot.sim_time_utc) + offset * 60000,
  ).toISOString();
  const end = new Date(Date.parse(start) + duration * 60000).toISOString();
  const draft: InstructionDraft = {
    kind,
    resource_id: kind === "PROTECTED_SLOT" ? null : resource,
    flight_id: kind === "PROTECTED_SLOT" ? flight : null,
    starts_at_utc: start,
    ends_at_utc: end,
    parameters: {},
  };
  const dirty = () => {
    setPreview(null);
    setError("");
  };
  return (
    <section
      className="panel instruction-panel"
      aria-label="Staff instruction editor"
    >
      <div className="panel-heading">
        <div>
          <span className="eyebrow">STAFF CONTROL</span>
          <h2>Issue an instruction</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close instruction editor"
          onClick={onClose}
        >
          <X size={18} />
        </button>
      </div>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            const result = await client.preview(sessionId, draft);
            setVersion(snapshot.state_version);
            setPreview(result);
          } catch (e) {
            setError(String(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label htmlFor="instruction-kind">Instruction type</label>
        <select
          id="instruction-kind"
          value={kind}
          onChange={(e) => {
            const k = e.target.value as typeof kind;
            setKind(k);
            setResource(resourcesFor(k)[0]);
            dirty();
          }}
        >
          {Object.entries(instructionLabel).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        {kind === "PROTECTED_SLOT" ? (
          <>
            <label htmlFor="instruction-flight">Protected flight</label>
            <select
              id="instruction-flight"
              value={flight}
              onChange={(e) => {
                setFlight(e.target.value);
                dirty();
              }}
            >
              {snapshot.flights.map((f) => (
                <option key={f.flight_id} value={f.flight_id}>
                  {f.airline}
                  {f.flight_number} · {f.origin} → {f.destination}
                </option>
              ))}
            </select>
          </>
        ) : (
          <>
            <label htmlFor="instruction-resource">Affected resource</label>
            <select
              id="instruction-resource"
              value={resource}
              onChange={(e) => {
                setResource(e.target.value);
                dirty();
              }}
            >
              {resources.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </>
        )}
        <div className="form-grid">
          <div>
            <label htmlFor="start-offset">Start in (minutes)</label>
            <input
              id="start-offset"
              type="number"
              min="0"
              max="120"
              required
              value={offset}
              onChange={(e) => {
                setOffset(Number(e.target.value));
                dirty();
              }}
            />
          </div>
          <div>
            <label htmlFor="duration">Duration (minutes)</label>
            <input
              id="duration"
              type="number"
              min="1"
              max="120"
              required
              value={duration}
              onChange={(e) => {
                setDuration(Number(e.target.value));
                dirty();
              }}
            />
          </div>
        </div>
        <p className="time-window">
          {dateTime(start)}
          <br />
          to {dateTime(end)}
        </p>
        {!preview && (
          <button className="button primary full" disabled={busy} type="submit">
            Preview instruction <ArrowRight size={16} />
          </button>
        )}
      </form>
      {error && (
        <p className="alert danger" role="alert">
          {error}
        </p>
      )}
      {preview && (
        <div className="preview-box">
          <span className="eyebrow">READ-ONLY PREVIEW</span>
          <h3>
            {instructionLabel[preview.instruction.kind]} ·{" "}
            {preview.instruction.resource_id ??
              snapshot.flights.find(
                (f) => f.flight_id === preview.instruction.flight_id,
              )?.flight_number}
          </h3>
          <p>
            {dateTime(preview.instruction.starts_at_utc)} →{" "}
            {dateTime(preview.instruction.ends_at_utc!)}
          </p>
          <p>
            Confirmation adds a restriction and invalidates pending plans. It
            does not authorize a recovery schedule.
          </p>
          {preview.issues.map((issue) => (
            <p className="alert danger" key={issue}>
              <AlertTriangle size={15} />
              {issue}
            </p>
          ))}
          {version !== snapshot.state_version && (
            <p className="alert danger">
              State changed. Preview the instruction again.
            </p>
          )}
          <div className="button-row">
            <button className="button" onClick={() => setPreview(null)}>
              Revise
            </button>
            <button
              className="button primary"
              disabled={
                busy ||
                preview.issues.length > 0 ||
                version !== snapshot.state_version
              }
              onClick={async () => {
                setBusy(true);
                try {
                  await onConfirm(preview.instruction, version);
                  onClose();
                } catch (e) {
                  setError(String(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              Confirm instruction
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
