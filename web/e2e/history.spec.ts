import { expect, test } from "@playwright/test";

const SIMPLE_SQL =
  "SELECT region, count(*) AS customers FROM customers GROUP BY region ORDER BY region";
// 笛卡尔积计数远超语句超时：运行中取消走 best effort 中止路径（#12）。
const LONG_SQL = "SELECT count(*) FROM order_items a, order_items b, orders o";
// 确定性执行失败（列不存在）：不自动重跑，attempt 1 直接 failed。
const FAIL_SQL = "SELECT no_such_column FROM customers";

/** 从面板文本提取运行号（面板均含「运行 #N」）。 */
async function runIdFrom(text: string): Promise<number> {
  return Number(text.match(/运行 #(\d+)/)![1]);
}

test.describe("查询工作台历史与生命周期操作", () => {
  test("取消运行：取消中反馈后落入 cancelled 终态", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").fill(LONG_SQL);
    await page.getByTestId("submit").click();

    const progress = page.getByTestId("in-progress");
    await expect(progress).toContainText("执行中", { timeout: 15_000 });

    await page.getByTestId("cancel-run").click();
    // 运行中取消受理为 cancelling（202）：面板反馈「取消中」
    await expect(progress).toContainText("取消中");
    // best effort 中止由 keeper 周期执行（≤10s），语句超时 10s 兜底
    const cancelled = page.getByTestId("cancelled");
    await expect(cancelled).toContainText("查询已取消", { timeout: 45_000 });
    const runId = await runIdFrom((await cancelled.textContent())!);
    // 历史行反映 cancelled 终态
    await expect(page.getByTestId(`history-row-${runId}`)).toContainText("已取消");
  });

  test("失败运行可重试并进入新运行详情", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").fill(FAIL_SQL);
    await page.getByTestId("submit").click();

    const failed = page.getByTestId("failed");
    await expect(failed).toBeVisible();
    const oldId = await runIdFrom((await failed.textContent())!);

    await page.getByTestId("retry-run").click();
    // 重试创建新运行（retry_of 指向原运行），工作台进入新运行详情
    await expect(page.getByTestId("in-progress")).toBeVisible();
    await expect(failed).toBeVisible({ timeout: 15_000 });
    const newId = await runIdFrom((await failed.textContent())!);
    expect(newId).toBeGreaterThan(oldId);
    // 历史列表展示重试关系与终态
    await expect(page.getByTestId(`history-row-${newId}`)).toContainText(`重试自 #${oldId}`);
    await expect(page.getByTestId(`history-row-${newId}`)).toContainText("失败");
  });

  test("历史列表分页可用并展示终态", async ({ page }) => {
    // 21 条同步拒绝的运行（默认页大小 20）：第 21 条在第二页
    const seeded: number[] = [];
    for (let i = 0; i < 21; i++) {
      const resp = await page.request.post("/api/v1/query-runs", {
        data: { sql: "DELETE FROM customers" },
      });
      expect(resp.status()).toBe(422);
      const body = (await resp.json()) as { run: { id: number } };
      seeded.push(body.run.id);
    }

    await page.goto("/");
    const rows = page.getByTestId("history-table").locator("tbody tr");
    await expect(rows).toHaveCount(20, { timeout: 10_000 });
    // 首页是最新 20 条：全部为刚创建的拒绝行
    await expect(rows.first()).toContainText("策略拒绝");

    await page.getByTestId("history-more").click();
    // 最早一条 seed 恰好落在第二页首位
    await expect(page.getByTestId(`history-row-${seeded[0]}`)).toBeVisible();
    await expect(page.getByTestId(`history-row-${seeded[0]}`)).toContainText("策略拒绝");
  });

  test("结果超过保留期：410 有明确反馈", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").fill(SIMPLE_SQL);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("result-table")).toBeVisible();
    const runId = await runIdFrom((await page.getByTestId("result-meta").textContent())!);

    // 24 小时保留期无法在测试内真实等待：拦截本次读取，锁定 410 的
    // UI 反馈合同（其余请求照常放行）。
    await page.route(`**/api/v1/query-runs/${runId}/result`, (route) =>
      route.fulfill({
        status: 410,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "QY_RESULT_EXPIRED",
            message: "结果已超过保留期，无法读取；运行审计记录仍保留",
          },
        }),
      })
    );

    await page.getByTestId(`view-${runId}`).click();
    const expired = page.getByTestId("result-expired");
    await expect(expired).toBeVisible();
    await expect(expired).toContainText("超过保留期");
    await expect(expired).toContainText("审计记录仍保留");
  });

  test("结果不可读取：409 有明确反馈", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").fill(SIMPLE_SQL);
    await page.getByTestId("submit").click();
    await expect(page.getByTestId("result-table")).toBeVisible();
    const runId = await runIdFrom((await page.getByTestId("result-meta").textContent())!);

    await page.route(`**/api/v1/query-runs/${runId}/result`, (route) =>
      route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "QY_RESULT_NOT_AVAILABLE",
            message: "该运行没有可读取的结果",
          },
        }),
      })
    );

    await page.getByTestId(`view-${runId}`).click();
    const unavailable = page.getByTestId("result-unavailable");
    await expect(unavailable).toBeVisible();
    await expect(unavailable).toContainText("没有可读取的结果");
  });
});
