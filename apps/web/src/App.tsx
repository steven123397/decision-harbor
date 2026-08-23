import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  Database,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from 'lucide-react'

import { httpApi, type ApiClient, type ApiError, type QueryResult, type QueryRun, type QueryRunStatus } from './api'
import './styles.css'

export type { ApiClient, QueryResponse, ResultResponse } from './api'

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
  execution_attempts_exhausted: 'The query did not complete within its execution attempt limit.',
  audit_unavailable: 'The audit store is unavailable.',
  service_not_ready: 'The service is not ready.',
  policy_internal_error: 'The query policy could not complete.',
  internal_error: 'The query could not be completed.',
  unsupported_result_type: 'The query returned a result type that is not supported.',
  result_too_large: 'The query result exceeds the supported size limit.',
  result_not_ready: 'The query result is not ready yet.',
  result_unavailable: 'The query result is not available for this run.',
  query_run_not_found: 'The query run was not found.',
}

const TERMINAL_STATUSES: ReadonlySet<QueryRunStatus> = new Set([
  'succeeded',
  'failed',
  'rejected',
  'cancelled',
])

const STATUS_LABELS: Partial<Record<QueryRunStatus, string>> = {
  queued: 'Queued',
  running: 'Running',
  cancelling: 'Cancelling',
}

const STATUS_HINTS: Partial<Record<QueryRunStatus, string>> = {
  queued: 'The query is waiting for an available worker.',
  running: 'The worker is executing the query against the analytics database.',
  cancelling: 'The cancellation request is being applied.',
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

const ACTIVE_RUN_KEY = 'decisionharbor.active-run'

type ViewState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'restoring' }
  | { kind: 'active'; run: QueryRun }
  | { kind: 'complete'; run: QueryRun | null; result: QueryResult | null; error: ApiError | null }

