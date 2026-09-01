import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App, type ApiClient, type QueryResponse } from './App'


const succeeded: QueryResponse = {
  data: {
    query_run: {
      id: '75e24c21-416c-4bd8-a37d-68667f4ec753',
      status: 'succeeded',
      returned_row_count: 2,
      result_truncated: true,
      duration_ms: 14,
      error_code: null,
      error_summary: null,
    },
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

const queued: QueryResponse = {
  data: {
    query_run: {
      ...succeeded.data!.query_run,
      status: 'queued',
      returned_row_count: null,
      result_truncated: null,
      duration_ms: null,
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
  result_too_large: 'The query result is too large to store.',
  unsupported_result_type: 'The query returned a result type that is not supported.',
  query_run_not_found: 'The query run was not found.',
  execution_interrupted: 'The query execution was interrupted before completion.',
  execution_attempts_exhausted: 'Automatic execution attempts were exhausted.',
  result_not_ready: 'The query result is not ready yet.',
  result_unavailable: 'This query run has no readable result.',
} as const


function api(
  runQuery = vi.fn().mockResolvedValue(succeeded),
  getQueryRun = vi.fn().mockResolvedValue(succeeded),
): ApiClient {
  return {
    checkReady: vi.fn().mockResolvedValue(true),
    runQuery,
    getQueryRun,
    getQueryResult: vi.fn().mockResolvedValue({
      data: { result: succeeded.data!.result },
      error: null,
    }),
  }
}


describe('query workbench', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('opens directly on an editable workbench', async () => {
    render(<App api={api()} />)

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Query workbench' })).toBeInTheDocument()
    expect(screen.getByLabelText('SQL query')).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Run query' })).toBeEnabled()
  })

  it('disables duplicate submission while the query is running', async () => {
    const pending = new Promise<QueryResponse>(() => undefined)
    render(<App api={api(vi.fn().mockReturnValue(pending))} />)
    const runButton = await screen.findByRole('button', { name: 'Run query' })

    await userEvent.click(runButton)

    expect(runButton).toBeDisabled()
    expect(screen.getByText('Submitting')).toBeInTheDocument()
  })

  it('polls an accepted run and reads its persisted result after success', async () => {
    const getQueryRun = vi.fn().mockResolvedValueOnce(queued).mockResolvedValueOnce(succeeded)
    const pollingApi = api(vi.fn().mockResolvedValue(queued), getQueryRun)
    render(<App api={pollingApi} pollIntervalMs={20} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Queued')).toBeInTheDocument()
    expect(await screen.findByText('Query succeeded')).toBeInTheDocument()
    expect(pollingApi.getQueryRun).toHaveBeenCalledTimes(2)
    expect(pollingApi.getQueryResult).toHaveBeenCalledTimes(1)
  })

  it('restores the current run after a page refresh', async () => {
    localStorage.setItem('decisionharbor.current-query-run', succeeded.data!.query_run.id)
    const restoredApi = api()

    render(<App api={restoredApi} pollIntervalMs={0} />)

    expect(await screen.findByText('Query succeeded')).toBeInTheDocument()
    expect(screen.getByText('East')).toBeInTheDocument()
    expect(restoredApi.runQuery).not.toHaveBeenCalled()
    expect(restoredApi.getQueryRun).toHaveBeenCalledWith(succeeded.data!.query_run.id)
    expect(restoredApi.getQueryResult).toHaveBeenCalledWith(succeeded.data!.query_run.id)
  })

  it('does not overlap polling and stops after unmount', async () => {
    let resolvePoll: ((response: QueryResponse) => void) | undefined
    const pendingPoll = new Promise<QueryResponse>((resolve) => {
      resolvePoll = resolve
    })
    const getQueryRun = vi.fn().mockReturnValue(pendingPoll)
    const pollingApi = api(vi.fn().mockResolvedValue(queued), getQueryRun)
    localStorage.setItem('decisionharbor.current-query-run', queued.data!.query_run.id)

    const { unmount } = render(<App api={pollingApi} pollIntervalMs={0} />)
    await waitFor(() => expect(getQueryRun).toHaveBeenCalledTimes(1))
    expect(getQueryRun).toHaveBeenCalledWith(queued.data!.query_run.id)

    unmount()
    resolvePoll?.(queued)
    await Promise.resolve()
    expect(getQueryRun).toHaveBeenCalledTimes(1)
  })

  it('stops without persisting a received run when submission cannot be completed', async () => {
    const receivedFailure: QueryResponse = {
      data: {
        query_run: {
          ...queued.data!.query_run,
          status: 'received',
        },
      },
      error: {
        code: 'audit_unavailable',
        message: 'Raw internal detail',
      },
    }
    const getQueryRun = vi.fn()
    render(<App api={api(vi.fn().mockResolvedValue(receivedFailure), getQueryRun)} pollIntervalMs={0} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText(knownErrorMessages.audit_unavailable)).toBeInTheDocument()
    expect(getQueryRun).not.toHaveBeenCalled()
    expect(localStorage.getItem('decisionharbor.current-query-run')).toBeNull()
  })

  it('renders the result table, audit facts, and truncation warning', async () => {
    render(<App api={api()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('East')).toBeInTheDocument()
    expect(screen.getByText('4500.25')).toBeInTheDocument()
    expect(screen.getByText('2 rows')).toBeInTheDocument()
    expect(screen.getByText('Result truncated')).toBeInTheDocument()
    expect(screen.getByText('75e24c21-416c-4bd8-a37d-68667f4ec753')).toBeInTheDocument()
  })

  it('renders policy rejection separately from execution failure', async () => {
    const rejected: QueryResponse = {
      data: {
        query_run: {
          ...succeeded.data!.query_run,
          status: 'rejected',
          returned_row_count: null,
          result_truncated: null,
          error_code: 'sql_object_not_allowed',
          error_summary: 'Object is not allowed.',
        },
      },
      error: {
        code: 'sql_object_not_allowed',
        message: 'Object is not allowed.',
        query_run_id: '75e24c21-416c-4bd8-a37d-68667f4ec753',
      },
    }
    render(<App api={api(vi.fn().mockResolvedValue(rejected))} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Query rejected')).toBeInTheDocument()
    expect(screen.getByText('sql_object_not_allowed')).toBeInTheDocument()
    expect(screen.getByText(knownErrorMessages.sql_object_not_allowed)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('distinguishes an expired persisted result from an unavailable result', async () => {
    const expiredApi = api(vi.fn().mockResolvedValue(queued))
    expiredApi.getQueryResult = vi.fn().mockResolvedValue({
      data: null,
      error: { code: 'result_expired', message: 'Internal retention detail.' },
    })
    render(<App api={expiredApi} />)

    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Result expired')).toBeInTheDocument()
    expect(screen.getByText('This result expired after the retention window. Run the query again to refresh it.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it.each(Object.entries(knownErrorMessages))(
    'maps known API error code %s to its stable display message',
    async (code, expectedMessage) => {
      const failed: QueryResponse = {
        data: null,
        error: { code, message: 'Raw internal detail' },
      }
      render(<App api={api(vi.fn().mockResolvedValue(failed))} />)
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
    render(<App api={api(vi.fn().mockResolvedValue(failed))} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Execution failed')).toBeInTheDocument()
    expect(screen.getByText('future_error')).toBeInTheDocument()
    expect(screen.getByText('The query could not be completed.')).toBeInTheDocument()
    expect(screen.queryByText('Raw internal detail')).not.toBeInTheDocument()
  })

  it('can explicitly recheck readiness after the service recovers', async () => {
    const checkReady = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    const recoveryApi = { ...api(), checkReady }
    render(<App api={recoveryApi} />)

    expect(await screen.findByText('Unavailable')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run query' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Check readiness' }))

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run query' })).toBeEnabled()
  })
})
