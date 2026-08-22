import { expect, test, type APIRequestContext } from "@playwright/test";

const SIMPLE_SQL =
  "SELECT region, count(*) AS customers FROM customers GROUP BY region ORDER BY region";
// 笛卡尔积计数远超语句超时：每次执行 10s 后以 QY_TIMEOUT 失败，
// 属基础设施类失败，自动重跑满 attempt 1→3（ADR-0019）。
const LONG_SQL = "SELECT count(*) FROM order_items a, order_items b, orders o";
// 7 列宽表：窄视口下必然超出视口宽度，验证横向滚动容器兜住溢出。
const WIDE_SQL = "SELECT * FROM order_items ORDER BY id LIMIT 20";

async function cancelActiveRuns(request: APIRequestContext) {
  const resp = await request.get("/api/v1/query-runs?limit=100");
  const { runs } = (await resp.json()) as {
    runs: { id: number; state: string }[];
  };
  const active = runs.filter(
    (run) => !["succeeded", "rejected", "failed", "cancelled"].includes(run.state)
  );
  for (const run of active) {
    await request.post(`/api/v1/query-runs/${run.id}/cancel`);
  }
  // 等取消真正落定：运行中取消的 best effort 中止要等 keeper 周期，
  // 期间执行线程仍占用 worker 本地并发槽——不等待会让下一个用例
  // 排队，撞上 5 秒默认断言超时。
  await expect
    .poll(
      async () => {
        const states = await Promise.all(
          active.map(async (run) => {
            const r = await request.get(`/api/v1/query-runs/${run.id}`);
            return ((await r.json()) as { run: { state: string } }).run.state;
          })
        );
        return states.every((state) =>
          ["succeeded", "rejected", "failed", "cancelled"].includes(state)
        );
      },
      { timeout: 30_000 }
    )
    .toBe(true);
}

test.describe("查询工作台异步主流程", () => {
  test("提交合法 SQL：轮询至 succeeded，结果表格可见", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").fill(SIMPLE_SQL);
    await page.getByTestId("submit").click();

    const table = page.getByTestId("result-table");
    await expect(table).toBeVisible();
    await expect(table.locator("thead th")).toHaveText(["region", "customers"]);
    await expect(table.locator("tbody tr")).toHaveCount(5);
    await expect(page.getByTestId("result-meta")).toContainText(/共 5 行 · 耗时 \d+ ms/);
  });

  test("策略拒绝同步呈现拒绝码与可读说明", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").fill("DELETE FROM customers");
    await page.getByTestId("submit").click();

    const rejected = page.getByTestId("rejected");
    await expect(rejected).toBeVisible();
    await expect(rejected).toContainText("QY_FORBIDDEN_STATEMENT");
    await expect(rejected).toContainText("仅允许只读查询语句");
  });

  test("失败运行呈现稳定错误码与摘要", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").fill("SELECT no_such_column FROM customers");
    await page.getByTestId("submit").click();

    const failed = page.getByTestId("failed");
    await expect(failed).toBeVisible();
    await expect(failed).toContainText("QY_EXECUTION_ERROR");
    await expect(failed).toContainText("查询执行失败，请检查列名与表达式");
  });

  test(
    "长查询展示执行中与尝试编号推进，超时后展示失败面板",
    { timeout: 90_000 },
    async ({ page }) => {
      await page.goto("/");
      await page.getByTestId("sql-input").fill(LONG_SQL);
      await page.getByTestId("submit").click();

      const progress = page.getByTestId("in-progress");
      await expect(progress).toBeVisible();
      await expect(progress).toContainText(/排队中|执行中/);
      // 第 1 次执行超时后自动重跑：认领第 2 次执行时 attempt 递增，
      // 工作台应能看到尝试编号（用户故事 18）。
      await expect(progress).toContainText("第 2 次尝试", { timeout: 30_000 });
      // QY_TIMEOUT 重跑满 attempt 1→3 后才落终态：3 × 10s 语句超时加
      // 调度与轮询余量。
      await expect(page.getByTestId("failed")).toContainText("QY_TIMEOUT", {
        timeout: 60_000,
      });
    }
  );

  test("全局并发占满时新提交展示排队中", { timeout: 90_000 }, async ({ page }) => {
    await page.goto("/");
    const saturateIds: number[] = [];
    try {
      // 4 条长查询占满全局并发闸门 4（ADR-0019）：各占一个执行租约，
      // 每条至少压住 10s（语句超时），后续提交只能排队。
      for (let i = 0; i < 4; i++) {
        const resp = await page.request.post("/api/v1/query-runs", {
          data: { sql: LONG_SQL },
        });
        expect(resp.status()).toBe(202);
        saturateIds.push(((await resp.json()) as { run: { id: number } }).run.id);
      }
      await expect
        .poll(
          async () => {
            const states = await Promise.all(
              saturateIds.map(async (id) => {
                const r = await page.request.get(`/api/v1/query-runs/${id}`);
                return ((await r.json()) as { run: { state: string } }).run.state;
              })
            );
            return states.filter((s) => s === "running").length;
          },
          { timeout: 30_000 }
        )
        .toBe(4);

      await page.getByTestId("sql-input").fill(LONG_SQL);
      await page.getByTestId("submit").click();
      await expect(page.getByTestId("in-progress")).toContainText("排队中");
    } finally {
      // 清理占位运行，避免拖慢后续用例：排队中取消确定生效，
      // 运行中 best effort 中止（#12）。
      await cancelActiveRuns(page.request);
    }
  });

  test("结果达到行数上限时展示截断标记与实际行数", async ({ page }) => {
    // order_items 有 3000 行，超过 500 行上限：截断标记与 500 行可见。
    await page.goto("/");
    await page.getByTestId("sql-input").fill("SELECT id FROM order_items");
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("result-meta")).toContainText(
      /共 500 行 · 耗时 \d+ ms · 结果已截断，仅显示前 500 行/
    );
    await expect(page.getByTestId("result-table").locator("tbody tr")).toHaveCount(500);
  });

  test("键盘可完成提交", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").focus();
    await page.keyboard.type(SIMPLE_SQL);
    await page.keyboard.press("Tab");
    await page.keyboard.press("Enter");

    await expect(page.getByTestId("result-table")).toBeVisible();
  });

  test("Ctrl+Enter 快捷提交", async ({ page }) => {
    await page.goto("/");
    await page.getByTestId("sql-input").focus();
    await page.keyboard.type(SIMPLE_SQL);
    await page.keyboard.press("Control+Enter");

    await expect(page.getByTestId("result-table")).toBeVisible();
  });

  test("窄视口布局可用：无横向溢出，宽表在容器内滚动", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/");
    await page.getByTestId("sql-input").fill(WIDE_SQL);
    await page.getByTestId("submit").click();

    await expect(page.getByTestId("result-table")).toBeVisible();
    // 宽表超出视口：滚动容器兜住，页面本身不产生横向滚动。
    const wrap = page.getByTestId("result-table-wrap");
    expect(await wrap.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
    const noPageOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth
    );
    expect(noPageOverflow).toBe(true);
  });
});
