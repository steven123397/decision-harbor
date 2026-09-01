export type QueryRunStatus =
  | 'received'
  | 'rejected'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelling'
  | 'cancelled'

export type QueryRun = {
  id: string
  raw_sql: string
  status: QueryRunStatus
  policy_decision: string
  policy_version: string
  referenced_objects: string[]
  statement_timeout_ms: number
  max_rows: number
  returned_row_count: number | null
  result_truncated: boolean | null
  error_code: string | null
  error_summary: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  retry_of: string | null
}

export type QueryResult = {
  columns: Array<{ name: string; type: string }>
  rows: Array<Array<null | boolean | number | string>>
  truncated: boolean
}

export type ApiError = {
  code: string
  message: string
  query_run_id?: string
}

export type QueryResponse = {
  data: { query_run: QueryRun } | null
  error: ApiError | null
}

export type ResultResponse = {
  data: { result: QueryResult } | null
  error: ApiError | null
}

export type HistoryResponse = {
  data: { query_runs: QueryRun[]; next_cursor: string | null } | null
  error: ApiError | null
}

export type ApiClient = {
  checkReady(): Promise<boolean>
  submitQuery(sql: string): Promise<QueryResponse>
  getQueryRun(id: string): Promise<QueryResponse>
  getQueryResult(id: string): Promise<ResultResponse>
  cancelRun(id: string): Promise<QueryResponse>
  retryRun(id: string): Promise<QueryResponse>
  listHistory(cursor?: string): Promise<HistoryResponse>
}

const SERVICE_NOT_READY: ApiError = { code: 'service_not_ready', message: 'The service is not ready.' }

async function postJson(path: string): Promise<QueryResponse> {
  try {
    const response = await fetch(path, { method: 'POST' })
    return (await response.json()) as QueryResponse
  } catch {
    return { data: null, error: SERVICE_NOT_READY }
  }
}

export const httpApi: ApiClient = {
  async checkReady() {
    try {
      const response = await fetch('/ready')
      return response.ok
    } catch {
      return false
    }
  },
  async submitQuery(sql) {
    try {
      const response = await fetch('/api/v1/query-runs', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sql }),
      })
      return (await response.json()) as QueryResponse
    } catch {
      return { data: null, error: SERVICE_NOT_READY }
    }
  },
  async getQueryRun(id) {
    try {
      const response = await fetch(`/api/v1/query-runs/${id}`)
      return (await response.json()) as QueryResponse
    } catch {
      return { data: null, error: SERVICE_NOT_READY }
    }
  },
  async getQueryResult(id) {
    try {
      const response = await fetch(`/api/v1/query-runs/${id}/result`)
      return (await response.json()) as ResultResponse
    } catch {
      return { data: null, error: SERVICE_NOT_READY }
    }
  },
  cancelRun(id) {
    return postJson(`/api/v1/query-runs/${id}/cancel`)
  },
  retryRun(id) {
    return postJson(`/api/v1/query-runs/${id}/retry`)
  },
  async listHistory(cursor) {
    try {
      const params = new URLSearchParams()
      if (cursor !== undefined) params.set('cursor', cursor)
      const query = params.size > 0 ? `?${params.toString()}` : ''
      const response = await fetch(`/api/v1/query-runs${query}`)
      return (await response.json()) as HistoryResponse
    } catch {
      return { data: null, error: SERVICE_NOT_READY }
    }
  },
}
