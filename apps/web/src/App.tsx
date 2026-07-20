import { useState, type FormEvent } from "react";
import { getApiBase, submitQuery, type QueryEnvelope } from "./api";

const DEFAULT_SQL = `SELECT c.region,
       COUNT(*) AS order_count
FROM customers c
JOIN orders o ON o.customer_id = c.id
WHERE o.status = 'confirmed'
GROUP BY c.region
ORDER BY c.region`;

export function App() {
  const [sql, setSql] = useState(DEFAULT_SQL);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<QueryEnvelope | null>(null);
  const [clientError, setClientError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setRunning(true);
    setClientError(null);
    setResult(null);
    try {
      const envelope = await submitQuery(sql);
      setResult(envelope);
    } catch (err) {
      setClientError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  const succeeded = result?.status === "succeeded";
  const columns = result?.data?.columns ?? [];
  const rows = result?.data?.rows ?? [];

  return (
    <main>
      <h1>DecisionHarbor 查询工作台</h1>
      <p className="subtitle">
        显式 SQL · 受治理只读执行 · API: {getApiBase()}
      </p>

      <form onSubmit={onSubmit}>
        <label htmlFor="sql">SQL</label>
        <textarea
          id="sql"
          data-testid="sql-input"
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          spellCheck={false}
        />
        <div className="actions">
          <button type="submit" data-testid="submit-query" disabled={running}>
            {running ? "执行中…" : "提交查询"}
          </button>
        </div>
      </form>

      {running && (
        <div className="status running" data-testid="status-running">
          执行中…
        </div>
      )}

      {clientError && (
        <div className="status error" data-testid="client-error">
          {clientError}
        </div>
      )}

      {result && !running && result.status !== "succeeded" && (
        <div className="status error" data-testid="query-error">
          <div>
            状态: {result.status}
            {result.id ? ` · ID: ${result.id}` : ""}
          </div>
          <div>
            {result.error?.code}: {result.error?.message}
          </div>
        </div>
      )}

      {succeeded && !running && (
        <section data-testid="query-result">
          <div className="meta">
            状态 succeeded
            {result.id ? ` · ID ${result.id}` : ""}
            {` · 行数 ${result.data?.row_count ?? rows.length}`}
            {` · 耗时 ${result.data?.duration_ms ?? "?"} ms`}
          </div>
          <table>
            <thead>
              <tr>
                {columns.map((col) => (
                  <th key={col.name}>{col.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>{cell === null ? "NULL" : String(cell)}</td>
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
