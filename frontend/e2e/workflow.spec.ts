import { test, expect } from "@playwright/test";
for (const outcome of ["unknown", "expired", "fallback", "no-plan"]) {
  test(`failure condition: ${outcome}`, async ({ page }) => {
    await page.goto("/");
    await page.getByText("Demo conditions", { exact: false }).click();
    await page.getByLabel("Next negotiation outcome").selectOption(outcome);
    await page.getByRole("button", { name: "Start negotiation" }).click();
    if (outcome === "no-plan") {
      await expect(page.locator(".proposal-status")).toContainText(
        "no feasible plan found",
      );
      await expect(
        page.getByRole("button", { name: "Approve in simulator" }),
      ).toHaveCount(0);
    } else if (outcome === "fallback") {
      await expect(page.locator(".proposal-status")).toContainText("FALLBACK");
      await expect(
        page.getByRole("button", { name: "Approve in simulator" }),
      ).toBeEnabled();
    } else {
      await expect(
        page.getByRole("button", { name: "Approve in simulator" }),
      ).toBeDisabled();
      await expect(page.locator(".proposal-panel")).toContainText(
        outcome === "expired" ? "Expired proposal" : "unknown",
      );
    }
  });
}
test("complete recovery workflow, rejection boundary, disconnect and manual control", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto("/");
  await expect(page.getByText("Keep the recovery moving.")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("01-controller-desktop.png"),
    fullPage: true,
  });
  const board = page.locator(".flight-table tbody");
  const initial = await board.innerText();
  await page.getByRole("button", { name: "Add instruction" }).click();
  await page.getByRole("button", { name: "Preview instruction" }).click();
  await expect(page.getByText("READ-ONLY PREVIEW")).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("02-instruction-preview.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Confirm instruction" }).click();
  await page.getByRole("button", { name: "Start negotiation" }).click();
  await expect(
    page.getByRole("button", { name: "Approve in simulator" }),
  ).toBeEnabled();
  expect(await board.innerText()).toBe(initial);
  await page.screenshot({
    path: testInfo.outputPath("03-proposal-review.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Reject proposal" }).click();
  expect(await board.innerText()).toBe(initial);
  await page.getByRole("button", { name: "Request a new plan" }).click();
  await expect(
    page.getByRole("button", { name: "Approve in simulator" }),
  ).toBeEnabled();
  await page.getByText("Demo conditions", { exact: false }).click();
  await page.getByRole("button", { name: "Simulate disconnect" }).click();
  await expect(
    page.getByRole("button", { name: "Approve in simulator" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Reconnect demo" }).click();
  await expect(
    page.getByRole("button", { name: "Approve in simulator" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Approve in simulator" }).click();
  await expect(page.locator(".proposal-status")).toContainText("authorized");
  expect(await board.innerText()).not.toBe(initial);
  await page.getByRole("button", { name: "Manual", exact: true }).click();
  await expect(
    page.getByText("Manual control is active.", { exact: false }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Request a new plan" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("region", { name: "Confirmed restrictions" }),
  ).toContainText("Runway closure");
  expect(errors).toEqual([]);
});
test("narrow screen and keyboard access", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByText("Keep the recovery moving.")).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Skip to controller" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "Add instruction" }).click();
  await expect(page.getByLabel("Instruction type")).toBeFocused();
  await page.getByLabel("Instruction type").selectOption("TAXIWAY_BLOCKAGE");
  await page.getByRole("button", { name: "Preview instruction" }).click();
  await expect(
    page.getByRole("button", { name: "Confirm instruction" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("04-controller-mobile.png"),
    fullPage: true,
  });
});
