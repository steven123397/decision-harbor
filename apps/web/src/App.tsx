import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Check,
  Clipboard,
  DatabaseZap,
  Play,
  RotateCcw,
  ShieldCheck,
  Timer,
} from "lucide-react";

import { ApiError, checkReadiness, submitQuery, type QueryRunResponse, type RunState } from "./api";

const DEFAULT_SQL = `SELECT
  c.region,
  COUNT(DISTINCT c.id) AS customer_count
FROM analytics.customers AS c
GROUP BY c.region
ORDER BY customer_count DESC;`;

const stateLabels: Record<RunState, string> = {
  received: "RECEIVED",
  executing: "EXECUTING",
  succeeded: "SUCCEEDED",
  rejected: "REJECTED",
  failed: "FAILED",
};

export default function App() {
  const [sql, setSql] = useState(DEFAULT_SQL);
  const [run, setRun] = useState<QueryRunResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState<boolean | null>(null);
  const [transportError, setTransportError] = useState<ApiError | Error | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    checkReadiness().then(setReady).catch(() => setReady(false));
  }, []);

  const lineNumbers = useMemo(() => sql.split("\n").map((_, index) => index + 1), [sql]);
  const currentState = busy ? "executing" : run?.state ?? "received";
  const stateLabel = stateLabels[currentState];

  async function executeQuery() {
    setBusy(true);
    setTransportError(null);
    setRun(null);
    try {
      setRun(await submitQuery(sql));
    } catch (error) {
      setTransportError(error instanceof Error ? error : new Error("Request failed"));
    } finally {
      setBusy(false);
    }
  }

  function resetQuery() {
    setSql(DEFAULT_SQL);
    setRun(null);
    setTransportError(null);
  }

  async function copyQuery() {
    await navigator.clipboard.writeText(sql);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><span>DH</span></div>
          <div>
            <p className="eyebrow">CONTROLLED ANALYTICS</p>
            <h1>DecisionHarbor</h1>
          </div>
        </div>
        <div className="topbar-meta">
          <span className={`service-pill ${ready ? "is-ready" : ready === false ? "is-down" : ""}`}>
            <span className="status-dot" />
            {ready ? "READY" : ready === false ? "NOT READY" : "CHECKING"}
          </span>
          <span className="version-stamp">SQL / V1</span>
        </div>
      </header>

      <main className="workspace">
        <section className="workspace-heading">
          <div>
            <p className="eyebrow accent">QUERY RUN / 01</p>
            <h2>Ask the warehouse precisely.</h2>
          </div>
          <p className="heading-note">Explicit SQL. Audited access. Read-only results.</p>
        </section>

        <section className="console-grid">
          <article className="query-panel panel-line">
            <div className="panel-header">
              <div className="panel-title"><Activity size={16} /><span>SQL EDITOR</span></div>
              <div className="panel-actions">
                <button className="icon-button" onClick={copyQuery} title="Copy SQL" aria-label="Copy SQL">
                  {copied ? <Check size={16} /> : <Clipboard size={16} />}
                </button>
                <button className="icon-button" onClick={resetQuery} title="Reset SQL" aria-label="Reset SQL">
                  <RotateCcw size={16} />
                </button>
              </div>
            </div>
            <div className="editor-frame">
              <div className="line-gutter" aria-hidden="true">
                {lineNumbers.map((line) => <span key={line}>{String(line).padStart(2, "0")}</span>)}
              </div>
              <textarea
                aria-label="SQL query"
                spellCheck={false}
                value={sql}
                onChange={(event) => setSql(event.target.value)}
              />
            </div>
            <div className="editor-footer">
              <span><ShieldCheck size={14} /> AST policy active</span>
              <span>UTF-8 / {new TextEncoder().encode(sql).length} B</span>
            </div>
            <button className="execute-button" disabled={busy || !sql.trim()} onClick={executeQuery}>
              <Play size={17} fill="currentColor" />
              {busy ? "Executing query" : "Execute query"}
            </button>
          </article>

          <aside className="run-panel panel-line">
            <div className="panel-header">
              <div className="panel-title"><DatabaseZap size={16} /><span>RUN TELEMETRY</span></div>
              <span className={`state-label state-${currentState}`}>{stateLabel}</span>
            </div>
            <div className="telemetry-grid">
              <div className="telemetry-item"><span>POLICY</span><strong>{run?.policy.decision?.toUpperCase() ?? "PENDING"}</strong></div>
              <div className="telemetry-item"><span>ROWS</span><strong>{run?.row_count ?? "--"}</strong></div>
              <div className="telemetry-item"><span>DURATION</span><strong>{run?.duration_ms != null ? `${run.duration_ms} ms` : "--"}</strong></div>
              <div className="telemetry-item"><span>RUN ID</span><strong className="mono">{run?.id?.slice(0, 8) ?? "--------"}</strong></div>
            </div>
            <div className="result-area">
              {transportError && <Notice tone="danger" title="Transport error" message={transportError.message} />}
              {!transportError && !run && !busy && <EmptyState />}
              {busy && <div className="running-state"><span className="pulse-ring" /><div><strong>Executing against analytics</strong><p>Waiting for an audited result.</p></div></div>}
              {!busy && run?.state === "rejected" && <Notice tone="warning" title={run.error?.code ?? "Query rejected"} message={run.error?.message ?? "The query did not pass policy."} />}
              {!busy && run?.state === "failed" && <Notice tone="danger" title={run.error?.code ?? "Query failed"} message={run.error?.message ?? "The query could not be completed."} />}
              {!busy && run?.state === "succeeded" && run.result && <ResultTable result={run.result} />}
            </div>
          </aside>
        </section>
      </main>

      <footer className="footer-bar">
        <span><Timer size={14} /> Read-only session</span>
        <span>analytics / platform audit separated</span>
      </footer>
    </div>
  );
}

function EmptyState() {
  return <div className="empty-state"><span className="empty-dash">--</span><p>Run a query to inspect the result set.</p></div>;
}

function Notice({ tone, title, message }: { tone: "warning" | "danger"; title: string; message: string }) {
  return <div className={`notice notice-${tone}`}><AlertTriangle size={19} /><div><strong>{title}</strong><p>{message}</p></div></div>;
}

function ResultTable({ result }: { result: NonNullable<QueryRunResponse["result"]> }) {
  return <div className="table-wrap"><table><thead><tr>{result.columns.map((column) => <th key={column.name}><span>{column.name}</span><small>{column.type}</small></th>)}</tr></thead><tbody>{result.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, cellIndex) => <td key={`${rowIndex}-${cellIndex}`}>{value == null ? <span className="null-value">NULL</span> : String(value)}</td>)}</tr>)}</tbody></table></div>;
}
