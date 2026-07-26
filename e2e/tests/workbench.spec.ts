// 浏览器主流程：输入 SQL、提交、状态展示、结果或拒绝信息（见 docs/design/workbench.md）
import { expect, test } from "@playwright/test";

test("提交合法查询后展示结果表格", async ({ page }) => {
  await page.goto("/");
  await page
    .getByTestId("sql-input")
    .fill("SELECT count(*) AS n FROM orders WHERE status = 'confirmed'");
  await page.getByTestId("submit-query").click();

  await expect(page.getByTestId("query-status")).toHaveText("成功");
  const table = page.getByTestId("result-table");
  await expect(table).toBeVisible();
  // 契约固定事实：confirmed 订单 720 张
  await expect(table).toContainText("720");
});

test("提交期间展示执行中且按钮禁用", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("sql-input").fill("SELECT PG_SLEEP(1.5)");
  await page.getByTestId("submit-query").click();

  await expect(page.getByTestId("query-status")).toHaveText("执行中");
  await expect(page.getByTestId("submit-query")).toBeDisabled();
  await expect(page.getByTestId("query-status")).toHaveText("成功", {
    timeout: 15_000,
  });
});

test("被禁查询展示稳定错误码", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("sql-input").fill("DELETE FROM customers");
  await page.getByTestId("submit-query").click();

  await expect(page.getByTestId("query-status")).toHaveText("已拒绝");
  const panel = page.getByTestId("error-panel");
  await expect(panel).toContainText("policy_forbidden_statement");
  await expect(page.getByTestId("result-table")).toHaveCount(0);
});
