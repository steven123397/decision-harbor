import { useEffect, useRef, useState } from "react";
import { pollOnce, submitSql, ViewState } from "./api";

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

export default function Workbench() {
  const [sql, setSql] = useState("");
  const [view, setView] = useState<ViewState>({ kind: "idle" });
  const [submitting, setSubmitting] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const formRef = useRef<HTMLFormElement>(null);

  const busy = submitting || view.kind === "in-progress";

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!sql.trim() || busy) return;
    if (timer.current) clearTimeout(timer.current);
    setSubmitting(true);
    try {
      const { view: afterSubmit, runId } = await submitSql(sql);
      setView(afterSubmit);
      if (runId !== null && afterSubmit.kind === "in-progress") {
        schedulePoll(runId);
      }
    } finally {
      setSubmitting(false);
    }
  }

  function schedulePoll(runId: number) {
    timer.current = setTimeout(async () => {
      const next = await pollOnce(runId);
      setView(next);
      if (next.kind === "in-progress") schedulePoll(runId);
    }, POLL_INTERVAL_MS);
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
          <p data-testid="in-progress" style={panels.progress}>
            {view.label} · 运行 #{view.runId}
            {view.attempt > 1 ? ` · 第 ${view.attempt} 次尝试` : ""}
          </p>
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
          <p data-testid="failed" style={panels.error}>
            <strong>执行失败（错误码 {view.code}）</strong> · 运行 #{view.runId}：{view.message}
            {view.attempt > 1 ? `（共尝试 ${view.attempt} 次）` : ""}
          </p>
        )}
        {view.kind === "cancelled" && (
          <p data-testid="cancelled" style={panels.neutral}>
            <strong>查询已取消</strong> · 运行 #{view.runId}：{view.message}
          </p>
        )}
        {view.kind === "network-error" && (
          <p data-testid="network-error" style={panels.error}>
            {view.message}
          </p>
        )}
      </section>
    </main>
  );
}
