import { describe, expect, it } from "vitest";
import { isTerminal, runToViewState } from "./api";

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

describe("runToViewState 中间态推进", () => {
  it("received 呈现「已受理」，不混称排队", () => {
    const view = runToViewState({ id: 7, state: "received", attempt: 1 } as never);
    expect(view).toEqual({
      kind: "in-progress",
      runId: 7,
      state: "received",
      label: "已受理",
      attempt: 1,
    });
  });

  it("queued 呈现「排队中」", () => {
    const view = runToViewState({ id: 8, state: "queued", attempt: 1 } as never);
    expect(view).toEqual({
      kind: "in-progress",
      runId: 8,
      state: "queued",
      label: "排队中",
      attempt: 1,
    });
  });

  it("running 呈现「执行中」并透传 attempt", () => {
    const view = runToViewState({ id: 9, state: "running", attempt: 2 } as never);
    expect(view).toEqual({
      kind: "in-progress",
      runId: 9,
      state: "running",
      label: "执行中",
      attempt: 2,
    });
  });

  it("cancelling 呈现「取消中」", () => {
    const view = runToViewState({ id: 10, state: "cancelling", attempt: 1 } as never);
    expect(view).toMatchObject({ kind: "in-progress", label: "取消中" });
  });

  it("未知中间态退回原始 state 作为文案", () => {
    const view = runToViewState({ id: 11, state: "mystery", attempt: 1 } as never);
    expect(view).toMatchObject({ kind: "in-progress", label: "mystery" });
  });
});

describe("runToViewState 终态", () => {
  it("succeeded 合并快照", () => {
    const view = runToViewState(
      { id: 1, state: "succeeded", duration_ms: 12, attempt: 1 } as never,
      {
        columns: [{ name: "region", type: "varchar" }],
        rows: [["East"]],
        row_count: 1,
        truncated: false,
        expires_at: "2026-08-19T00:00:00+00:00",
      }
    );
    expect(view).toEqual({
      kind: "succeeded",
      runId: 1,
      columns: [{ name: "region", type: "varchar" }],
      rows: [["East"]],
      rowCount: 1,
      truncated: false,
      durationMs: 12,
    });
  });

  it("succeeded 截断快照保留截断标记与实际行数", () => {
    const view = runToViewState(
      { id: 12, state: "succeeded", duration_ms: 30, attempt: 1 } as never,
      {
        columns: [{ name: "id", type: "bigint" }],
        rows: [["1"], ["2"]],
        row_count: 500,
        truncated: true,
        expires_at: "2026-08-19T00:00:00+00:00",
      }
    );
    expect(view).toMatchObject({
      kind: "succeeded",
      rowCount: 500,
      truncated: true,
    });
  });

  it("succeeded 快照缺失时展示空表", () => {
    const view = runToViewState(
      { id: 2, state: "succeeded", duration_ms: 5, attempt: 1 } as never,
      null
    );
    expect(view).toMatchObject({ kind: "succeeded", columns: [], rows: [], rowCount: 0 });
  });

  it("rejected 取拒绝码与可读说明", () => {
    const view = runToViewState({
      id: 3,
      state: "rejected",
      attempt: 1,
      rejection_code: "QY_FORBIDDEN_STATEMENT",
      rejection_message: "仅允许只读查询语句",
    } as never);
    expect(view).toEqual({
      kind: "rejected",
      runId: 3,
      code: "QY_FORBIDDEN_STATEMENT",
      message: "仅允许只读查询语句",
    });
  });

  it("failed 取稳定错误码、摘要与 attempt", () => {
    const view = runToViewState({
      id: 4,
      state: "failed",
      attempt: 3,
      error_code: "QY_TIMEOUT",
      error_message: "查询执行超时，已被语句超时限制中止",
    } as never);
    expect(view).toEqual({
      kind: "failed",
      runId: 4,
      code: "QY_TIMEOUT",
      message: "查询执行超时，已被语句超时限制中止",
      attempt: 3,
    });
  });

  it("failed 缺失错误字段时用兜底码", () => {
    const view = runToViewState({ id: 5, state: "failed", attempt: 1 } as never);
    expect(view).toMatchObject({ kind: "failed", code: "QY_UNKNOWN" });
  });

  it("cancelled 是独立终态面板，不占用错误码命名空间", () => {
    const view = runToViewState({ id: 6, state: "cancelled", attempt: 1 } as never);
    expect(view).toEqual({
      kind: "cancelled",
      runId: 6,
      message: "查询已取消，未产生结果",
    });
  });
});
