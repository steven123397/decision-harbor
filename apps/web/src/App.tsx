import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import {
  AlertTriangle,
  ArchiveX,
  Ban,
  CheckCircle2,
  Database,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  TimerOff,
  XCircle,
} from 'lucide-react'

import { httpApi, type ApiClient, type ApiError, type HistoryResponse, type QueryResult, type QueryRun, type QueryRunStatus } from './api'
import './styles.css'

export type { ApiClient, HistoryResponse, QueryResponse, ResultResponse } from './api'

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
  invalid_idempotency_key: 'The idempotency key is not valid.',
  invalid_pagination: 'The pagination parameters are invalid.',
  idempotency_conflict: 'The idempotency key was already used with different input.',
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
  result_expired: 'The query result has expired.',
  query_run_not_found: 'The query run was not found.',
  query_run_not_cancellable: 'This run can no longer be cancelled.',
  query_run_not_retryable: 'This run cannot be retried.',
}

const TERMINAL_STATUSES: ReadonlySet<QueryRunStatus> = new Set([
  'succeeded',
  'failed',
  'rejected',
  'cancelled',
])

const CANCELLABLE_STATUSES: ReadonlySet<QueryRunStatus> = new Set(['queued', 'running'])

// 瞬时可用性错误可恢复，轮询继续；其余错误不可恢复，轮询终止。
const TRANSIENT_ERROR_CODES: ReadonlySet<string> = new Set(['service_not_ready', 'audit_unavailable'])

const STATUS_LABELS: Record<QueryRunStatus, string> = {
  received: 'Received',
  rejected: 'Rejected',
  queued: 'Queued',
  running: 'Running',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelling: 'Cancelling',
  cancelled: 'Cancelled',
}

const STATUS_HINTS: Partial<Record<QueryRunStatus, string>> = {
  queued: 'The query is waiting for an available worker.',
  running: 'The worker is executing the query against the analytics database.',
  cancelling: 'The cancellation request is being applied.',
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString([], { hour12: false })
}

function formatRowCount(count: number): string {
  return `${count} ${count === 1 ? 'row' : 'rows'}`
}

const ACTIVE_RUN_KEY = 'decisionharbor.active-run'

type ResultFeedback = 'expired' | 'unavailable'

type ViewState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'restoring' }
  | { kind: 'active'; run: QueryRun }
  | {
      kind: 'settled'
      run: QueryRun | null
      result: QueryResult | null
      resultFeedback: ResultFeedback | null
      error: ApiError | null
    }

type HistoryState = {
  runs: QueryRun[]
  nextCursor: string | null
  loading: boolean
  loadingMore: boolean
  error: ApiError | null
}

const INITIAL_HISTORY: HistoryState = { runs: [], nextCursor: null, loading: true, loadingMore: false, error: null }

