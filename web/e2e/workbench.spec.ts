import { expect, test } from "@playwright/test";

test.describe("查询工作台主链", () => {
  test("允许的查询展示结果表格", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").fill(
      "SELECT region, count(*) AS customers FROM customers GROUP BY region ORDER BY region"
    );
    await page.getByTestId("submit").click();

    const table = page.getByTestId("result-table");
    await expect(table).toBeVisible();
    await expect(table.locator("thead th")).toHaveText(["region", "customers"]);
    await expect(table.locator("tbody tr")).toHaveCount(5);
    await expect(page.getByTestId("result-meta")).toContainText(/共 5 行 · 耗时 \d+ ms/);
  });

  test("违规查询展示拒绝原因", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").fill("DELETE FROM customers");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("rejected")).toContainText("QY_FORBIDDEN_STATEMENT");
  });
});
