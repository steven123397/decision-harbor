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
  status: QueryRunStatus
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
  data: { query_run: QueryRun } | null
  error: ApiError | null
}

export type ResultResponse = {
  data: { result: QueryResult } | null
  error: ApiError | null
}

export type ApiClient = {
  checkReady(): Promise<boolean>
  submitQuery(sql: string): Promise<QueryResponse>
  getQueryRun(id: string): Promise<QueryResponse>
  getQueryResult(id: string): Promise<ResultResponse>
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
      return {
        data: null,
        error: { code: 'service_not_ready', message: 'The service is not ready.' },
      }
    }
  },
  async getQueryRun(id) {
    try {
      const response = await fetch(`/api/v1/query-runs/${id}`)
      return (await response.json()) as QueryResponse
    } catch {
      return {
        data: null,
        error: { code: 'service_not_ready', message: 'The service is not ready.' },
      }
    }
  },
  async getQueryResult(id) {
    try {
      const response = await fetch(`/api/v1/query-runs/${id}/result`)
      return (await response.json()) as ResultResponse
    } catch {
      return {
        data: null,
        error: { code: 'service_not_ready', message: 'The service is not ready.' },
      }
    }
  },
}
