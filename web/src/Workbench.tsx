import { useEffect, useRef, useState } from "react";
import {
  cancelRun,
  fetchHistory,
  HistoryPage,
  pollOnce,
  retryRun,
  submitSql,
  ViewState,
} from "./api";
import RunHistory from "./RunHistory";

const POLL_INTERVAL_MS = 500;

const pageStyle: React.CSSProperties = {
  fontFamily: "system-ui, sans-serif",
  maxWidth: 960,
  margin: "0 auto",
  padding: "24px 16px",
};

// 状态面板按层级着色：推进中、策略拒绝、执行失败、已取消各有其形。
const panelBase: React.CSSProperties = {
  margin: "16px 0",
  padding: "12px 16px",
  border: "1px solid",
  borderRadius: 6,
  overflowWrap: "anywhere",
};

const panels = {
  progress: { ...panelBase, borderColor: "#9db8d9", background: "#f0f5fb" },
  warn: { ...panelBase, borderColor: "#d9b36c", background: "#fdf6e7" },
  error: { ...panelBase, borderColor: "#d98c8c", background: "#fdeeec" },
  neutral: { ...panelBase, borderColor: "#c8c8c8", background: "#f5f5f5" },
};

const actionButton: React.CSSProperties = {
  marginTop: 8,
  marginRight: 8,
  padding: "6px 16px",
};

