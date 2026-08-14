import { describe, expect, it } from "vitest";
import { toViewState } from "./api";

describe("toViewState", () => {
  it("映射成功响应", () => {
    const view = toViewState({
      outcome: "succeeded",
      run: { duration_ms: 12, truncated: false } as never,
      result: {
        columns: [{ name: "region", type: "varchar" }],
        rows: [["East"]],
        row_count: 1,
        truncated: false,
      },
    });
    expect(view).toEqual({
      kind: "succeeded",
      columns: [{ name: "region", type: "varchar" }],
      rows: [["East"]],
      rowCount: 1,
      truncated: false,
      durationMs: 12,
    });
  });

  it("映射截断结果", () => {
    const view = toViewState({
      outcome: "succeeded",
      run: { duration_ms: 30, truncated: true } as never,
      result: { columns: [], rows: [], row_count: 1000, truncated: true },
    });
    expect(view.kind).toBe("succeeded");
    if (view.kind === "succeeded") expect(view.truncated).toBe(true);
  });

  it("映射策略拒绝", () => {
    const view = toViewState({
      outcome: "rejected",
      run: { rejection_code: "QY_FORBIDDEN_STATEMENT", rejection_message: "仅允许只读查询语句" } as never,
    });
    expect(view).toEqual({
      kind: "rejected",
      code: "QY_FORBIDDEN_STATEMENT",
      message: "仅允许只读查询语句",
    });
  });

  it("映射执行失败", () => {
    const view = toViewState({
      outcome: "failed",
      run: { error_code: "QY_TIMEOUT", error_message: "查询执行超时" } as never,
    });
    expect(view).toEqual({ kind: "failed", code: "QY_TIMEOUT", message: "查询执行超时" });
  });
});
