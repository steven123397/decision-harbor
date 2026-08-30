import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Clock,
  Database,
  FileQuestion,
  FileWarning,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  XCircle,
} from 'lucide-react'

import { TRANSPORT_ERROR, httpApi, type ApiClient, type QueryResult, type QueryRun, type RunResponse } from './api'
import {
  readTrackedRunId,
  rememberTrackedRunId,
  trackedRun,
  useTrackedRun,
  type ResultProblem,
  type TrackedRun,
} from './runTracking'
import './styles.css'

export type { ApiClient, RunResponse } from './api'

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
  [TRANSPORT_ERROR]: 'The service could not be reached.',
}

const PENDING_COPY: Partial<Record<QueryRun['status'], { title: string; detail: string }>> = {
  queued: { title: 'Queued', detail: 'A worker will run this query shortly.' },
  running: { title: 'Running', detail: 'The query worker is executing this run.' },
  cancelling: { title: 'Cancelling', detail: 'The query worker is stopping this run.' },
}

type Phase = 'idle' | 'submitting' | 'submit-failed' | 'tracking'

export function App({ api = httpApi }: { api?: ApiClient }) {
  const [sql, setSql] = useState(DEFAULT_SQL)
  const [readiness, setReadiness] = useState<'checking' | 'ready' | 'unavailable'>('checking')
  const [phase, setPhase] = useState<Phase>('idle')
  const [submitFailure, setSubmitFailure] = useState<RunResponse | null>(null)
  const { state: tracked, track } = useTrackedRun(api)
  // Only a page that opened on a run identifier may overwrite the editor with
  // the SQL of the run it restores.
  const mayRestoreSql = useRef(readTrackedRunId() !== null)

  useEffect(() => {
    let active = true
    api.checkReady().then((ready) => {
      if (active) setReadiness(ready ? 'ready' : 'unavailable')
    })
    return () => {
      active = false
    }
  }, [api])

  useEffect(() => {
    const restored = readTrackedRunId()
    if (restored === null) return
    setPhase('tracking')
    track(restored)
  }, [track])

  useEffect(() => {
    if (!mayRestoreSql.current) return
    const run = trackedRun(tracked)
    if (run === null) return
    mayRestoreSql.current = false
    if (run.raw_sql.trim()) setSql(run.raw_sql)
  }, [tracked])

  const submitting = phase === 'submitting'

  const runQuery = async () => {
    if (!sql.trim() || submitting || readiness !== 'ready') return
    setPhase('submitting')
    const response = await api.runQuery(sql)
    const run = response.data?.query_run ?? null
    if (response.error?.code === 'service_not_ready' || response.error?.code === TRANSPORT_ERROR) {
      setReadiness('unavailable')
    }
    if (run !== null) {
      rememberTrackedRunId(run.id)
      setPhase('tracking')
      track(run.id, run)
      mayRestoreSql.current = false
      return
    }
    setSubmitFailure(response)
    setPhase('submit-failed')
  }

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
              disabled={submitting || readiness !== 'ready' || !sql.trim()}
            >
              {submitting ? <LoaderCircle className="spin" size={17} /> : <Play size={17} fill="currentColor" />}
              Run query
            </button>
          </div>
          <textarea
            aria-label="SQL query"
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            spellCheck={false}
            disabled={submitting}
          />
        </section>

        <section className="output" aria-live="polite">
          {phase === 'idle' && <IdleState />}
          {phase === 'submitting' && <SubmittingState />}
          {phase === 'submit-failed' && submitFailure && <SubmitFailureState response={submitFailure} />}
          {phase === 'tracking' && <TrackedState state={tracked} />}
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

function SubmittingState() {
  return (
    <div className="run-state running-state">
      <LoaderCircle className="spin" size={21} />
      <div><strong>Submitting</strong><span>Policy checks are in progress.</span></div>
    </div>
  )
}

function SubmitFailureState({ response }: { response: RunResponse }) {
  const code = response.error?.code ?? 'internal_error'
  return (
    <ErrorState
      kind="failed"
      title="Query not started"
      code={code}
      message={ERROR_MESSAGES[code] ?? ERROR_MESSAGES.internal_error}
      nextStep="No query run was created. Check the query and the service, then run it again."
    />
  )
}

function TrackedState({ state }: { state: TrackedRun }) {
  switch (state.kind) {
    case 'idle':
      return <SubmittingState />
    case 'restoring':
      return <RestoringState runId={state.runId} reconnecting={state.reconnecting} />
    case 'pending':
      return <PendingState run={state.run} reconnecting={state.reconnecting} />
    case 'success':
      return <SuccessState run={state.run} result={state.result} problem={state.problem} />
    case 'terminal':
      return <TerminalState run={state.run} />
    case 'missing':
      return <MissingState runId={state.runId} />
  }
}

function RestoringState({ runId, reconnecting }: { runId: string; reconnecting: boolean }) {
  return (
    <div className="run-state running-state">
      <LoaderCircle className="spin" size={21} />
      <div>
        <strong>Restoring run</strong>
        <span>Reading the query run this page address points at.</span>
        {reconnecting && <ReconnectingNotice />}
      </div>
      <RunIdFact runId={runId} />
    </div>
  )
}

function PendingState({ run, reconnecting }: { run: QueryRun; reconnecting: boolean }) {
  const copy = PENDING_COPY[run.status] ?? PENDING_COPY.queued!
  return (
    <div className="run-state running-state">
      <LoaderCircle className="spin" size={21} />
      <div>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
        <span>The result is not ready yet — this page keeps polling.</span>
        {reconnecting && <ReconnectingNotice />}
      </div>
      <AuditFacts run={run} />
    </div>
  )
}

function SuccessState({
  run,
  result,
  problem,
}: {
  run: QueryRun
  result: QueryResult | null
  problem: ResultProblem | null
}) {
  return (
    <>
      <div className="result-summary">
        <div className="run-state success-state">
          <CheckCircle2 size={21} />
          <div>
            <strong>Query succeeded</strong>
            {result === null && problem === null && <span>Reading the result snapshot…</span>}
          </div>
        </div>
        <AuditFacts run={run} />
      </div>
      {problem !== null && <ResultProblemState problem={problem} />}
      {result !== null && (
        <>
          {result.truncated && (
            <div className="truncated-notice">
              <AlertTriangle size={17} />
              <strong>Result truncated</strong>
              <span>Only the configured row limit is shown.</span>
            </div>
          )}
          <div className="table-frame">
            <table>
              <thead>
                <tr>
                  {result.columns.map((column, index) => (
                    <th key={`${column.name}-${index}`}><span>{column.name}</span><small>{column.type}</small></th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {row.map((cell, columnIndex) => (
                      <td key={columnIndex}>
                        {cell === null ? <span className="null-value">NULL</span> : String(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}

function TerminalState({ run }: { run: QueryRun }) {
  if (run.status === 'rejected') {
    return (
      <ErrorState
        kind="rejected"
        title="Query rejected"
        code={run.error_code ?? 'unsupported_sql'}
        message={ERROR_MESSAGES[run.error_code ?? ''] ?? ERROR_MESSAGES.unsupported_sql}
        nextStep="Edit the SQL so it passes the query policy, then run it again."
        run={run}
      />
    )
  }
  if (run.status === 'failed') {
    return (
      <ErrorState
        kind="failed"
        title="Execution failed"
        code={run.error_code ?? 'internal_error'}
        message={ERROR_MESSAGES[run.error_code ?? ''] ?? ERROR_MESSAGES.internal_error}
        nextStep="This query passed the policy check but did not finish. Run it again or adjust the SQL."
        run={run}
      />
    )
  }
  return (
    <div className="run-state cancelled-state">
      <CircleSlash size={21} />
      <div>
        <strong>Query cancelled</strong>
        <span>This run was cancelled before it published a result.</span>
      </div>
      <AuditFacts run={run} />
    </div>
  )
}

const RESULT_PROBLEM_COPY: Record<ResultProblem, { title: string; explanation: string }> = {
  result_expired: {
    title: 'Result no longer retained',
    explanation: 'This query succeeded, but its result snapshot is older than the 24 hour retention window.',
  },
  result_unavailable: {
    title: 'No result to read',
    explanation: 'This query succeeded, but no result snapshot is stored for it any more.',
  },
}

function ResultProblemState({ problem }: { problem: ResultProblem }) {
  const { title, explanation } = RESULT_PROBLEM_COPY[problem]
  return (
    <NoticeState
      className={`notice-state notice-${problem}`}
      icon={problem === 'result_expired' ? Clock : FileWarning}
      title={title}
      code={problem}
    >
      <p>{explanation}</p>
      <p className="notice-next">Run the query again to create a fresh result snapshot.</p>
    </NoticeState>
  )
}

function MissingState({ runId }: { runId: string }) {
  return (
    <NoticeState
      className="notice-state notice-missing"
      icon={FileQuestion}
      title="Query run not available"
      code="query_run_not_found"
    >
      <p>This query run does not exist in the audit store.</p>
      <p className="notice-next">Run the query again to create a new query run.</p>
      <RunIdFact runId={runId} />
    </NoticeState>
  )
}

/**
 * The panel for an outcome that is a fact about the run rather than a failure
 * of it: the run is known, and what is missing or gone is its result.
 */
function NoticeState({
  className,
  icon: Icon,
  title,
  code,
  children,
}: {
  className: string
  icon: typeof Clock
  title: string
  code: string
  children: ReactNode
}) {
  return (
    <div className={className}>
      <div className="notice-heading">
        <Icon size={22} />
        <div><strong>{title}</strong><code>{code}</code></div>
      </div>
      {children}
    </div>
  )
}

function ErrorState({
  kind,
  title,
  code,
  message,
  nextStep,
  run,
}: {
  kind: 'rejected' | 'failed'
  title: string
  code: string
  message: string
  nextStep: string
  run?: QueryRun
}) {
  const Icon = kind === 'rejected' ? AlertTriangle : XCircle
  return (
    <div className={`error-state error-${kind}`}>
      <div className="error-heading">
        <Icon size={22} />
        <div><strong>{title}</strong><code>{code}</code></div>
      </div>
      <p>{message}</p>
      <p className="error-next">{nextStep}</p>
      {run && <AuditFacts run={run} />}
    </div>
  )
}

function ReconnectingNotice() {
  return <span className="reconnecting-notice">Reconnecting</span>
}

function RunIdFact({ runId }: { runId: string }) {
  return (
    <dl className="audit-facts">
      <div><dt>Run ID</dt><dd>{runId}</dd></div>
    </dl>
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
