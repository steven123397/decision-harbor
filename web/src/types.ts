export type RunStatus = 'running' | 'succeeded' | 'failed' | 'rejected'

export interface ErrorDetail {
  code: string
  message: string
}

export interface ResultColumn {
  name: string
  type: string
}

export interface ResultDetail {
  columns: ResultColumn[]
  rows: unknown[][]
}

export interface QueryRun {
  id: string
  sql: string
  status: RunStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  row_count: number | null
  duration_ms: number | null
  error: ErrorDetail | null
  result: ResultDetail | null
}
