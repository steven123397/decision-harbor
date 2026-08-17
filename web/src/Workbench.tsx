import { useEffect, useRef, useState } from "react";
import { pollOnce, submitSql, ViewState } from "./api";

const POLL_INTERVAL_MS = 500;

export default function Workbench() {
  const [sql, setSql] = useState("");
  const [view, setView] = useState<ViewState>({ kind: "idle" });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const busy = view.kind === "running";

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!sql.trim() || busy) return;
    if (timer.current) clearTimeout(timer.current);
    setView({ kind: "running" });
    const { view: afterSubmit, runId } = await submitSql(sql);
    setView(afterSubmit);
    if (runId !== null && afterSubmit.kind === "running") {
      schedulePoll(runId);
    }
  }

  function schedulePoll(runId: number) {
    timer.current = setTimeout(async () => {
      const next = await pollOnce(runId);
      setView(next);
      if (next.kind === "running") schedulePoll(runId);
    }, POLL_INTERVAL_MS);
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 960, margin: "0 auto", padding: 24 }}>
      <h1>DecisionHarbor 查询工作台</h1>
      <form onSubmit={onSubmit}>
        <textarea
          data-testid="sql-input"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
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
        >
          执行查询
        </button>
      </form>

      <section data-testid="status-area" aria-live="polite">
        {view.kind === "running" && (
          <p data-testid="running">执行中…</p>
        )}
        {view.kind === "succeeded" && (
          <>
            <p data-testid="result-meta">
              共 {view.rowCount} 行 · 耗时 {view.durationMs} ms
              {view.truncated ? ` · 结果已截断，仅显示前 ${view.rowCount} 行` : ""}
            </p>
            <div style={{ overflowX: "auto" }}>
              <table data-testid="result-table" border={1} cellPadding={6} style={{ borderCollapse: "collapse" }}>
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
          <p data-testid="rejected">
            查询被拒绝（{view.code}）：{view.message}
          </p>
        )}
        {view.kind === "failed" && (
          <p data-testid="failed">
            查询失败（{view.code}）：{view.message}
          </p>
        )}
        {view.kind === "network-error" && (
          <p data-testid="network-error">{view.message}</p>
        )}
      </section>
    </main>
  );
}
