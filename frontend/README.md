# Groundline — Member 4 controller

A runnable controller workspace for the airport disruption simulator. It implements the Member 4 assignment using React, TypeScript, Vite, React Konva and Supabase Auth. The complete Word master plan, common specification and Member 4 packet were read before implementation.

**Runs immediately in MOCK mode without credentials.** The 24-flight fixture, schematic, agent messages, validation results and comparison metrics are synthetic. This frontend is not the simulation engine and does not establish operational feasibility. Live integration requires Members 1–3's API, normalized scenario and agents.

## Start on this Windows machine

From the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\frontend\start.ps1
```

Open http://127.0.0.1:5173. The launcher detects the Codex bundled Node runtime, so Node does not need to be on your current PATH. Leave its terminal running; Ctrl+C stops it. It installs locked dependencies if missing.

## Standard setup

Node 22.12+ and pnpm 11:

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

No `.env` is needed for the demo. `pnpm build` creates `dist/`; `pnpm preview` serves the production build. Deploy `dist/` to static hosting after setting the desired build-time environment variables. `npm install` and `npm run dev` also work, but pnpm uses the committed lockfile.

## Demonstrate the product

1. Open **Add instruction**, keep runway 28R, start now and duration 20 minutes. Preview is read-only; **Confirm instruction** records the restriction.
2. Start negotiation. Two scripted rounds produce an immutable plan for 24 flights. The published flight board remains unchanged.
3. Inspect slots, routes, gates, concessions, check statuses, agent ballots and plan identity. Restrictions and overlapping published reservations stay visible.
4. Reject to preserve the schedule, or **Approve in simulator** to publish the complete fixture plan. Advance the clock to animate simulated positions.
5. Open **Demo conditions** for fallback after a ground veto, no candidate, unknown fuel check, or expiration. Disconnect while reviewing a plan: approval becomes unavailable. Reconnect reloads the state.
6. Select **Manual** to cancel pending planning and prevent new negotiations while preserving restrictions and published reservations.
7. Explore replay, comparison, flight filtering, gate-time provenance and redacted JSONL trace export. Reset asks before clearing the current simulation.

The editor also supports taxiway blockage, equipment outage and protected flight slots. Times are stored as UTC; the UI displays the SFO date and PST/PDT automatically. Map positions are illustrative; they are not derived from radar or historical tracks. Motion respects the reduced-motion preference.

## Verification

```sh
pnpm build
pnpm test
pnpm exec playwright install chromium
pnpm test:e2e
```

Tests cover the preview boundary; immutable published schedules during negotiation/rejection; full-plan approval and retry idempotency; stale/expired proposals; manual cancellation; unknown/missing checks; incomplete or conflicting ballots; fallback; authenticated API errors; SSE parsing/reconnection; and keyboard/mobile workflows. They validate frontend and mock behavior, not backend database policies or real aircraft constraints.

Browser tests write workflow screenshots to `evidence/`. The final verification counts and limitations are recorded in `HANDOFF.md`.

## Connect the team's backend

Copy `.env.example` to `.env.local` and configure:

```dotenv
VITE_API_MODE=live
VITE_API_BASE_URL=http://localhost:8000/api/v1
VITE_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
VITE_SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLISHABLE_KEY
```

Restart Vite. Sign in with an existing Supabase account that has project membership. The UI uses Supabase only for authentication. Every operational mutation goes through FastAPI. Bearer tokens are sent in request headers, including the fetch-based SSE connection, never query parameters. There are no privileged or model credentials in the frontend.

**The API adapter targets a provisional UI contract, not a published canonical backend schema.** The master plan gives endpoint names and entity fields but does not provide all response shapes, resource coordinates, instruction enums, or metric definitions. See `INTEGRATION.md` for exact examples and gaps. A configuration switch selects the adapter; aligning the provisional contract with Member 1's generated API is still required before live use.

## Source organization

| Path | Purpose |
| --- | --- |
| `src/App.tsx` | Workspace, session lifecycle, stream freshness, commands, replay and comparison |
| `src/components/` | Konva map, flight timeline, typed instruction editor, proposal review |
| `src/api/client.ts` | Single MOCK/LIVE selection point |
| `src/api/live.ts` | Supabase Auth setup, authenticated HTTP and reconnecting SSE |
| `src/api/mock.ts`, `fixtures.ts` | Explicit scripted adapter and reproducible 24-flight fixture |
| `src/api/generated.ts` | Types generated by openapi-typescript |
| `contracts/ui-draft.openapi.json` | Clearly marked provisional frontend API contract |
| `scripts/make-ui-contract.mjs` | Rebuilds the provisional schema; never overwrites backend contracts |
| `src/domain.ts` | Time display and fail-closed authorization eligibility |
| `e2e/`, `src/**/*.test.*` | Browser, adapter and component tests |

Regenerate provisional types with `pnpm generate:types`. At integration, use Member 1's exported OpenAPI as the input instead. No backend, data-processing, Supabase migrations, root configuration or raw datasets were changed.

Implementation references: [React Konva](https://konvajs.org/docs/react/index.html), [Supabase React integration](https://supabase.com/docs/guides/getting-started/quickstarts/reactjs), [Vite](https://vite.dev/guide/).
