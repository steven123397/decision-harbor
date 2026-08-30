import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { App, type ApiClient, type RunResponse } from './App'
import type { QueryResult, QueryRun } from './api'


const RUN_ID = '75e24c21-416c-4bd8-a37d-68667f4ec753'
const RUN_SQL = 'SELECT region FROM customers'

const RESULT: QueryResult = {
  columns: [
    { name: 'region', type: 'character varying' },
    { name: 'revenue', type: 'numeric' },
  ],
  rows: [
    ['East', '4500.25'],
    ['West', '3900.10'],
  ],
  truncated: true,
}

const queued: QueryRun = {
  id: RUN_ID,
  raw_sql: RUN_SQL,
  status: 'queued',
  returned_row_count: null,
  result_truncated: null,
  duration_ms: null,
  error_code: null,
  error_summary: null,
}

const succeeded: QueryRun = {
  ...queued,
  status: 'succeeded',
  returned_row_count: 2,
  result_truncated: true,
  duration_ms: 14,
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
  query_run_not_found: 'The query run was not found.',
  execution_interrupted: 'The query execution was interrupted before completion.',
  network_error: 'The service could not be reached.',
} as const


function api(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    checkReady: vi.fn().mockResolvedValue(true),
    runQuery: vi.fn().mockResolvedValue({ data: { query_run: queued }, error: null }),
    getRun: vi.fn().mockResolvedValue({ data: { query_run: succeeded }, error: null }),
    getResult: vi.fn().mockResolvedValue({ data: { result: RESULT }, error: null }),
    ...overrides,
  }
}


describe('query workbench', () => {
  it('opens directly on an editable workbench', async () => {
    render(<App api={api()} />)

    expect(await screen.findByText('Ready')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Query workbench' })).toBeInTheDocument()
    expect(screen.getByLabelText('SQL query')).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Run query' })).toBeEnabled()
  })

  it('disables duplicate submission while the query is being submitted', async () => {
    const pending = new Promise<RunResponse>(() => undefined)
    render(<App api={api({ runQuery: vi.fn().mockReturnValue(pending) })} />)
    const runButton = await screen.findByRole('button', { name: 'Run query' })

    await userEvent.click(runButton)

    expect(runButton).toBeDisabled()
    expect(screen.getByText('Submitting')).toBeInTheDocument()
  })

  it('shows the queued run while the worker owns execution', async () => {
    const getRun = vi.fn().mockReturnValue(new Promise<RunResponse>(() => undefined))
    render(<App api={api({ getRun })} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Queued')).toBeInTheDocument()
    expect(screen.getByText(RUN_ID)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('renders the result table, audit facts, and truncation warning', async () => {
    render(<App api={api()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('East')).toBeInTheDocument()
    expect(screen.getByText('4500.25')).toBeInTheDocument()
    expect(screen.getByText('2 rows')).toBeInTheDocument()
    expect(screen.getByText('14 ms')).toBeInTheDocument()
    expect(screen.getByText('Result truncated')).toBeInTheDocument()
    expect(screen.getByText(RUN_ID)).toBeInTheDocument()
  })

  it('renders policy rejection separately from execution failure', async () => {
    const rejected: RunResponse = {
      data: {
        query_run: {
          ...queued,
          status: 'rejected',
          error_code: 'sql_object_not_allowed',
          error_summary: 'Object is not allowed.',
        },
      },
      error: {
        code: 'sql_object_not_allowed',
        message: 'Object is not allowed.',
        query_run_id: RUN_ID,
      },
    }
    render(<App api={api({ runQuery: vi.fn().mockResolvedValue(rejected) })} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Query rejected')).toBeInTheDocument()
    expect(screen.getByText('sql_object_not_allowed')).toBeInTheDocument()
    expect(screen.getByText(knownErrorMessages.sql_object_not_allowed)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it.each(Object.entries(knownErrorMessages))(
    'maps known API error code %s to its stable display message',
    async (code, expectedMessage) => {
      const failed: RunResponse = {
        data: null,
        error: { code, message: 'Raw internal detail' },
      }
      render(<App api={api({ runQuery: vi.fn().mockResolvedValue(failed) })} />)
      await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

      expect(await screen.findByText(expectedMessage)).toBeInTheDocument()
      expect(screen.queryByText('Raw internal detail')).not.toBeInTheDocument()
    },
  )

  it('reports a submission that produced no run separately from a failed run', async () => {
    const failed: RunResponse = {
      data: null,
      error: { code: 'future_error', message: 'Raw internal detail' },
    }
    render(<App api={api({ runQuery: vi.fn().mockResolvedValue(failed) })} />)
    await userEvent.click(await screen.findByRole('button', { name: 'Run query' }))

    expect(await screen.findByText('Query not started')).toBeInTheDocument()
    expect(screen.queryByText('Execution failed')).not.toBeInTheDocument()
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
