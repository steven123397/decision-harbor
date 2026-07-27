import { useEffect, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Database,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from 'lucide-react'

import { httpApi, type ApiClient, type QueryResponse, type QueryRun } from './api'
import './styles.css'

export type { ApiClient, QueryResponse } from './api'

const DEFAULT_SQL = `SELECT
  c.region,
  round(sum(oi.quantity * oi.unit_price * (1 - oi.discount_rate)), 2) AS revenue
FROM customers AS c
JOIN orders AS o ON o.customer_id = c.id
JOIN order_items AS oi ON oi.order_id = o.id
WHERE o.status = 'confirmed'
GROUP BY c.region
ORDER BY revenue DESC;`

const ERROR_MESSAGES: Record<string, string> = {
  invalid_request: 'The request could not be accepted.',
  sql_empty: 'Enter a SQL query before running it.',
  sql_too_large: 'The SQL query exceeds the supported size limit.',
  sql_parse_error: 'The SQL query could not be parsed.',
  multiple_statements: 'Submit exactly one SQL statement.',
  sql_statement_not_allowed: 'This SQL statement is not allowed.',
  sql_object_not_allowed: 'This query references an object outside the analytics dataset.',
  sql_function_not_allowed: 'This query uses a function that is not allowed.',
  unsupported_sql: 'This SQL construct is not supported.',
  query_semantic_error: 'The query is not valid for this dataset.',
  query_capacity_exceeded: 'The query service is busy. Run it again shortly.',
  query_timeout: 'The query exceeded its time limit.',
  analytics_unavailable: 'The analytics database is unavailable.',
  audit_unavailable: 'The audit store is unavailable.',
  service_not_ready: 'The service is not ready.',
  policy_internal_error: 'The query policy could not complete.',
  internal_error: 'The query could not be completed.',
  unsupported_result_type: 'The query returned a result type that is not supported.',
  query_run_not_found: 'The query run was not found.',
  execution_interrupted: 'The query execution was interrupted before completion.',
}

type ViewState =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'complete'; response: QueryResponse }

export function App({ api = httpApi }: { api?: ApiClient }) {
  const [sql, setSql] = useState(DEFAULT_SQL)
  const [readiness, setReadiness] = useState<'checking' | 'ready' | 'unavailable'>('checking')
  const [view, setView] = useState<ViewState>({ kind: 'idle' })

  useEffect(() => {
    let active = true
    api.checkReady().then((ready) => {
      if (active) setReadiness(ready ? 'ready' : 'unavailable')
    })
    return () => {
      active = false
    }
  }, [api])

  const runQuery = async () => {
    if (!sql.trim() || view.kind === 'running' || readiness !== 'ready') return
    setView({ kind: 'running' })
    const response = await api.runQuery(sql)
    setView({ kind: 'complete', response })
    if (response.error?.code === 'service_not_ready') setReadiness('unavailable')
  }

  const running = view.kind === 'running'
  const checkReadiness = async () => {
    setReadiness('checking')
    setReadiness((await api.checkReady()) ? 'ready' : 'unavailable')
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true"><Database size={19} /></span>
          <span className="brand-name">DecisionHarbor</span>
        </div>
        <div className="readiness-group">
          <div className={`readiness readiness-${readiness}`} role="status">
            <span className="status-dot" aria-hidden="true" />
            {readiness === 'checking' ? 'Checking' : readiness === 'ready' ? 'Ready' : 'Unavailable'}
          </div>
          {readiness === 'unavailable' && (
            <button
              type="button"
              className="icon-button"
              aria-label="Check readiness"
              title="Check readiness"
              onClick={checkReadiness}
            >
              <RefreshCw size={15} />
            </button>
          )}
        </div>
      </header>

      <main>
        <section className="workspace-heading">
          <div>
            <span className="section-kicker">Sales analytics</span>
            <h1>Query workbench</h1>
          </div>
          <div className="policy-lock"><ShieldCheck size={17} /> Governed read-only</div>
        </section>

        <section className="editor-panel" aria-label="Query editor">
          <div className="editor-bar">
            <span>PostgreSQL</span>
            <button
              type="button"
              className="run-button"
              onClick={runQuery}
              disabled={running || readiness !== 'ready' || !sql.trim()}
            >
              {running ? <LoaderCircle className="spin" size={17} /> : <Play size={17} fill="currentColor" />}
              Run query
            </button>
          </div>
          <textarea
            aria-label="SQL query"
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            spellCheck={false}
            disabled={running}
          />
        </section>

        <section className="output" aria-live="polite">
          {view.kind === 'idle' && <IdleState />}
          {view.kind === 'running' && <RunningState />}
          {view.kind === 'complete' && <CompletedState response={view.response} />}
        </section>
      </main>
    </div>
  )
}

