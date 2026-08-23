import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App, type ApiClient } from './App'
import type { QueryRun, QueryResponse, ResultResponse } from './api'


const RUN_ID = '75e24c21-416c-4bd8-a37d-68667f4ec753'

function runOf(status: QueryRun['status'], overrides: Partial<QueryRun> = {}): QueryRun {
  return {
    id: RUN_ID,
    status,
    returned_row_count: null,
    result_truncated: null,
    duration_ms: null,
    error_code: null,
    error_summary: null,
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
  query_run_not_found: 'The query run was not found.',
} as const


function api(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    checkReady: vi.fn().mockResolvedValue(true),
    submitQuery: vi.fn().mockResolvedValue(queued),
    getQueryRun: vi.fn().mockResolvedValue({ data: { query_run: succeededRun }, error: null }),
    getQueryResult: vi.fn().mockResolvedValue(result),
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
