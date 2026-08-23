export type QueryRun = {
  id: string
  status: 'received' | 'queued' | 'running' | 'succeeded' | 'rejected' | 'failed'
  returned_row_count: number | null
  result_truncated: boolean | null
  duration_ms: number | null
  error_code: string | null
  error_summary: string | null
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
  data: { query_run: QueryRun; result?: QueryResult } | null
  error: ApiError | null
}

export type QueryResultResponse = {
  data: { result: QueryResult } | null
  error: ApiError | null
}

export type ApiClient = {
  checkReady(): Promise<boolean>
  runQuery(sql: string): Promise<QueryResponse>
  getQueryRun(runId: string): Promise<QueryResponse>
  getQueryResult(runId: string): Promise<QueryResultResponse>
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
  async runQuery(sql) {
    try {
      const response = await fetch('/api/v1/query-runs', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ sql }),
      })
      return (await response.json()) as QueryResponse
    } catch {
      return {
        data: null,
        error: { code: 'service_not_ready', message: 'The service is not ready.' },
      }
    }
  },
  async getQueryRun(runId) {
    try {
      const response = await fetch(`/api/v1/query-runs/${encodeURIComponent(runId)}`)
      return (await response.json()) as QueryResponse
    } catch {
      return {
        data: null,
        error: { code: 'service_not_ready', message: 'The service is not ready.' },
      }
    }
  },
  async getQueryResult(runId) {
    try {
      const response = await fetch(`/api/v1/query-runs/${encodeURIComponent(runId)}/result`)
      return (await response.json()) as QueryResultResponse
    } catch {
      return {
        data: null,
        error: { code: 'service_not_ready', message: 'The service is not ready.' },
      }
    }
  },
}
