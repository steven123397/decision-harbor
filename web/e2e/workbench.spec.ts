import { expect, test } from "@playwright/test";

test("allowed SQL shows running then a result table", async ({ page }) => {
  await page.route("**/api/v1/query-runs", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 400));
    await route.continue();
  });
  await page.goto("/");
  await page.getByLabel("SQL").fill("SELECT id, region FROM customers ORDER BY id LIMIT 2");
  await page.getByRole("button", { name: "提交" }).click();
  await expect(page.getByRole("status")).toHaveText("执行中");
  await expect(page.getByRole("columnheader", { name: "id" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "region" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "1" })).toBeVisible();
});

test("rejected SQL shows the policy error", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("SQL").fill("DELETE FROM orders");
  await page.getByRole("button", { name: "提交" }).click();
  await expect(page.getByRole("alert")).toContainText("POLICY_DENIED");
});
