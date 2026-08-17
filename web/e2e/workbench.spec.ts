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

  test("执行错误展示失败面板", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").fill("SELECT no_such_column FROM customers");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("failed")).toContainText("QY_EXECUTION_ERROR");
  });

  test("长查询先展示执行中状态，超时后展示失败面板", { timeout: 90_000 }, async ({ page }) => {
    // 笛卡尔积计数远超语句超时：请求返回前「执行中」可见，最终以
    // QY_TIMEOUT 失败收场。QY_TIMEOUT 属基础设施类失败，自动重跑
    // 满 attempt 1→3 后才落终态（ADR-0019）：3 次执行 × 10s 语句
    // 超时，断言窗口须覆盖 ~30s 加调度与轮询余量。
    await page.goto("/");
    await page.getByTestId("sql-input").fill(
      "SELECT count(*) FROM order_items a, order_items b, orders o"
    );
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("running")).toBeVisible();
    await expect(page.getByTestId("failed")).toContainText("QY_TIMEOUT", { timeout: 60_000 });
  });
});
