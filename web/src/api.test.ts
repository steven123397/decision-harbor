import { describe, expect, it } from "vitest";
import {
  canCancel,
  canRetry,
  cancelOutcome,
  historyOutcome,
  isTerminal,
  resultOutcome,
  retryOutcome,
  runToViewState,
  stateLabel,
} from "./api";

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

describe("stateLabel", () => {
  it("八个状态逐个可辨，终态与中间态共用一套文案", () => {
    expect(stateLabel("received")).toBe("已受理");
    expect(stateLabel("queued")).toBe("排队中");
    expect(stateLabel("running")).toBe("执行中");
    expect(stateLabel("cancelling")).toBe("取消中");
    expect(stateLabel("succeeded")).toBe("成功");
    expect(stateLabel("rejected")).toBe("策略拒绝");
    expect(stateLabel("failed")).toBe("失败");
    expect(stateLabel("cancelled")).toBe("已取消");
  });

  it("未知状态退回原始值", () => {
    expect(stateLabel("mystery")).toBe("mystery");
  });
});

describe("canCancel / canRetry", () => {
  it("中间态可取消，终态不可取消", () => {
    for (const state of ["received", "queued", "running", "cancelling"]) {
      expect(canCancel({ state })).toBe(true);
    }
    for (const state of ["succeeded", "rejected", "failed", "cancelled"]) {
      expect(canCancel({ state })).toBe(false);
    }
  });

  it("仅 failed / cancelled 可重试；rejected 不给重试入口", () => {
    expect(canRetry({ state: "failed" })).toBe(true);
    expect(canRetry({ state: "cancelled" })).toBe(true);
    expect(canRetry({ state: "succeeded" })).toBe(false);
    expect(canRetry({ state: "rejected" })).toBe(false);
    expect(canRetry({ state: "queued" })).toBe(false);
  });
});

describe("cancelOutcome", () => {
  it("202 受理为取消中（in-progress），轮询继续推进", () => {
    const outcome = cancelOutcome(202, {
      run: { id: 21, state: "cancelling", attempt: 1 } as never,
    });
    expect(outcome).toEqual({
      kind: "accepted",
      view: { kind: "in-progress", runId: 21, state: "cancelling", label: "取消中", attempt: 1 },
    });
  });

  it("200 是确定生效或幂等重复：cancelled 终态视图", () => {
    const outcome = cancelOutcome(200, {
      run: { id: 22, state: "cancelled", attempt: 1 } as never,
    });
    expect(outcome.kind).toBe("accepted");
    expect(outcome.kind === "accepted" && outcome.view.kind).toBe("cancelled");
  });

  it("409 冲突透传服务端文案", () => {
    const outcome = cancelOutcome(409, {
      detail: { code: "run_not_cancellable", message: "该运行已处于终态，无法取消" },
    });
    expect(outcome).toEqual({
      kind: "conflict",
      message: "该运行已处于终态，无法取消",
    });
  });

  it("缺 detail 的 409 与其他状态码按兜底文案处理", () => {
    expect(cancelOutcome(409, null)).toEqual({ kind: "conflict", message: "该运行无法取消" });
    expect(cancelOutcome(500, null).kind).toBe("error");
  });
});

describe("retryOutcome", () => {
  it("202 携带新运行（retry_of 关系），进入新运行的中间态视图", () => {
    const outcome = retryOutcome(202, {
      run: { id: 31, state: "queued", attempt: 1, retry_of: 30 } as never,
    });
    expect(outcome).toEqual({
      kind: "accepted",
      view: { kind: "in-progress", runId: 31, state: "queued", label: "排队中", attempt: 1 },
    });
  });

  it("409 冲突透传指引文案（如策略拒绝不可重试）", () => {
    const outcome = retryOutcome(409, {
      detail: { code: "run_not_retryable", message: "策略拒绝的运行不能重试，请修改 SQL 后重新提交" },
    });
    expect(outcome).toEqual({
      kind: "conflict",
      message: "策略拒绝的运行不能重试，请修改 SQL 后重新提交",
    });
  });

  it("其他状态码按错误处理", () => {
    expect(retryOutcome(502, null).kind).toBe("error");
  });
});

describe("resultOutcome", () => {
  const snapshot = {
    columns: [{ name: "region", type: "varchar" }],
    rows: [["East"]],
    row_count: 1,
    truncated: false,
    expires_at: "2026-08-23T00:00:00+00:00",
  };

  it("200 携带快照", () => {
    expect(resultOutcome(200, { result: snapshot })).toEqual({
      kind: "snapshot",
      result: snapshot,
    });
  });

  it("410 是过期：与 409 不可读分开反馈", () => {
    expect(resultOutcome(410, null)).toEqual({ kind: "expired" });
    expect(resultOutcome(409, null)).toEqual({ kind: "unavailable" });
  });

  it("其他状态码按错误透传", () => {
    expect(resultOutcome(500, null)).toEqual({ kind: "error", status: 500 });
  });
});

describe("historyOutcome", () => {
  it("200 解析运行列表与不透明游标", () => {
    const outcome = historyOutcome(200, {
      runs: [{ id: 1, state: "succeeded", attempt: 1 } as never],
      next_cursor: "Y3Vyc29y",
    });
    expect(outcome).toEqual({
      kind: "page",
      page: { runs: [{ id: 1, state: "succeeded", attempt: 1 }], nextCursor: "Y3Vyc29y" },
    });
  });

  it("末页 next_cursor 为 null", () => {
    const outcome = historyOutcome(200, { runs: [], next_cursor: null });
    expect(outcome.kind === "page" && outcome.page.nextCursor).toBeNull();
  });

  it("非 200 按错误处理", () => {
    expect(historyOutcome(500, null).kind).toBe("error");
  });
});