export function App({ api = httpApi, pollIntervalMs = 1000 }: { api?: ApiClient; pollIntervalMs?: number }) {
  const [sql, setSql] = useState(DEFAULT_SQL)
  const [readiness, setReadiness] = useState<'checking' | 'ready' | 'unavailable'>('checking')
  const [view, setView] = useState<ViewState>({ kind: 'idle' })
  const [history, setHistory] = useState<HistoryState>(INITIAL_HISTORY)
  const [actionPending, setActionPending] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const pollTokenRef = useRef(0)
  const historyTokenRef = useRef(0)
  const actionPendingRef = useRef(false)

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

  const applyHistoryResponse = (response: HistoryResponse, append: boolean) => {
    const data = response.data
    if (data) {
      setHistory((current) =>
        append
          ? {
              runs: [...current.runs, ...data.query_runs],
              nextCursor: data.next_cursor,
              loading: false,
              loadingMore: false,
              error: null,
            }
          : {
              runs: data.query_runs,
              nextCursor: data.next_cursor,
              loading: false,
              loadingMore: false,
              error: null,
            },
      )
    } else {
      setHistory((current) => ({ ...current, loading: false, loadingMore: false, error: response.error }))
    }
  }

  const refreshHistory = useCallback(async () => {
    const token = ++historyTokenRef.current
    setHistory((current) => ({ ...current, loading: true, error: null }))
    const response = await api.listHistory()
    if (historyTokenRef.current !== token) return
    applyHistoryResponse(response, false)
  }, [api])

  useEffect(() => {
    void refreshHistory()
  }, [refreshHistory])

  const loadMoreHistory = async () => {
    if (!history.nextCursor || history.loadingMore) return
    const cursor = history.nextCursor
    const token = ++historyTokenRef.current
    setHistory((current) => ({ ...current, loadingMore: true }))
    const response = await api.listHistory(cursor)
    if (historyTokenRef.current !== token) return
    applyHistoryResponse(response, true)
  }

  const finish = async (run: QueryRun, token: number) => {
    sessionStorage.removeItem(ACTIVE_RUN_KEY)
    if (run.status !== 'succeeded') {
      setView({ kind: 'settled', run, result: null, resultFeedback: null, error: null })
      void refreshHistory()
      return
    }
    // 成功事实已确认；结果快照按未就绪（继续轮询）、不可用、已过期分别反馈。
    const response = await api.getQueryResult(run.id)
    if (pollTokenRef.current !== token) return
    const code = response.data ? null : response.error?.code
    if (response.data?.result) {
      setView({ kind: 'settled', run, result: response.data.result, resultFeedback: null, error: null })
    } else if (code === 'result_not_ready' || (code && TRANSIENT_ERROR_CODES.has(code))) {
      void poll(run.id, token)
      return
    } else if (code === 'result_expired') {
      setView({ kind: 'settled', run, result: null, resultFeedback: 'expired', error: null })
    } else if (code === 'result_unavailable') {
      setView({ kind: 'settled', run, result: null, resultFeedback: 'unavailable', error: null })
    } else {
      setView({ kind: 'settled', run, result: null, resultFeedback: null, error: response.error })
    }
    void refreshHistory()
  }

  const poll = async (runId: string, token: number, immediate = false, transientStreak = 0) => {
    // 瞬时错误可恢复故继续轮询，但按指数退避拉长间隔（上限 10 倍），不在不可用服务上打满频率。
    const backoffMs = transientStreak === 0 ? 0 : Math.min(pollIntervalMs * 2 ** transientStreak, pollIntervalMs * 10)
    if (!immediate) await delay(pollIntervalMs + backoffMs)
    if (pollTokenRef.current !== token) return
    const response = await api.getQueryRun(runId)
    if (pollTokenRef.current !== token) return
    const run = response.data?.query_run
    if (!run) {
      const code = response.error?.code
      if (code && TRANSIENT_ERROR_CODES.has(code)) {
        void poll(runId, token, false, transientStreak + 1)
        return
      }
      sessionStorage.removeItem(ACTIVE_RUN_KEY)
      setView({ kind: 'settled', run: null, result: null, resultFeedback: null, error: response.error })
      void refreshHistory()
      return
    }
    if (TERMINAL_STATUSES.has(run.status)) {
      await finish(run, token)
      return
    }
    sessionStorage.setItem(ACTIVE_RUN_KEY, run.id)
    setView({ kind: 'active', run })
    void poll(runId, token)
  }

  const runQuery = async () => {
    if (!sql.trim() || busy || readiness !== 'ready') return
    const token = ++pollTokenRef.current
    setActionError(null)
    setView({ kind: 'submitting' })
    const response = await api.submitQuery(sql)
    if (pollTokenRef.current !== token) return
    if (response.error?.code === 'service_not_ready') setReadiness('unavailable')
    const run = response.data?.query_run
    if (!run) {
      setView({ kind: 'settled', run: null, result: null, resultFeedback: null, error: response.error })
      return
    }
    if (TERMINAL_STATUSES.has(run.status)) {
      await finish(run, token)
      return
    }
    sessionStorage.setItem(ACTIVE_RUN_KEY, run.id)
    setView({ kind: 'active', run })
    void refreshHistory()
    void poll(run.id, token)
  }

  const openRun = (runId: string) => {
    const token = ++pollTokenRef.current
    setActionError(null)
    setView({ kind: 'restoring' })
    void poll(runId, token, true)
  }

  // 提交之外的用户动作互斥执行：同一时刻至多一个取消/重试在途，重复触发被忽略。
  const runExclusiveAction = async (action: () => Promise<void>) => {
    if (actionPendingRef.current) return
    actionPendingRef.current = true
    setActionPending(true)
    setActionError(null)
    try {
      await action()
    } finally {
      actionPendingRef.current = false
      setActionPending(false)
    }
  }

  const cancelRun = (runId: string) =>
    runExclusiveAction(async () => {
      const response = await api.cancelRun(runId)
      const run = response.data?.query_run
      if (run && TERMINAL_STATUSES.has(run.status)) {
        // queued 取消等直接终态：接管轮询令牌并立即收敛。
        const token = ++pollTokenRef.current
        await finish(run, token)
        return
      }
      if (run) {
        setView((current) => (current.kind === 'active' ? { kind: 'active', run } : current))
        return
      }
      if (response.error) setActionError(messageFor(response.error, null))
    })

  const retryRun = (sourceRunId: string) =>
    runExclusiveAction(async () => {
      const response = await api.retryRun(sourceRunId)
      const run = response.data?.query_run
      if (run) {
        const token = ++pollTokenRef.current
        if (TERMINAL_STATUSES.has(run.status)) {
          // 重试 SQL 被策略拒绝：新 rejected 运行作为终态呈现。
          await finish(run, token)
          return
        }
        sessionStorage.setItem(ACTIVE_RUN_KEY, run.id)
        setView({ kind: 'active', run })
        void refreshHistory()
        void poll(run.id, token)
        return
      }
      if (response.error) setActionError(messageFor(response.error, null))
    })

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
            <ActiveState run={view.run} actionError={actionError} onCancel={cancelRun} />
          )}
          {view.kind === 'settled' && (
            <SettledState
              run={view.run}
              result={view.result}
              resultFeedback={view.resultFeedback}
              error={view.error}
              actionError={actionError}
              actionPending={actionPending}
              onRetry={retryRun}
            />
          )}
        </section>

        <HistoryPanel
          history={history}
          onOpen={openRun}
          onLoadMore={loadMoreHistory}
          onRefresh={() => void refreshHistory()}
        />
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

