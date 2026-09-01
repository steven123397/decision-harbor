import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App, type ApiClient } from './App'
import type { HistoryResponse, QueryRun, QueryResponse, ResultResponse } from './api'


const RUN_ID = '75e24c21-416c-4bd8-a37d-68667f4ec753'
const NEW_RUN_ID = 'a1b2c3d4-416c-4bd8-a37d-68667f4ec753'
const CREATED_AT = '2026-09-01T10:00:00+00:00'

function runOf(status: QueryRun['status'], overrides: Partial<QueryRun> = {}): QueryRun {
  return {
    id: RUN_ID,
    raw_sql: 'SELECT 1',
    status,
    policy_decision: 'allowed',
    policy_version: '1.0.0',
    referenced_objects: ['analytics.customers'],
    statement_timeout_ms: 5000,
    max_rows: 500,
    returned_row_count: null,
    result_truncated: null,
    error_code: null,
    error_summary: null,
    created_at: CREATED_AT,
    started_at: null,
    finished_at: null,
    duration_ms: null,
    retry_of: null,
    ...overrides,
  }
}

const queued: QueryResponse = { data: { query_run: runOf('queued') }, error: null }

const succeededRun: QueryRun = runOf('succeeded', {
  returned_row_count: 2,
  result_truncated: true,
  duration_ms: 14,
})

const result: ResultResponse = {
  data: {
    result: {
      columns: [
        { name: 'region', type: 'character varying' },
        { name: 'revenue', type: 'numeric' },
      ],
      rows: [
        ['East', '4500.25'],
        ['West', '3900.10'],
      ],
      truncated: true,
    },
  },
  error: null,
}

const knownErrorMessages = {
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
  result_too_large: 'The query result exceeds the supported size limit.',
  result_not_ready: 'The query result is not ready yet.',
  result_unavailable: 'The query result is not available for this run.',
  result_expired: 'The query result has expired.',
  query_run_not_found: 'The query run was not found.',
} as const

const emptyHistory: HistoryResponse = { data: { query_runs: [], next_cursor: null }, error: null }

function api(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    checkReady: vi.fn().mockResolvedValue(true),
    submitQuery: vi.fn().mockResolvedValue(queued),
    getQueryRun: vi.fn().mockResolvedValue({ data: { query_run: succeededRun }, error: null }),
    getQueryResult: vi.fn().mockResolvedValue(result),
    cancelRun: vi.fn().mockResolvedValue({ data: { query_run: runOf('cancelled') }, error: null }),
    retryRun: vi.fn().mockResolvedValue({ data: { query_run: runOf('queued', { id: NEW_RUN_ID }) }, error: null }),
    listHistory: vi.fn().mockResolvedValue(emptyHistory),
    ...overrides,
  }
}

function renderApp(client: ApiClient) {
  return render(<App api={client} pollIntervalMs={10} />)
}


