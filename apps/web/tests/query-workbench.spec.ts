import { expect, test } from "@playwright/test";

test("executes an allowed query and shows the result table", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("DecisionHarbor")).toBeVisible();
  await page.getByRole("button", { name: "Execute query" }).click();
  await expect(page.getByText("SUCCEEDED")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator("table")).toBeVisible();
});

test("shows a stable rejection for an unauthorized object", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("SQL query").fill("SELECT * FROM platform.query_runs");
  await page.getByRole("button", { name: "Execute query" }).click();
  await expect(page.getByText("object_not_allowed")).toBeVisible({ timeout: 15_000 });
});
