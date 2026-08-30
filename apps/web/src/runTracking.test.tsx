import { fireEvent, render, screen } from '@testing-library/react'
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App, type ApiClient } from './App'
import { POLL_INTERVAL_MS } from './runTracking'
import type { QueryResult, QueryRun, RunResponse } from './api'

const RUN_ID = '75e24c21-416c-4bd8-a37d-68667f4ec753'
const RUN_SQL = 'SELECT region, count(*) AS orders FROM customers GROUP BY region'

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

function run(overrides: Partial<QueryRun> = {}): QueryRun {
  return {
    id: RUN_ID,
    raw_sql: RUN_SQL,
    status: 'queued',
    returned_row_count: null,
    result_truncated: null,
    duration_ms: null,
    error_code: null,
    error_summary: null,
    ...overrides,
  }
}

function runEnvelope(value: QueryRun): RunResponse {
  return { data: { query_run: value }, error: null }
}

function failure(code: string): RunResponse {
  return { data: null, error: { code, message: 'Raw internal detail' } }
}

function ok<T>(data: T): { data: T; error: null } {
  return { data, error: null }
}

function api(overrides: Partial<ApiClient> = {}): ApiClient {
  return {
    checkReady: vi.fn().mockResolvedValue(true),
    runQuery: vi.fn().mockResolvedValue(ok({ query_run: run() })),
    getRun: vi
      .fn()
      .mockResolvedValue(
        runEnvelope(run({ status: 'succeeded', returned_row_count: 2, result_truncated: true, duration_ms: 14 })),
      ),
    getResult: vi.fn().mockResolvedValue(ok({ result: RESULT })),
    ...overrides,
  }
}

/** A response that never settles, so a poll stays in flight. */
function parked(): Promise<RunResponse> {
  return new Promise<RunResponse>(() => undefined)
}

