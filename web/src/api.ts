/** 受理与轮询的视图状态映射（唯一值得单元锁定的纯逻辑）。 */

export interface RunColumn {
  name: string;
  type: string;
}

export interface RunRecord {
  id: number;
  state: string;
  sql: string;
  attempt: number;
  rejection_code: string | null;
  rejection_message: string | null;
  row_count: number | null;
  truncated: boolean;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
}

export interface SnapshotResult {
  columns: RunColumn[];
  rows: string[][];
  row_count: number;
  truncated: boolean;
  expires_at: string;
}

/** 中间态逐个可辨（CONTEXT.md：received 与 queued 不混称「排队」）；
 * 终态四者互斥，cancelled 不并入失败面板。 */
export type ViewState =
  | { kind: "idle" }
  | { kind: "in-progress"; runId: number; state: string; label: string; attempt: number }
  | {
      kind: "succeeded";
      runId: number;
      columns: RunColumn[];
      rows: string[][];
      rowCount: number;
      truncated: boolean;
      durationMs: number;
    }
  | { kind: "rejected"; runId: number; code: string; message: string }
  | { kind: "failed"; runId: number; code: string; message: string; attempt: number }
  | { kind: "cancelled"; runId: number; message: string }
  | { kind: "network-error"; message: string };

export const TERMINAL_STATES = ["succeeded", "rejected", "failed", "cancelled"] as const;

const IN_PROGRESS_LABELS: Record<string, string> = {
  received: "已受理",
  queued: "排队中",
  running: "执行中",
  cancelling: "取消中",
};

export function isTerminal(state: string): boolean {
  return (TERMINAL_STATES as readonly string[]).includes(state);
}

/** 运行记录 → 视图状态；succeeded 的结果细节由快照补充。 */
export function runToViewState(run: RunRecord, snapshot?: SnapshotResult | null): ViewState {
  const attempt = run.attempt ?? 1;
  if (!isTerminal(run.state)) {
    return {
      kind: "in-progress",
      runId: run.id,
      state: run.state,
      label: IN_PROGRESS_LABELS[run.state] ?? run.state,
      attempt,
    };
  }
  switch (run.state) {
    case "succeeded":
      return {
        kind: "succeeded",
        runId: run.id,
        columns: snapshot?.columns ?? [],
        rows: snapshot?.rows ?? [],
        rowCount: snapshot?.row_count ?? 0,
        truncated: snapshot?.truncated ?? false,
        durationMs: run.duration_ms ?? 0,
      };
    case "rejected":
      return {
        kind: "rejected",
        runId: run.id,
        code: run.rejection_code ?? "QY_UNKNOWN",
        message: run.rejection_message ?? "查询被策略拒绝",
      };
    case "cancelled":
      return { kind: "cancelled", runId: run.id, message: "查询已取消，未产生结果" };
    default:
      return {
        kind: "failed",
        runId: run.id,
        code: run.error_code ?? "QY_UNKNOWN",
        message: run.error_message ?? "查询执行失败",
        attempt,
      };
  }
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<{ status: number; body: T | null }> {
  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch {
    throw new NetworkError();
  }
  let body: T | null = null;
  try {
    body = (await resp.json()) as T;
  } catch {
    body = null;
  }
  return { status: resp.status, body };
}

class NetworkError extends Error {}

function networkFailure(status?: number): ViewState {
  return {
    kind: "network-error",
    message: status === undefined ? "无法连接查询服务" : `请求失败（HTTP ${status}）`,
  };
}

/** 提交 → 立即受理（202，响应已含 queued 运行记录）或同步拒绝（422）；
 * 随后由调用方轮询。 */
export async function submitSql(
  sql: string
): Promise<{ view: ViewState; runId: number | null }> {
  let resp: { status: number; body: { run?: RunRecord } | null };
  try {
    resp = await fetchJson<{ run?: RunRecord }>("/api/v1/query-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
    });
  } catch {
    return { view: networkFailure(), runId: null };
  }
  const run = resp.body?.run;
  if ((resp.status === 202 || resp.status === 422) && run) {
    return { view: runToViewState(run), runId: run.id };
  }
  return { view: networkFailure(resp.status), runId: null };
}

/** 轮询一次：非终态继续推进中间态；终态合并快照（succeeded）落定视图。 */
export async function pollOnce(runId: number): Promise<ViewState> {
  let got: { status: number; body: { run?: RunRecord } | null };
  try {
    got = await fetchJson<{ run?: RunRecord }>(`/api/v1/query-runs/${runId}`);
  } catch {
    return networkFailure();
  }
  const run = got.status === 200 ? got.body?.run : undefined;
  if (!run) return networkFailure(got.status);
  if (!isTerminal(run.state)) return runToViewState(run);

  if (run.state === "succeeded") {
    try {
      const snap = await fetchJson<{ result?: SnapshotResult }>(
        `/api/v1/query-runs/${runId}/result`
      );
      return runToViewState(run, snap.status === 200 ? snap.body?.result : null);
    } catch {
      return runToViewState(run, null);
    }
  }
  return runToViewState(run);
}
