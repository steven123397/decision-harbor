import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

describe("query workbench", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a successful query result", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "ready" }) })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            id: "run-001",
            raw_sql: "SELECT 1",
            state: "succeeded",
            outcome: "succeeded",
            created_at: "2026-08-14T10:00:00Z",
            policy: { decision: "allowed", code: null },
            row_count: 1,
            duration_ms: 4,
            result: { columns: [{ name: "value", type: "integer" }], rows: [["1"]], row_count: 1, duration_ms: 3 },
            error: null,
          }),
        }),
    );

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Execute query" }));

    expect(await screen.findByText("SUCCEEDED")).toBeInTheDocument();
    expect(screen.getByText("value")).toBeInTheDocument();
    expect(within(screen.getByRole("table")).getByText("1")).toBeInTheDocument();
  });

  it("renders a stable policy rejection", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce({ ok: true, json: async () => ({ status: "ready" }) })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            id: "run-002",
            raw_sql: "SELECT * FROM platform.query_runs",
            state: "rejected",
            outcome: "rejected",
            created_at: "2026-08-14T10:00:00Z",
            policy: { decision: "rejected", code: "object_not_allowed" },
            row_count: null,
            duration_ms: 1,
            result: null,
            error: { code: "object_not_allowed", message: "Query references an unauthorized object" },
          }),
        }),
    );

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Execute query" }));

    await waitFor(() => expect(screen.getByText("object_not_allowed")).toBeInTheDocument());
    expect(screen.getByText("Query references an unauthorized object")).toBeInTheDocument();
  });
});