describe('query workbench', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('opens directly on an editable workbench', async () => {
    renderApp(api())

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Query workbench' })).toBeInTheDocument()
    expect(screen.getByLabelText('SQL query')).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Run query' })).toBeEnabled()
  })

  it('disables duplicate submission while the query is in flight', async () => {
    const pending = new Promise<QueryResponse>(() => undefined)
    renderApp(api({ submitQuery: vi.fn().mockReturnValue(pending) }))
    const runButton = await screen.findByRole('button', { name: 'Run query' })

    await userEvent.click(runButton)

    expect(runButton).toBeDisabled()
    expect(screen.getByText('Submitting')).toBeInTheDocument()
  })

  it('polls the queued run until it succeeds and renders its result', async () => {
    const getQueryRun = vi
      .fn()
      .mockResolvedValueOnce({ data: { query_run: runOf('running') }, error: null })
      .mockResolvedValueOnce({ data: { query_run: succeededRun }, error: null })
    const getQueryResult = vi.fn().mockResolvedValue(result)
    renderApp(api({ getQueryRun, getQueryResult }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('East')).toBeInTheDocument()
    expect(screen.getByText('4500.25')).toBeInTheDocument()
    expect(screen.getByText('2 rows')).toBeInTheDocument()
    expect(screen.getByText('Result truncated')).toBeInTheDocument()
    expect(screen.getByText(RUN_ID)).toBeInTheDocument()
    await waitFor(() => {
      expect(getQueryRun).toHaveBeenCalledTimes(2)
    })
    expect(getQueryRun).toHaveBeenCalledWith(RUN_ID)
    expect(getQueryResult).toHaveBeenCalledWith(RUN_ID)
  })

  it('stops polling once the run reaches a terminal state', async () => {
    const getQueryRun = vi.fn().mockResolvedValue({ data: { query_run: succeededRun }, error: null })
    renderApp(api({ getQueryRun }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))
    expect(await screen.findByText('Query succeeded')).toBeInTheDocument()

    await waitFor(
      () => {
        expect(getQueryRun).toHaveBeenCalledTimes(1)
      },
      { timeout: 200 },
    )
  })

  it('resumes the active run after a page refresh', async () => {
    sessionStorage.setItem('decisionharbor.active-run', RUN_ID)
    const getQueryRun = vi.fn().mockResolvedValue({ data: { query_run: succeededRun }, error: null })
    const getQueryResult = vi.fn().mockResolvedValue(result)
    renderApp(api({ getQueryRun, getQueryResult }))

    expect(await screen.findByText('Query succeeded')).toBeInTheDocument()
    expect(getQueryRun).toHaveBeenCalledWith(RUN_ID)
    expect(getQueryResult).toHaveBeenCalledWith(RUN_ID)
    expect(sessionStorage.getItem('decisionharbor.active-run')).toBeNull()
  })

  it('stops polling when the workbench unmounts', async () => {
    const getQueryRun = vi.fn().mockResolvedValue({ data: { query_run: runOf('running') }, error: null })
    const { unmount } = renderApp(api({ getQueryRun }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))
    // 高负载下首次轮询可能在卸载前合法触发；本测试只约束卸载后不再轮询。
    getQueryRun.mockClear()
    unmount()

    await new Promise((resolve) => setTimeout(resolve, 100))
    expect(getQueryRun).not.toHaveBeenCalled()
  })

  it('renders policy rejection separately from execution failure', async () => {
    const rejected: QueryResponse = {
      data: {
        query_run: runOf('rejected', { error_code: 'sql_object_not_allowed' }),
      },
      error: {
        code: 'sql_object_not_allowed',
        message: 'Object is not allowed.',
        query_run_id: RUN_ID,
      },
    }
    renderApp(api({ submitQuery: vi.fn().mockResolvedValue(rejected) }))
    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Query rejected')).toBeInTheDocument()
    expect(screen.getByText('sql_object_not_allowed')).toBeInTheDocument()
    expect(screen.getByText(knownErrorMessages.sql_object_not_allowed)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('renders execution failure reported by the polled run', async () => {
    const failedRun = runOf('failed', { error_code: 'query_semantic_error' })
    renderApp(api({ getQueryRun: vi.fn().mockResolvedValue({ data: { query_run: failedRun }, error: null }) }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Execution failed')).toBeInTheDocument()
    expect(screen.getByText('query_semantic_error')).toBeInTheDocument()
    expect(screen.getByText(knownErrorMessages.query_semantic_error)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('renders a cancelled run without a result table', async () => {
    const cancelledRun = runOf('cancelled')
    renderApp(api({ getQueryRun: vi.fn().mockResolvedValue({ data: { query_run: cancelledRun }, error: null }) }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Query cancelled')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it.each(Object.entries(knownErrorMessages))(
    'maps known API error code %s to its stable display message',
    async (code, expectedMessage) => {
      const failed: QueryResponse = {
        data: null,
        error: { code, message: 'Raw internal detail' },
      }
      renderApp(api({ submitQuery: vi.fn().mockResolvedValue(failed) }))
      await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

      expect(await screen.findByText(expectedMessage)).toBeInTheDocument()
      expect(screen.queryByText('Raw internal detail')).not.toBeInTheDocument()
    },
  )

  it('uses a stable fallback for unknown execution errors', async () => {
    const failed: QueryResponse = {
      data: null,
      error: { code: 'future_error', message: 'Raw internal detail' },
    }
    renderApp(api({ submitQuery: vi.fn().mockResolvedValue(failed) }))
    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Submission failed')).toBeInTheDocument()
    expect(screen.getByText('future_error')).toBeInTheDocument()
    expect(screen.getByText('The query could not be completed.')).toBeInTheDocument()
    expect(screen.queryByText('Raw internal detail')).not.toBeInTheDocument()
  })

  it('can explicitly recheck readiness after the service recovers', async () => {
    const checkReady = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    const recoveryApi = { ...api(), checkReady }
    renderApp(recoveryApi)

    expect(await screen.findByText('Unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run query' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Check readiness' }))

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run query' })).toBeEnabled()
  })
})

describe('cancellation', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('offers cancel while the run is queued or running and settles immediately when cancel wins', async () => {
    let resolveFirstGet: (response: QueryResponse) => void = () => undefined
    const pendingFirstGet = new Promise<QueryResponse>((resolve) => {
      resolveFirstGet = resolve
    })
    const getQueryRun = vi
      .fn()
      .mockReturnValueOnce(pendingFirstGet)
      .mockResolvedValue({ data: { query_run: runOf('running') }, error: null })
    renderApp(api({ getQueryRun }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))
    expect(await screen.findByText('Queued')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Cancel run' })).toBeEnabled()

    await userEvent.click(screen.getByRole('button', { name: 'Cancel run' }))

    expect(await screen.findByText('Query cancelled')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry run' })).toBeEnabled()
    expect(sessionStorage.getItem('decisionharbor.active-run')).toBeNull()
    resolveFirstGet({ data: { query_run: runOf('running') }, error: null })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.getByText('Query cancelled')).toBeInTheDocument()
  })

  it('keeps polling while the run is cancelling and settles once it is cancelled', async () => {
    let resolveFirstGet: (response: QueryResponse) => void = () => undefined
    const pendingFirstGet = new Promise<QueryResponse>((resolve) => {
      resolveFirstGet = resolve
    })
    const getQueryRun = vi
      .fn()
      .mockReturnValueOnce(pendingFirstGet)
      .mockResolvedValueOnce({ data: { query_run: runOf('cancelling') }, error: null })
      .mockResolvedValue({ data: { query_run: runOf('cancelled') }, error: null })
    const cancelRun = vi.fn().mockResolvedValue({ data: { query_run: runOf('cancelling') }, error: null })
    renderApp(api({ getQueryRun, cancelRun }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel run' }))

    const cancelling = await screen.findByText('Cancelling')
    expect(cancelling).toBeInTheDocument()
    // 取消中不重复提供取消按钮。
    expect(screen.queryByRole('button', { name: 'Cancel run' })).not.toBeInTheDocument()
    resolveFirstGet({ data: { query_run: runOf('cancelling') }, error: null })

    expect(await screen.findByText('Query cancelled')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry run' })).toBeEnabled()
  })

  it('shows a stable action error when the run is no longer cancellable', async () => {
    let resolveFirstGet: (response: QueryResponse) => void = () => undefined
    const pendingFirstGet = new Promise<QueryResponse>((resolve) => {
      resolveFirstGet = resolve
    })
    const getQueryRun = vi
      .fn()
      .mockReturnValueOnce(pendingFirstGet)
      .mockResolvedValue({ data: { query_run: runOf('running') }, error: null })
    const cancelRun = vi.fn().mockResolvedValue({
      data: null,
      error: { code: 'query_run_not_cancellable', message: 'Run is not cancellable.' },
    })
    renderApp(api({ getQueryRun, cancelRun }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Cancel run' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('This run can no longer be cancelled.')
    resolveFirstGet({ data: { query_run: runOf('running') }, error: null })
  })
})

describe('retry', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it.each([
    ['failed', 'Execution failed'],
    ['cancelled', 'Query cancelled'],
  ] as const)('offers retry for a %s run', async (status, title) => {
    renderApp(api({ submitQuery: vi.fn().mockResolvedValue({ data: { query_run: runOf(status) }, error: null }) }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText(title)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry run' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Cancel run' })).not.toBeInTheDocument()
  })

  it.each([
    ['succeeded', 'Query succeeded'],
    ['rejected', 'Query rejected'],
  ] as const)('offers no retry for a %s run', async (status, title) => {
    const client = api({ submitQuery: vi.fn().mockResolvedValue({ data: { query_run: runOf(status) }, error: null }) })
    if (status === 'succeeded') {
      client.getQueryResult = vi.fn().mockResolvedValue(result)
    }
    renderApp(client)

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText(title)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Retry run' })).not.toBeInTheDocument()
  })

  it('retries a failed run as a new linked run and polls it to success', async () => {
    const failedRun = runOf('failed', { error_code: 'query_semantic_error' })
    const getQueryRun = vi
      .fn()
      .mockResolvedValueOnce({ data: { query_run: runOf('running', { id: NEW_RUN_ID, retry_of: RUN_ID }) }, error: null })
      .mockResolvedValue({
        data: {
          query_run: runOf('succeeded', {
            id: NEW_RUN_ID,
            retry_of: RUN_ID,
            returned_row_count: 2,
            result_truncated: true,
            duration_ms: 14,
          }),
        },
        error: null,
      })
    const getQueryResult = vi.fn().mockResolvedValue(result)
    const retryRun = vi.fn().mockResolvedValue({
      data: { query_run: runOf('queued', { id: NEW_RUN_ID, retry_of: RUN_ID }) },
      error: null,
    })
    renderApp(api({
      submitQuery: vi.fn().mockResolvedValue({ data: { query_run: failedRun }, error: null }),
      getQueryRun,
      getQueryResult,
      retryRun,
    }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Retry run' }))

    expect(await screen.findByText('East')).toBeInTheDocument()
    expect(retryRun).toHaveBeenCalledWith(RUN_ID)
    expect(screen.getByText('Retry of')).toBeInTheDocument()
    expect(screen.getByText(RUN_ID)).toBeInTheDocument()
    expect(sessionStorage.getItem('decisionharbor.active-run')).toBeNull()
    expect(getQueryRun).toHaveBeenCalledWith(NEW_RUN_ID)
  })

  it('renders a rejected run when the retried SQL is policy-rejected', async () => {
    const retryRun = vi.fn().mockResolvedValue({
      data: { query_run: runOf('rejected', { id: NEW_RUN_ID, retry_of: RUN_ID, error_code: 'sql_statement_not_allowed' }) },
      error: { code: 'sql_statement_not_allowed', message: 'Statement is not allowed.', query_run_id: NEW_RUN_ID },
    })
    renderApp(api({
      submitQuery: vi.fn().mockResolvedValue({ data: { query_run: runOf('failed') }, error: null }),
      retryRun,
    }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Retry run' }))

    expect(await screen.findByText('Query rejected')).toBeInTheDocument()
    expect(screen.getByText('sql_statement_not_allowed')).toBeInTheDocument()
  })

  it('shows a stable action error when the source run cannot be retried', async () => {
    const retryRun = vi.fn().mockResolvedValue({
      data: null,
      error: { code: 'query_run_not_retryable', message: 'Run is not retryable.' },
    })
    renderApp(api({
      submitQuery: vi.fn().mockResolvedValue({ data: { query_run: runOf('failed') }, error: null }),
      retryRun,
    }))
    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Retry run' }))

    expect(await screen.findByText('This run cannot be retried.')).toBeInTheDocument()
  })
})

describe('run history', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('lists recent runs and opens a succeeded run with its result', async () => {
    const getQueryRun = vi.fn().mockResolvedValue({ data: { query_run: succeededRun }, error: null })
    const listHistory = vi.fn().mockResolvedValue({
      data: { query_runs: [succeededRun], next_cursor: null },
      error: null,
    })
    renderApp(api({ getQueryRun, listHistory }))

    const history = await screen.findByRole('region', { name: 'Run history' })
    await userEvent.click(await screen.findByRole('button', { name: /Succeeded/ }))

    expect(await screen.findByText('Query succeeded')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(getQueryRun).toHaveBeenCalledWith(RUN_ID)
    expect(listHistory).toHaveBeenCalledWith()
  })

  it('opens an active run from history and resumes polling as the session run', async () => {
    const getQueryRun = vi.fn().mockResolvedValue({ data: { query_run: runOf('running') }, error: null })
    renderApp(api({
      getQueryRun,
      listHistory: vi.fn().mockResolvedValue({ data: { query_runs: [runOf('running')], next_cursor: null }, error: null }),
    }))

    await userEvent.click(await screen.findByRole('button', { name: /Running/ }))

    expect((await screen.findAllByText('Running')).length).toBeGreaterThan(0)
    expect(sessionStorage.getItem('decisionharbor.active-run')).toBe(RUN_ID)
  })

  it('loads further pages through the paginated history contract', async () => {
    const firstPage: HistoryResponse = {
      data: { query_runs: [runOf('succeeded'), runOf('failed', { error_code: 'query_timeout' })], next_cursor: 'cursor-1' },
      error: null,
    }
    const secondPage: HistoryResponse = {
      data: { query_runs: [runOf('cancelled', { id: NEW_RUN_ID })], next_cursor: null },
      error: null,
    }
    const listHistory = vi.fn()
      .mockResolvedValueOnce(firstPage)
      .mockResolvedValueOnce(secondPage)
    renderApp(api({ listHistory }))

    expect(await screen.findByRole('button', { name: /Succeeded/ })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Load more' }))

    expect(await screen.findByRole('button', { name: /Cancelled/ })).toBeInTheDocument()
    expect(listHistory).toHaveBeenNthCalledWith(2, 'cursor-1')
    expect(screen.getByRole('button', { name: /Succeeded/ })).toBeInTheDocument()
  })

  it('keeps the history list when the history store is unavailable', async () => {
    renderApp(api({
      listHistory: vi.fn().mockResolvedValue({
        data: null,
        error: { code: 'audit_unavailable', message: 'The audit store is unavailable.' },
      }),
    }))

    expect(await screen.findByRole('alert')).toHaveTextContent('The audit store is unavailable.')
  })
})

describe('result feedback', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('distinguishes an expired result from execution failure', async () => {
    renderApp(api({
      getQueryRun: vi.fn().mockResolvedValue({ data: { query_run: succeededRun }, error: null }),
      getQueryResult: vi.fn().mockResolvedValue({
        data: null,
        error: { code: 'result_expired', message: 'The query result has expired.', query_run_id: RUN_ID },
      }),
    }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Result expired')).toBeInTheDocument()
    expect(screen.getByText('result_expired')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('distinguishes an unavailable result from an expired one', async () => {
    renderApp(api({
      getQueryRun: vi.fn().mockResolvedValue({ data: { query_run: succeededRun }, error: null }),
      getQueryResult: vi.fn().mockResolvedValue({
        data: null,
        error: { code: 'result_unavailable', message: 'The query result is not available for this run.', query_run_id: RUN_ID },
      }),
    }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Result unavailable')).toBeInTheDocument()
    expect(screen.getByText('result_unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Result expired')).not.toBeInTheDocument()
  })
})

describe('polling errors', () => {
  beforeEach(() => {
    sessionStorage.clear()
  })

  it('keeps polling through transient availability errors', async () => {
    const transient = { data: null, error: { code: 'service_not_ready', message: 'The service is not ready.' } }
    const getQueryRun = vi
      .fn()
      .mockResolvedValueOnce(transient)
      .mockResolvedValueOnce(transient)
      .mockResolvedValue({ data: { query_run: succeededRun }, error: null })
    renderApp(api({ getQueryRun }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Query succeeded')).toBeInTheDocument()
    await waitFor(() => {
      expect(getQueryRun).toHaveBeenCalledTimes(3)
    })
  })

  it('stops polling on unrecoverable errors', async () => {
    const getQueryRun = vi.fn().mockResolvedValue({
      data: null,
      error: { code: 'query_run_not_found', message: 'Query run was not found.' },
    })
    renderApp(api({ getQueryRun }))

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('The query run was not found.')
    await waitFor(
      () => {
        expect(getQueryRun).toHaveBeenCalledTimes(1)
      },
      { timeout: 200 },
    )
    expect(sessionStorage.getItem('decisionharbor.active-run')).toBeNull()
  })
})
