import { expect, test } from "@playwright/test";

test("allows a read-only aggregation query", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("sql-input").fill(`
    SELECT region, COUNT(*) AS n
    FROM customers
    GROUP BY region
    ORDER BY region
  `);
  await page.getByTestId("submit-query").click();
  await expect(page.getByTestId("query-result")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("query-result")).toContainText("succeeded");
});

test("shows rejection for write SQL", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("sql-input").fill("DELETE FROM customers WHERE id = 1");
  await page.getByTestId("submit-query").click();
  await expect(page.getByTestId("query-error")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("query-error")).toContainText("rejected");
});
