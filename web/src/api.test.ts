import { describe, expect, it } from "vitest";
import { isTerminal, terminalToViewState } from "./api";

describe("isTerminal", () => {
  it("四终态为 true，中间态为 false", () => {
    for (const s of ["succeeded", "rejected", "failed", "cancelled"]) {
      expect(isTerminal(s)).toBe(true);
    }
    for (const s of ["received", "queued", "running", "cancelling"]) {
      expect(isTerminal(s)).toBe(false);
    }
  });
});

describe("terminalToViewState", () => {
  it("succeeded 合并快照", () => {
    const view = terminalToViewState(
      { state: "succeeded", duration_ms: 12 } as never,
      {
        columns: [{ name: "region", type: "varchar" }],
        rows: [["East"]],
        row_count: 1,
        truncated: false,
        expires_at: "2026-08-18T00:00:00+00:00",
      }
    );
    expect(view).toEqual({
      kind: "succeeded",
      columns: [{ name: "region", type: "varchar" }],
      rows: [["East"]],
      rowCount: 1,
      truncated: false,
      durationMs: 12,
    });
  });

  it("succeeded 快照缺失时展示空表", () => {
    const view = terminalToViewState({ state: "succeeded", duration_ms: 5 } as never, null);
    expect(view.kind).toBe("succeeded");
  });

  it("rejected 取拒绝码与说明", () => {
    const view = terminalToViewState({
      state: "rejected",
      rejection_code: "QY_FORBIDDEN_STATEMENT",
      rejection_message: "仅允许只读查询语句",
    } as never);
    expect(view).toEqual({
      kind: "rejected",
      code: "QY_FORBIDDEN_STATEMENT",
      message: "仅允许只读查询语句",
    });
  });

  it("failed 取错误码与摘要", () => {
    const view = terminalToViewState({
      state: "failed",
      error_code: "QY_TIMEOUT",
      error_message: "查询执行超时",
    } as never);
    expect(view).toEqual({ kind: "failed", code: "QY_TIMEOUT", message: "查询执行超时" });
  });

  it("cancelled 呈现取消面板（本地文案，非错误码）", () => {
    const view = terminalToViewState({ state: "cancelled" } as never);
    expect(view).toEqual({ kind: "failed", code: "已取消", message: "查询已取消，未产生结果" });
  });
});
