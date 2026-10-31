import { test, expect } from "@playwright/test";

// Full-stack check against the real backend (Members 1, 2 and 3), not the mock client.
// Start backend.coordination.server in AUTH_MODE=demo and the UI with VITE_API_MODE=live
// VITE_AUTH_MODE=demo, then run: LIVE_E2E=1 node scripts/test-e2e.mjs e2e/live-integration.spec.ts
test.skip(!process.env.LIVE_E2E, "Set LIVE_E2E=1 with live backend and UI running");

test("live: restriction, two-round agent consensus, approval boundary and authorization", async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto("/");
  await expect(page.getByText("LIVE API", { exact: false })).toBeVisible();

  // Choose Member 1's synthetic scenario; the SFO historical scenarios are also listed.
  await page.getByLabel("Toggle guide and data coverage").click();
  const picker = page.locator("#scenario-picker");
  await expect(picker.locator("option")).toHaveCount(3);
  await picker.selectOption("sfo-synthetic-v1");
  await page.getByLabel("Toggle guide and data coverage").click();
  await expect(page.locator(".flight-table tbody tr").first()).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("live-01-workspace.png"), fullPage: true });

  const board = page.locator(".flight-table tbody");
  const initial = await board.innerText();
  await page.getByRole("button", { name: "Add instruction" }).click();
  await page.getByLabel("Resource").selectOption("28L");
  await page.getByRole("button", { name: "Preview instruction" }).click();
  await expect(page.getByText("READ-ONLY PREVIEW")).toBeVisible();
  await page.getByRole("button", { name: "Confirm instruction" }).click();
  await expect(page.getByRole("region", { name: "Confirmed restrictions" })).toContainText("Runway closure");

  await page.getByRole("button", { name: "Start negotiation" }).click();
  const approve = page.getByRole("button", { name: "Approve in simulator" });
  await expect(approve).toBeEnabled({ timeout: 60_000 });
  await expect(page.locator(".proposal-panel")).toContainText("CONSENSUS");
  // Negotiation alone never publishes the proposal.
  expect(await board.innerText()).toBe(initial);
  await page.screenshot({ path: testInfo.outputPath("live-02-consensus-review.png"), fullPage: true });

  await approve.click();
  await expect(page.locator(".proposal-status")).toContainText("authorized", { timeout: 30_000 });
  await expect.poll(() => board.innerText()).not.toBe(initial);
  await page.screenshot({ path: testInfo.outputPath("live-03-authorized.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("live SFO: real airport map, consensus on 2015 traffic, ground movements and moving aircraft", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto("/");
  await page.getByLabel("Toggle guide and data coverage").click();
  await page.locator("#scenario-picker").selectOption("sfo-2015-12-21-2000-dev");
  await page.getByLabel("Toggle guide and data coverage").click();
  // Real layout map from Member 2 geometry, with the ground list waiting for a plan.
  await expect(page.getByText("FAA AL-375 RUNWAYS", { exact: false })).toBeVisible();
  await expect(page.getByRole("region", { name: "Ground movements" })).toContainText("No authorized plan yet");
  await page.locator(".airport-panel").screenshot({ path: testInfo.outputPath("sfo-01-map-before-plan.png") });

  await page.getByRole("button", { name: "Add instruction" }).click();
  await page.getByLabel("Resource").selectOption("10R-28L");
  await page.getByRole("button", { name: "Preview instruction" }).click();
  await page.getByRole("button", { name: "Confirm instruction" }).click();
  await page.getByRole("button", { name: "Start negotiation" }).click();
  const approve = page.getByRole("button", { name: "Approve in simulator" });
  await expect(approve).toBeEnabled({ timeout: 120_000 });
  await expect(page.locator(".proposal-panel")).toContainText("CONSENSUS");
  await approve.click();
  await expect(page.locator(".proposal-status")).toContainText("authorized", { timeout: 30_000 });

  const ground = page.getByRole("region", { name: "Ground movements" });
  await expect(ground.locator("tbody tr").first()).toBeVisible();
  await expect(ground).toContainText("Push from SIM-");
  await page.locator(".ground-table tbody tr").first().click();
  for (let i = 0; i < 90; i++) {
    // Advance to the busy part of the window so aircraft are on approach, taxiing and at gates.
    if (await page.locator(".map-caption-right").innerText().then((t) => !t.startsWith("0 moving"))) break;
    await page.getByRole("button", { name: "+1 min" }).click();
    await page.waitForTimeout(150);
  }
  await expect(page.locator(".map-caption-right")).not.toContainText("0 moving");
  await page.locator(".airport-panel").screenshot({ path: testInfo.outputPath("sfo-02-map-with-traffic.png") });
  await ground.screenshot({ path: testInfo.outputPath("sfo-03-ground-movements.png") });
  expect(errors).toEqual([]);
});
