import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

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


function api(runQuery = vi.fn().mockResolvedValue(succeeded)): ApiClient {
  return {
    checkReady: vi.fn().mockResolvedValue(true),
    runQuery,
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

  it('disables duplicate submission while the query is running', async () => {
    const pending = new Promise<QueryResponse>(() => undefined)
    render(<App api={api(vi.fn().mockReturnValue(pending))} />)
    const runButton = await screen.findByRole('button', { name: 'Run query' })

    await userEvent.click(runButton)

    expect(runButton).toBeDisabled()
    expect(screen.getByText('Running')).toBeInTheDocument()
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
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

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