function ActiveState({
  run,
  actionError,
  onCancel,
}: {
  run: QueryRun
  actionError: string | null
  onCancel: (runId: string) => void
}) {
  return (
    <div>
      <ProgressState
        label={STATUS_LABELS[run.status]}
        hint={STATUS_HINTS[run.status] ?? 'The query run is being processed.'}
      />
      <p className="poll-note">The result is not ready yet; polling continues.</p>
      {CANCELLABLE_STATUSES.has(run.status) && (
        <div className="run-actions">
          <button type="button" className="cancel-button" onClick={() => onCancel(run.id)}>
            Cancel run
          </button>
        </div>
      )}
      {actionError && <p className="action-error" role="alert">{actionError}</p>}
    </div>
  )
}

function SettledState({
  run,
  result,
  resultFeedback,
  error,
  actionError,
  actionPending,
  onRetry,
}: {
  run: QueryRun | null
  result: QueryResult | null
  resultFeedback: ResultFeedback | null
  error: ApiError | null
  actionError: string | null
  actionPending: boolean
  onRetry: (runId: string) => void
}) {
  const retryActions = (runId: string) => (
    <>
      <div className="run-actions">
        <button type="button" className="retry-button" disabled={actionPending} onClick={() => onRetry(runId)}>
          Retry run
        </button>
      </div>
      {actionError && <p className="action-error" role="alert">{actionError}</p>}
    </>
  )

  if (run?.status === 'succeeded') {
    if (result) return <SuccessState run={run} result={result} />
    if (resultFeedback === 'expired') {
      return (
        <ErrorState
          kind="expired"
          title="Result expired"
          code="result_expired"
          message="The stored result snapshot passed its retention window and is no longer readable. Run the query again for a fresh result."
          run={run}
        />
      )
    }
    if (resultFeedback === 'unavailable') {
      return (
        <ErrorState
          kind="unavailable"
          title="Result unavailable"
          code="result_unavailable"
          message="The result snapshot is not available for this run. The recorded audit facts remain readable."
          run={run}
        />
      )
    }
    return <ProgressState label="Loading result" hint="Fetching the stored result snapshot." />
  }
  if (run?.status === 'rejected') {
    return (
      <ErrorState
        kind="rejected"
        title="Query rejected"
        code={error?.code ?? run.error_code ?? 'unsupported_sql'}
        message={messageFor(error, run)}
        run={run}
      >
        <p className="next-step">Modify the SQL in the editor and submit it again; policy rejections cannot be retried.</p>
      </ErrorState>
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
      >
        {retryActions(run.id)}
      </ErrorState>
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
      >
        {retryActions(run.id)}
      </ErrorState>
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

function ErrorState({
  kind,
  title,
  code,
  message,
  run,
  children,
}: {
  kind: 'rejected' | 'failed' | 'cancelled' | 'expired' | 'unavailable'
  title: string
  code: string
  message: string
  run?: QueryRun
  children?: ReactNode
}) {
  const iconByKind = {
    rejected: AlertTriangle,
    failed: XCircle,
    cancelled: Ban,
    expired: TimerOff,
    unavailable: ArchiveX,
  } as const
  const Icon = iconByKind[kind]
  return (
    <div className={`error-state error-${kind}`} role="alert">
      <div className="error-heading"><Icon size={22} /><div><strong>{title}</strong><code>{code}</code></div></div>
      <p>{message}</p>
      {run && <AuditFacts run={run} />}
      {children}
    </div>
  )
}

function HistoryPanel({
  history,
  onOpen,
  onLoadMore,
  onRefresh,
}: {
  history: HistoryState
  onOpen: (runId: string) => void
  onLoadMore: () => void
  onRefresh: () => void
}) {
  return (
    <section className="history-panel" aria-label="Run history">
      <div className="history-head">
        <h2>Run history</h2>
        <button
          type="button"
          className="icon-button"
          aria-label="Refresh history"
          title="Refresh history"
          disabled={history.loading}
          onClick={onRefresh}
        >
          <RefreshCw size={14} className={history.loading ? 'spin' : undefined} />
        </button>
      </div>
      {history.error && <p className="action-error" role="alert">{messageFor(history.error, null)}</p>}
      {history.runs.length === 0 && !history.loading && !history.error && (
        <p className="history-empty">No query runs yet.</p>
      )}
      <ul className="history-list">
        {history.runs.map((run) => (
          <li key={run.id}>
            <button type="button" className="history-row" onClick={() => onOpen(run.id)}>
              <span className={`status-chip chip-${run.status}`}>{STATUS_LABELS[run.status]}</span>
              <span className="history-meta">
                <time dateTime={run.created_at}>{formatTime(run.created_at)}</time>
                {run.error_code && <span className="history-error">{run.error_code}</span>}
                {run.returned_row_count !== null && <span>{formatRowCount(run.returned_row_count)}</span>}
              </span>
              <span className="history-id">{run.id.slice(0, 8)}</span>
            </button>
          </li>
        ))}
      </ul>
      {history.nextCursor && (
        <button type="button" className="load-more-button" disabled={history.loadingMore} onClick={onLoadMore}>
          {history.loadingMore ? 'Loading…' : 'Load more'}
        </button>
      )}
    </section>
  )
}

function AuditFacts({ run }: { run: QueryRun }) {
  return (
    <dl className="audit-facts">
      <div><dt>Run ID</dt><dd>{run.id}</dd></div>
      {run.retry_of && <div><dt>Retry of</dt><dd>{run.retry_of}</dd></div>}
      {run.returned_row_count !== null && <div><dt>Rows</dt><dd>{formatRowCount(run.returned_row_count)}</dd></div>}
      {run.duration_ms !== null && <div><dt>Duration</dt><dd>{run.duration_ms} ms</dd></div>}
    </dl>
  )
}

function messageFor(error: ApiError | null, run: QueryRun | null): string {
  const code = error?.code ?? run?.error_code
  return (code && ERROR_MESSAGES[code]) ?? 'The query could not be completed.'
}
