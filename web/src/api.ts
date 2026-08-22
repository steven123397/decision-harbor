/** 受理、轮询与生命周期操作的视图状态映射（唯一值得单元锁定的纯逻辑）。 */

export interface RunColumn {
  name: string;
  type: string;
}

export interface RunRecord {
  id: number;
  state: string;
  sql: string;
  attempt: number;
  retry_of: number | null;
  rejection_code: string | null;
  rejection_message: string | null;
  row_count: number | null;
  truncated: boolean;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export interface SnapshotResult {
  columns: RunColumn[];
  rows: string[][];
  row_count: number;
  truncated: boolean;
  expires_at: string;
}

/** 中间态逐个可辨（CONTEXT.md：received 与 queued 不混称「排队」）；
 * 终态四者互斥，cancelled 不并入失败面板。结果读取的 409/410 有专属
 * 反馈（#16）：过期是「曾经有过、超出了保留期」，与「无可读结果」不同层。 */
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
  | { kind: "expired"; runId: number }
  | { kind: "result-unavailable"; runId: number }
  | { kind: "network-error"; message: string };

export const TERMINAL_STATES = ["succeeded", "rejected", "failed", "cancelled"] as const;

/** 状态面板与历史列表共用的状态文案；八个状态逐个可辨。 */
export const STATE_LABELS: Record<string, string> = {
  received: "已受理",
  queued: "排队中",
  running: "执行中",
  cancelling: "取消中",
  succeeded: "成功",
  rejected: "策略拒绝",
  failed: "失败",
  cancelled: "已取消",
};

export function stateLabel(state: string): string {
  return STATE_LABELS[state] ?? state;
}

export function isTerminal(state: string): boolean {
  return (TERMINAL_STATES as readonly string[]).includes(state);
}

/** 中间态可取消（排队取消确定生效，运行中 best effort 中止）；
 * 终态不可再取消（ADR-0018）。资格规则与 API 侧 409 判定镜像，
 * 只用于隐藏入口——服务端仍是事实源，冲突由 409 文案兜底呈现。 */
export function canCancel(run: { state: string }): boolean {
  return !isTerminal(run.state);
}

/** 仅 failed / cancelled 可重试——重试创建新运行，不复活原运行；
 * rejected 引导改 SQL 重新提交，不给重试入口（CONTEXT.md「重试关系」）。
 * 同样是 API 侧 409 判定的镜像，只控入口可见性。 */
export function canRetry(run: { state: string }): boolean {
  return run.state === "failed" || run.state === "cancelled";
}

/** 运行记录 → 视图状态；succeeded 的结果细节由快照补充。 */
export function runToViewState(run: RunRecord, snapshot?: SnapshotResult | null): ViewState {
  const attempt = run.attempt ?? 1;
  if (!isTerminal(run.state)) {
    return {
      kind: "in-progress",
      runId: run.id,
      state: run.state,
      label: stateLabel(run.state),
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

export interface HistoryPage {
  runs: RunRecord[];
  nextCursor: string | null;
}

export type HistoryOutcome =
  | { kind: "page"; page: HistoryPage }
  | { kind: "error"; message: string };

export function historyOutcome(
  status: number,
  body: { runs?: RunRecord[]; next_cursor?: string | null } | null
): HistoryOutcome {
  if (status === 200 && body?.runs) {
    return { kind: "page", page: { runs: body.runs, nextCursor: body.next_cursor ?? null } };
  }
  return { kind: "error", message: `历史列表加载失败（HTTP ${status}）` };
}

/** 取消结果：受理（202 取消中 / 200 确定生效或幂等重复）、终态冲突 409、
 * 其余按错误处理。视图来自响应内的运行记录。 */
export type CancelOutcome =
  | { kind: "accepted"; view: ViewState }
  | { kind: "conflict"; message: string }
  | { kind: "error"; message: string };

export function cancelOutcome(
  status: number,
  body: RunEnvelope | null
): CancelOutcome {
  const run = body?.run;
  if ((status === 200 || status === 202) && run) {
    return { kind: "accepted", view: runToViewState(run) };
  }
  if (status === 409) {
    return { kind: "conflict", message: body?.detail?.message ?? "该运行无法取消" };
  }
  return { kind: "error", message: `取消请求失败（HTTP ${status}）` };
}

/** 重试结果：202 携带新运行（retry_of 指向原运行）；rejected 等的 409
 * 附可读指引（ADR-0018）。 */
export type RetryOutcome =
  | { kind: "accepted"; view: ViewState }
  | { kind: "conflict"; message: string }
  | { kind: "error"; message: string };

export function retryOutcome(
  status: number,
  body: RunEnvelope | null
): RetryOutcome {
  const run = body?.run;
  if (status === 202 && run) {
    return { kind: "accepted", view: runToViewState(run) };
  }
  if (status === 409) {
    return { kind: "conflict", message: body?.detail?.message ?? "该运行无法重试" };
  }
  return { kind: "error", message: `重试请求失败（HTTP ${status}）` };
}

/** 快照读取结果：200 快照 / 410 超过保留期 / 409 无可读结果（含未就绪）。 */
export type ResultOutcome =
  | { kind: "snapshot"; result: SnapshotResult }
  | { kind: "expired" }
  | { kind: "unavailable" }
  | { kind: "error"; status: number };

export function resultOutcome(
  status: number,
  body: { result?: SnapshotResult } | null
): ResultOutcome {
  if (status === 200 && body?.result) return { kind: "snapshot", result: body.result };
  if (status === 410) return { kind: "expired" };
  if (status === 409) return { kind: "unavailable" };
  return { kind: "error", status };
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

interface RunEnvelope {
  run?: RunRecord;
  detail?: { code?: string; message?: string };
}

function networkFailure(status?: number): ViewState {
  return {
    kind: "network-error",
    message: status === undefined ? "无法连接查询服务" : `请求失败（HTTP ${status}）`,
  };
}

const HISTORY_PAGE_LIMIT = 20;

/** 历史列表一页（按创建时间降序）；cursor 不透明，来自上一页的 nextCursor。 */
export async function fetchHistory(cursor?: string | null): Promise<HistoryOutcome> {
  const url = cursor
    ? `/api/v1/query-runs?limit=${HISTORY_PAGE_LIMIT}&cursor=${encodeURIComponent(cursor)}`
    : `/api/v1/query-runs?limit=${HISTORY_PAGE_LIMIT}`;
  try {
    const resp = await fetchJson<{ runs?: RunRecord[]; next_cursor?: string | null }>(url);
    return historyOutcome(resp.status, resp.body);
  } catch {
    return { kind: "error", message: "无法连接查询服务" };
  }
}

export async function cancelRun(runId: number): Promise<CancelOutcome> {
  try {
    const resp = await fetchJson<RunEnvelope>(`/api/v1/query-runs/${runId}/cancel`, {
      method: "POST",
    });
    return cancelOutcome(resp.status, resp.body);
  } catch {
    return { kind: "error", message: "无法连接查询服务" };
  }
}

export async function retryRun(runId: number): Promise<RetryOutcome> {
  try {
    const resp = await fetchJson<RunEnvelope>(`/api/v1/query-runs/${runId}/retry`, {
      method: "POST",
    });
    return retryOutcome(resp.status, resp.body);
  } catch {
    return { kind: "error", message: "无法连接查询服务" };
  }
}

/** succeeded 终态的视图落定：读快照；410/409 转专属反馈视图。 */
async function succeededView(run: RunRecord): Promise<ViewState> {
  let snap: { status: number; body: { result?: SnapshotResult } | null };
  try {
    snap = await fetchJson<{ result?: SnapshotResult }>(`/api/v1/query-runs/${run.id}/result`);
  } catch {
    return networkFailure();
  }
  const outcome = resultOutcome(snap.status, snap.body);
  switch (outcome.kind) {
    case "snapshot":
      return runToViewState(run, outcome.result);
    case "expired":
      return { kind: "expired", runId: run.id };
    case "unavailable":
      return { kind: "result-unavailable", runId: run.id };
    default:
      return networkFailure(outcome.status);
  }
}

/** 提交 → 立即受理（202，响应已含 queued 运行记录）或同步拒绝（422）；
 * 随后由调用方轮询。 */
export async function submitSql(
  sql: string
): Promise<{ view: ViewState; runId: number | null }> {
  let resp: { status: number; body: RunEnvelope | null };
  try {
    resp = await fetchJson<RunEnvelope>("/api/v1/query-runs", {
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

/** 读取一次运行并落定视图：中间态原样返回（调用方决定是否继续轮询），
 * 终态落定对应面板（succeeded 经快照读取，410/409 有专属反馈）。
 * 既是活动运行的轮询单步，也是打开历史运行详情的读取器。 */
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
  if (run.state === "succeeded") return succeededView(run);
  return runToViewState(run);
}
