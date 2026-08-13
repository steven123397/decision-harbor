import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, test, vi } from "vitest";
import { App } from "./App";
import type { QueryRun } from "./types";

afterEach(() => {
  vi.unstubAllGlobals();
});

function mockFetch(run: QueryRun, delayMs = 20) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise((resolve) => {
          setTimeout(() => {
            resolve({
              ok: true,
              json: async () => run,
            });
          }, delayMs);
        }),
    ),
  );
}

test("shows running then a result table", async () => {
  const user = userEvent.setup();
  mockFetch({
    id: "11111111-1111-1111-1111-111111111111",
    status: "succeeded",
    sql: "SELECT 1",
    result: {
      columns: [{ name: "id", type: "int4" }],
      rows: [[1]],
      row_count: 1,
    },
    error: null,
    duration_ms: 3,
    created_at: "2026-08-13T00:00:00+00:00",
  });
  render(<App />);
  await user.click(screen.getByRole("button", { name: "提交" }));
  expect(screen.getByRole("status")).toHaveTextContent("执行中");
  expect(await screen.findByRole("columnheader", { name: "id" })).toBeInTheDocument();
  expect(screen.getByRole("cell", { name: "1" })).toBeInTheDocument();
});

test("shows rejected error code and message", async () => {
  const user = userEvent.setup();
  mockFetch({
    id: "22222222-2222-2222-2222-222222222222",
    status: "rejected",
    sql: "DELETE FROM orders",
    result: null,
    error: { code: "POLICY_DENIED", message: "Only a single read-only SELECT or set operation is allowed." },
    duration_ms: 1,
    created_at: "2026-08-13T00:00:00+00:00",
  });
  render(<App />);
  await user.click(screen.getByRole("button", { name: "提交" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("POLICY_DENIED");
});

test("shows failed error code and message", async () => {
  const user = userEvent.setup();
  mockFetch({
    id: "33333333-3333-3333-3333-333333333333",
    status: "failed",
    sql: "SELECT 1",
    result: null,
    error: { code: "EXECUTION_TIMEOUT", message: "Query exceeded the statement timeout." },
    duration_ms: 5,
    created_at: "2026-08-13T00:00:00+00:00",
  });
  render(<App />);
  await user.click(screen.getByRole("button", { name: "提交" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("EXECUTION_TIMEOUT");
});