export function App({ api = httpApi, pollIntervalMs = 1000 }: { api?: ApiClient; pollIntervalMs?: number }) {
  const [sql, setSql] = useState(DEFAULT_SQL)
  const [readiness, setReadiness] = useState<'checking' | 'ready' | 'unavailable'>('checking')
  const [view, setView] = useState<ViewState>({ kind: 'idle' })
  const pollTokenRef = useRef(0)

  useEffect(() => {
    let active = true
    api.checkReady().then((ready) => {
      if (active) setReadiness(ready ? 'ready' : 'unavailable')
    })
    return () => {
      active = false
    }
  }, [api])

  // 卸载（含页面刷新前）使轮询令牌失效：停止一切在途轮询。
  useEffect(() => () => {
    pollTokenRef.current += 1
  }, [])

  // 页面刷新后按运行标识恢复未完成运行的轮询；浏览器会话不是查询生命周期的所有者。
  useEffect(() => {
    const savedRunId = sessionStorage.getItem(ACTIVE_RUN_KEY)
    if (!savedRunId) return
    const token = ++pollTokenRef.current
    setView({ kind: 'restoring' })
    void poll(savedRunId, token)
  }, [])

  const finish = async (run: QueryRun, token: number) => {
    sessionStorage.removeItem(ACTIVE_RUN_KEY)
    if (run.status !== 'succeeded') {
      setView({ kind: 'complete', run, result: null, error: null })
      return
    }
    const response = await api.getQueryResult(run.id)
    if (pollTokenRef.current !== token) return
    setView({
      kind: 'complete',
      run,
      result: response.data?.result ?? null,
      error: response.data ? null : response.error,
    })
  }

  const poll = async (runId: string, token: number) => {
    await delay(pollIntervalMs)
    if (pollTokenRef.current !== token) return
    const response = await api.getQueryRun(runId)
    if (pollTokenRef.current !== token) return
    const run = response.data?.query_run
    if (!run) {
      setView({ kind: 'complete', run: null, result: null, error: response.error })
      return
    }
    if (TERMINAL_STATUSES.has(run.status)) {
      await finish(run, token)
      return
    }
    setView({ kind: 'active', run })
    void poll(runId, token)
  }

  const runQuery = async () => {
    if (!sql.trim() || busy || readiness !== 'ready') return
    const token = ++pollTokenRef.current
    setView({ kind: 'submitting' })
    const response = await api.submitQuery(sql)
    if (pollTokenRef.current !== token) return
    if (response.error?.code === 'service_not_ready') setReadiness('unavailable')
    const run = response.data?.query_run
    if (!run) {
      setView({ kind: 'complete', run: null, result: null, error: response.error })
      return
    }
    if (TERMINAL_STATUSES.has(run.status)) {
      await finish(run, token)
      return
    }
    sessionStorage.setItem(ACTIVE_RUN_KEY, run.id)
    setView({ kind: 'active', run })
    void poll(run.id, token)
  }

  const busy = view.kind === 'submitting' || view.kind === 'active'
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
              disabled={busy || readiness !== 'ready' || !sql.trim()}
            >
              {busy ? <LoaderCircle className="spin" size={17} /> : <Play size={17} fill="currentColor" />}
              Run query
            </button>
          </div>
          <textarea
            aria-label="SQL query"
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            spellCheck={false}
            disabled={busy}
          />
        </section>

        <section className="output" aria-live="polite">
          {view.kind === 'idle' && <IdleState />}
          {view.kind === 'submitting' && (
            <ProgressState label="Submitting" hint="The governance policy is evaluating the SQL." />
          )}
          {view.kind === 'restoring' && (
            <ProgressState
              label="Restoring run"
              hint="Recovering the query run state after the page reloaded."
            />
          )}
          {view.kind === 'active' && (
            <ProgressState
              label={STATUS_LABELS[view.run.status] ?? 'Submitted'}
              hint={STATUS_HINTS[view.run.status] ?? 'The query run is being processed.'}
            />
          )}
          {view.kind === 'complete' && (
            <CompletedState run={view.run} result={view.result} error={view.error} />
          )}
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

function ProgressState({ label, hint }: { label: string; hint: string }) {
  return (
    <div className="run-state running-state">
      <LoaderCircle className="spin" size={21} />
      <div><strong>{label}</strong><span>{hint}</span></div>
    </div>
  )
}

function CompletedState({
  run,
  result,
  error,
}: {
  run: QueryRun | null
  result: QueryResult | null
  error: ApiError | null
}) {
  if (run?.status === 'succeeded' && result) {
    return <SuccessState run={run} result={result} />
  }
  if (run?.status === 'rejected') {
    return (
      <ErrorState
        kind="rejected"
        title="Query rejected"
        code={error?.code ?? run.error_code ?? 'unsupported_sql'}
        message={messageFor(error, run)}
        run={run}
      />
    )
  }
  if (run?.status === 'cancelled') {
    return (
      <ErrorState
        kind="cancelled"
        title="Query cancelled"
        code={run.error_code ?? 'cancelled'}
        message="The query run was cancelled and will not produce a result."
        run={run}
      />
    )
  }
  if (run) {
    return (
      <ErrorState
        kind="failed"
        title="Execution failed"
        code={error?.code ?? run.error_code ?? 'internal_error'}
        message={messageFor(error, run)}
        run={run}
      />
    )
  }
  return (
    <ErrorState
      kind="failed"
      title="Submission failed"
      code={error?.code ?? 'internal_error'}
      message={messageFor(error, null)}
    />
  )
}

function SuccessState({ run, result }: { run: QueryRun; result: QueryResult }) {
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

function ErrorState({ kind, title, code, message, run }: { kind: 'rejected' | 'failed' | 'cancelled'; title: string; code: string; message: string; run?: QueryRun }) {
  const Icon = kind === 'rejected' ? AlertTriangle : kind === 'cancelled' ? Ban : XCircle
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

function messageFor(error: ApiError | null, run: QueryRun | null): string {
  const code = error?.code ?? run?.error_code
  return (code && ERROR_MESSAGES[code]) ?? 'The query could not be completed.'
}
