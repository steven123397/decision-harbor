export type RunStatus =
  | 'received'
  | 'rejected'
  | 'queued'
  | 'running'
  | 'cancelling'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export type QueryRun = {
  id: string
  raw_sql: string
  status: RunStatus
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

export type Envelope<T> = {
  data: T | null
  error: ApiError | null
}

export type RunResponse = Envelope<{ query_run: QueryRun }>

export type ResultResponse = Envelope<{ result: QueryResult }>

/** The code a failed request carries when the service could not be reached. */
export const TRANSPORT_ERROR = 'network_error'

export type ApiClient = {
  checkReady(): Promise<boolean>
  runQuery(sql: string): Promise<RunResponse>
  getRun(runId: string): Promise<RunResponse>
  getResult(runId: string): Promise<ResultResponse>
}

async function request<T>(path: string, init?: RequestInit): Promise<Envelope<T>> {
  try {
    const response = await fetch(path, init)
    return (await response.json()) as Envelope<T>
  } catch {
    return { data: null, error: { code: TRANSPORT_ERROR, message: 'The service could not be reached.' } }
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
  async runQuery(sql) {
    return request('/api/v1/query-runs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ sql }),
    })
  },
  async getRun(runId) {
    return request(`/api/v1/query-runs/${encodeURIComponent(runId)}`)
  },
  async getResult(runId) {
    return request(`/api/v1/query-runs/${encodeURIComponent(runId)}/result`)
  },
}
