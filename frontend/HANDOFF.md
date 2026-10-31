# Member 4 completion report

## Delivered

Groundline, a runnable React/TypeScript/Vite controller with a React Konva airport view, flight board, runway/gate timeline, typed staff instructions, immutable proposal review, approval/rejection, clock controls, manual mode, replay, comparison, and redacted trace export. Supabase sign-in and an authenticated FastAPI/SSE adapter are implemented behind the same interface as the explicit mock.

All authored source, configuration, lockfile, tests, documentation and screenshot evidence are under `frontend/`. The existing source documents and raw datasets are preserved. No commits or deployments were made.

## Run and verify

On this machine, from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\frontend\start.ps1
```

Standard setup: `cd frontend`, `pnpm install --frozen-lockfile`, `pnpm dev`. Build: `pnpm build`. Unit/component verification: `pnpm test`. Browser verification: `pnpm exec playwright install chromium`, then `pnpm test:e2e`.

Verification on 31 October 2026:

| Check | Result |
| --- | --- |
| TypeScript and production Vite build | Passed |
| Vitest adapter/domain/component tests | 23 passed |
| Chromium browser workflows | 6 passed |
| Desktop 1440 × 1100 and mobile 390 × 844 | Rendered, screenshots inspected |
| Keyboard navigation | Skip link and editor focus verified |
| Mobile page overflow | No horizontal page overflow; timeline scrolls within its panel |
| Browser runtime errors in full workflow | None |
| Negotiation/rejection publish boundary | Published flight board remained unchanged |
| Complete approval | Changed full published fixture plan after authorization |
| Failure paths | Unknown, expired, stale, missing/contradictory ballots, missing checks, fallback, no candidate, disconnect, manual cancellation |
| Access handling | Missing token prevents fetch; mocked 401/403/409/422/503 surfaced |
| SSE | Split CRLF/multiline parsing, heartbeat, cursor replay and reconnect verified |

Browser evidence: `evidence/01-controller-desktop.png`, `02-instruction-preview.png`, `03-proposal-review.png`, `04-controller-mobile.png`. The reproducible tests regenerate these files.

## Example

Input: runway closure on 28R, start at 10:00 PDT on 15 July 2015, duration 20 minutes. Preview does not change state. Confirmation adds the instruction at state v2 and displays two overlapping published reservations. Two mock rounds return a 24-flight plan, with zero live model calls, scripted checks and an exact SHA-256 identity. The first flight's runway slot changes from 10:08 to proposed 10:21. The published 10:08 remains until authorization; rejection leaves it untouched.

## Contract and assumptions

No canonical backend schema was available. `contracts/ui-draft.openapi.json` and its generated TypeScript are explicitly provisional; the shared contract has not been changed. Full field/response/resource alignment requests and examples are in `INTEGRATION.md`.

The default scenario is a synthetic 24-flight, two-hour UI fixture with seed 42, six gates and an illustrative SFO schematic. The mock is not a physics, resource or constraint engine. It does not prove that protected slots, fuel, runway intersections, taxi paths, gates, crews or equipment are operationally feasible. It emits clearly labeled scripted check and agent results to exercise the controller workflow.

Replay retains the latest 100 snapshots in browser memory. Trace export retains the latest 500 observed events with actor and arbitrary payload removed. Comparison baseline/holding/taxi values are illustrative fixture values. The map is an indicative surface view with a subset of aircraft markers; all 24 flights are available in the flight board. No historical source-row claim, measured optimization gain or live model result is made.

## Remaining integration and acceptance

The Member 4 frontend demo is runnable and its applicable local tests pass. The integrated competition system is **not yet validated**: canonical FastAPI responses, Supabase credentials/test memberships, Member 2's normalized historical scenario and sourced graph, Member 3's independent live agents, durable replay/evaluation, full backend access/atomicity/recovery tests, and controller usability feedback remain external dependencies.

**Exact next action:** Member 1 supplies canonical OpenAPI plus one snapshot and completed negotiation JSON. Regenerate frontend types, align the isolated adapter, configure `.env.local`, and rerun the approval-boundary workflow against the real backend with authorized and unauthorized Supabase users. Then substitute Member 2's data/layout and Member 3's live negotiation records. No UI rewrite is intended, but schema/resource alignment is required.
