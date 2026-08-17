/** 受理与轮询的视图状态映射（唯一值得单元锁定的纯逻辑）。 */

export interface RunColumn {
  name: string;
  type: string;
}

export interface RunRecord {
  id: number;
  state: string;
  sql: string;
  rejection_code: string | null;
  rejection_message: string | null;
  row_count: number | null;
  truncated: boolean;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
}

export interface AcceptedResponse {
  run: RunRecord;
}

export interface SnapshotResult {
  columns: RunColumn[];
  rows: string[][];
  row_count: number;
  truncated: boolean;
  expires_at: string;
}

/** 非终态统一呈现为「执行中」；终态细分由 pollOnce 合并快照得出。 */
export type ViewState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "succeeded"; columns: RunColumn[]; rows: string[][]; rowCount: number; truncated: boolean; durationMs: number }
  | { kind: "rejected"; code: string; message: string }
  | { kind: "failed"; code: string; message: string }
  | { kind: "network-error"; message: string };

export const TERMINAL_STATES = ["succeeded", "rejected", "failed", "cancelled"] as const;

export function isTerminal(state: string): boolean {
  return (TERMINAL_STATES as readonly string[]).includes(state);
}

/** 终态运行 → 视图状态；结果细节在 succeeded 时由快照补充。 */
export function terminalToViewState(run: RunRecord, snapshot?: SnapshotResult | null): ViewState {
  switch (run.state) {
    case "succeeded":
      return {
        kind: "succeeded",
        columns: snapshot?.columns ?? [],
        rows: snapshot?.rows ?? [],
        rowCount: snapshot?.row_count ?? 0,
        truncated: snapshot?.truncated ?? false,
        durationMs: run.duration_ms ?? 0,
      };
    case "rejected":
      return {
        kind: "rejected",
        code: run.rejection_code ?? "QY_UNKNOWN",
        message: run.rejection_message ?? "查询被策略拒绝",
      };
    case "cancelled":
      // 取消不是执行侧失败：不占用错误码命名空间，纯本地呈现
      return { kind: "failed", code: "已取消", message: "查询已取消，未产生结果" };
    default:
      return {
        kind: "failed",
        code: run.error_code ?? "QY_UNKNOWN",
        message: run.error_message ?? "查询执行失败",
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

/** 提交 → 立即受理（202）或同步拒绝（422）；随后由调用方轮询。 */
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
  if (resp.status === 202 && resp.body?.run) {
    return { view: { kind: "running" }, runId: resp.body.run.id };
  }
  if (resp.status === 422 && resp.body?.run) {
    return { view: terminalToViewState(resp.body.run), runId: resp.body.run.id };
  }
  return { view: networkFailure(resp.status), runId: null };
}

/** 轮询一次：非终态继续 running；终态合并快照（succeeded）落定视图。 */
export async function pollOnce(runId: number): Promise<ViewState> {
  let got: { status: number; body: { run?: RunRecord } | null };
  try {
    got = await fetchJson<{ run?: RunRecord }>(`/api/v1/query-runs/${runId}`);
  } catch {
    return networkFailure();
  }
  const run = got.status === 200 ? got.body?.run : undefined;
  if (!run) return networkFailure(got.status);
  if (!isTerminal(run.state)) return { kind: "running" };

  if (run.state === "succeeded") {
    try {
      const snap = await fetchJson<{ result?: SnapshotResult }>(
        `/api/v1/query-runs/${runId}/result`
      );
      return terminalToViewState(run, snap.status === 200 ? snap.body?.result : null);
    } catch {
      return terminalToViewState(run, null);
    }
  }
  return terminalToViewState(run);
}