export default function Workbench() {
  const [sql, setSql] = useState("");
  const [view, setView] = useState<ViewState>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);
  const [history, setHistory] = useState<HistoryPage | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 当前轮询目标：打开别的运行详情时，旧目标在途的最后一次轮询结果
  // 不得覆盖新视图（清定时器挡不住已在 await 中的回调）。
  const pollTarget = useRef<number | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  const busy = submitting || view.kind === "in-progress";

  useEffect(() => {
    void refreshHistory();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshHistory() {
    const outcome = await fetchHistory(null);
    if (outcome.kind === "page") {
      setHistory(outcome.page);
      setHistoryError(null);
    } else {
      setHistoryError(outcome.message);
    }
  }

  async function loadHistoryMore() {
    if (!history?.nextCursor) return;
    const outcome = await fetchHistory(history.nextCursor);
    if (outcome.kind === "page") {
      setHistory({
        runs: [...history.runs, ...outcome.page.runs],
        nextCursor: outcome.page.nextCursor,
      });
      setHistoryError(null);
    } else {
      // 翻页失败归历史区呈现，保留已加载的行
      setHistoryError(outcome.message);
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!sql.trim() || busy) return;
    stopPolling();
    setSubmitting(true);
    try {
      const { view: afterSubmit, runId } = await submitSql(sql);
      setView(afterSubmit);
      // 受理与拒绝都会产生历史行，刷新首页。
      void refreshHistory();
      if (runId !== null && afterSubmit.kind === "in-progress") {
        startPolling(runId);
      }
    } finally {
      setSubmitting(false);
    }
  }

  function startPolling(runId: number) {
    pollTarget.current = runId;
    if (timer.current) clearTimeout(timer.current);
    schedulePoll(runId);
  }

  function stopPolling() {
    pollTarget.current = null;
    if (timer.current) clearTimeout(timer.current);
  }

  function schedulePoll(runId: number) {
    timer.current = setTimeout(async () => {
      const next = await pollOnce(runId);
      if (pollTarget.current !== runId) return;
      setView(next);
      if (next.kind === "in-progress") schedulePoll(runId);
      else void refreshHistory();
    }, POLL_INTERVAL_MS);
  }

  /** 打开历史运行的详情：先停旧轮询（终态详情不得被在途回调覆盖），
   * 中间态则接管为新轮询目标；终态直接落定面板（succeeded 走快照
   * 读取，410/409 有专属反馈）。 */
  async function openRun(runId: number) {
    setActionMessage(null);
    stopPolling();
    const next = await pollOnce(runId);
    setView(next);
    if (next.kind === "in-progress") startPolling(runId);
  }

  async function onCancel(runId: number) {
    setActionMessage(null);
    const outcome = await cancelRun(runId);
    if (outcome.kind === "accepted") {
      setView(outcome.view);
      if (outcome.view.kind === "in-progress") startPolling(runId);
      else stopPolling();
      void refreshHistory();
    } else {
      // 409 等冲突是稳定事实：呈现服务端文案（如「已处于终态，无法取消」）。
      setActionMessage(outcome.message);
      void refreshHistory();
    }
  }

  async function onRetry(runId: number) {
    setActionMessage(null);
    const outcome = await retryRun(runId);
    if (outcome.kind === "accepted") {
      setView(outcome.view);
      if (outcome.view.kind === "in-progress") startPolling(outcome.view.runId);
      else stopPolling();
      void refreshHistory();
    } else {
      setActionMessage(outcome.message);
    }
  }

  function onSqlKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    // 键盘快捷提交：Ctrl/Cmd+Enter；Tab 到按钮后 Enter 是原生路径。
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      formRef.current?.requestSubmit();
    }
  }

  return (
    <main style={pageStyle}>
      <h1>DecisionHarbor 查询工作台</h1>
      <form ref={formRef} onSubmit={onSubmit}>
        <label htmlFor="sql-input" style={{ display: "block", marginBottom: 8, fontWeight: 600 }}>
          SQL 查询
        </label>
        <textarea
          id="sql-input"
          data-testid="sql-input"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onKeyDown={onSqlKeyDown}
          rows={8}
          spellCheck={false}
          style={{
            width: "100%",
            fontFamily: "ui-monospace, monospace",
            boxSizing: "border-box",
          }}
          placeholder="输入只读 SELECT 查询，例如 SELECT region, count(*) FROM customers GROUP BY region"
        />
        <button
          data-testid="submit"
          type="submit"
          disabled={busy || !sql.trim()}
          style={{ marginTop: 8, padding: "8px 20px" }}
        >
          执行查询
        </button>
      </form>

      <section data-testid="status-area" aria-live="polite">
        {submitting && (
          <p data-testid="submitting" style={panels.progress}>
            正在提交…
          </p>
        )}
        {view.kind === "in-progress" && (
          <>
            <p data-testid="in-progress" style={panels.progress}>
              {view.label} · 运行 #{view.runId}
              {view.attempt > 1 ? ` · 第 ${view.attempt} 次尝试` : ""}
            </p>
            <button data-testid="cancel-run" style={actionButton} onClick={() => onCancel(view.runId)}>
              取消查询
            </button>
          </>
        )}
        {view.kind === "succeeded" && (
          <>
            <p data-testid="result-meta">
              运行 #{view.runId} · 共 {view.rowCount} 行 · 耗时 {view.durationMs} ms
              {view.truncated ? ` · 结果已截断，仅显示前 ${view.rowCount} 行` : ""}
            </p>
            <div data-testid="result-table-wrap" style={{ overflowX: "auto", maxWidth: "100%" }}>
              <table
                data-testid="result-table"
                border={1}
                cellPadding={6}
                style={{ borderCollapse: "collapse" }}
              >
                <thead>
                  <tr>
                    {view.columns.map((c) => (
                      <th key={c.name}>{c.name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {view.rows.map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j}>{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
        {view.kind === "rejected" && (
          <p data-testid="rejected" style={panels.warn}>
            <strong>策略拒绝（拒绝码 {view.code}）</strong> · 运行 #{view.runId}：{view.message}
          </p>
        )}
        {view.kind === "failed" && (
          <>
            <p data-testid="failed" style={panels.error}>
              <strong>执行失败（错误码 {view.code}）</strong> · 运行 #{view.runId}：{view.message}
              {view.attempt > 1 ? `（共尝试 ${view.attempt} 次）` : ""}
            </p>
            <button data-testid="retry-run" style={actionButton} onClick={() => onRetry(view.runId)}>
              重试
            </button>
          </>
        )}
        {view.kind === "cancelled" && (
          <>
            <p data-testid="cancelled" style={panels.neutral}>
              <strong>查询已取消</strong> · 运行 #{view.runId}：{view.message}
            </p>
            <button data-testid="retry-run" style={actionButton} onClick={() => onRetry(view.runId)}>
              重试
            </button>
          </>
        )}
        {view.kind === "expired" && (
          <p data-testid="result-expired" style={panels.warn}>
            <strong>结果已过期</strong> · 运行 #{view.runId}：结果已超过保留期，无法读取；运行
            审计记录仍保留
          </p>
        )}
        {view.kind === "result-unavailable" && (
          // 「未完成」不经此面板：UI 只对 succeeded 运行发起结果读取，
          // 非终态运行打开详情呈现中间态面板本身就是未完成反馈。
          <p data-testid="result-unavailable" style={panels.neutral}>
            <strong>结果不可读取</strong> · 运行 #{view.runId}：该运行没有可读取的结果
          </p>
        )}
        {view.kind === "network-error" && (
          <p data-testid="network-error" style={panels.error}>
            {view.message}
          </p>
        )}
        {actionMessage && (
          <p data-testid="action-message" style={panels.warn}>
            {actionMessage}
          </p>
        )}
      </section>

      <RunHistory
        page={history}
        error={historyError}
        onView={openRun}
        onCancel={onCancel}
        onRetry={onRetry}
        onMore={loadHistoryMore}
      />
    </main>
  );
}
