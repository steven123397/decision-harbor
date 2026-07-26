import { useState } from "react";

// 响应契约见 docs/design/query-runs-api.md
interface QueryRunError {
  code: string;
  message: string;
}

interface QueryRun {
  id: string;
  status: "succeeded" | "rejected" | "failed";
  row_count: number | null;
  truncated: boolean;
  duration_ms: number | null;
  error: QueryRunError | null;
  created_at: string;
}

interface QueryResult {
  columns: { name: string; type: string }[];
  rows: (string | number | boolean | null)[][];
}

interface QueryRunResponse {
  query_run: QueryRun;
  result: QueryResult | null;
}

type Outcome =
  | { kind: "run"; run: QueryRun; result: QueryResult | null }
  | { kind: "request_error"; message: string };

const STATUS_LABELS: Record<QueryRun["status"], string> = {
  succeeded: "成功",
  rejected: "已拒绝",
  failed: "失败",
};

export function App() {
  const [sql, setSql] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  async function submit() {
    if (submitting || sql.trim() === "") {
      return;
    }
    setSubmitting(true);
    // 清除上一次错误；上一次成功结果保留到新结果返回，避免闪烁
    setOutcome((previous) =>
      previous?.kind === "run" && previous.run.status === "succeeded" ? previous : null,
    );
    try {
      const response = await fetch("/api/v1/query-runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql }),
      });
      const body = await response.json();
      if (!response.ok) {
        const message = body?.error
          ? `${body.error.code}：${body.error.message}`
          : `请求失败（HTTP ${response.status}）`;
        setOutcome({ kind: "request_error", message });
        return;
      }
      const parsed = body as QueryRunResponse;
      setOutcome({ kind: "run", run: parsed.query_run, result: parsed.result });
    } catch {
      setOutcome({ kind: "request_error", message: "网络错误，请求未完成" });
    } finally {
      setSubmitting(false);
    }
  }

  const statusText = submitting
    ? "执行中"
    : outcome === null
      ? "等待提交"
      : outcome.kind === "request_error"
        ? "请求失败"
        : STATUS_LABELS[outcome.run.status];

  const run = outcome?.kind === "run" ? outcome.run : null;
  const result = outcome?.kind === "run" ? outcome.result : null;

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: 16, fontFamily: "sans-serif" }}>
      <h1>DecisionHarbor 查询工作台</h1>
      <p>提交一条只读 SQL，在受治理规则内查询销售分析数据。</p>
      <textarea
        data-testid="sql-input"
        value={sql}
        onChange={(event) => setSql(event.target.value)}
        rows={8}
        style={{ width: "100%", fontFamily: "monospace" }}
        placeholder="SELECT region, count(*) FROM customers GROUP BY region"
      />
      <div style={{ margin: "8px 0" }}>
        <button
          data-testid="submit-query"
          onClick={submit}
          disabled={submitting || sql.trim() === ""}
        >
          提交查询
        </button>
        <span data-testid="query-status" style={{ marginLeft: 12 }}>
          {statusText}
        </span>
      </div>

      {outcome?.kind === "request_error" && (
        <p data-testid="error-panel" role="alert">
          {outcome.message}
        </p>
      )}

      {run && run.error && (
        <p data-testid="error-panel" role="alert">
          错误码 {run.error.code}：{run.error.message}（记录 {run.id}）
        </p>
      )}

      {run && run.status === "succeeded" && result && (
        <section>
          <p>
            返回 {run.row_count} 行，耗时 {run.duration_ms} ms。
            {run.truncated && (
              <strong data-testid="truncation-notice"> 结果已按上限截断。</strong>
            )}
          </p>
          <table data-testid="result-table" border={1} cellPadding={4}>
            <thead>
              <tr>
                {result.columns.map((column) => (
                  <th key={column.name} title={column.type}>
                    {column.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((value, cellIndex) => (
                    <td key={cellIndex}>{value === null ? "" : String(value)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </main>
  );
}
