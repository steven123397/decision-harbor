// 工作台组件测试：状态切换与响应渲染分支（见 docs/design/workbench.md）
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

function jsonResponse(body: unknown, status = 201): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const succeededBody = {
  query_run: {
    id: "11111111-1111-1111-1111-111111111111",
    status: "succeeded",
    row_count: 2,
    truncated: false,
    duration_ms: 12,
    error: null,
    created_at: "2026-07-26T08:00:00Z",
  },
  result: {
    columns: [{ name: "region", type: "varchar" }],
    rows: [["East"], ["North"]],
  },
};

const rejectedBody = {
  query_run: {
    id: "22222222-2222-2222-2222-222222222222",
    status: "rejected",
    row_count: null,
    truncated: false,
    duration_ms: null,
    error: {
      code: "policy_forbidden_statement",
      message: "语句包含被禁止的操作：DELETE",
    },
    created_at: "2026-07-26T08:00:00Z",
  },
  result: null,
};

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  cleanup();
});

async function typeAndSubmit(sql: string) {
  const user = userEvent.setup();
  await user.type(screen.getByTestId("sql-input"), sql);
  await user.click(screen.getByTestId("submit-query"));
  return user;
}

test("提交成功后渲染结果表格与元信息", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(succeededBody)));
  render(<App />);
  await typeAndSubmit("SELECT region FROM customers");

  await waitFor(() => {
    expect(screen.getByTestId("query-status").textContent).toBe("成功");
  });
  const table = screen.getByTestId("result-table");
  expect(table.textContent).toContain("region");
  expect(table.textContent).toContain("East");
  expect(screen.queryByTestId("error-panel")).toBeNull();
  expect(screen.queryByTestId("truncation-notice")).toBeNull();
});

test("请求在途时展示执行中并禁用按钮", async () => {
  let resolveFetch: (value: Response) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    ),
  );
  render(<App />);
  await typeAndSubmit("SELECT 1");

  expect(screen.getByTestId("query-status").textContent).toBe("执行中");
  expect(screen.getByTestId("submit-query")).toHaveProperty("disabled", true);

  resolveFetch(jsonResponse(succeededBody));
  await waitFor(() => {
    expect(screen.getByTestId("query-status").textContent).toBe("成功");
  });
});

test("被拒绝的查询展示错误码与记录标识", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(rejectedBody)));
  render(<App />);
  await typeAndSubmit("DELETE FROM customers");

  await waitFor(() => {
    expect(screen.getByTestId("query-status").textContent).toBe("已拒绝");
  });
  const panel = screen.getByTestId("error-panel");
  expect(panel.textContent).toContain("policy_forbidden_statement");
  expect(panel.textContent).toContain(rejectedBody.query_run.id);
  expect(screen.queryByTestId("result-table")).toBeNull();
});

test("截断结果展示截断提示", async () => {
  const truncatedBody = {
    ...succeededBody,
    query_run: { ...succeededBody.query_run, truncated: true, row_count: 1000 },
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(truncatedBody)));
  render(<App />);
  await typeAndSubmit("SELECT id FROM order_items");

  await waitFor(() => {
    expect(screen.getByTestId("truncation-notice")).toBeTruthy();
  });
});

test("HTTP 422 展示请求级错误而非查询结果", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      jsonResponse({ error: { code: "invalid_request", message: "请求不合法" } }, 422),
    ),
  );
  render(<App />);
  await typeAndSubmit("SELECT 1");

  await waitFor(() => {
    expect(screen.getByTestId("query-status").textContent).toBe("请求失败");
  });
  expect(screen.getByTestId("error-panel").textContent).toContain("invalid_request");
});