function IdleState() {
  return (
    <div className="empty-state">
      <Database size={22} />
      <span>No query run in this session</span>
    </div>
  )
}

function RunningState() {
  return (
    <div className="run-state running-state">
      <LoaderCircle className="spin" size={21} />
      <div><strong>Running</strong><span>Policy and database checks are in progress.</span></div>
    </div>
  )
}

function CompletedState({ response }: { response: QueryResponse }) {
  const run = response.data?.query_run
  if (run?.status === 'succeeded' && response.data?.result) {
    return <SuccessState run={run} result={response.data.result} />
  }
  if (run?.status === 'rejected') {
    return (
      <ErrorState
        kind="rejected"
        title="Query rejected"
        code={response.error?.code ?? run.error_code ?? 'unsupported_sql'}
        message={safeMessage(response)}
        run={run}
      />
    )
  }
  return (
    <ErrorState
      kind="failed"
      title="Execution failed"
      code={response.error?.code ?? run?.error_code ?? 'internal_error'}
      message={safeMessage(response)}
      run={run}
    />
  )
}

function SuccessState({ run, result }: { run: QueryRun; result: NonNullable<QueryResponse['data']>['result'] }) {
  if (!result) return null
  return (
    <>
      <div className="result-summary">
        <div className="run-state success-state"><CheckCircle2 size={21} /><strong>Query succeeded</strong></div>
        <AuditFacts run={run} />
      </div>
      {result.truncated && (
        <div className="truncated-notice"><AlertTriangle size={17} /><strong>Result truncated</strong><span>Only the configured row limit is shown.</span></div>
      )}
      <div className="table-frame">
        <table>
          <thead>
            <tr>{result.columns.map((column, index) => <th key={`${column.name}-${index}`}><span>{column.name}</span><small>{column.type}</small></th>)}</tr>
          </thead>
          <tbody>
            {result.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>{row.map((cell, columnIndex) => <td key={columnIndex}>{cell === null ? <span className="null-value">NULL</span> : String(cell)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function ErrorState({ kind, title, code, message, run }: { kind: 'rejected' | 'failed'; title: string; code: string; message: string; run?: QueryRun }) {
  const Icon = kind === 'rejected' ? AlertTriangle : XCircle
  return (
    <div className={`error-state error-${kind}`}>
      <div className="error-heading"><Icon size={22} /><div><strong>{title}</strong><code>{code}</code></div></div>
      <p>{message}</p>
      {run && <AuditFacts run={run} />}
    </div>
  )
}

function AuditFacts({ run }: { run: QueryRun }) {
  return (
    <dl className="audit-facts">
      <div><dt>Run ID</dt><dd>{run.id}</dd></div>
      {run.returned_row_count !== null && <div><dt>Rows</dt><dd>{run.returned_row_count} rows</dd></div>}
      {run.duration_ms !== null && <div><dt>Duration</dt><dd>{run.duration_ms} ms</dd></div>}
    </dl>
  )
}

function safeMessage(response: QueryResponse): string {
  const code = response.error?.code ?? response.data?.query_run.error_code
  return (code && ERROR_MESSAGES[code]) ?? 'The query could not be completed.'
}
