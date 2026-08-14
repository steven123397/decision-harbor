/** 响应解析到视图状态的映射（唯一值得单元锁定的纯逻辑）。 */

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

export interface QueryRunResponse {
  outcome: "succeeded" | "rejected" | "failed";
  run: RunRecord;
  result?: {
    columns: RunColumn[];
    rows: string[][];
    row_count: number;
    truncated: boolean;
  };
}

export type ViewState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "succeeded"; columns: RunColumn[]; rows: string[][]; rowCount: number; truncated: boolean; durationMs: number }
  | { kind: "rejected"; code: string; message: string }
  | { kind: "failed"; code: string; message: string }
  | { kind: "network-error"; message: string };

export function toViewState(resp: QueryRunResponse): ViewState {
  switch (resp.outcome) {
    case "succeeded":
      return {
        kind: "succeeded",
        columns: resp.result?.columns ?? [],
        rows: resp.result?.rows ?? [],
        rowCount: resp.result?.row_count ?? 0,
        truncated: resp.result?.truncated ?? false,
        durationMs: resp.run.duration_ms ?? 0,
      };
    case "rejected":
      return {
        kind: "rejected",
        code: resp.run.rejection_code ?? "QY_UNKNOWN",
        message: resp.run.rejection_message ?? "查询被策略拒绝",
      };
    case "failed":
      return {
        kind: "failed",
        code: resp.run.error_code ?? "QY_UNKNOWN",
        message: resp.run.error_message ?? "查询执行失败",
      };
  }
}

export async function submitSql(sql: string): Promise<ViewState> {
  let resp: Response;
  try {
    resp = await fetch("/api/v1/query-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
    });
  } catch {
    return { kind: "network-error", message: "无法连接查询服务" };
  }
  if (!resp.ok && resp.status !== 200) {
    return { kind: "network-error", message: `请求失败（HTTP ${resp.status}）` };
  }
  const body = (await resp.json()) as QueryRunResponse;
  return toViewState(body);
}