describe('workbench polling and results', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    window.history.replaceState({}, '', '/')
  })

  afterEach(() => {
    vi.useRealTimers()
    window.history.replaceState({}, '', '/')
  })

  // The poll settles outside React's event handlers, so the flush has to run
  // inside act: otherwise the update waits for a scheduler task that fake
  // timers never deliver.
  async function advance(ms = 0) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms)
    })
  }

  // The first flush lets readiness settle, which is what enables the button.
  async function submit() {
    await advance(0)
    fireEvent.click(screen.getByRole('button', { name: 'Run query' }))
    await advance(0)
  }

  it('follows a run from queued through running to a successful result', async () => {
    const getRun = vi
      .fn<() => Promise<RunResponse>>()
      .mockResolvedValueOnce(runEnvelope(run({ status: 'queued' })))
      .mockResolvedValueOnce(runEnvelope(run({ status: 'running' })))
      .mockResolvedValueOnce(
        runEnvelope(run({ status: 'succeeded', returned_row_count: 2, result_truncated: true, duration_ms: 14 })),
      )
    render(<App api={api({ getRun })} />)
    await submit()

    expect(screen.getByText('Queued')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    await advance(POLL_INTERVAL_MS)
    expect(screen.getByText('Running')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    await advance(POLL_INTERVAL_MS)
    expect(screen.getByText('Query succeeded')).toBeInTheDocument()
    expect(screen.getByText('East')).toBeInTheDocument()
    expect(screen.getByText('4500.25')).toBeInTheDocument()
    expect(screen.getByText('2 rows')).toBeInTheDocument()
    expect(screen.getByText('14 ms')).toBeInTheDocument()
    expect(screen.getByText('Result truncated')).toBeInTheDocument()
    expect(screen.getByText(RUN_ID)).toBeInTheDocument()
    expect(getRun).toHaveBeenCalledTimes(3)
  })

  it('keeps waiting when the result is not ready instead of showing a failure', async () => {
    const getResult = vi
      .fn()
      .mockResolvedValueOnce({ data: null, error: { code: 'result_not_ready', message: 'Not ready.' } })
      .mockResolvedValueOnce(ok({ result: RESULT }))
    render(<App api={api({ getResult })} />)
    await submit()

    expect(screen.getByText('Query succeeded')).toBeInTheDocument()
    expect(screen.getByText(/Reading the result snapshot/)).toBeInTheDocument()
    expect(screen.queryByText('Execution failed')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    await advance(POLL_INTERVAL_MS)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('East')).toBeInTheDocument()
  })

  it('says the result is not ready while the run is still queued', async () => {
    render(<App api={api({ getRun: vi.fn().mockReturnValue(parked()) })} />)
    await submit()

    expect(screen.getByText('Queued')).toBeInTheDocument()
    expect(screen.getByText(/The result is not ready yet/)).toBeInTheDocument()
    expect(screen.queryByText('Execution failed')).not.toBeInTheDocument()
  })

  it('keeps polling after a transient failure and says it is reconnecting', async () => {
    const getRun = vi
      .fn<() => Promise<RunResponse>>()
      .mockResolvedValueOnce(failure('internal_error'))
      .mockResolvedValueOnce(
        runEnvelope(run({ status: 'succeeded', returned_row_count: 2, result_truncated: false, duration_ms: 9 })),
      )
    render(<App api={api({ getRun })} />)
    await submit()

    expect(screen.getByText('Queued')).toBeInTheDocument()
    expect(screen.getByText('Reconnecting')).toBeInTheDocument()

    await advance(POLL_INTERVAL_MS)
    expect(screen.getByText('Query succeeded')).toBeInTheDocument()
    expect(screen.getByText('2 rows')).toBeInTheDocument()
  })

  it('keeps the last known run on screen when a later poll fails', async () => {
    const getRun = vi
      .fn<() => Promise<RunResponse>>()
      .mockResolvedValueOnce(
        runEnvelope(run({ status: 'succeeded', returned_row_count: 2, result_truncated: false, duration_ms: 9 })),
      )
      .mockResolvedValueOnce(failure('audit_unavailable'))
      .mockResolvedValue(
        runEnvelope(run({ status: 'succeeded', returned_row_count: 2, result_truncated: false, duration_ms: 9 })),
      )
    const getResult = vi
      .fn()
      .mockResolvedValueOnce({ data: null, error: { code: 'result_not_ready', message: 'Not ready.' } })
      .mockResolvedValueOnce(ok({ result: RESULT }))
    render(<App api={api({ getRun, getResult })} />)
    await submit()
    expect(screen.getByText('Query succeeded')).toBeInTheDocument()

    await advance(POLL_INTERVAL_MS)
    expect(screen.getByText('Query succeeded')).toBeInTheDocument()
    expect(screen.queryByText('Restoring run')).not.toBeInTheDocument()

    await advance(POLL_INTERVAL_MS)
    expect(screen.getByRole('table')).toBeInTheDocument()
  })

  it('restores the same run, its SQL and its result from the run identifier in the address', async () => {
    window.history.replaceState({}, '', `/?run=${RUN_ID}`)
    const runQuery = vi.fn()
    render(<App api={api({ runQuery })} />)
    await advance(0)

    expect(screen.getByLabelText('SQL query')).toHaveValue(RUN_SQL)
    expect(screen.getByText('Query succeeded')).toBeInTheDocument()
    expect(screen.getByText('East')).toBeInTheDocument()
    expect(screen.getByText(RUN_ID)).toBeInTheDocument()
    expect(runQuery).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Run query' })).toBeEnabled()
  })

  it('shows a policy rejection as its own outcome with a next step', async () => {
    const runQuery = vi.fn().mockResolvedValue({
      data: {
        query_run: run({
          status: 'rejected',
          error_code: 'sql_object_not_allowed',
          error_summary: 'Object is not allowed.',
        }),
      },
      error: { code: 'sql_object_not_allowed', message: 'Object is not allowed.', query_run_id: RUN_ID },
    })
    render(<App api={api({ runQuery })} />)
    await submit()

    expect(screen.getByText('Query rejected')).toBeInTheDocument()
    expect(screen.getByText('sql_object_not_allowed')).toBeInTheDocument()
    expect(screen.getByText('Query rejected').closest('.error-state')).toHaveClass('error-rejected')
    expect(screen.getByText(/Edit the SQL/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows an execution failure as a different outcome from a policy rejection', async () => {
    const getRun = vi
      .fn()
      .mockResolvedValue(
        runEnvelope(run({ status: 'failed', error_code: 'query_timeout', error_summary: 'Timed out.' })),
      )
    render(<App api={api({ getRun })} />)
    await submit()

    expect(screen.getByText('Execution failed')).toBeInTheDocument()
    expect(screen.getByText('query_timeout')).toBeInTheDocument()
    expect(screen.getByText('Execution failed').closest('.error-state')).toHaveClass('error-failed')
    expect(screen.getByText(/passed the policy check/)).toBeInTheDocument()
    expect(screen.queryByText('Query rejected')).not.toBeInTheDocument()
  })

  it('explains an expired result and what to do next', async () => {
    const getResult = vi
      .fn()
      .mockResolvedValue({ data: null, error: { code: 'result_expired', message: 'Expired.' } })
    render(<App api={api({ getResult })} />)
    await submit()

    expect(screen.getByText('Query succeeded')).toBeInTheDocument()
    expect(screen.getByText('Result no longer retained')).toBeInTheDocument()
    expect(screen.getByText('result_expired')).toBeInTheDocument()
    expect(screen.getByText(/Run the query again/)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('explains an unavailable result differently from an expired one', async () => {
    const getResult = vi
      .fn()
      .mockResolvedValue({ data: null, error: { code: 'result_unavailable', message: 'No result.' } })
    render(<App api={api({ getResult })} />)
    await submit()

    expect(screen.getByText('No result to read')).toBeInTheDocument()
    expect(screen.getByText('result_unavailable')).toBeInTheDocument()
    expect(screen.queryByText('Result no longer retained')).not.toBeInTheDocument()
    expect(screen.getByText(/Run the query again/)).toBeInTheDocument()
  })

  it('stops polling once the run reaches a terminal state', async () => {
    const getRun = vi
      .fn()
      .mockResolvedValue(
        runEnvelope(run({ status: 'succeeded', returned_row_count: 2, result_truncated: false, duration_ms: 9 })),
      )
    render(<App api={api({ getRun })} />)
    await submit()
    expect(screen.getByText('Query succeeded')).toBeInTheDocument()

    await advance(POLL_INTERVAL_MS * 10)

    expect(getRun).toHaveBeenCalledTimes(1)
  })

  it('stops polling after the page unloads', async () => {
    const getRun = vi.fn().mockResolvedValue(runEnvelope(run({ status: 'queued' })))
    const view = render(<App api={api({ getRun })} />)
    await submit()
    expect(getRun).toHaveBeenCalledTimes(1)

    view.unmount()
    await advance(POLL_INTERVAL_MS * 5)

    expect(getRun).toHaveBeenCalledTimes(1)
  })

  it('stops polling when the run cannot be read back at all', async () => {
    const getRun = vi.fn().mockResolvedValue(failure('query_run_not_found'))
    render(<App api={api({ getRun })} />)
    await submit()

    expect(screen.getByText('Query run not available')).toBeInTheDocument()
    expect(screen.getByText('query_run_not_found')).toBeInTheDocument()

    await advance(POLL_INTERVAL_MS * 5)

    expect(getRun).toHaveBeenCalledTimes(1)
  })

  it('stops polling when the result read reports the run is gone', async () => {
    const getResult = vi.fn().mockResolvedValue(failure('query_run_not_found'))
    render(<App api={api({ getResult })} />)
    await submit()

    expect(screen.getByText('Query run not available')).toBeInTheDocument()
    expect(screen.getByText('query_run_not_found')).toBeInTheDocument()
    expect(screen.queryByText('Query succeeded')).not.toBeInTheDocument()

    await advance(POLL_INTERVAL_MS * 5)

    expect(getResult).toHaveBeenCalledTimes(1)
  })

  it('never sends overlapping polls for the same query run', async () => {
    const getRun = vi.fn().mockReturnValue(parked())
    render(<App api={api({ getRun })} />)
    await submit()
    expect(getRun).toHaveBeenCalledTimes(1)

    await advance(POLL_INTERVAL_MS * 5)

    expect(getRun).toHaveBeenCalledTimes(1)
    expect(getRun.mock.calls.every((call) => call[0] === RUN_ID)).toBe(true)
  })

  it('polls the newest run after a second submission', async () => {
    const secondRunId = '11111111-2222-4333-8444-555555555555'
    const getRun = vi.fn().mockReturnValue(parked())
    const runQuery = vi
      .fn()
      .mockResolvedValueOnce(ok({ query_run: run() }))
      .mockResolvedValueOnce(ok({ query_run: run({ id: secondRunId, raw_sql: 'SELECT 1' }) }))
    render(<App api={api({ getRun, runQuery })} />)
    await submit()
    await submit()
    await advance(POLL_INTERVAL_MS * 3)

    expect(getRun).toHaveBeenLastCalledWith(secondRunId)
    expect(new URLSearchParams(window.location.search).get('run')).toBe(secondRunId)
  })
})
